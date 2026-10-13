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
    """34118 currently provides IHS, with RF/Moscow in both source parts."""
    import pandas as pd
    from pipeline.data_access import _parse_emiss_34118_periods_file
    keys = ["region", "year", "month", "period_type"]
    frames = []
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
        frames.append((source, frame))
    expected = pd.concat([frame for _, frame in sorted(frames, key=lambda pair: pair[0].stat().st_mtime_ns)], ignore_index=True)
    expected = expected.drop_duplicates(keys, keep="last").set_index(keys)
    consumed = access.load_emiss_34118_periods().set_index(keys)
    for period, row in expected.iterrows():
        require(period in consumed.index, "dynamic 34118 period is absent from the consumer")
        chosen = consumed.loc[period]
        require(chosen.source_file == row.source_file and finite(chosen["ИЖС"])
                and math.isclose(chosen["ИЖС"], row["ИЖС"], rel_tol=1e-10, abs_tol=1e-8),
                "dynamic 34118 measurements are masked by another source")


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
    for raw in raw_rows:
        row = consumed[str(raw["source_id"])]
        require(row.get("name_card") == raw["name_card"], "erz-cards canonical name differs from its source")
        for column in measured_columns(raw):
            value = number(raw[column])
            if value is not None:
                require(number(row.get(column)) == value, "erz-cards canonical measurement differs from its source")


def validate_sources(access):
    """Read canonical marts directly so a raw/derived fallback cannot mask gaps."""
    from pipeline.build_realty_marts import _specs
    from pipeline.data_access import NA_INDICATORS, RASPROD_TABLE_SHEETS
    from pipeline.registry import by_id

    payloads = {}
    for spec in _specs(access):
        files = access.source_files(spec.name)
        require(files, f"{spec.name} has no source files")
        payloads[spec.name] = access._load_realty_mart(spec.name, files)
    monitoring = payloads["monitoring_2_0"]
    for key in ("rv", "oks"):
        require_frame(monitoring.get(key), "monitoring/" + key, numeric=("Общая площадь",))
    kvart = payloads["kvartirografia"]
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

    construction = payloads["construction_operational"]
    require(any(p.name.startswith("construction_operational_") for p in access.source_files("construction_operational")),
            "construction has no dynamic snapshot")
    require_frame(construction.get("construction"), "construction", regions=True, numeric=("value_thousand_m2",))
    require_frame(construction.get("sales"), "construction/sales", regions=True)
    for row in construction["sales"].to_dict("records"):
        require_construction_sales(row, "construction/sales/" + str(row.get("region_key")))
    require_frame(construction.get("permits"), "construction/permits", numeric=("value_thousand_m2",))
    rasprod = payloads["rasprodannost"]
    from pipeline.source_records import file_sha256
    validate_rasprod_sources(access, rasprod)
    top = payloads["erzrf_top"]
    for key in ("obyem_stroitelstva", "obyem_vvoda", "nakopl_vvod", "potreb_kachestva", "skorost"):
        for region in ("rf", "msk"):
            require_frame(top.get(key, {}).get(region), f"erz-top/{key}/{region}")
    require_frame(payloads["erzrf_cards"], "erz-cards")
    validate_erz_cards(access, payloads["erzrf_cards"])
    require_frame(payloads["escrow_manual"], "escrow_manual")
    require(any(getattr(frame, "empty", True) is False for frame in payloads["vvod_static"].values()),
            "vvod_static is empty")
    require_frame(payloads["emiss_34118"], "emiss_34118")
    from pipeline.download_provenance import receipt_path
    fedstat_ids = {"43246", "57824", "31074_часть1", "31074_часть2", "34118_часть1", "34118_часть2"}
    verified_downloads = {}
    for source in access._raw_files([access.DOWNLOADS_DIR], ["*.xls*"]):
        receipt = receipt_path(source)
        if receipt.is_file():
            info = json.loads(receipt.read_text(encoding="utf-8"))
            if info.get("source_id") in fedstat_ids and info.get("sha256") == file_sha256(source):
                verified_downloads.setdefault(info["source_id"], []).append(source)
    require(fedstat_ids <= set(verified_downloads), "fedstat lacks verified downloads: " +
            ", ".join(sorted(fedstat_ids - set(verified_downloads))))
    validate_dynamic_commissioning(access, verified_downloads)

    # Historical CSV additions are allowed only alongside the live processed
    # indicators. Empty ETL skips cannot inherit a falsely complete publication.
    for identifier in ("avg_salary", "ipc", *NA_INDICATORS):
        indicator = by_id(identifier)
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


def validate_pages(get):
    """Use the same meaningful API reports before and after a release switch."""
    catalogs = {route: get(route) for route in ROUTES}
    developers = catalogs["/api/v1/catalog"].get("controls", {}).get("developers", [])
    require(developers, "profile catalog has no developers")
    developer = next((row for row in developers if "msk" in row.get("regions", [])), None)
    require(developer, "profile catalog has no Moscow developer")
    profile = get("/api/v1/profile?" + urlencode({"developer": developer["developerKey"], "region": "msk"}))
    require(profile.get("profiles"), "developer profile is empty")
    for region in ("msk", "rf"):
        apartments = get("/api/v1/apartments?region=" + region)
        require(apartments.get("apartments") and apartments.get("distribution") and apartments.get("developers"),
                "apartments overview lacks rows for " + region)
        require(any(finite(row.get("count")) for row in apartments["apartments"]), "apartments lack measurements")
        detail = get("/api/v1/apartments/developer?" + urlencode({"region": region, "developer": apartments["developers"][0]["name"]}))
        require(finite(detail.get("summary", {}).get("countThousand")), "apartment developer lacks measurements")
        sales = get("/api/v1/sales?region=" + region)
        require(sales.get("metrics") and all(finite(row.get("value")) for row in sales["metrics"]), "sales lack measurements for " + region)
        construction = get("/api/v1/construction?region=" + region)
        require(construction.get("metrics") and all(finite(row.get("value")) for row in construction["metrics"]),
                "construction lacks measurements for " + region)
        require(construction.get("permits", {}).get("rows"), "construction permits are empty")
        require_construction_sales(construction.get("sales"), "construction report sales for " + region)
        annual = get("/api/v1/commissioning/annual?region=" + region)
        require(annual.get("charts") and any(chart.get("rows") for chart in annual["charts"]), "annual commissioning is empty")
    operational = get("/api/v1/commissioning/operational")
    require(operational.get("tables") and any(table.get("rows") for table in operational["tables"]), "operational commissioning is empty")
    require(get("/api/v1/map?only_with_coords=false").get("rows"), "map is empty")
    require(get("/api/v1/linear").get("summary"), "linear commissioning is empty")
    for family in ("salary", "ipc", "accounts"):
        report = get("/api/v1/economics/" + family)
        require(report.get("charts") and any(finite(point.get("y")) for chart in report["charts"]
                for series in chart.get("series", []) for point in series.get("points", [])),
                "economics/" + family + " has no measured chart points")
