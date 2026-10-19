"""Read the existing operational pages using their normal period controls."""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

from pipeline.downloaders.nashdom_operational_contract import (
    AccessVerificationRequired, BASE, EmptyReport, MONTHS, OPTIONAL_SALES,
    ReportError, SALES_FIELDS, TransportError, numeric, period, utc_now,
)

PAGE_PATHS = {
    "construction": "/" + quote("аналитика/показатели_жилищного_строительства"),
    "sales": "/" + quote("аналитика/реализация_строящихся_квартир"),
}
REGIONS = {"rf": "Все", "msk": "Город Москва"}

# Observe requests made by the page itself. Do not issue requests, read response
# bodies, change headers/cookies, or interact with an access-verification page.
TRACK_REQUESTS = """
const tracker = {pending:0, finished:[], changed:performance.now()};
window.__operationalHistoryRequests = tracker;
const relevant = value => {
  try { const u=new URL(value, location.href); return u.origin===location.origin && u.pathname.includes('/api/'); }
  catch { return false; }
};
const begin = url => {
  if (!relevant(url)) return null;
  tracker.pending++; tracker.changed=performance.now();
  return {url:new URL(url,location.href).href, started:performance.now()};
};
const end = (item,status) => {
  if (!item || item.done) return;
  item.done=true;
  tracker.pending--; tracker.changed=performance.now();
  tracker.finished.push({url:item.url,status});
  if(tracker.finished.length>80) tracker.finished.shift();
};
const fetchOriginal=window.fetch;
const fetches=[];
const observer=new PerformanceObserver(list=>{
  for(const entry of list.getEntries()) {
    if(entry.initiatorType!=='fetch' || !(entry.responseEnd>0)) continue;
    const item=fetches.findLast(value=>!value.done && !value.bodyComplete && value.url===entry.name && entry.startTime>=value.started);
    if(!item) continue;
    item.bodyComplete=true;
    if(item.status!==undefined) end(item,item.status);
  }
});
observer.observe({entryTypes:['resource']});
window.fetch=function(...args) {
  const item=begin(typeof args[0]==='string' ? args[0] : args[0]?.url);
  if(item) fetches.push(item);
  try { return fetchOriginal.apply(this,args).then(response=>{
    if(item) {item.status=response.status;if(item.bodyComplete) end(item,item.status);}
    return response;
  },error=>{end(item,0);throw error;}); }
  catch(error) {end(item,0);throw error;}
};
const openOriginal=XMLHttpRequest.prototype.open, sendOriginal=XMLHttpRequest.prototype.send;
const requests=new WeakMap();
XMLHttpRequest.prototype.open=function(method,url,...args) {
  requests.set(this,url); return openOriginal.call(this,method,url,...args);
};
XMLHttpRequest.prototype.send=function(...args) {
  const item=begin(requests.get(this));
  this.addEventListener('loadend',()=>end(item,this.status),{once:true});
  try {return sendOriginal.apply(this,args);} catch(error) {end(item,0);throw error;}
};
"""


def page_url(block):
    if block not in PAGE_PATHS:
        raise ReportError("unknown operational page")
    return BASE + PAGE_PATHS[block]


def page_number(value, field):
    """Convert a literal displayed cell, never derive an area from a share."""
    if value is None or str(value).strip() in {"", "—", "-"}:
        if field in OPTIONAL_SALES:
            return None
        raise ReportError("missing page measurement: " + field)
    if isinstance(value, (int, float)):
        return numeric(value)
    if not isinstance(value, str):
        raise ReportError("invalid page measurement: " + field)
    units = ("%",) if field.endswith("_pct") else ("млн руб.", "млн руб") if field == "funds_million_rub" else ("руб.", "руб") if field.endswith("_rub") else ("тыс. м²", "тыс. м2")
    text = value.strip()
    for unit in units:
        if text.endswith(unit):
            text = text[:-len(unit)].strip()
            break
    if not re.fullmatch(r"\d[\d \u00a0\u202f]*(?:[,.]\d+)?", text):
        raise ReportError("invalid literal page measurement: " + field)
    return numeric(float(re.sub(r"\s", "", text).replace(",", ".")))


def matching_request(item, block, month, region=None):
    if not isinstance(item, dict) or item.get("status") != 200:
        return False
    location = urlsplit(str(item.get("url", "")))
    if location.scheme + "://" + location.netloc != BASE:
        return False
    prefix = "/" + quote("аналитика") + "/api/" + ("consolidated/housing_constructor/" if block == "construction" else "rpp/")
    if not location.path.startswith(prefix):
        return False
    query = parse_qs(location.query)
    year, num = map(int, period(month).split("-"))
    selected_region = query.get("regionCode", query.get("regionCd"))
    if region == "msk" and selected_region != ["77"]:
        return False
    if region == "rf" and selected_region not in (None, ["all"]):
        return False
    return query.get("repYear") == [str(year)] and query.get("repMonth") == [str(num)]


def selected_month_matches(value, month):
    try:
        return period(value) == month
    except ReportError:
        return False


def normalize_page(block, month, observations, *, collected_at):
    month = period(month)
    expected = {(r, k) for r in REGIONS for k in ("living", "total")} if block == "construction" else {(r, None) for r in REGIONS}
    if not isinstance(observations, list) or len(observations) != len(expected):
        raise ReportError("incomplete operational page observations")
    found, rows = set(), []
    for item in observations:
        if not isinstance(item, dict):
            raise ReportError("invalid operational page observation")
        region, kind = item.get("region_key"), item.get("area_kind")
        key = (region, kind if block == "construction" else None)
        if key not in expected or key in found:
            raise ReportError("duplicate or wrong page region/mode")
        found.add(key)
        raw, proof = item.get("snapshot"), item.get("selection")
        if (item.get("url") != page_url(block) or item.get("requested_period") != month
                or not isinstance(raw, dict) or not isinstance(proof, dict)
                or raw.get("region") != REGIONS[region]
                or period(raw.get("report_period")) != month
                or period(proof.get("selected_period")) != month
                or proof.get("render_stable") is not True or proof.get("pending_requests") != 0):
            raise ReportError("page selection/measurement identity mismatch")
        if proof.get("kind") == "initial_page":
            if period(proof.get("latest_period")) != month:
                raise ReportError("initial page cannot prove a historical selection")
        elif proof.get("kind") == "period_control":
            if not any(matching_request(request, block, month, region) for request in proof.get("completed_requests", [])):
                raise ReportError("selected month has no completed page request")
        else:
            raise ReportError("unconfirmed page selection")
        date = raw.get("report_date", "")
        if date:
            from datetime import datetime
            try:
                datetime.strptime(date, "%d.%m.%Y")
            except (TypeError, ValueError) as exc:
                raise ReportError("invalid page report date") from exc
        # Some pages retain their global latest publication date after changing
        # the month. It is not evidence of a historical report's publication date.
        if proof.get("kind") == "period_control" and (
                not proof.get("initial_report_date") or date == proof["initial_report_date"]):
            date = ""
        year, num = map(int, month.split("-"))
        common = {"region_key": region, "region": REGIONS[region],
                  "report_period": f"{MONTHS[num-1]} {year}", "report_date": date,
                  "collected_at": collected_at, "period_verified": True,
                  "source_url": page_url(block), "evidence_kind": "browser_page",
                  "page_evidence": item, "report_date_evidence": "selected rendered page" if date else "not supplied"}
        if block == "construction":
            if raw.get("area_kind") != kind:
                raise ReportError("page area mode mismatch")
            value = page_number(raw.get("area_thousand_m2"), "area_thousand_m2")
            if value <= 0:
                raise EmptyReport("empty construction page measurement")
            rows.append({**common, "area_kind": kind, "area_thousand_m2": value})
        else:
            values = raw.get("metrics")
            if not isinstance(values, dict) or set(values) != set(SALES_FIELDS):
                raise ReportError("incomplete apartment page measurements")
            metrics = {field: page_number(values[field], field) for field in SALES_FIELDS}
            if metrics["total_living_thousand_m2"] <= 0:
                raise EmptyReport("empty apartment page measurement")
            if (abs(metrics["sold_thousand_m2"] + metrics["unsold_thousand_m2"] - metrics["sales_open_thousand_m2"]) > 2
                    or abs(metrics["sales_open_thousand_m2"] + metrics["sales_not_open_thousand_m2"] - metrics["total_living_thousand_m2"]) > 2):
                raise ReportError("page apartment component areas differ")
            percentages = [metrics[k] for k in metrics if k.endswith("_pct")]
            if any(v is not None and v > 100 for v in percentages) or (all(v is not None for v in percentages) and abs(sum(percentages) - 100) > 2):
                raise ReportError("invalid page apartment shares")
            rows.append({**common, "metrics": metrics})
    return rows


class BrowserClient:
    """One ordinary browser, existing page readers and calendar controls."""
    def __init__(self, raw_dir, *, delay=0.5):
        self.raw_dir, self.delay = Path(raw_dir), delay
        self.driver = None
        self.bounds = {}

    def __enter__(self):
        try:
            for block in PAGE_PATHS:
                self._load(block, "rf")
                self.bounds[block] = period(self._selected())
            return self
        except BaseException:
            self.__exit__()
            raise

    def _selected(self):
        from nashdom_checker import _get_rasprod_current_period
        return _get_rasprod_current_period(self.driver)

    def _access_check(self):
        state = self.driver.execute_script("""
          const visible=e=>{const r=e.getBoundingClientRect();return r.width>0&&r.height>0&&getComputedStyle(e).visibility!=='hidden';};
          return {denied:/доступ\\s+заблокирован|access\\s+denied|forbidden/i.test(document.title),
            captcha:[...document.querySelectorAll('iframe[src*="captcha" i], #id_captcha_frame_div canvas, #id_captcha_frame_div input, #id_captcha_frame_div button, #id_captcha_frame_div img')].some(visible)};
        """)
        if isinstance(state, dict) and (state.get("denied") or state.get("captcha")):
            raise AccessVerificationRequired("DOM.RF denied ordinary page access or requires manual verification; collection stopped")

    def _load(self, block, region):
        import nashdom_checker as source
        if self.driver is None:
            self.driver = source.create_chrome(download_dir=self.raw_dir, headless=source.HEADLESS)
        self._access_check()
        self.driver, ready = source._load_operational_region(self.driver, page_url(block), block, REGIONS[region])
        self._access_check()
        if not ready:
            raise TransportError("ordinary operational page/region did not load: " + block + "/" + region)
        self.driver.execute_script(TRACK_REQUESTS)

    def _network_state(self):
        return self.driver.execute_script("""
          const t=window.__operationalHistoryRequests;
          return t ? {pending:t.pending, idle:performance.now()-t.changed, finished:t.finished} : null;
        """)

    def _select(self, block, month, region):
        import nashdom_checker as source
        from selenium.webdriver.support.ui import WebDriverWait
        before = period(self._selected())
        if before == month:
            return {"kind": "initial_page", "latest_period": before}
        initial_date = source._parse_russian_date(self.driver.execute_script("return document.body.innerText") or "")
        year, num = map(int, month.split("-"))
        if not source._open_date_picker(self.driver) or not source._navigate_calendar_to_year(self.driver, year):
            raise ReportError("operational month picker could not select year")
        if num - 1 not in source._get_available_month_indices(self.driver):
            self.driver.execute_script("document.body.click()")
            raise EmptyReport("month is disabled on the operational page: " + month)
        if not source._click_month_by_index(self.driver, num - 1):
            raise ReportError("operational month picker did not select month")

        def completed(driver):
            self._access_check()
            state = self._network_state()
            return (state if selected_month_matches(self._selected(), month) and isinstance(state, dict)
                    and state.get("pending") == 0 and state.get("idle", 0) >= 350
                    and any(matching_request(item, block, month, region) for item in state.get("finished", [])) else False)

        state = WebDriverWait(self.driver, 45, poll_frequency=0.25).until(completed)
        return {"kind": "period_control", "initial_report_date": initial_date,
                "completed_requests": [item for item in state["finished"] if matching_request(item, block, month, region)]}

    def _snapshot(self, block, region, kind, month):
        import nashdom_checker as source
        from selenium.webdriver.support.ui import WebDriverWait
        previous, stable = None, 0

        def rendered(driver):
            nonlocal previous, stable
            self._access_check()
            if not selected_month_matches(self._selected(), month) or source._operational_selected_region(driver) != REGIONS[region]:
                return False
            try:
                raw = (source._read_construction_snapshot(driver) if block == "construction" else
                       source._parse_apartment_sales_html(driver.page_source, source._operational_selected_region(driver)))
            except ValueError:
                return False
            if not selected_month_matches(raw.get("report_period"), month) or (kind and raw.get("area_kind") != kind):
                return False
            state = self._network_state()
            if not isinstance(state, dict) or state.get("pending") != 0 or state.get("idle", 0) < 350:
                stable = 0
                return False
            signature = hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest()
            stable = stable + 1 if signature == previous else 0
            previous = signature
            return raw if stable >= 2 else False

        return WebDriverWait(self.driver, 35, poll_frequency=0.25).until(rendered)

    def collect(self, block, month):
        import nashdom_checker as source
        from selenium.common.exceptions import TimeoutException, WebDriverException
        month = period(month)
        if block not in self.bounds or month > self.bounds[block]:
            raise ReportError("requested month exceeds observed page period")
        observations = []
        try:
            for region in REGIONS:
                time.sleep(self.delay)
                self._load(block, region)
                proof = self._select(block, month, region)
                for kind, label in (("living", "Жилая"), ("total", "Общая")) if block == "construction" else ((None, None),):
                    if kind and not source._click_area_mode(self.driver, label):
                        raise ReportError("operational area mode did not switch")
                    raw = self._snapshot(block, region, kind, month)
                    observations.append({"url": page_url(block), "region_key": region,
                                         **({"area_kind": kind} if kind else {}),
                                         "requested_period": month, "snapshot": raw,
                                         "selection": {**proof, "selected_period": self._selected(),
                                                       "render_stable": True, "pending_requests": 0}})
            stamp = utc_now()
            return {"schemaVersion": 1, "evidence_kind": "browser_page", "block": block,
                    "period": month, "collected_at": stamp, "observations": observations,
                    "rows": normalize_page(block, month, observations, collected_at=stamp)}
        except (TimeoutException, WebDriverException) as exc:
            raise TransportError("ordinary operational page did not finish selected period: " + block + "/" + month + "; " + type(exc).__name__) from exc

    def __exit__(self, *args):
        driver, self.driver = self.driver, None
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass
