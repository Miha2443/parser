"""Pure operational source contracts, usable by the read-only backend."""
from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import quote, urlencode

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


def validate_current_snapshot(payload):
    """Current publication requires all literal measures, independently of history."""
    if not isinstance(payload, dict):
        raise ReportError("current snapshot is not an object")
    construction, sales = payload.get("construction"), payload.get("sales")
    if (not isinstance(construction, list) or len(construction) != 4
            or any(not isinstance(row, dict) for row in construction)
            or {(row.get("region_key"), row.get("area_kind")) for row in construction} !=
               {(region, kind) for region in ("rf", "msk") for kind in ("living", "total")}):
        raise ReportError("current construction RF/Moscow living/total coverage is incomplete")
    if (not isinstance(sales, list) or len(sales) != 2 or any(not isinstance(row, dict) for row in sales)
            or {row.get("region_key") for row in sales} != {"rf", "msk"}):
        raise ReportError("current apartment sales RF/Moscow coverage is incomplete")
    for rows in (construction, sales):
        if len({period(row.get("report_period")) for row in rows}) != 1:
            raise ReportError("current block report periods are inconsistent")
        for row in rows:
            date = row.get("report_date")
            if date:
                try:
                    datetime.strptime(date, "%d.%m.%Y")
                except (TypeError, ValueError) as exc:
                    raise ReportError("current block report date is invalid") from exc
    if any(numeric(row.get("area_thousand_m2")) <= 0 for row in construction):
        raise ReportError("current construction area is not positive")
    for row in sales:
        raw = row.get("metrics")
        if not isinstance(raw, dict) or not set(SALES_FIELDS) <= set(raw):
            raise ReportError("current apartment sales metrics are missing")
        values = {key: numeric(raw[key]) for key in SALES_FIELDS}
        if (abs(values["sold_thousand_m2"] + values["unsold_thousand_m2"] - values["sales_open_thousand_m2"]) > 2
                or abs(values["sales_open_thousand_m2"] + values["sales_not_open_thousand_m2"] - values["total_living_thousand_m2"]) > 2
                or abs(sum(values[key] for key in values if key.endswith("_pct")) - 100) > 1
                or any(values[key] > 100 for key in values if key.endswith("_pct"))):
            raise ReportError("current apartment sales measurements are inconsistent")
