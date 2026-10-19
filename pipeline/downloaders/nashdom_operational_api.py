"""The two official DOM.RF operational dashboards, with response identity checks."""
from __future__ import annotations

import json
import sys
import time
import uuid
from collections import deque
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import requests
from selenium.common.exceptions import InvalidSessionIdException, NoSuchWindowException, TimeoutException, WebDriverException
from selenium.webdriver.support.ui import WebDriverWait

from pipeline.downloaders.nashdom_operational_contract import (
    AccessVerificationRequired, API, BASE, BROWSER_DOCUMENT_TIMEOUT, EmptyReport,
    MAX_RESPONSE_BYTES, MONTHS, OPTIONAL_SALES, PATHS, ReportError, SALES_FIELDS,
    TransportError, normalize, numeric, period, request_url, response_preview,
    source_row, utc_now, verification_html,
)


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
