"""Read the report API used by Nashdom's page, with explicit period/region.

Requests run in the site's browser session: plain HTTP clients receive 403.
No calendar clicks or rounded DOM values are used as period evidence.
"""
from __future__ import annotations

import math
from datetime import datetime
from urllib.parse import urlencode


API_PATH = "/portal-analytics/api/ready-construction/"
MONTHS = ("Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
          "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь")
METRICS = (
    ("squareSumChart", "squareSumIndex", "squareSum", "Объем жилищного строительства"),
    ("soldPercChart", "soldPercIndex", "soldPerc", "Распроданность"),
    ("soldReadyPercChart", "soldReadyPercIndex", "soldReadyPerc", "Отношение распроданности к стройготовности"),
    ("readyPercChart", "readyPercIndex", "readyPerc", "Стройготовность"),
)
SECTIONS = {
    "foChart": "Федеральные округа",
    "regionChart": "Регионы",
    "devChart": "Девелоперы",
    "devValueChart": "Объём строительства девелоперов",
    "cityPopulationChart": "Населённые пункты по численности",
    "classChart": "Класс недвижимости",
}


class ReportError(ValueError):
    pass


def number(value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ReportError(f"expected numeric API value, got {value!r}")
    if not math.isfinite(value) or value < 0:
        raise ReportError(f"invalid API value: {value!r}")
    return float(value)


def optional_text(value, *, divisor=1) -> str:
    # A missing developer/forecast metric is a source blank, never a zero.
    return "" if value is None else str(number(value) / divisor)


def urls(region: str, endpoints: list[str], year: int | None = None,
         month: int | None = None) -> list[str]:
    if region not in {"rf", "msk"}:
        raise ReportError("unknown report region")
    if (year is None) != (month is None):
        raise ReportError("year and month must be supplied together")
    if year is not None and (type(year) is not int or type(month) is not int
                             or not 2020 <= year <= datetime.now().year or not 1 <= month <= 12):
        raise ReportError("invalid report period")
    result = []
    for endpoint in endpoints:
        if endpoint not in {"dynamics", "index", "charts", "ready-year-charts"}:
            raise ReportError("unknown report endpoint")
        query = {}
        if endpoint == "dynamics":
            query["dynamicChartType"] = ",".join(metric[0] for metric in METRICS)
        elif year is not None:
            query.update(repMonth=month, repYear=year)
        if region == "msk":
            query["regionCd"] = "77"
        result.append(API_PATH + endpoint + "?" + urlencode(query))
    return result


def fetch(driver, request_urls: list[str]) -> list[dict]:
    """Fetch a small batch in parallel, with a bounded timeout per request."""
    driver.set_script_timeout(30)
    result = driver.execute_async_script(r"""
        const done = arguments[arguments.length - 1];
        Promise.all(arguments[0].map(async url => {
            const controller = new AbortController();
            const timer = setTimeout(() => controller.abort(), 20000);
            try {
                const response = await fetch(url, {signal: controller.signal,
                    credentials: 'same-origin', cache: 'no-store'});
                if (!response.ok) return {url, status: response.status};
                return {url, status: response.status, data: await response.json()};
            } catch (error) {
                return {url, error: String(error)};
            } finally { clearTimeout(timer); }
        })).then(done).catch(error => done([{error: String(error)}]));
    """, request_urls)
    if not isinstance(result, list) or len(result) != len(request_urls):
        raise ReportError("incomplete API batch")
    data = []
    for url, response in zip(request_urls, result):
        if (not isinstance(response, dict) or response.get("url") != url
                or response.get("status") != 200 or not isinstance(response.get("data"), dict)):
            raise ReportError(f"API request failed: {url}: {response}")
        data.append(response["data"])
    return data


def history(payload: dict) -> dict[tuple[int, int], dict[str, float]]:
    """Correlate all four exact monthly series; reject gaps between metrics."""
    charts = payload.get("dynamicCharts", [])
    series = {}
    for chart_key, index_key, _, _ in METRICS:
        candidates = [chart for chart in charts if chart.get("dynamicChartType") == chart_key]
        if len(candidates) != 1:
            raise ReportError(f"missing/duplicate historical series: {chart_key}")
        values = {}
        for year in candidates[0].get("repYears", []):
            y = year.get("repYear")
            for row in year.get("repMonths", []):
                m = row.get("repMonth")
                if type(y) is not int or type(m) is not int or not 1 <= m <= 12:
                    raise ReportError("invalid API history period")
                if not 2020 <= y <= datetime.now().year:
                    continue
                key = (y, m)
                if key in values:
                    raise ReportError("duplicate historical month")
                values[key] = number(row.get("value"))
        series[index_key] = values
    keys = set(next(iter(series.values())))
    if not keys or any(set(values) != keys for values in series.values()):
        raise ReportError("monthly API metric coverage differs")
    return {key: {metric: values[key] for metric, values in series.items()} for key in keys}


def matches_history(entry: dict, expected: dict) -> bool:
    """Reuse a checkpoint only while its exact KPIs still match the source."""
    if entry.get("api_verified") is not True:
        return False
    kpi = {row.get("название"): row.get("значение") for row in entry.get("kpi", [])}
    try:
        return all(math.isclose(float(kpi[title]) * (1000 if field == "squareSum" else 1),
                                number(expected[index]), rel_tol=0, abs_tol=0.011)
                   for _, index, field, title in METRICS)
    except (KeyError, TypeError, ValueError):
        return False


def report(region: str, year: int, month: int, expected: dict,
           index: dict, charts: dict, ready: dict, *, page_url: str) -> dict:
    """Convert source JSON into the existing dashboard's raw file schema."""
    request_urls = urls(region, ["index", "charts", "ready-year-charts"], year, month)
    for _, key, _, _ in METRICS:
        actual, wanted = number(index.get(key)), number(expected.get(key))
        if not math.isclose(actual, wanted, rel_tol=0, abs_tol=0.011):
            raise ReportError(f"{region}/{year}-{month:02}: index/history mismatch for {key}")
    if number(index["squareSumIndex"]) <= 0:
        raise ReportError("empty report")
    years = ready.get("readyYears")
    if not isinstance(years, list):
        raise ReportError("missing delivery-year breakdown")
    kpi = []
    latest_year = max((row.get("readyYear", 0) for row in years), default=0)
    for _, index_key, forecast_key, title in METRICS:
        volume = forecast_key == "squareSum"
        forecasts = {}
        for row in years:
            y = row.get("readyYear")
            if type(y) is not int or y < year:
                raise ReportError("invalid delivery year")
            label = str(y) + ("+" if y == latest_year else "")
            if label in forecasts:
                raise ReportError("duplicate delivery year")
            forecasts[label] = optional_text(row.get(forecast_key), divisor=1000 if volume else 1)
        value = number(index[index_key])
        kpi.append({"название": title, "значение": str(value / 1000 if volume else value),
                    "единица": "тыс. м²" if volume else "%", "по_годам": forecasts})
    tables = {}
    source_charts = charts.get("charts", [])
    for chart_type, section in SECTIONS.items():
        matches = [chart for chart in source_charts if chart.get("chartType") == chart_type]
        if len(matches) != 1 or not isinstance(matches[0].get("data"), list):
            raise ReportError(f"missing/duplicate table: {chart_type}")
        rows = matches[0]["data"]
        if region == "msk" and chart_type == "regionChart":
            if len(rows) != 1 or rows[0].get("regionCd") != 77:
                raise ReportError("Moscow report contains a different region")
        if chart_type == "regionChart":
            volume = sum(number(row.get("squareSum")) for row in rows)
            if not math.isclose(volume, index["squareSumIndex"], rel_tol=1e-7, abs_tol=1):
                raise ReportError("regional table total differs from report KPI")
        tables[section] = [
            {"наименование": str(row.get("key") or ""),
             "Объем жил. строительства": optional_text(row.get("squareSum")),
             "Распроданность": optional_text(row.get("soldPerc")),
             "Стройготовность": optional_text(row.get("readyPerc")),
             "Отношение распроданности к стройготовности": optional_text(row.get("soldReadyPerc")),
             **({"devCalcGroupId": row["devCalcGroupId"]} if "devCalcGroupId" in row else {})}
            for row in rows
        ]
    return {"report_date": "", "report_period": f"{MONTHS[month-1]} {year}",
            "region": "Москва" if region == "msk" else "Россия", "region_key": region,
            "year": year, "month_num": month, "month_name": MONTHS[month-1],
            "source": "rasprodannost", "url": page_url, "kpi": kpi, "tables": tables,
            "scraped_at": datetime.now().isoformat(timespec="seconds"),
            "api_verified": True, "api_urls": request_urls,
            "period_evidence": "explicit API period/region; index matches monthly dynamics"}
