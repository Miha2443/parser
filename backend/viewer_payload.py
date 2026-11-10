"""Pure, fail-closed graph projections for authenticated viewers.

This is not authorization: middleware must allowlist GET routes, reject exports,
and project successful JSON bytes before sending them. Each object below is an
allowlist; new API fields are private until deliberately added here. Chart rows
and normalized aggregates are public, unlike worksheet rows and raw registries.

Compatibility exceptions: tree readers require a file and Excel address, so
``restricted``/``A1`` are synthetic placeholders, never source evidence. Sector
row arrays are synthetic ordinal slots preserving aggregate counts. Mortgage
readers require their fixed public provider URLs; no supplied URL is forwarded.
Business labels, map display locations/accuracy classes, periods, aggregate counts, opaque data
versions and rounding tolerances remain public. This is not anonymization of
business data or a general secret detector for arbitrary prose in chart labels.
"""
from __future__ import annotations

import hashlib
import math
import re
from urllib.parse import urlsplit


_PRIVATE_KEY = re.compile(
    r"password|passwd|secret|credential|token|authorization|cookie|connection|dsn|"
    r"source.?file|source.?path|source.?url|source.?row|source.?cell|file.?path|"
    r"provenance|evidence|raw.?data|raw.?record|(?:^|[ _])(?:файл|лист|ячейка)(?:$|[ _])", re.I)
_PRIVATE_TEXT = re.compile(
    r"[a-z][a-z0-9+.-]*://|[a-z]:[\\/]|\\\\|"
    r"(?:^|[\s\"'=])/?(?:runtime|releases|downloads|data/raw|data/processed|data/marts|"
    r"etc|opt|home|tmp|Users)(?:[/\\]|$)|"
    r"\.(?:xlsx?|csv|jsonl?|pkl|sqlite|db|parquet)(?:\b|$)|"
    r"password\s*[:=]|passwd\s*[:=]|secret\s*[:=]|token\s*[:=]|"
    r"(?:postgres|mysql|redis|mongodb)\b|\bBearer\s+|-----BEGIN .*PRIVATE KEY", re.I)


def _scalar(value):
    if isinstance(value, str):
        return "restricted" if _PRIVATE_TEXT.search(value) else value
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return None


def _blank(value):
    if isinstance(value, list):
        return []
    if isinstance(value, dict):
        return {}
    return None if value is None else ""


def _empty(_):
    return []


def _hidden(_):
    return None


def _fields(names="", **children):
    return {**dict.fromkeys(names.split(), _scalar), **children,
            "notes": _blank, "sourceNote": _blank, "sourceNotes": _blank}


def _project(value, spec):
    if value is None:
        return None
    if callable(spec):
        return spec(value)
    if isinstance(spec, list):
        return [_project(item, spec[0]) for item in value] if isinstance(value, list) else []
    if not isinstance(value, dict):
        return {}
    return {key: _project(value[key], child) for key, child in spec.items() if key in value}


def _mapping(spec):
    def project(value):
        if not isinstance(value, dict):
            return {}
        return {key: _project(item, spec) for key, item in value.items()
                if isinstance(key, str) and not _PRIVATE_KEY.search(key) and _scalar(key) == key}
    return project


def _numbers(value):
    return {key: item for key, item in value.items()
            if isinstance(key, str) and not _PRIVATE_KEY.search(key) and _scalar(key) == key
            and (item is None or isinstance(item, (int, float)) and not isinstance(item, bool))}


def _table(value):
    """Keep declared aggregate columns, not arbitrary fields attached to rows."""
    value = value if isinstance(value, dict) else {}
    result = _project(value, _fields("id title unit periodLabel region aggregateField",
                                   columns=[_fields("id label")]))
    columns = [column for column in result.get("columns", [])
               if isinstance(column.get("id"), str) and not _PRIVATE_KEY.search(column["id"])]
    result["columns"] = columns
    names = {column["id"] for column in columns}
    # These are chart timing/completeness fields, not worksheet identifiers.
    names.update(("year", "actualMonth", "periodComplete", "annualComplete"))
    result["rows"] = [_project(row, _fields(**dict.fromkeys(names, _scalar)))
                      for row in value.get("rows", []) if isinstance(row, dict)]
    return result


_CHOICE = _fields("id label unit")
_POINT = _fields("x y value")
_SERIES = _fields("id name unit color", points=[_POINT])
_PERIOD = _fields("id label reportDate year month")
_VALUE = _fields("value")
_CHART = _fields("x", period=[_VALUE], remainder=[_VALUE], totals=[_VALUE], growth=[_VALUE],
                 actualMonths=[_scalar])
_CHART["x"] = [_scalar]
_SOURCE = _fields("date collectedAt downloadedAt fileDate", files=_empty, file=_hidden,
                  sheet=_hidden, issues=_empty, dateEvidence=_blank,
                  fileEvidence=lambda v: v if v in ("selected", "candidates") else "selected", recordedSources=_empty,
                  recordedSourceEvidence=_blank, downloadSummary=_blank,
                  provenance=lambda _: {"columns": [], "rows": []})
_META = _fields("schemaVersion version generatedAt reportDate", source=_SOURCE,
                releasePeriods=[_fields("region metric year month")])


def _with_table(value, **fields):
    return {**_table(value), **_project(value, fields)}


def _annual_precision(value):
    return {key: _scalar(item) for key, item in value.items()
            if (key == "year" or key.endswith("_source_precision_million"))
            and (item is None or isinstance(item, (int, float)) and not isinstance(item, bool))}


def _annual_chart(value):
    return _with_table(value, **_fields("totals controlTotal", series=[_SERIES],
        summaries=[_fields("from to", values=[_fields("id label value")])],
        factPeriod=_fields("year month"), categoryProvenance=[_annual_precision],
        forecast=_fields("metric", sourceFields=[_scalar], points=[_fields("year value")])))


def _operational_table(value):
    return _with_table(value, chart=_CHART, annualForecasts=[_fields("year value")])


_SELECTION = _fields("region permitKind month year quarter cumulative excludeMkd indicator characteristicMonth",
    structure=_fields("mode monthFrom monthTo yearFrom yearTo"))
_STRUCTURE_ROW = _fields("id label value knownSum monthChange yearChangePct renovation renovationKnownSum renovationMonthChange renovationYearChangePct")
_LINEAR_ROW = _fields("code indicator unit year quarter plan fact percent")
_OPERATIONAL = {**_META, **_fields("currentYear periodLabel region", selection=_SELECTION,
    tables=[_operational_table], tree=_numbers, treeGrowth=_numbers, treeRows=[_fields("id label value")],
    structure=_fields("year requestedMonth actualMonth sourceDate", rows=[_STRUCTURE_ROW]),
    sourceDetails=_fields("date", file=_hidden))}
_ANNUAL = {**_META, **_fields(region=_CHOICE, charts=[_annual_chart],
    forecastSource=_fields("date unitBasis", file=_hidden))}
_PRESENTATION_ROW = _fields("year selected annualActual annualForecast basis difference remainder growth periodComplete annualComplete observedThrough actualMonth", evidence=_empty)


def _permits(value):
    return _with_table(value, chart=_CHART, presentation=_fields(rows=[_PRESENTATION_ROW]))


_FORECAST_ROW = _fields("year value housing_living housing_total mop_embedded nonresidential offices industry social trade hotels other", cells=lambda _: {})
_FORECAST = _fields("unit unitBasis", source=_fields(file=_hidden, sheet=lambda _: "Прогнозы"),
                    rows=[_FORECAST_ROW])


def _breakdown(value):
    return _with_table(value, series=[_SERIES], notes=_blank)


_PERMIT = {**_META, **_fields("contractRevision", selection=_SELECTION,
    controls=_fields(permitKinds=[_CHOICE], monthsByKind=_mapping([_scalar]),
        structure=_fields(modes=[_CHOICE], years=[_scalar], months=[_scalar],
            coverage=[_fields("blockId year annualComplete observedThrough", months=[_scalar],
                completeMonths=[_scalar], evidence=_empty)])),
    permits=_permits, forecast=_FORECAST, breakdowns=[_breakdown],
    areaVariants=_fields(living=_fields(permits=_permits, forecast=_FORECAST),
                         full=_fields(permits=_permits, forecast=_FORECAST)))}
_CONSTRUCTION = {**_META, **_fields("constructionPeriod", selection=_SELECTION,
    salesReadinessPeriod=_PERIOD, metrics=[_fields("id label value unit digits")],
    sales=_mapping(_scalar), permits=_permits, sourceDetails=_fields("date", file=_hidden))}
_DOMRF = {**_META, **_fields(region=_CHOICE,
    construction=_fields(period=_PERIOD, metrics=_fields("living total"),
        history=[_fields("period label reportDate living total", sourceFile=lambda _: "")]),
    sales=_fields(period=_PERIOD, metrics=_numbers,
        history=[_fields("period label reportDate", metrics=_numbers, sourceFile=lambda _: "")]))}

_ROOM = _fields("type sharePercent")
_APARTMENT_ROW = _fields("id name apartmentThousandCount areaThousandM2 place", rooms=[_ROOM])
_APARTMENTS = {**_META, **_fields("region developerCount regionCount",
    apartments=[_fields("type count areaThousandM2")], distribution=[_fields("range sharePercent")],
    developers=[_APARTMENT_ROW], regions=[_APARTMENT_ROW])}
_APARTMENT_DETAIL = {**_META, **_fields("region", developer=_APARTMENT_ROW,
    summary=_fields("countThousand areaThousandM2 averageAreaM2 marketSharePercent marketBaseAreaThousandM2 place totalDevelopers"),
    referenceAverages=[_fields("region averageAreaM2")], rooms=[_ROOM], comparison=[_APARTMENT_ROW])}


def _sales_chart(value):
    return _project(value, _fields("id title kind unit", series=[_SERIES]))


_SALES = {**_META, **_fields(region=_CHOICE, period=_PERIOD,
    metrics=[_fields("id label unit value digits")], charts=[_sales_chart], tables=[_table])}
_LINEAR_YEAR = _fields("year fact factKind factPeriod factSource plan forecast")
_LINEAR = {**_META, **_fields("currentYear", sources=[_fields("id date", file=_hidden, sheet=_hidden)],
    metrics=[_fields("id label unit fact factPeriod factSource annualPlan forecast target targetKind percent remaining",
                     years=[_LINEAR_YEAR])])}

_MORTGAGE_VALUE = _fields("volumeRub count ratePct termMonths averageAmountRub")
_MORTGAGE_ANNUAL = _fields("annual selected actualThroughMonth annualYoYPct selectedYoYPct")
_MORTGAGE_SOURCE = {**_SOURCE, "url": lambda _: "https://наш.дом.рф/аналитика/ипотечное_кредитование"}
_PREFERENTIAL_SOURCE = {**_SOURCE, "url": lambda _: "https://дом.рф/programmy-gosudarstvennoj-podderzhki/operational-reporting/"}
_MORTGAGE = {**_META, "source": _MORTGAGE_SOURCE, **_fields("regionLabel", selection=_SELECTION,
    annual=[_fields("year", volume=_MORTGAGE_ANNUAL, count=_MORTGAGE_ANNUAL)],
    monthly=[_fields("period", all=_MORTGAGE_VALUE, primary=_MORTGAGE_VALUE, secondary=_MORTGAGE_VALUE)])}
_PROGRAMS = ("preferential", "family", "it")
_CHARACTERISTICS = "averageAmountMln ratePct termMonths ownFundsPct areaM2 priceThousandM2"
_FLOW = _fields("volumeMln count")
_FLOW_YEAR = _fields("year throughMonth", programs=_fields(**dict.fromkeys(_PROGRAMS, _FLOW)), total=_FLOW)
_CHAR_YEAR = _fields("year throughMonth", throughMonths=_fields(_CHARACTERISTICS),
    programThroughMonths=_fields(**dict.fromkeys(_PROGRAMS, _fields(_CHARACTERISTICS))),
    programs=_fields(**dict.fromkeys(_PROGRAMS, _fields(_CHARACTERISTICS))))
_PREFERENTIAL = {**_META, "source": _PREFERENTIAL_SOURCE,
    **_fields("regionLabel latestPeriod latestPeriodPartial", selection=_SELECTION,
        annual=[_FLOW_YEAR], cumulative=[_FLOW_YEAR], characteristicsAnnual=[_CHAR_YEAR], characteristicsCumulative=[_CHAR_YEAR])}

_ECONOMIC_KEYS = "period region views ytd months quarters regions index_base block1_regions block2_regions block3_regions block4_regions structure_region index_region structure_mode structure_industries index_industries show_total"
_ECONOMIC_SELECTION = _fields(**{key: ([_scalar] if key in "views months quarters regions block1_regions block2_regions block3_regions block4_regions structure_industries index_industries".split() else _scalar) for key in _ECONOMIC_KEYS.split()})


def _economic_chart(value):
    return _with_table(value, **_fields("kind stack unit", series=[_SERIES], totals=[_POINT]))


_ECONOMICS = {**_META, **_fields("family", selection=_ECONOMIC_SELECTION, charts=[_economic_chart], tables=[_table])}
_ECONOMIC_CATALOG = {**_META, **_fields("family minimumYear", defaults=_ECONOMIC_SELECTION,
    years=[_scalar], regions=[_CHOICE], controls=[_fields("id type", default=lambda v: [_scalar(x) for x in v] if isinstance(v, list) else _scalar(v),
        options=[_CHOICE], periods=[_scalar], optionsByRegion=_mapping([_CHOICE]), optionsByRegionMode=_mapping(_mapping([_CHOICE])))],
    blocks=[_fields("id title kind unit stack regionControl industryControl")],
    quarterOptionsByRegionMode=_mapping(_mapping([_scalar])),
    structureIndustries=_mapping(_mapping([_CHOICE])), indexIndustries=_mapping([_CHOICE]))}

_CATEGORIES = "residential common nonresidentialInHousing standaloneNonresidential"
_PROFILE_ANNUAL_ROW = _fields("year totalM2", valuesM2=_fields(_CATEGORIES))
_DONUT = _fields("id title region status rowCount totalM2 displayTotalThousandM2", years=[_scalar],
                 segments=[_fields("key label valueM2 displayThousandM2")])
_PROFILE_SALES = _fields("status", period=_PERIOD,
    values=_fields("soldPercent readinessPercent ratioPercent"), display=_fields("soldPercent readinessPercent ratioPercent"))
_OBJECTS = lambda _: {"columns": [], "rows": [], "rowCount": 0, "sourceNote": ""}
_PROFILE = _fields("id developer developerKey region",
    controls=_fields("developer apartmentRegion latestMonitoringYear previousMonitoringYear commissioningScope"),
    ratings=_fields("score displayScore", rows=[_fields("id title", rf=_fields("place displayPlace"), msk=_fields("place displayPlace"))]),
    housingComparison=[_DONUT], categoryDonuts=[_DONUT],
    annual=_fields("region", rows=[_PROFILE_ANNUAL_ROW], allYears=[_PROFILE_ANNUAL_ROW],
        legend=_fields("totalM2 displayTotalMillionM2", valuesM2=_fields(_CATEGORIES), displayMillionM2=_fields(_CATEGORIES), years=[_scalar])),
    rasprod=_fields(msk=_PROFILE_SALES, rf=_PROFILE_SALES),
    apartments=_fields("region status totalCount totalAreaThousandM2 averageAreaM2 marketTotalAreaThousandM2 marketSharePercent reportDate",
        availableRegions=[_scalar], rows=[_fields("type count sharePercent areaThousandM2 displayCount displaySharePercent displayAreaThousandM2")],
        display=_fields("totalCount totalAreaThousandM2 averageAreaM2 marketSharePercent")),
    delays=_fields(cards=[_fields("id region title valueM2 baseM2 percent displayValue displayBase displayPercent")], inputs=_fields(pairedYears=[_scalar])),
    escrow=_fields("region status loanRub debtRub revenueRub debtSharePercent coveragePercent rowCount",
        display=_fields("loanRub debtRub revenueRub debtSharePercent coveragePercent")),
    objects=_fields(commissioned=_OBJECTS, permitted=_OBJECTS))
_PROFILES = {**_META, **_fields(sourceDates=_fields("monitoring_2_0 erzrf_top erzrf_cards rasprodannost kvartirografia escrow_manual"),
    controls=_fields("frozen", developers=[_fields("developer developerKey", regions=[_scalar])]),
    provenance=lambda _: {"sources": {}, "issues": []}, profiles=[_PROFILE])}

_SNAPSHOT = _fields("date capturedAt publishedAt delayedCapture", release=lambda _: "restricted", sha256=lambda _: "0" * 64)
_TREE = {**_META, **_fields("unit", snapshot=_SNAPSHOT,
    metrics=[_fields("id label value", cell=lambda _: "A1")],
    nonresidentialSectors=_fields("status canonicalValue registryValue", sheet=lambda _: "restricted",
        areaColumn=lambda _: "restricted", sectorColumn=lambda _: "restricted",
        filters=_fields(**dict.fromkeys(("Назначение", "Статус объекта", "Статус РС"), _scalar)),
        items=[_fields("id label value count", rows=_empty)]))}
_TREE["source"] = _fields("downloadedAt fileDate", file=lambda _: "restricted",
                          sheet=lambda _: "Дерево текущ стр во", column=lambda _: "Итог")


def _tree(value):
    result = _project(value, _TREE)
    offset = 2
    for sector in result.get("nonresidentialSectors", {}).get("items", []):
        count = sector.get("count", 0)
        if not isinstance(count, int) or isinstance(count, bool) or not 0 < count <= 1048575 or offset + count > 1048577:
            raise ValueError("Invalid viewer sector count")
        sector["rows"] = list(range(offset, offset + count))
        offset += count
    return result


def _map_id(value):
    text = str(value)
    if re.fullmatch(r"viewer-[a-f0-9]{24}", text):
        return text
    return "viewer-" + hashlib.sha256(text.encode()).hexdigest()[:24]


# Keep only the normalized accuracy class: otherwise approximate markers would
# falsely acquire the frontend's precise-point styling. No geocoder proof leaks.
_MAP_OBJECT = _fields("address developer object_name status okrug district year lat lon area_total area_living apartments has_coords quality",
                      id=_map_id, quality=lambda v: v if v in ("located", "approximate", "missing") else "missing")
_COUNTS = _fields("total onMap located approximate missing")
_MAP = {**_META, **_fields(developers=[_scalar], statuses=[_scalar], okrugs=[_scalar], years=_fields("min max"),
    scope=_fields("rvRows oksRows excludedOksRows missingIssueDateRows"),
    rows=[_MAP_OBJECT], totals=_COUNTS, filteredTotals=_COUNTS,
    geojson=_fields("type", features=[_fields("type", id=_map_id, geometry=_fields("type", coordinates=[_scalar]), properties=_MAP_OBJECT)]))}

_COMMISSIONING_CATALOG = {**_META, **_fields("defaultMonth currentYear", months=[_CHOICE], years=[_scalar])}
_LINEAR_CATALOG = {**_META, **_fields(years=[_fields("id label defaultQuarter", quarters=[_scalar])], indicators=[_CHOICE])}
_MARKET_CATALOG = {**_META, **_fields(regions=[_CHOICE], permitKinds=[_CHOICE], monthsByKind=_mapping([_scalar]))}
_MORTGAGE_CATALOG = {**_META, **_fields("defaultRegion", regions=[_fields("id label", periods=[_scalar], characteristicPeriods=[_scalar])], months=[_CHOICE])}

_ROUTES = {
    "/api/v1/catalog": _PROFILES, "/api/v1/profile": _PROFILES,
    "/api/v1/apartments": _APARTMENTS, "/api/v1/apartments/developer": _APARTMENT_DETAIL,
    "/api/v1/apartments/catalog": {**_META, **_fields(regions=[_CHOICE], developersByRegion=_mapping([_fields("id name place")]))},
    "/api/v1/sales": _SALES,
    "/api/v1/sales/catalog": {**_META, **_fields(regions=[_fields("id label", periods=[_PERIOD])])},
    "/api/v1/commissioning/operational": _OPERATIONAL,
    "/api/v1/commissioning/operational/catalog": _COMMISSIONING_CATALOG,
    "/api/v1/commissioning/annual": _ANNUAL,
    "/api/v1/commissioning/annual/catalog": _MARKET_CATALOG,
    "/api/v1/construction": _CONSTRUCTION, "/api/v1/construction/catalog": _MARKET_CATALOG,
    "/api/v1/construction/permits": _PERMIT, "/api/v1/construction/domrf": _DOMRF,
    "/api/v1/construction/tree": _tree,
    "/api/v1/construction-tree/history": _fields("schemaVersion", snapshots=[_SNAPSHOT]),
    "/api/v1/linear": {**_META, **_fields(selection=_SELECTION, summary=[_LINEAR_ROW], trend=[_LINEAR_ROW], allPeriods=[_LINEAR_ROW])},
    "/api/v1/linear/catalog": _LINEAR_CATALOG, "/api/v1/linear/dashboard": _LINEAR,
    "/api/v1/map": _MAP, "/api/v1/map/catalog": _MAP,
    "/api/v1/mortgage": _MORTGAGE,
    "/api/v1/mortgage/catalog": {**_MORTGAGE_CATALOG, "source": _MORTGAGE_SOURCE},
    "/api/v1/preferential-mortgage": _PREFERENTIAL,
    "/api/v1/preferential-mortgage/catalog": {**_MORTGAGE_CATALOG, "source": _PREFERENTIAL_SOURCE},
}
for _family in ("salary", "ipc", "accounts"):
    _ROUTES[f"/api/v1/economics/{_family}"] = _ECONOMICS
    _ROUTES[f"/api/v1/economics/{_family}/report"] = _ECONOMICS
    _ROUTES[f"/api/v1/economics/{_family}/catalog"] = _ECONOMIC_CATALOG


def viewer_payload(path: str, payload: dict) -> dict:
    """Return a detached viewer graph; unknown routes raise ValueError."""
    if not isinstance(path, str) or not isinstance(payload, dict):
        raise ValueError("Viewer projection requires a path and JSON object")
    route = urlsplit(path).path
    spec = _ROUTES.get(route)
    if spec is None and re.fullmatch(r"/api/v1/construction-tree/history/\d{4}-\d{2}-\d{2}", route):
        spec = _tree
    if spec is None:
        raise ValueError("Unsupported viewer API path")
    return _project(payload, spec)
