"""Mandatory source and page coverage shared by candidate and live readiness.

This is a publication contract, not a data-volume target. Zero is a valid
measurement; missing frames, populations or measurements are not.
"""
from __future__ import annotations

import math
import json
from numbers import Real
from urllib.parse import urlencode

SOURCES = frozenset({"monitoring", "kvart", "construction", "rasprod", "rosstat",
                     "fedstat", "erz-top", "erz-cards"})
OPTIONAL_SOURCES = frozenset({"erz-top", "erz-cards"})
REQUIRED_SOURCES = SOURCES - OPTIONAL_SOURCES
CATALOG_ROUTES = tuple("/api/v1/" + name + "/catalog" for name in (
    "apartments", "sales", "economics/salary", "economics/ipc", "economics/accounts",
    "map", "linear", "commissioning/annual", "commissioning/operational", "construction"))
ROUTES = ("/api/v1/catalog",) + CATALOG_ROUTES
CONSTRUCTION_SALES_FIELDS = (
    "total_living_thousand_m2", "sales_open_thousand_m2", "sold_thousand_m2", "sold_pct",
    "unsold_thousand_m2", "unsold_pct", "sales_not_open_thousand_m2", "sales_not_open_pct",
    "price_per_m2_rub", "funds_million_rub",
)


def require(condition, description):
    if not condition:
        raise RuntimeError("Incomplete dashboard coverage: " + description)


def finite(value):
    return isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(value)


def require_frame(frame, label, *, regions=False, numeric=()):
    import pandas as pd
    require(isinstance(frame, pd.DataFrame) and not frame.empty, label + " is empty")
    populations = ("rf", "msk") if regions else (None,)
    for region in populations:
        if region:
            require("region_key" in frame, label + " has no region_key")
        selected = frame[frame.region_key.eq(region)] if region else frame
        require(not selected.empty, f"{label} lacks {region or 'rows'}")
        for column in numeric:
            require(column in selected and selected[column].map(finite).any(),
                    f"{label} lacks measured {column} for {region or 'rows'}")


def require_construction_sales(row, label):
    require(isinstance(row, dict), label + " is missing")
    for field in CONSTRUCTION_SALES_FIELDS:
        require(finite(row.get(field)), label + " lacks measured " + field)


def validate_monitoring_history(access):
    """The named monthly housing source is mandatory for publication."""
    history = access.load_monitoring_operational_history()["housing_monthly"]
    require_frame(history, "operational housing monthly history", numeric=("value_thousand_m2",))
    require({"year", "month", "value_thousand_m2"} <= set(history.columns), "operational housing history schema is invalid")
    require(not history.duplicated(["year", "month"]).any(), "operational housing history contains duplicate periods")
    for row in history.to_dict("records"):
        year, month, value = row["year"], row["month"], row["value_thousand_m2"]
        require(finite(year) and year > 0 and year == int(year) and finite(month) and month == int(month)
                and 1 <= month <= 12 and finite(value) and value >= 0,
                "operational housing history contains invalid periods/measurements")
    return history


def kvart_details_match(actual, expected):
    from pipeline.rasprod_contract import number
    if not isinstance(actual, dict) or set(actual) != set(expected):
        return False
    try:
        return all(math.isclose(number(actual[room][field]), float(expected[room][field]), rel_tol=1e-10, abs_tol=1e-8)
                   for room in expected for field in ("count", "area"))
    except (ValueError, TypeError, KeyError):
        return False


def validate_rasprod_sources(access, rasprod):
    from pipeline.source_records import file_sha256
    from pipeline.rasprod_contract import KPI_NAMES, TABLES, key, number, validate_history
    latest = max(access.source_files("rasprodannost"), key=lambda path: path.stat().st_mtime_ns)
    source_json, coverage_file = latest.with_suffix(".json"), latest.with_suffix(".coverage.json")
    require(source_json.is_file() and coverage_file.is_file(), "rasprod has no verified API coverage metadata")
    coverage = json.loads(coverage_file.read_text(encoding="utf-8"))
    require(coverage.get("complete") is True and coverage.get("json_sha256") == file_sha256(source_json)
            and coverage.get("xlsx_sha256") == file_sha256(latest), "rasprod API coverage does not match source files")
    expected_rows, confirmed_rows = coverage.get("expected", []), coverage.get("confirmed", [])
    require(isinstance(expected_rows, list) and isinstance(confirmed_rows, list), "rasprod API period metadata is invalid")
    try:
        expected = {key(dict(region_key=row[0], year=row[1], month_num=row[2])) for row in expected_rows if len(row) == 3}
        confirmed = {key(dict(region_key=row[0], year=row[1], month_num=row[2])) for row in confirmed_rows if len(row) == 3}
    except (ValueError, TypeError, IndexError) as exc:
        raise RuntimeError("Incomplete dashboard coverage: invalid rasprod API population") from exc
    require(expected and len(expected) == len(expected_rows) and len(confirmed) == len(confirmed_rows)
            and expected == confirmed and {row[0] for row in expected} == {"rf", "msk"},
            "rasprod API periods were not all confirmed exactly once")
    entries = json.loads(source_json.read_text(encoding="utf-8"))
    try:
        validate_history(entries, expected)
    except ValueError as exc:
        raise RuntimeError("Incomplete dashboard coverage: " + str(exc)) from exc
    raw_by_key = {key(row): row for row in entries}
    kpi = rasprod.get("kpi")
    require_frame(kpi, "rasprod/kpi", regions=True, numeric=("значение_num",))
    period_columns = ["region_key", "year", "month"]
    require(set(period_columns + ["название"]) <= set(kpi.columns), "rasprod canonical KPI schema is invalid")
    require(set(kpi[period_columns].itertuples(index=False, name=None)) == expected,
            "rasprod canonical mart period set differs from expected API periods")
    for period, rows in kpi.groupby(period_columns):
        names = rows["название"].astype(str).str.strip().str.casefold()
        require(len(rows) == len(KPI_NAMES) and set(names) == KPI_NAMES, f"rasprod mart lacks four distinct KPIs for {period}")
        raw_values = {row["название"].strip().casefold(): number(row["значение"]) for row in raw_by_key[period]["kpi"]}
        for index, row in rows.iterrows():
            require(finite(row["значение_num"]) and math.isclose(row["значение_num"], raw_values[names.loc[index]], rel_tol=1e-10, abs_tol=1e-8),
                    f"rasprod mart KPI differs from verified API report for {period}")
    for table in TABLES:
        frame = rasprod.get(table)
        require_frame(frame, "rasprod/" + table, regions=True)
        require(set(period_columns) <= set(frame.columns), "rasprod table has no period columns: " + table)
        require(set(frame[period_columns].itertuples(index=False, name=None)) == expected,
                "rasprod table period set differs from expected API periods: " + table)
        require("Объем жил. строительства_num" in frame, "rasprod table lacks volume measurements: " + table)
        for period, rows in frame.groupby(period_columns):
            require(rows["Объем жил. строительства_num"].map(finite).any(), f"rasprod/{table} has no measured rows for {period}")


def validate_dynamic_commissioning(access, verified_downloads):
    """Admit actual labelled cells; TOTAL and rural IHS are separate measures."""
    from pipeline.data_access import _parse_emiss_34118_periods_file
    from pipeline.commissioning_validation import validate_emiss_frame
    keys = ["region", "year", "month", "period_type"]
    oracles = []
    bases = [path for path in access.source_files("emiss_34118") if path.name == "emiss_34118_base.xls"]
    for base in bases:
        try:
            oracles.append((base, validate_emiss_frame(base, _parse_emiss_34118_periods_file(base))))
        except ValueError as exc:
            raise RuntimeError("Incomplete dashboard coverage: " + str(exc)) from exc
    for identifier in ("34118_часть1", "34118_часть2"):
        source = max(verified_downloads[identifier], key=lambda path: path.stat().st_mtime_ns)
        frame = _parse_emiss_34118_periods_file(source)
        require_frame(frame, "fedstat/" + identifier, numeric=("ИЖС",))
        require(set(keys + ["quarter", "source_file"]) <= set(frame.columns), identifier + " lacks period/source provenance")
        require(not frame.duplicated(keys).any(), identifier + " contains duplicate periods")
        for region in ("РФ", "Москва"):
            selected = frame[frame.region.eq(region)]
            require(not selected.empty and selected["ИЖС"].map(finite).any(), identifier + " lacks measured IHS for " + region)
            require(selected.source_file.eq(source.name).all(), identifier + " has incorrect source provenance")
            require(selected.month.between(1, 12).all() and selected.year.gt(0).all()
                    and selected.period_type.isin(["month", "quarter", "ytd", "year"]).all(), identifier + " has invalid periods")
        try:
            oracles.append((source, validate_emiss_frame(source, frame)))
        except ValueError as exc:
            raise RuntimeError("Incomplete dashboard coverage: " + str(exc)) from exc
    # Later exports can supersede individual categories, never erase an older
    # explicit MKD measurement merely because their own rows contain no MKD.
    expected = {}
    ordered = [(source, cells) for source, cells in oracles if source in bases] + sorted(
        [(source, cells) for source, cells in oracles if source not in bases], key=lambda pair: pair[0].stat().st_mtime_ns)
    for source, cells in ordered:
        expected.update(cells)
    consumed = access.load_emiss_34118_periods().set_index(keys)
    require(not consumed.index.duplicated().any(), "34118 consumer contains duplicate periods")
    for key, source in expected.items():
        period, category = key[:4], key[4]
        require(period in consumed.index, "dynamic 34118 period is absent from the consumer")
        chosen = consumed.loc[period]
        require(finite(chosen.get(category)) and math.isclose(chosen[category], source["value"], rel_tol=1e-10, abs_tol=1e-8),
                "dynamic 34118 category is masked or relabelled: " + category)
        require(all(chosen.get(category + "_" + suffix) == source[suffix]
                    for suffix in ("source_file", "source_row", "source_column", "source_label")),
                "dynamic 34118 category provenance differs from its raw source cell: " + category)
    annual = access.load_emiss_34118().set_index("year")
    require(not annual.index.duplicated().any(), "34118 annual mart contains duplicate years")
    for key, source in expected.items():
        region, year, month, kind, category = key
        if region != "РФ" or month != 12 or kind not in {"year", "ytd"}:
            continue
        require(year in annual.index, "34118 annual category disappeared from its canonical mart")
        chosen = annual.loc[year]
        require(finite(chosen.get(category)) and math.isclose(chosen[category], source["value"], rel_tol=1e-10, abs_tol=1e-8)
                and chosen.get(category + "_source_file") == source["source_file"],
                "34118 annual category differs from its raw source cell: " + category)


def validate_monitoring_registry(access, rv=None):
    from pipeline.commissioning_validation import validate_primary_rv
    files = access.source_files("monitoring_2_0")
    require(files, "monitoring has no workbook")
    source = max(files, key=lambda path: path.stat().st_mtime_ns)
    if rv is None:
        rv = access.load_monitoring_2_0()["rv"]
    try:
        return validate_primary_rv(source, rv)
    except ValueError as exc:
        raise RuntimeError("Incomplete dashboard coverage: " + str(exc)) from exc


def validate_erz_cards(access, canonical):
    from pipeline.erz_cards_contract import measured_columns, number, target_keys, validate_rows, validate_selection, validate_workbook
    from pipeline.source_records import file_sha256
    files = access.source_files("erzrf_cards")
    workbook = max(files, key=lambda path: path.stat().st_mtime_ns)
    targets = access._raw_files(access.ERZRF_PATHS, ["top_developers_rf_*.json"], recursive=True)
    require(targets, "erz-cards has no actual TOP target population")
    target_file = max(targets, key=lambda path: path.stat().st_mtime_ns)
    target_payload = json.loads(target_file.read_text(encoding="utf-8"))
    try:
        selection = validate_selection(target_payload)
        expected = target_keys(target_payload.get("developers"))
        raw_rows, confirmed = validate_workbook(workbook, expected)
        validate_rows(canonical.to_dict("records"), expected)
    except ValueError as exc:
        raise RuntimeError("Incomplete dashboard coverage: " + str(exc)) from exc
    sidecar = workbook.with_suffix(".xlsx.provenance.json")
    require(sidecar.is_file(), "erz-cards lacks verified target coverage metadata")
    coverage = json.loads(sidecar.read_text(encoding="utf-8"))
    require(coverage.get("schema_version") == 1 and coverage.get("complete") is True
            and coverage.get("limited") is False and coverage.get("failed") == []
            and coverage.get("targets_file") == target_file.name and coverage.get("targets_sha256") == file_sha256(target_file)
            and coverage.get("sha256") == file_sha256(workbook) and coverage.get("selection") == selection
            and coverage.get("expected") == expected and coverage.get("confirmed") == confirmed,
            "erz-cards verified target coverage is incomplete or does not match its sources")
    consumed = {str(row["source_id"]): row for row in canonical.to_dict("records")}
    unavailable = [{"source_id": row["source_id"], "reason": row["history_evidence"],
                    "page_sha256": row["page_sha256"]}
                   for row in raw_rows if row.get("history_status") == "not_published"]
    require(coverage.get("unavailable", []) == unavailable, "erz-cards unpublished history metadata differs from its source")
    for raw in raw_rows:
        row = consumed[str(raw["source_id"])]
        require(row.get("name_card") == raw["name_card"], "erz-cards canonical name differs from its source")
        if raw.get("history_status") == "not_published":
            require(all(str(row.get(key)).removesuffix(".0") == str(raw.get(key)).removesuffix(".0")
                        for key in ("history_status", "history_evidence", "page_card_id", "page_sha256")),
                    "erz-cards unpublished history evidence differs from its source")
        for column in measured_columns(raw):
            value = number(raw[column])
            if value is not None:
                require(number(row.get(column)) == value, "erz-cards canonical measurement differs from its source")


def validate_kvart_sources(access, kvart):
    kvart_jsons = [path for path in access.source_files("kvartirografia") if path.suffix == ".json"]
    require(kvart_jsons, "kvartirografia has no collector coverage JSON")
    raw_kvart = json.loads(max(kvart_jsons, key=lambda path: path.stat().st_mtime_ns).read_text(encoding="utf-8"))
    require(isinstance(raw_kvart, list), "kvartirografia coverage JSON is invalid")
    from pipeline.downloaders.nashdom_kvart_api import resolve as resolve_kvart, resolve_regions, validate_apartment_totals
    require(len(raw_kvart) == 2 and {row.get("region_key") for row in raw_kvart} == {"rf", "msk"},
            "kvartirografia raw snapshot must contain exactly RF and Moscow")
    resolved_regions = {}
    for region in ("rf", "msk"):
        raw_region = next((row for row in raw_kvart if row.get("region_key") == region), {})
        coverage = raw_region.get("per_dev_coverage", {})
        expected = {row.get("наименование") for row in raw_region.get("developers", []) if row.get("наименование")}
        require(coverage.get("complete") is True and not coverage.get("limited") and not coverage.get("missing")
                and coverage.get("expected") == coverage.get("completed") == len(expected) and bool(expected),
                "kvartirografia collector lacks complete per-developer coverage for " + region)
        evidence = raw_region.get("developer_api") or {}
        require(evidence.get("region_key") == region, "kvartirografia lacks API population evidence for " + region)
        require(raw_region.get("report_date") and evidence.get("report_date") == raw_region["report_date"],
                "kvartirografia API and DOM report dates differ")
        try:
            resolved = resolve_kvart(raw_region["developers"], evidence)
            resolve_regions(raw_region.get("regions", []), evidence)
            validate_apartment_totals(raw_region.get("apartments", []), evidence)
        except ValueError as exc:
            raise RuntimeError("Incomplete dashboard coverage: " + str(exc)) from exc
        actual = raw_region.get("apartments_per_dev", [])
        require(len(actual) == len(resolved), "kvartirografia raw details do not cover the API population")
        actual_by_name = {row.get("site_name"): row for row in actual}
        require(len(actual_by_name) == len(actual), "kvartirografia raw details contain duplicate names")
        for item in resolved:
            row = actual_by_name.get(item["site_name"], {})
            require(row.get("api_verified") is True and row.get("source_id") == item["source_id"]
                    and kvart_details_match(row.get("apartments"), item["apartments"]),
                    "kvartirografia raw detail differs from its unique API identity")
        resolved_regions[region] = resolved
    for key, numbers in (("apartments", ("количество_шт_num", "площадь_тыс_м²_num")),
                         ("distribution", ("доля_num",)),
                         ("developers", ("квартиры_тыс_шт_num", "площадь_тыс_м²_num")),
                         ("regions", ("площадь_тыс_м²_num",))):
        require_frame(kvart.get(key), "kvartirografia/" + key, regions=True, numeric=numbers)
    # All aggregate developers need usable detailed apartment payloads.
    detailed = kvart.get("apartments_per_dev")
    require_frame(detailed, "kvartirografia/apartments_per_dev", regions=True,
                  numeric=("Все_количество_шт_num",))
    for region in ("rf", "msk"):
        evidence = next(row["developer_api"] for row in raw_kvart if row["region_key"] == region)
        try:
            canonical_developers = kvart["developers"].loc[lambda f: f.region_key.eq(region)]
            canonical_regions = kvart["regions"].loc[lambda f: f.region_key.eq(region)]
            exact_developers = resolve_kvart(canonical_developers.to_dict("records"), evidence)
            exact_regions = resolve_regions(canonical_regions.to_dict("records"), evidence)
            canonical_apartments = kvart["apartments"].loc[lambda f: f.region_key.eq(region)]
            exact_apartments = validate_apartment_totals(canonical_apartments.to_dict("records"), evidence)
        except ValueError as exc:
            raise RuntimeError("Incomplete dashboard coverage: canonical " + str(exc)) from exc
        for frame, exact in ((canonical_developers, exact_developers), (canonical_regions, exact_regions)):
            for item in exact:
                row = frame.loc[frame["наименование"].eq(item["site_name"])].iloc[0]
                for field, expected in (("квартиры_тыс_шт_num", float(item["apartments"]["all"]["count"]) / 1000),
                                        ("площадь_тыс_м²_num", float(item["apartments"]["all"]["area"]))):
                    require(finite(row[field]) and math.isclose(row[field], expected, rel_tol=1e-10, abs_tol=1e-8),
                            "kvartirografia canonical aggregate lacks the exact API measurement")
                from pipeline.downloaders.nashdom_kvart_api import ROOM_FIELDS
                count = float(item["apartments"]["all"]["count"])
                for kind, field in ROOM_FIELDS.items():
                    expected = float(item["apartments"][kind]["count"]) / count * 100
                    require(finite(row.get(field + "_num")) and math.isclose(row[field + "_num"], expected, rel_tol=1e-10, abs_tol=1e-8),
                            "kvartirografia canonical room share lacks the exact API measurement")
        from pipeline.downloaders.nashdom_kvart_api import apartment_kind
        for row in canonical_apartments.to_dict("records"):
            exact = exact_apartments[apartment_kind(row)]
            require(finite(row.get("количество_шт_num")) and row["количество_шт_num"] == exact["count"]
                    and finite(row.get("площадь_тыс_м²_num"))
                    and math.isclose(row["площадь_тыс_м²_num"], exact["area"] / 1000, rel_tol=1e-10, abs_tol=1e-8),
                    "kvartirografia canonical main apartment total lacks the exact API measurement")
        names = set(kvart["developers"].loc[lambda f: f.region_key.eq(region), "наименование"].astype(str))
        found = set(detailed.loc[detailed.region_key.eq(region), "наименование"].astype(str))
        require(not names - found, f"kvartirografia incomplete per-developer coverage for {region}")
        selected = detailed.loc[detailed.region_key.eq(region)]
        require(len(selected) == len(resolved_regions[region]), "kvartirografia canonical details do not cover the API population")
        for item in resolved_regions[region]:
            rows = selected.loc[selected["наименование"].eq(item["site_name"])]
            require(len(rows) == 1, "kvartirografia canonical details have missing/duplicate identities")
            row = rows.iloc[0]
            require(str(row.get("source_id")) == item["source_id"] and row.get("api_verified") == True,
                    "kvartirografia canonical source identity is missing or incorrect")
            for room, prefix in (("all", "Все"), ("ONE", "1комн"), ("TWO", "2комн"), ("THREE", "3комн"), ("FOUR", "4+комн")):
                for field, suffix in (("count", "количество_шт_num"), ("area", "площадь_тыс_м²_num")):
                    value = row.get(prefix + "_" + suffix)
                    require(finite(value) and math.isclose(value, float(item["apartments"][room][field]), rel_tol=1e-10, abs_tol=1e-8),
                            "kvartirografia canonical measurement differs from verified API detail")



def validate_construction_sources(access, construction, *, require_permits=True):
    require(any(p.name.startswith("construction_operational_") for p in access.source_files("construction_operational")),
            "construction has no dynamic snapshot")
    require_frame(construction.get("construction"), "construction", regions=True, numeric=("value_thousand_m2",))
    require_frame(construction.get("sales"), "construction/sales", regions=True)
    for row in construction["sales"].to_dict("records"):
        require_construction_sales(row, "construction/sales/" + str(row.get("region_key")))
    if require_permits:
        require_frame(construction.get("permits"), "construction/permits", numeric=("value_thousand_m2",))


def validate_economic_sources(access, *, aliases=frozenset({"fedstat", "rosstat"}), processed=True):
    from pipeline.data_access import NA_INDICATORS
    from pipeline.registry import by_id
    from pipeline.source_records import file_sha256
    from pipeline.download_provenance import receipt_path
    fedstat_ids = {"43246", "57824", "31074_часть1", "31074_часть2", "34118_часть1", "34118_часть2"}
    verified_downloads = {}
    for source in access._raw_files([access.DOWNLOADS_DIR], ["*.xls*"]):
        receipt = receipt_path(source)
        if receipt.is_file():
            info = json.loads(receipt.read_text(encoding="utf-8"))
            if info.get("source_id") in fedstat_ids and info.get("sha256") == file_sha256(source):
                verified_downloads.setdefault(info["source_id"], []).append(source)
    if "fedstat" in aliases:
        require(fedstat_ids <= set(verified_downloads), "fedstat lacks verified downloads: " +
                ", ".join(sorted(fedstat_ids - set(verified_downloads))))
        validate_dynamic_commissioning(access, verified_downloads)

    # Historical CSV additions are allowed only alongside the live processed
    # indicators. Empty ETL skips cannot inherit a falsely complete publication.
    for identifier in ("avg_salary", "ipc", *NA_INDICATORS):
        indicator = by_id(identifier)
        if indicator.source not in aliases:
            continue
        sources = access._raw_files([access.DOWNLOADS_DIR], list(indicator.file_patterns))
        require(sources, f"{indicator.source}/{identifier} has no downloaded source")
        if not processed:
            import pandas as pd
            for source in sources:
                with pd.ExcelFile(source):
                    pass
            continue
        target = access.DATA_PROCESSED / f"{identifier}.pkl"
        require(target.is_file(), f"{indicator.source}/{identifier} processed file is missing")
        frame = access.load_indicator(identifier)
        require_frame(frame, f"{indicator.source}/{identifier}", numeric=("value",))
        sources = access._raw_files([access.DOWNLOADS_DIR], list(indicator.file_patterns))
        require(sources, f"{indicator.source}/{identifier} has no downloaded source")
        require(all(source.stat().st_mtime_ns <= target.stat().st_mtime_ns for source in sources),
                f"{identifier} processed file predates its downloaded sources")
        require("source_file" in frame and frame.source_file.notna().all(), f"{identifier} has no provenance")
        names = {source.name for source in sources}
        require(set(frame.source_file.astype(str)) <= names, f"{identifier} references unavailable source files")
        if identifier in {"avg_salary", "ipc"}:
            for source_id in indicator.source_ids:
                require(set(frame.source_file.astype(str)) & {source.name for source in verified_downloads[source_id]},
                        f"{identifier} has no processed contribution from {source_id}")
        if identifier in {"avg_salary", "ipc"}:
            regions = frame.region.astype(str).str.casefold()
            require(regions.str.contains("моск").any() and regions.str.contains("российская федерация").any(),
                    f"{identifier} lacks Moscow/RF")

    return verified_downloads


def validate_erz_sources(access, top=None, cards=None):
    """Validate one raw or canonical ERZ cohort without reading other sources."""
    top = access.load_erzrf_top() if top is None else top
    cards = access.load_erzrf_cards() if cards is None else cards
    for key in ("obyem_stroitelstva", "obyem_vvoda", "nakopl_vvod", "potreb_kachestva", "skorost"):
        for region in ("rf", "msk"):
            frame = top.get(key, {}).get(region)
            if access.source_files("erzrf_top"):
                require_frame(frame, f"erz-top/{key}/{region}")
            else:
                require(frame is None or getattr(frame, "empty", False), "ERZ top mart has data without source files")
    if not access.source_files("erzrf_top"):
        def has_frame_rows(value):
            if isinstance(value, dict):
                return any(has_frame_rows(item) for item in value.values())
            return hasattr(value, "empty") and not value.empty
        require(not has_frame_rows(top), "ERZ top mart has data without source files")
        names = top.get("all_developers")
        require(names is None or (isinstance(names, (list, tuple)) and not names)
                or getattr(names, "empty", False), "ERZ top mart has developer facts without source files")
    if access.source_files("erzrf_cards"):
        require_frame(cards, "erz-cards")
        validate_erz_cards(access, cards)
    else:
        require(getattr(cards, "empty", False), "ERZ cards mart has data without source files")


def validate_source_raw(access, alias):
    """Validate only this provider before combining independently selected families."""
    from pipeline.source_availability import source_files
    require(source_files(access.PROJECT_ROOT, alias), alias + " has no source files")
    if alias == "monitoring":
        data = access.load_monitoring_2_0()
        for key in ("rv", "oks"):
            require_frame(data.get(key), "monitoring/" + key, numeric=("Общая площадь",))
        validate_monitoring_registry(access, data["rv"])
        validate_monitoring_history(access)
    elif alias == "kvart":
        validate_kvart_sources(access, access.load_kvartirografia())
    elif alias == "rasprod":
        validate_rasprod_sources(access, access.load_rasprodannost())
    elif alias == "construction":
        validate_construction_sources(access, access.load_construction_operational(), require_permits=False)
    elif alias in {"fedstat", "rosstat"}:
        validate_economic_sources(access, aliases={alias}, processed=False)
    elif alias in {"erz-top", "erz-cards"}:
        validate_erz_sources(access)


def validate_sources(access, *, require_erz=False):
    """Read canonical marts directly so a raw/derived fallback cannot mask gaps."""
    from pipeline.build_realty_marts import _specs

    from pipeline.source_availability import unavailable, source_files, indicator_ids
    absent = unavailable(access.PROJECT_ROOT)
    from pipeline.source_availability import read_publication, verify_inventory
    verify_inventory(access.PROJECT_ROOT, read_publication(access.PROJECT_ROOT))
    for alias in absent:
        require(not source_files(access.PROJECT_ROOT, alias), "Unavailable source still has raw data: " + alias)
        require(not any((access.DATA_PROCESSED / (key + ".pkl")).exists() for key in indicator_ids({alias})),
                "Unavailable source still has processed data: " + alias)
    owners = {"monitoring_2_0": "monitoring", "kvartirografia": "kvart", "construction_operational": "construction",
              "rasprodannost": "rasprod", "erzrf_top": "erz-top", "erzrf_cards": "erz-cards", "emiss_34118": "fedstat"}
    payloads = {}
    for spec in _specs(access):
        files = access.source_files(spec.name)
        if owners.get(spec.name) not in absent and (spec.name not in {"erzrf_top", "erzrf_cards"} or require_erz):
            require(files, f"{spec.name} has no source files")
        payloads[spec.name] = access._load_realty_mart(spec.name, files)
    monitoring = payloads["monitoring_2_0"]
    def empty_frames(value):
        if isinstance(value, dict):
            return all(empty_frames(item) for item in value.values())
        return not hasattr(value, "empty") or value.empty
    for alias, family in (("monitoring", "monitoring_2_0"), ("kvart", "kvartirografia"), ("rasprod", "rasprodannost")):
        if alias in absent:
            require(empty_frames(payloads[family]), "Unavailable source has fabricated mart rows: " + alias)
    if "construction" in absent:
        for field in ("construction", "sales"):
            require(empty_frames(payloads["construction_operational"].get(field)), "Unavailable construction has fabricated " + field)
    if "monitoring" not in absent:
        for key in ("rv", "oks"):
            require_frame(monitoring.get(key), "monitoring/" + key, numeric=("Общая площадь",))
        validate_monitoring_registry(access, monitoring["rv"])
        validate_monitoring_history(access)
    if "kvart" not in absent:
        validate_kvart_sources(access, payloads["kvartirografia"])
    if "construction" not in absent:
        validate_construction_sources(access, payloads["construction_operational"], require_permits="monitoring" not in absent)
    rasprod = payloads["rasprodannost"]
    if "rasprod" not in absent:
        validate_rasprod_sources(access, rasprod)
    validate_erz_sources(access, payloads["erzrf_top"], payloads["erzrf_cards"])
    require_frame(payloads["escrow_manual"], "escrow_manual")
    require(any(getattr(frame, "empty", True) is False for frame in payloads["vvod_static"].values()),
            "vvod_static is empty")
    require_frame(payloads["emiss_34118"], "emiss_34118")
    validate_economic_sources(access, aliases={"fedstat", "rosstat"} - absent)


def validate_pages(get, *, source_states=None):
    """Use the same meaningful API reports before and after a release switch."""
    from pipeline.source_availability import route_sources
    absent = {alias for alias, row in (source_states or {}).items() if row.get("state") == "unavailable"}
    original_get = get
    def get(route):
        payload = original_get(route)
        blocked = route_sources(route.split("?")[0]) & absent
        if blocked:
            require(payload.get("sourceUnavailable") is True and set(payload.get("sources", [])) == blocked,
                    "API does not explicitly declare unavailable source: " + route)
            return {}
        return payload
    catalogs = {route: get(route) for route in ROUTES}
    developers = catalogs["/api/v1/catalog"].get("controls", {}).get("developers", [])
    require(developers or "monitoring" in absent, "profile catalog has no developers")
    developer = next((row for row in developers if "msk" in row.get("regions", [])), None)
    require(developer or "monitoring" in absent, "profile catalog has no Moscow developer")
    if developer:
        profile = get("/api/v1/profile?" + urlencode({"developer": developer["developerKey"], "region": "msk"}))
        require(profile.get("profiles") or bool(route_sources("/api/v1/profile") & absent), "developer profile is empty")
    for region in ("msk", "rf"):
        apartments = get("/api/v1/apartments?region=" + region)
        if "kvart" not in absent:
            require(apartments.get("apartments") and apartments.get("distribution") and apartments.get("developers"),
                    "apartments overview lacks rows for " + region)
            require(any(finite(row.get("count")) for row in apartments["apartments"]), "apartments lack measurements")
            detail = get("/api/v1/apartments/developer?" + urlencode({"region": region, "developer": apartments["developers"][0]["name"]}))
            require(finite(detail.get("summary", {}).get("countThousand")), "apartment developer lacks measurements")
        sales = get("/api/v1/sales?region=" + region)
        require("rasprod" in absent or sales.get("metrics") and all(finite(row.get("value")) for row in sales["metrics"]), "sales lack measurements for " + region)
        construction = get("/api/v1/construction?region=" + region)
        if "construction" not in absent:
            metrics = construction.get("metrics", [])
            require(metrics and all((row.get("value") is None if "rasprod" in absent and row.get("id") in {"sold", "ready", "ratio"}
                                     else finite(row.get("value"))) for row in metrics), "construction lacks measurements for " + region)
        if not ({"construction", "monitoring"} & absent):
            require(construction.get("permits", {}).get("rows"), "construction permits are empty")
        if "construction" not in absent:
            require_construction_sales(construction.get("sales"), "construction report sales for " + region)
        annual = get("/api/v1/commissioning/annual?region=" + region)
        require(annual.get("charts") and any(chart.get("rows") for chart in annual["charts"]), "annual commissioning is empty")
    operational = get("/api/v1/commissioning/operational")
    require("monitoring" in absent or operational.get("tables") and any(table.get("rows") for table in operational["tables"]), "operational commissioning is empty")
    require(get("/api/v1/map?only_with_coords=false").get("rows") or "monitoring" in absent, "map is empty")
    require(get("/api/v1/linear").get("summary"), "linear commissioning is empty")
    for family in ("salary", "ipc", "accounts"):
        report = get("/api/v1/economics/" + family)
        require(("rosstat" if family == "accounts" else "fedstat") in absent or report.get("charts") and any(finite(point.get("y")) for chart in report["charts"]
                for series in chart.get("series", []) for point in series.get("points", [])),
                "economics/" + family + " has no measured chart points")
