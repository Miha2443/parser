"""The two official DOM.RF operational dashboards, with response identity checks."""
from __future__ import annotations

import json
import math
import re
import sys
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit, urlunsplit

import requests
from selenium.common.exceptions import InvalidSessionIdException, NoSuchWindowException, TimeoutException, WebDriverException
from selenium.webdriver.support.ui import WebDriverWait

BASE = "https://xn--80az8a.xn--d1aqf.xn--p1ai"
API = "/" + quote("аналитика") + "/api"
PATHS = {"construction": API + "/consolidated/housing_constructor", "sales": API + "/rpp"}
MAX_RESPONSE_BYTES = 1024 * 1024
BROWSER_DOCUMENT_TIMEOUT = 45
MONTHS = ("Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
          "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь")
SALES_FIELDS = {
    "total_living_thousand_m2": "totalSquare", "sales_open_thousand_m2": "openSquare",
    "sold_thousand_m2": "soldSquare", "unsold_thousand_m2": "unsoldSquare",
    "sales_not_open_thousand_m2": "unopenedSquare", "sold_pct": "soldPerc",
    "unsold_pct": "unsoldPerc", "sales_not_open_pct": "unopenedPerc",
    "price_per_m2_rub": "priceAvg", "funds_million_rub": "soldAmount",
}
OPTIONAL_SALES = {"sold_pct", "unsold_pct", "sales_not_open_pct", "price_per_m2_rub", "funds_million_rub"}


class ReportError(ValueError):
    pass


class TransportError(ReportError):
    pass


class AccessVerificationRequired(TransportError):
    """The source requires permitted browser access; never solve a CAPTCHA."""


def verification_html(body):
    if not isinstance(body, str) or not body.lstrip("\ufeff \t\r\n").startswith("<"):
        return False

    class Markup(HTMLParser):
        found = False

        def handle_starttag(self, tag, attrs):
            values = dict(attrs)
            source = str(values.get("src") or values.get("href") or "").casefold()
            if (tag == "js-challenge-loader" or values.get("id") == "id_captcha_frame_div"
                    or "servicepipe.tech/loaders/" in source or "sp_rotated_captcha/" in source):
                self.found = True

    parser = Markup()
    parser.feed(body)
    return parser.found


def response_preview(body, limit=2048):
    if verification_html(body):
        return "[Source browser verification page; HTML omitted]"
    return "[HTML omitted]" if body.lstrip("\ufeff \t\r\n").startswith("<") else body[:limit]


class EmptyReport(ReportError):
    """A successful source envelope with no rows is not a transport failure."""


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def period(value):
    if not isinstance(value, str):
        raise ReportError("period must be text")
    parts = value.split()
    if len(parts) == 2 and parts[0].casefold() in {x.casefold() for x in MONTHS}:
        year, month = int(parts[1]), [x.casefold() for x in MONTHS].index(parts[0].casefold()) + 1
    elif re.fullmatch(r"\d{4}-\d{2}", value):
        year, month = map(int, value.split("-"))
    else:
        raise ReportError(f"invalid period: {value!r}")
    if not 2000 <= year <= 2100 or not 1 <= month <= 12:
        raise ReportError("period out of bounds")
    return f"{year:04d}-{month:02d}"


def numeric(value):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < 0):
        raise ReportError(f"invalid source measurement: {value!r}")
    return value


def request_url(block, month, region):
    year, num = map(int, period(month).split("-"))
    query = {"repYear": year, "repMonth": num}
    endpoint = "/total"
    if region == "msk":
        query["regionCode"] = 77
        if block == "construction":
            endpoint = "/region"
    elif region != "rf":
        raise ReportError("unknown region")
    return PATHS[block] + endpoint + "?" + urlencode(query)


def source_row(payload, month, *, subject="100"):
    if not isinstance(payload, dict) or str(payload.get("errcode")) != "0":
        raise ReportError("API error envelope")
    rows = payload.get("data")
    if rows == []:
        raise EmptyReport(f"source returned an empty monthly report for {month}")
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise ReportError("empty or ambiguous monthly source response")
    row = rows[0]
    year, num = map(int, period(month).split("-"))
    if (type(row.get("repYear")) is not int or type(row.get("repMonth")) is not int
            or (row["repYear"], row["repMonth"]) != (year, num)):
        raise ReportError(f"requested {month}, source returned a different period")
    if str(row.get("subject")) != subject:
        raise ReportError("source returned a different regional identity")
    return row


def normalize(block, month, responses, *, collected_at, report_date=""):
    """Keep literal API measurements, including source percentages/price/funds."""
    year, num = map(int, period(month).split("-"))
    result = []
    for region in ("rf", "msk"):
        response = responses[region]
        url = request_url(block, month, region)
        if response.get("url") != url:
            raise ReportError("response URL/scope mismatch")
        row = source_row(response["payload"], month,
                         subject="77" if block == "construction" and region == "msk" else "100")
        common = {"region_key": region, "region": "Все" if region == "rf" else "Город Москва",
                  "report_period": f"{MONTHS[num - 1]} {year}", "report_date": report_date,
                  "collected_at": collected_at, "period_verified": True, "source_url": BASE + url,
                  "period_evidence": {"repYear": row["repYear"], "repMonth": row["repMonth"]},
                  "report_date_evidence": "official current page" if report_date else "not supplied for historical selection"}
        if block == "construction":
            for kind, field in (("living", "livingSquareSum"), ("total", "generalSquareSum")):
                value = numeric(row.get(field))
                if value <= 0:
                    raise ReportError("empty construction measurement")
                result.append({**common, "area_kind": kind, "area_thousand_m2": value})
        else:
            # /total has a constant subject=100 even under a regionCode filter.
            # Verify the filtered region endpoint independently; never infer areas.
            identity = response.get("region_identity")
            if region == "msk":
                check = source_row(identity, month, subject="77")
                if numeric(check.get("totalSquare")) != numeric(row.get("totalSquare")):
                    raise ReportError("filtered sales total differs from Moscow regional response")
            metrics = {}
            for name, field in SALES_FIELDS.items():
                if field not in row:
                    raise ReportError(f"missing source measurement: {field}")
                value = row[field]
                metrics[name] = None if value is None and name in OPTIONAL_SALES else numeric(value)
            if metrics["total_living_thousand_m2"] <= 0:
                raise ReportError("empty sales measurement")
            if abs(metrics["sold_thousand_m2"] + metrics["unsold_thousand_m2"] - metrics["sales_open_thousand_m2"]) > 2:
                raise ReportError("sales component areas differ from open area")
            if abs(metrics["sales_open_thousand_m2"] + metrics["sales_not_open_thousand_m2"] - metrics["total_living_thousand_m2"]) > 2:
                raise ReportError("sales component areas differ from total")
            percentages = [v for k, v in metrics.items() if k.endswith("_pct")]
            if (any(v is not None and v > 100 for v in percentages)
                    or all(v is not None for v in percentages) and abs(sum(percentages) - 100) > 2):
                raise ReportError("invalid source percentages")
            result.append({**common, "metrics": metrics})
    return result


class OfficialClient:
    """Public API first; at most one same-origin browser, without UI hydration."""
    def __init__(self, raw_dir, *, delay=0.5):
        self.raw_dir, self.delay, self.driver = Path(raw_dir), delay, None
        self.session = None
        self.browser_attempted = False
        self.events = deque(maxlen=6)
        self.report_dates = {}
        self.page_periods = {}
        self.bounds = {}

    def __enter__(self):
        try:
            self.session = requests.Session()
            self.session.headers.update({"Accept": "application/json", "Referer": BASE + "/"})
            for block in ("sales", "construction"):
                meta = self.get(PATHS[block] + "/actualDate").get("data")
                if (not isinstance(meta, dict) or meta.get("complete") is not True
                        or type(meta.get("year")) is not int or type(meta.get("month")) is not int):
                    raise ReportError(f"invalid/incomplete latest official period: {block}")
                self.bounds[block] = period(f"{meta['year']:04d}-{meta['month']:02d}")
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    @staticmethod
    def _error(exc):
        message = exc.msg if isinstance(exc, WebDriverException) else str(exc)
        return f"{type(exc).__name__}: {str(message or '').strip() or '<empty message>'}"[:1024]

    def _diagnostic(self, stage, url, exc):
        evidence = {"at": utc_now(), "stage": stage, "url": BASE + url,
                    "error": self._error(exc), "events": list(self.events)}
        if self.driver is not None:
            try:
                snapshot = self.driver.execute_script("""
                    return {url:(location.origin + location.pathname).slice(0,1024),
                      readyState:document.readyState};
                """)
                if isinstance(snapshot, dict):
                    location = urlsplit(str(snapshot.get("url", "")))
                    evidence["browser"] = {
                        "url": urlunsplit((location.scheme, location.hostname or "", location.path, "", "")),
                        "readyState": snapshot.get("readyState") if snapshot.get("readyState") in
                                      {"loading", "interactive", "complete"} else "unknown",
                    }
            except Exception as snapshot_error:
                evidence["browser_error"] = self._error(snapshot_error)
        try:
            directory = self.raw_dir / ".construction_history" / "diagnostics"
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / f"transport_{time.time_ns()}_{uuid.uuid4().hex}.json"
            # All text fields and the event ring are bounded; retain only five files.
            path.write_text(json.dumps(evidence, ensure_ascii=True, indent=2), encoding="utf-8")
            for old in sorted(directory.glob("transport_*.json"), reverse=True)[5:]:
                old.unlink(missing_ok=True)
            return f"diagnostics={path}"
        except OSError as diagnostic_error:
            return f"diagnostics unavailable: {self._error(diagnostic_error)}"

    @staticmethod
    def _payload(body, url):
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeError) as exc:
            raise TransportError(f"non-JSON response: {url}") from exc
        if not isinstance(payload, dict) or str(payload.get("errcode")) != "0":
            raise ReportError(f"API error envelope: {url}")
        return payload

    def _http_get(self, url):
        last = None
        for attempt in range(2):
            time.sleep(self.delay if not attempt else max(2, self.delay))
            started = time.monotonic()
            try:
                with self.session.get(BASE + url, timeout=(5, 20), stream=True,
                                      allow_redirects=False) as response:
                    event = {"transport": "http", "url": url, "attempt": attempt + 1,
                             "status": response.status_code,
                             "content_type": response.headers.get("Content-Type", "")[:128]}
                    self.events.append(event)
                    if response.status_code != 200:
                        # Read only a small preview of an error page, never its full body.
                        preview = next(response.iter_content(chunk_size=2048), b"")
                        event["preview"] = response_preview(preview[:2048].decode("utf-8", errors="replace"))
                        if response.status_code in {401, 403}:
                            raise AccessVerificationRequired(f"HTTP status {response.status_code}; checking ordinary browser access: {url}")
                        raise TransportError(f"HTTP status {response.status_code}: {url}")
                    content = bytearray()
                    for chunk in response.iter_content(chunk_size=16384):
                        if len(content) + len(chunk) > MAX_RESPONSE_BYTES:
                            raise TransportError(f"HTTP response exceeds byte limit: {url}")
                        content.extend(chunk)
                        if time.monotonic() - started > 25:
                            raise TransportError(f"HTTP response exceeds time limit: {url}")
                    event["preview"] = response_preview(content.decode("utf-8", errors="replace"))
                    return self._payload(content, url)
            except AccessVerificationRequired:
                raise
            except (requests.RequestException, TransportError) as exc:
                last = exc
                self.events.append({"transport": "http", "url": url, "attempt": attempt + 1,
                                    "error": self._error(exc)})
        raise TransportError(f"HTTP transport failed: {url}: {self._error(last)}") from last

    def _wait_for_browser_document(self, url):
        expected = urlsplit(url)
        expected_query = "?" + expected.query if expected.query else ""
        state = {}
        verification_seen = False

        def ready(driver):
            nonlocal state, verification_seen
            try:
                state = driver.execute_script("""
                const visible = element => {
                  const rect = element.getBoundingClientRect(), style = getComputedStyle(element);
                  return rect.width > 0 && rect.height > 0 && style.display !== 'none'
                    && style.visibility !== 'hidden';
                };
                return {origin:location.origin, path:location.pathname.slice(0,1024),
                  queryMatches:location.search === arguments[0], readyState:document.readyState,
                  captchaVisible:[...document.querySelectorAll(
                    'iframe[src*="captcha" i], input[name*="captcha" i], '
                    + '#id_captcha_frame_div iframe, #id_captcha_frame_div canvas, '
                    + '#id_captcha_frame_div input, #id_captcha_frame_div button, #id_captcha_frame_div img'
                  )].some(visible),
                  challenge:!!document.querySelector('js-challenge-loader, script[src*="servicepipe.tech/loaders/"], script[src*="sp_rotated_captcha/"]'),
                  denied:/доступ\\s+заблокирован|access\\s+denied|forbidden/i.test(document.title)};
                """, expected_query)
            except WebDriverException as exc:
                message = self._error(exc).casefold()
                if (isinstance(exc, (InvalidSessionIdException, NoSuchWindowException))
                        or any(marker in message for marker in ("disconnected", "session deleted", "invalid session id", "no such window"))):
                    raise
                if any(marker in message for marker in ("execution context was destroyed", "cannot find context", "document unloaded")):
                    self.events.append({"transport": "browser_navigation", "event": "document_context_changed"})
                    return False
                raise
            if not isinstance(state, dict):
                raise TransportError("invalid browser document state")
            verification_seen |= state.get("challenge") is True
            if state.get("captchaVisible"):
                raise AccessVerificationRequired("DOM.RF requires a visible CAPTCHA; automatic collection stopped. Permitted access or manual verification is required.")
            if state.get("denied"):
                raise AccessVerificationRequired("DOM.RF denied browser access; automatic collection stopped. Permitted access is required.")
            return (state.get("origin") == BASE and state.get("path") == expected.path and state.get("queryMatches") is True
                    and state.get("readyState") == "complete" and state.get("challenge") is False)

        try:
            WebDriverWait(self.driver, BROWSER_DOCUMENT_TIMEOUT, poll_frequency=0.5).until(ready)
        except TimeoutException as exc:
            self.events.append({"transport": "browser_document", "state": state})
            if verification_seen:
                raise AccessVerificationRequired("DOM.RF browser verification did not finish; automatic collection stopped. Permitted access or manual verification is required.") from exc
            raise

    def _start_browser(self, url):
        from pipeline.selenium_utils import create_chrome

        self.browser_attempted = True
        try:
            self.driver = create_chrome(download_dir=self.raw_dir, headless=True, page_load_strategy="none")
            d = self.driver
            d.set_page_load_timeout(15)
            d.set_script_timeout(25)
            d.execute_cdp_cmd("Network.enable", {})
            # The shared helper hardcodes Windows/Chrome 124. Match the real OS/version.
            product = d.execute_cdp_cmd("Browser.getVersion", {})["product"].replace("HeadlessChrome/", "Chrome/")
            platform = ("X11; Linux x86_64" if sys.platform.startswith("linux") else
                        "Macintosh; Intel Mac OS X 10_15_7" if sys.platform == "darwin" else
                        "Windows NT 10.0; Win64; x64")
            d.execute_cdp_cmd("Network.setUserAgentOverride", {"userAgent":
                f"Mozilla/5.0 ({platform}) AppleWebKit/537.36 (KHTML, like Gecko) {product} Safari/537.36"})
            # A public JSON endpoint establishes the origin without loading any dashboard.
            try:
                d.get(BASE + url)
            except TimeoutException as exc:
                self.events.append({"transport": "browser_navigation", "error": self._error(exc)})
            self._wait_for_browser_document(url)
        except AccessVerificationRequired as exc:
            detail = self._diagnostic("browser_access_verification", url, exc)
            self._close_browser()
            raise AccessVerificationRequired(f"{exc}; {detail}") from exc
        except Exception as exc:
            detail = self._diagnostic("browser_initialization", url, exc)
            self._close_browser()
            raise TransportError(f"browser initialization failed: {self._error(exc)}; {detail}") from exc

    def _browser_get(self, url):
        if self.driver is None:
            raise TransportError("browser transport is unavailable after an earlier failure")
        last = None
        for attempt in range(2):
            time.sleep(self.delay if not attempt else max(2, self.delay))
            try:
                response = self.driver.execute_async_script("""
                const done=arguments[arguments.length-1], url=arguments[0];
                const controller=new AbortController(), timer=setTimeout(()=>controller.abort(),20000);
                fetch(url,{credentials:'same-origin',cache:'no-store',redirect:'error',
                           headers:{Accept:'application/json'},signal:controller.signal})
                  .then(async r=>{
                    const reader=r.body.getReader(), decoder=new TextDecoder();
                    let body='', size=0;
                    while(true) {
                      const {done:ended,value}=await reader.read();
                      if(ended) break;
                      size+=value.byteLength;
                      if(size>arguments[1]) { controller.abort(); throw Error('response exceeds byte limit'); }
                      body+=decoder.decode(value,{stream:true});
                    }
                    body+=decoder.decode();
                    done({status:r.status,body,content_type:r.headers.get('content-type')});
                  })
                  .catch(e=>done({error:String(e)})).finally(()=>clearTimeout(timer));
                """, url, MAX_RESPONSE_BYTES)
                if not isinstance(response, dict):
                    raise TransportError(f"invalid browser transport response: {type(response).__name__}")
                body = response.get("body", "")
                if not isinstance(body, str) or len(body.encode("utf-8")) > MAX_RESPONSE_BYTES:
                    raise TransportError("invalid/oversized browser response body")
                self.events.append({"transport": "browser", "url": url, "attempt": attempt + 1,
                                    "status": response.get("status"),
                                    "content_type": str(response.get("content_type", ""))[:128],
                                    "error": str(response.get("error", ""))[:1024], "preview": response_preview(body)})
                if response.get("status") in {401, 403} or verification_html(body):
                    reason = (f"denied API access with HTTP {response['status']}" if response.get("status") in {401, 403}
                              else "returned browser verification instead of JSON")
                    exc = AccessVerificationRequired(f"DOM.RF {reason}; automatic collection stopped. Permitted access or manual verification is required.")
                    detail = self._diagnostic("browser_access_verification", url, exc)
                    raise AccessVerificationRequired(f"{exc}; {detail}") from exc
                if response.get("status") != 200:
                    raise TransportError(f"browser fetch failed: status={response.get('status')} "
                                         f"error={str(response.get('error', ''))[:1024]}")
                return self._payload(body, url)
            except AccessVerificationRequired:
                raise
            except (WebDriverException, TransportError) as exc:
                last = exc
                self.events.append({"transport": "browser", "url": url, "attempt": attempt + 1,
                                    "error": self._error(exc)})
        detail = self._diagnostic("browser_fetch", url, last)
        raise TransportError(f"browser transport failed: {url}: {self._error(last)}; {detail}") from last

    def get(self, url):
        parsed = urlsplit(url)
        allowed = {path + suffix for path in PATHS.values() for suffix in ("/actualDate", "/total", "/region")}
        if parsed.scheme or parsed.netloc or parsed.fragment or parsed.path not in allowed:
            raise ReportError("only official operational API paths are allowed")
        if not self.browser_attempted:
            try:
                return self._http_get(url)
            except TransportError as exc:
                detail = self._diagnostic("http_fallback", url, exc)
                print(f"operational HTTP fallback: {exc}; {detail}", flush=True)
                self._start_browser(url)
        return self._browser_get(url)

    def collect(self, block, month):
        month = period(month)
        if month > self.bounds[block]:
            raise ReportError(f"requested {month} exceeds official {block} bound {self.bounds[block]}")
        responses = {}
        for region in ("rf", "msk"):
            url = request_url(block, month, region)
            responses[region] = {"url": url, "payload": self.get(url)}
            if block == "sales" and region == "msk":
                identity_url = url.replace("/total?", "/region?")
                responses[region]["region_identity"] = self.get(identity_url)
        stamp = utc_now()
        report_date = (self.report_dates.get(block, "")
                       if month == self.bounds[block] and self.page_periods.get(block) == month else "")
        rows = normalize(block, month, responses, collected_at=stamp, report_date=report_date)
        return {"schemaVersion": 1, "block": block, "period": month,
                "collected_at": stamp, "rows": rows, "responses": responses}

    def _close_browser(self):
        driver, self.driver = self.driver, None
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass

    def __exit__(self, *args):
        self._close_browser()
        if self.session is not None:
            session, self.session = self.session, None
            session.close()
