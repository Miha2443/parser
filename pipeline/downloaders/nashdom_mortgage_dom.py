"""Parse the public mortgage page without requests or cumulative-to-month guesses.

``discover_page`` returns the literal selected controls and their offered options.
``parse_page`` adds numeric ``metrics[all|primary|secondary]`` and separate
``metricEvidence``. Bottom-card measurements live in ``details`` with their own
basis/period; they are never silently assigned to the main report's month.
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation

from bs4 import BeautifulSoup, Tag


class MortgageDOMError(ValueError):
    """The rendered page does not establish the requested measurement identity."""


class AccessVerificationRequired(MortgageDOMError):
    """The supplied document is an access-verification page, not source data."""


MONTHS = (
    "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
)
REGION_LABELS = {"г. Москва": "msk", "Российская Федерация": "rf"}
SEGMENTS = ("all", "primary", "secondary")
METRICS = ("volumeRub", "count", "ratePct", "termMonths", "averageAmountRub")
MAIN_TITLES = {
    "Количество выданных кредитов": "count",
    "Объем выданных ипотечных жилищных кредитов": "volumeRub",
    "Средневзвешенная ставка в рублях": "ratePct",
}
DETAIL_TITLES = {
    "Средняя сумма кредита": "averageAmountRub",
    "Средний срок кредита": "termMonths",
    "Объем задолженности по ипотечным кредитам": "debtRub",
    "Доля просроченных платежей в общей задолженности": "delinquencyPct",
}
_EMPTY = {"", "-", "—", "–"}
_NUMBER = re.compile(r"[+-]?\d+(?:[.,]\d+)?\Z")
_PERIOD = re.compile(r"(\d{4})-(\d{2})\Z")


def _text(node: Tag | None) -> str:
    return " ".join(node.get_text(" ", strip=True).split()) if node else ""


def _one(node, selector: str, *, optional: bool = False):
    matches = node.select(selector)
    if len(matches) != 1:
        if optional and not matches:
            return None
        raise MortgageDOMError(f"expected one {selector}, got {len(matches)}")
    return matches[0]


def _section(node, label: str):
    labels = [item for item in node.select(".lbl-def") if _text(item) == label]
    if len(labels) != 1:
        raise MortgageDOMError(f"missing or ambiguous control: {label}")
    return labels[0].parent


def _disabled(radio, control) -> bool:
    if radio.has_attr("disabled"):
        return True
    node = radio
    while isinstance(node, Tag):
        if str(node.get("aria-disabled", "")).casefold() == "true":
            return True
        if node is control:
            break
        node = node.parent
    return False


def _options(node) -> list[dict]:
    values, result = set(), []
    for radio in node.select('input[type="radio"]'):
        value = radio.get("value")
        label = radio.find_parent("label")
        if not isinstance(value, str) or not value or value in values or label is None:
            raise MortgageDOMError("invalid or duplicate control option")
        caption = _text(label)
        if not caption:
            raise MortgageDOMError("control option has no literal label")
        values.add(value)
        if _disabled(radio, node):
            continue
        result.append({"value": value, "label": caption})
    if not result:
        raise MortgageDOMError("control has no offered radio options")
    return result


def _control(node, *, button: bool = False, active: bool = False) -> dict:
    options = _options(node)
    checked = node.select('input[type="radio"][checked]')
    if len(checked) != 1:
        raise MortgageDOMError("control must have exactly one checked option")
    selected = checked[0]
    if _disabled(selected, node):
        raise MortgageDOMError("selected control option is disabled")
    option = next(item for item in options if item["value"] == selected["value"])
    if active:
        active_radios = node.select('label.active input[type="radio"]')
        if len(active_radios) != 1 or active_radios[0] is not selected:
            raise MortgageDOMError("checked and active control options disagree")
    result = {"value": option["value"], "label": option["label"], "options": options}
    if button:
        caption = _text(_one(node, ".filter-list__btn button.custom-select"))
        if caption != option["label"]:
            raise MortgageDOMError("checked option differs from displayed control")
        result["buttonLabel"] = caption
    return result


def _soup(html: str):
    if not isinstance(html, str) or not html.strip():
        raise MortgageDOMError("empty mortgage page HTML")
    soup = BeautifulSoup(html, "html.parser")
    title = _text(soup.title).casefold()
    visible = _text(soup.body or soup).casefold()
    if (soup.select_one("js-challenge-loader, #id_captcha_frame_div, #captcha_root, #id_captcha_loaded")
            or "доступ заблокирован" in title
            or "access denied" in title
            or "подтвердите, что вы не робот" in visible
            or "запросы с вашего устройства похожи на автоматические" in visible):
        raise AccessVerificationRequired("source requires access verification")
    heading = _one(soup, "h1.head-title")
    if _text(heading) != "Ипотечное кредитование":
        raise MortgageDOMError("not the supported mortgage page")
    _one(soup, ".main__body")
    return soup


def _discover(soup) -> dict:
    main = _one(soup, ".main-filters")
    region = _control(_section(main, "Регион"), button=True)
    if region["label"] not in REGION_LABELS:
        raise MortgageDOMError("selected region is not Moscow or Russia")
    ranges = _one(_section(main, "Отчетная дата"), ".range-select")
    controls = [_control(item, button=True) for item in ranges.select(".filter-list")]
    if len(controls) != 2:
        raise MortgageDOMError("unsupported report-date controls")
    month_controls = [item for item in controls if re.fullmatch(r"\d{4}-\d{2}-01", item["value"])]
    year_controls = [item for item in controls if re.fullmatch(r"\d{4}", item["value"])]
    if len(month_controls) != 1 or len(year_controls) != 1:
        raise MortgageDOMError("cannot identify report year/month controls")
    month, year = month_controls[0], year_controls[0]
    year_number = int(year["value"])
    if year["label"] != year["value"]:
        raise MortgageDOMError("year label differs from selected year")
    available_years = []
    for option in year["options"]:
        if not re.fullmatch(r"\d{4}", option["value"]) or option["label"] != option["value"]:
            raise MortgageDOMError("unsupported offered report year")
        number = int(option["value"])
        if not 1900 <= number <= 2200:
            raise MortgageDOMError("invalid offered report year")
        available_years.append(number)
    available_periods = []
    for option in month["options"]:
        try:
            parsed = date.fromisoformat(option["value"])
        except (TypeError, ValueError) as exc:
            raise MortgageDOMError("invalid offered report month") from exc
        if (parsed.day != 1 or parsed.year != year_number
                or option["label"].casefold() != MONTHS[parsed.month - 1]):
            raise MortgageDOMError("month labels/years differ from report year")
        available_periods.append(parsed.strftime("%Y-%m"))
    selected_date = date.fromisoformat(month["value"])
    sidebar = _one(soup, ".filters-panel__content")
    sidebar_controls = {
        "basis": _control(_section(sidebar, "Период"), active=True),
        "currency": _control(_section(sidebar, "Валюта кредита"), button=True),
        "market": _control(_section(sidebar, "Рынок недвижимости"), button=True),
        "housing": _control(_section(sidebar, "Тип жилья"), button=True),
        "includeAutonomous": _control(_section(sidebar, "Автономные округа"), active=True),
        "dynamicStartYear": _control(_section(sidebar, "Динамика"), button=True),
    }
    supported = {
        "basis": {"year": "с начала года", "month": "за месяц"},
        "currency": {"ALL": "Всего", "OTH": "В валюте", "RUB": "В рублях"},
        "market": {"total": "Всего", "primary": "Первичный рынок", "secondary": "Вторичный рынок"},
        "housing": {"all": "Все", "multifamily": "МКД", "private": "ИЖС"},
        "includeAutonomous": {"yes": "Учитывать АО", "no": "Не учитывать"},
    }
    for name, choices in supported.items():
        for option in sidebar_controls[name]["options"]:
            if choices.get(option["value"]) != option["label"]:
                raise MortgageDOMError(f"unsupported {name} option")
    dynamic = sidebar_controls["dynamicStartYear"]
    if (not re.fullmatch(r"\d{4}", dynamic["value"])
            or dynamic["label"] != f'с {dynamic["value"]} года'):
        raise MortgageDOMError("invalid selected history start")
    detail_control = _control(_one(soup, ".main__body .section-btn .btn-group"), active=True)
    detail_currencies = {"В рублях": "RUB", "В валюте": "OTH"}
    if detail_control["value"] not in detail_currencies or detail_control["label"] != detail_control["value"]:
        raise MortgageDOMError("unsupported bottom-card currency")
    bank_section = _section(sidebar, "Кредитные организации")
    bank_button = _text(_one(bank_section, ".filter-list__btn button"))
    bank_values = [item.get("value") for item in bank_section.select("input[checked]")]
    selection = {
        "region": REGION_LABELS[region["label"]],
        "regionLabel": region["label"], "regionValue": region["value"],
        "period": selected_date.strftime("%Y-%m"),
        "year": year_number, "month": selected_date.month,
        **{name: item["value"] for name, item in sidebar_controls.items()},
        "detailCurrency": detail_currencies[detail_control["value"]],
        "creditOrganizations": bank_values,
        "creditOrganizationsLabel": bank_button,
    }
    regions = {}
    for option in region["options"]:
        key = REGION_LABELS.get(option["label"])
        if key:
            if key in regions:
                raise MortgageDOMError("duplicate supported region")
            regions[key] = dict(option)
    filters = {name: selection[name] for name in ("currency", "market", "housing")}
    return {
        "region": selection["region"], "period": selection["period"],
        "basis": selection["basis"], "filters": filters,
        "bottomCurrency": selection["detailCurrency"], "sourceDate": None,
        "years": sorted(available_years),
        "months": sorted(int(item[-2:]) for item in available_periods),
        "selection": selection,
        "availableYears": sorted(available_years),
        "availableMonths": sorted(int(item[-2:]) for item in available_periods),
        "availablePeriods": sorted(available_periods),
        "regions": regions,
        "controls": {"region": region, "month": month, "year": year,
                     **sidebar_controls, "detailCurrency": detail_control},
    }


def discover_page(html: str) -> dict:
    """Read checked controls/options; month availability applies to selected year only."""
    return _discover(_soup(html))


def _number(raw: str, *, signed: bool = False):
    normalized = re.sub(r"\s", "", raw).replace("−", "-")
    if normalized in _EMPTY:
        return None
    if not _NUMBER.fullmatch(normalized):
        raise MortgageDOMError("unsupported literal numeric value: " + raw)
    try:
        number = Decimal(normalized.replace(",", "."))
    except InvalidOperation as exc:
        raise MortgageDOMError("invalid literal number") from exc
    if not number.is_finite() or (not signed and number < 0):
        raise MortgageDOMError("invalid or negative measurement")
    return number


def _json_number(number):
    if number is None:
        return None
    if number == number.to_integral_value():
        return int(number)
    value = float(number)
    if value == float("inf") or value == float("-inf"):
        raise MortgageDOMError("measurement exceeds supported numeric range")
    return value


def _change(node) -> dict | None:
    badge = _one(node, ".badge", optional=True)
    if badge is None:
        return None
    unit = _text(_one(badge, ".badge-box__txt"))
    raw = _text(badge)
    if unit not in {"%", "п.п."} or not raw.endswith(unit):
        raise MortgageDOMError("unsupported change unit")
    value = raw[:-len(unit)].strip()
    return {"value": _json_number(_number(value, signed=True)),
            "unit": "pct" if unit == "%" else "pp",
            "rawValue": value, "rawUnit": unit, "rawText": raw}


def _basis(selection: dict) -> dict:
    period = selection["period"]
    return {"basis": selection["basis"], "period": period,
            "startPeriod": f'{selection["year"]}-01' if selection["basis"] == "year" else period,
            "endPeriod": period, "asOf": None, "periodLabel": None}


def _labelled_basis(label: str) -> dict:
    value = label.casefold().replace("–", "-").replace("—", "-")
    annual = re.fullmatch(r"за (\d{4}) год(?:а)?", value)
    if annual:
        year = int(annual[1])
        date(year, 1, 1)
        return {"basis": "year", "period": f"{year}-12", "year": year,
                "startPeriod": f"{year}-01", "endPeriod": f"{year}-12",
                "asOf": None, "periodLabel": label}
    interval = re.fullmatch(r"за ([а-яё]+)(?:\s*-\s*([а-яё]+))? (\d{4}) год(?:а)?", value)
    if interval and interval[1] in MONTHS and (interval[2] is None or interval[2] in MONTHS):
        first = MONTHS.index(interval[1]) + 1
        last = MONTHS.index(interval[2]) + 1 if interval[2] else first
        year = int(interval[3])
        date(year, last, 1)
        if last < first:
            raise MortgageDOMError("reversed labelled measurement period")
        return {"basis": "year" if interval[2] else "month",
                "period": f"{year}-{last:02d}", "year": year,
                "startPeriod": f"{year}-{first:02d}", "endPeriod": f"{year}-{last:02d}",
                "asOf": None, "periodLabel": label}
    stock = re.fullmatch(r"на (\d{2})\.(\d{2})\.(\d{4})", value)
    if stock:
        try:
            observed = date(int(stock[3]), int(stock[2]), int(stock[1]))
        except ValueError as exc:
            raise MortgageDOMError("invalid stock observation date") from exc
        return {"basis": "stock", "period": observed.strftime("%Y-%m"),
                "startPeriod": None, "endPeriod": None,
                "asOf": observed.isoformat(), "periodLabel": label}
    raise MortgageDOMError("unsupported labelled measurement period: " + label)


def _measurement(node, field: str, basis: dict, currency: str) -> dict:
    data = _one(node, ".a-data")
    raw_value = _one(data, ".a-data__data").get_text(" ", strip=True)
    raw_unit = _one(data, ".a-data__unit").get_text(" ", strip=True)
    unit = " ".join(raw_unit.split())
    units = {
        "count": ({"шт.": 1, "тыс. шт.": 1000}, "count"),
        "volumeRub": ({"₽": 1, "руб.": 1, "тыс. ₽": 1000, "млн ₽": 10**6, "млрд ₽": 10**9}, "rub"),
        "averageAmountRub": ({"₽": 1, "руб.": 1, "тыс. ₽": 1000, "млн ₽": 10**6, "млрд ₽": 10**9}, "rub"),
        "debtRub": ({"₽": 1, "руб.": 1, "тыс. ₽": 1000, "млн ₽": 10**6, "млрд ₽": 10**9}, "rub"),
        "ratePct": ({"%": 1}, "pct"),
        "delinquencyPct": ({"%": 1}, "pct"),
        "termMonths": ({"лет": 12, "год": 12, "года": 12, "мес.": 1}, "months"),
    }
    scales, canonical_unit = units[field]
    if unit not in scales:
        raise MortgageDOMError(f"unsupported {field} unit: {unit}")
    number = _number(raw_value)
    converted = _json_number(number * scales[unit] if number is not None else None)
    return {"value": converted, "unit": canonical_unit,
            "rawValue": raw_value, "rawUnit": raw_unit, "rawText": _text(data),
            "currency": currency, **basis, "change": _change(node)}


def _empty_metrics() -> dict:
    return {segment: dict.fromkeys(METRICS) for segment in SEGMENTS}


def _segments(body, field: str, basis: dict, currency: str) -> dict:
    values = {}
    top = _one(body, ".item_top")
    total = _one(top, ".item_total", optional=True)
    if total is not None:
        measurement = _measurement(total, field, basis, currency)
        delta = _one(top, ".item_delta", optional=True)
        measurement["change"] = _change(delta) if delta else None
        values["all"] = measurement
    for segment, label in (("primary", "Первичный рынок"), ("secondary", "Вторичный рынок")):
        item = _one(body, f".item_{segment}", optional=True)
        if item is not None:
            if _text(_one(item, "h6.card-subtitle")) != label:
                raise MortgageDOMError("market segment label differs from wrapper")
            values[segment] = _measurement(item, field, basis, currency)
    return values


def parse_page(html: str, *, expected_region: str, expected_period: str,
               require_month: bool = True) -> dict:
    """Read a verified selection, keeping annual, cumulative and stock data apart.

    ``metrics`` contains literal normalized top-card values. Loan amount/term
    enter it only when the bottom cards explicitly prove the same single month
    and the default all-market scope, in rubles. Otherwise they remain null and
    their independently labelled observations are preserved in ``details``.
    """
    if expected_region not in {"msk", "rf"}:
        raise MortgageDOMError("expected region must be msk or rf")
    match = _PERIOD.fullmatch(expected_period) if isinstance(expected_period, str) else None
    if not match:
        raise MortgageDOMError("expected period must be YYYY-MM")
    try:
        date(int(match[1]), int(match[2]), 1)
    except ValueError as exc:
        raise MortgageDOMError("invalid expected period") from exc
    soup = _soup(html)
    result = _discover(soup)
    selection = result["selection"]
    if selection["region"] != expected_region or selection["period"] != expected_period:
        raise MortgageDOMError("rendered region/report period differs from request")
    if require_month and selection["basis"] != "month":
        raise MortgageDOMError("rendered report is cumulative, not a monthly observation")
    metrics, evidence = _empty_metrics(), {segment: {} for segment in SEGMENTS}
    previous = None
    seen = set()
    body = _one(soup, ".main__body")
    for card in body.select(".main-card_item_body"):
        top = _one(card, ".item_top")
        title = _text(_one(top, "h5.title_name"))
        if title not in MAIN_TITLES:
            raise MortgageDOMError("unsupported main mortgage metric: " + title)
        field = MAIN_TITLES[title]
        header = _one(top, ":scope > .card_title")
        labelled = _one(header, ".card-subtitle, .item_period", optional=True)
        if labelled is not None:
            if field != "volumeRub" or previous is not None:
                raise MortgageDOMError("unsupported annual summary card")
            annual = _labelled_basis(_text(labelled))
            if (annual["basis"] != "year" or annual["startPeriod"][-2:] != "01"
                    or annual["endPeriod"][-2:] != "12"):
                raise MortgageDOMError("summary is not a labelled complete year")
            annual_metrics, annual_evidence = _empty_metrics(), {segment: {} for segment in SEGMENTS}
            volume = _measurement(_one(top, ".item_total"), field, annual, selection["currency"])
            delta = _one(top, ".item_delta", optional=True)
            volume["change"] = _change(delta) if delta else None
            annual_metrics["all"][field] = volume["value"]
            annual_evidence["all"][field] = volume
            bottom = _one(card, ".item_bottom")
            if _text(_one(bottom, "h5.title_name")) != "Средневзвешенная ставка в рублях":
                raise MortgageDOMError("unsupported previous-year rate title")
            rate_header = _one(bottom, ":scope > .card_title")
            if _labelled_basis(_text(_one(rate_header, ".card-subtitle, .item_period"))) != annual:
                raise MortgageDOMError("previous-year summary periods disagree")
            for segment in ("primary", "secondary"):
                item = _one(bottom, f".item_{segment}", optional=True)
                if item is not None:
                    caption = "Первичный рынок" if segment == "primary" else "Вторичный рынок"
                    if _text(_one(item, "h6.card-subtitle")) != caption:
                        raise MortgageDOMError("previous-year segment label differs")
                    observation = _measurement(item, "ratePct", annual, "RUB")
                    annual_metrics[segment]["ratePct"] = observation["value"]
                    annual_evidence[segment]["ratePct"] = observation
            previous = {"year": annual["year"], "basis": "year",
                        "metrics": annual_metrics, "metricEvidence": annual_evidence}
            continue
        if field in seen:
            raise MortgageDOMError("duplicate current mortgage metric")
        seen.add(field)
        currency = "RUB" if field == "ratePct" else selection["currency"]
        for segment, observation in _segments(card, field, _basis(selection), currency).items():
            metrics[segment][field] = observation["value"]
            evidence[segment][field] = {"title": title, **observation}
    if seen != set(MAIN_TITLES.values()):
        raise MortgageDOMError("missing current mortgage metric card")
    if not any(value is not None for group in metrics.values() for value in group.values()):
        raise MortgageDOMError("page contains no mortgage measurements")
    details = {}
    for card in body.select(".card_item_body"):
        title = _text(_one(card, "h5.title_name"))
        field = DETAIL_TITLES.get(title)
        if field is None or field in details:
            raise MortgageDOMError("unsupported or duplicate detail metric: " + title)
        labelled = _labelled_basis(_text(_one(card, ".item_period")))
        observation = _measurement(card, field, labelled, selection["detailCurrency"])
        details[field] = {"title": title, **observation}
    if set(details) != set(DETAIL_TITLES.values()):
        raise MortgageDOMError("incomplete detail metric markup")
    if (selection["basis"] == "month" and selection["market"] == "total"
            and selection["housing"] == "all" and not selection["creditOrganizations"]):
        for field in ("termMonths", "averageAmountRub"):
            observation = details[field]
            if (observation["basis"] == "month" and observation["currency"] == "RUB"
                    and observation["startPeriod"] == observation["endPeriod"] == expected_period):
                metrics["all"][field] = observation["value"]
                evidence["all"][field] = observation
    result.update(metrics=metrics, metricEvidence=evidence, previousYear=previous, details=details,
                  rawMetrics={"sourceCaption": _text(_one(soup, "h1.head-title")),
                              "selection": dict(selection), "current": evidence,
                              "previousYear": previous, "details": details})
    return result
