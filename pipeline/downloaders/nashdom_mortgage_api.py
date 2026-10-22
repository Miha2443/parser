"""Normalize captured public mortgage responses; no transport or browser code.

Only a matching monthly general/details pair is accepted. Annual summaries and
foreign-currency details remain source evidence, never monthly measurements.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal, localcontext
import json
import math
import re


ORIGIN = "https://xn--80az8a.xn--d1aqf.xn--p1ai"
ENDPOINTS = {
    name: f"{ORIGIN}/portal-analytics/api/mortgage/dashboard/{name}"
    for name in ("general", "details")
}
REGION_IDS = {"rf": 1, "msk": 1762}
METRICS = ("volumeRub", "count", "ratePct", "termMonths", "averageAmountRub")
_PREFIXES = {"all": "total", "primary": "primary", "secondary": "secondary"}


class MortgageAPIError(ValueError):
    """Captured responses do not establish the requested monthly observation."""


def _scope(region, period):
    if not isinstance(region, str) or region not in REGION_IDS:
        raise MortgageAPIError("unsupported mortgage region")
    if not isinstance(period, str) or not re.fullmatch(r"[1-9][0-9]{3}-[0-9]{2}", period):
        raise MortgageAPIError("invalid mortgage month")
    try:
        date.fromisoformat(period + "-01")
    except ValueError as exc:
        raise MortgageAPIError("invalid mortgage month") from exc


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise MortgageAPIError("duplicate JSON key")
        result[key] = value
    return result


def _constant(value):
    raise MortgageAPIError("non-finite JSON number")


def _object(value, label):
    if isinstance(value, str):
        try:
            value = json.loads(value, object_pairs_hook=_pairs, parse_constant=_constant)
        except (ValueError, RecursionError) as exc:
            raise MortgageAPIError(f"invalid {label} JSON") from exc
    if not isinstance(value, dict):
        raise MortgageAPIError(f"{label} must be a JSON object")
    return value


def match_request(url, method, payload, region, period):
    """Return the verified endpoint name, or None for any other request scope.

    Payloads may be decoded objects or JSON strings captured from a POST. Unknown
    request fields are rejected because they may introduce an unverified subset.
    """
    try:
        _scope(region, period)
        if not isinstance(url, str) or not isinstance(method, str) or method.upper() != "POST":
            return None
        endpoint = next((key for key, value in ENDPOINTS.items() if url == value), None)
        if endpoint is None:
            return None
        payload = _object(payload, "request")
        expected = {"regionId": REGION_IDS[region], "reportingPeriod": period + "-01",
                    "periodType": "month"}
        if endpoint == "general":
            expected["currency"] = "ALL"
        if type(payload.get("regionId")) is not int or payload != expected:
            return None
        return endpoint
    except (MortgageAPIError, TypeError, RecursionError):
        return None


def _clone_json(value):
    # Detach evidence from the caller without accepting custom/non-JSON values.
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise MortgageAPIError("non-finite source evidence")
        return value
    if isinstance(value, list):
        return [_clone_json(item) for item in value]
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return {key: _clone_json(item) for key, item in value.items()}
    raise MortgageAPIError("source evidence must contain only JSON values")


def _number(value, key):
    if value is None:
        return None
    if type(value) not in (int, float) or value < 0:
        raise MortgageAPIError(f"invalid source metric: {key}")
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise MortgageAPIError(f"invalid source metric: {key}")
    return value


def _metric(data, key):
    if key not in data:
        raise MortgageAPIError(f"missing source metric: {key}")
    return _number(data[key], key)


def _term_months(years):
    if years is None:
        return None
    number = Decimal(str(years))
    with localcontext() as context:
        context.prec = max(28, len(number.as_tuple().digits) + 2)
        months = number * 12
    result = int(months) if months == months.to_integral_value() else float(months)
    return _number(result, "termMonths")


def normalize_month(region, period, exchanges):
    """Normalize one scope-matched general/details pair.

    Evidence retains public request filters and the complete response body, not
    transport headers or session metadata. API amounts are rubles, counts are units, RUB rates are percent;
    only the RUB details' term in years needs conversion to months.
    """
    _scope(region, period)
    if not isinstance(exchanges, list) or len(exchanges) != 2:
        raise MortgageAPIError("one general and one details exchange are required")
    try:
        if not all(isinstance(item, dict) for item in exchanges):
            raise MortgageAPIError("exchange must be a JSON object")
        evidence = _clone_json([{key: item[key] for key in
            ("url", "method", "request", "status", "mimeType", "body") if key in item}
            for item in exchanges])
    except RecursionError as exc:
        raise MortgageAPIError("invalid source evidence nesting") from exc
    responses = {}
    details_unavailable = False
    for exchange in evidence:
        if not isinstance(exchange, dict):
            raise MortgageAPIError("exchange must be a JSON object")
        endpoint = match_request(exchange.get("url"), exchange.get("method"),
                                 exchange.get("request"), region, period)
        if endpoint is None:
            raise MortgageAPIError("exchange URL or request scope does not match")
        if endpoint in responses:
            raise MortgageAPIError("duplicate endpoint exchange")
        body = _object(exchange.get("body"), "response")
        status = exchange.get("status")
        if (endpoint == "details" and type(status) is int and status == 500
                and body.get("status") == "INTERNAL_SERVER_ERROR"
                and body.get("code") == "500" and "data" not in body):
            responses[endpoint] = None
            details_unavailable = True
            continue
        if type(status) is not int or status != 200:
            raise MortgageAPIError("source response status must be 200")
        if "data" not in body:
            raise MortgageAPIError("source response is missing data")
        responses[endpoint] = body["data"]
    if set(responses) != set(ENDPOINTS):
        raise MortgageAPIError("one general and one details exchange are required")
    general, details = responses["general"], responses["details"]
    if not isinstance(general, dict):
        raise MortgageAPIError("general data must be an object")
    if not details_unavailable and (not isinstance(details, list)
                                   or not all(isinstance(row, dict) for row in details)):
        raise MortgageAPIError("details data must be a list of objects")
    rubles = [] if details_unavailable else [row for row in details if row.get("currency") == "RUB"]
    if not details_unavailable and len(rubles) != 1:
        raise MortgageAPIError("exactly one RUB details row is required")
    metrics = {}
    for segment, prefix in _PREFIXES.items():
        metrics[segment] = {
            "volumeRub": _metric(general, prefix + "CreditAmount"),
            "count": _metric(general, prefix + "CreditCount"),
            "ratePct": _metric(general, prefix + "CreditAvgRate"),
            "termMonths": None,
            "averageAmountRub": None,
        }
    if rubles:
        metrics["all"]["termMonths"] = _term_months(_metric(rubles[0], "creditAvgPeriod"))
        metrics["all"]["averageAmountRub"] = _metric(rubles[0], "creditAmountAvg")
    return {"region": region, "period": period, "basis": "month", "metrics": metrics,
            "rawMetrics": {"exchanges": evidence}, "sourcePrecision": "raw response"}
