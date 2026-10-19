"""The two official DOM.RF operational dashboards, with response identity checks."""
from __future__ import annotations

import math
import re
import time
from datetime import datetime, timezone
from urllib.parse import quote, urlencode

from selenium.webdriver.support.ui import WebDriverWait

BASE = "https://xn--80az8a.xn--d1aqf.xn--p1ai"
API = "/" + quote("аналитика") + "/api"
PATHS = {"construction": API + "/consolidated/housing_constructor", "sales": API + "/rpp"}
PAGES = {"construction": "показатели_жилищного_строительства", "sales": "реализация_строящихся_квартир"}
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
    """One browser, serial bounded requests, no maps or third-party telemetry."""
    def __init__(self, raw_dir, *, delay=0.5):
        self.raw_dir, self.delay, self.driver = raw_dir, delay, None
        self.report_dates = {}
        self.page_periods = {}
        self.bounds = {}

    def __enter__(self):
        from pipeline.selenium_utils import create_chrome
        self.driver = create_chrome(download_dir=self.raw_dir, headless=True, page_load_strategy="none")
        d = self.driver
        d.execute_cdp_cmd("Network.enable", {})
        d.execute_cdp_cmd("Network.setBlockedURLs", {"urls": [
            "*yandex*", "*maps*", "*tile*", "*mail.ru*", "*vk.com*", "*sendsay*", "*tag-manager*"]})
        # The shared helper advertises Chrome 124; use the installed browser's UA.
        product = d.execute_cdp_cmd("Browser.getVersion", {})["product"]
        d.execute_cdp_cmd("Network.setUserAgentOverride", {"userAgent":
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            f"(KHTML, like Gecko) {product} Safari/537.36"})
        try:
            for block in ("sales", "construction"):
                self.open_page(block)
                meta = self.get(PATHS[block] + "/actualDate")["data"]
                if not isinstance(meta, dict) or meta.get("complete") is not True:
                    raise ReportError("latest official period is not complete")
                self.bounds[block] = period(f"{meta['year']:04d}-{meta['month']:02d}")
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def open_page(self, block):
        self.driver.get(BASE + "/" + quote("аналитика/" + PAGES[block]))
        WebDriverWait(self.driver, 40).until(lambda d: d.execute_script(
            "return !!window.webpackJsonp_N_E && document.body.innerText.includes('Отчетный период')"))
        body = self.driver.execute_script("return document.body.innerText")
        from nashdom_checker import _parse_russian_date
        report = re.search(r"Отчёт по данным на[^\n]+", body)
        self.report_dates[block] = _parse_russian_date(report.group(0)) if report else ""
        selected = re.search(r"Отчетный период\s+([А-Яа-яЁё]+\s+\d{4})", body)
        self.page_periods[block] = period(selected.group(1)) if selected else None

    def get(self, url):
        self.driver.set_script_timeout(30)
        for attempt in range(2):
            time.sleep(self.delay if not attempt else max(2, self.delay))
            response = self.driver.execute_async_script("""
                const done=arguments[arguments.length-1], url=arguments[0];
                const controller=new AbortController(), timer=setTimeout(()=>controller.abort(),20000);
                fetch(url,{credentials:'same-origin',cache:'no-store',signal:controller.signal})
                  .then(async r=>done({status:r.status,body:await r.text()}))
                  .catch(e=>done({error:String(e)})).finally(()=>clearTimeout(timer));
            """, url)
            if isinstance(response, dict) and response.get("status") == 200:
                import json
                try:
                    payload = json.loads(response["body"])
                except (ValueError, KeyError) as exc:
                    raise TransportError(f"non-JSON response: {url}") from exc
                if not isinstance(payload, dict) or str(payload.get("errcode")) != "0":
                    raise ReportError(f"API error: {url}")
                return payload
            if attempt:
                raise TransportError(f"blocked/failed source: {url}: {response.get('status', response.get('error'))}")
        raise TransportError(url)

    def collect(self, block, month):
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

    def __exit__(self, *args):
        if self.driver:
            self.driver.quit()
            self.driver = None
