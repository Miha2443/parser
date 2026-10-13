"""Manual disposable Debian verification of six public network sources.

This runner cannot publish. Production acceptance still requires all eight
sources; the public check must prove that full production acceptance fails.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time
from urllib.parse import parse_qs, urlencode, urlsplit

from docker.coverage import (finite, require, require_construction_sales, require_frame,
                             validate_construction_sources, validate_economic_sources,
                             validate_kvart_sources, validate_monitoring_history,
                             validate_rasprod_sources, validate_sources)

PUBLIC_SOURCES = frozenset({"monitoring", "construction", "kvart", "rasprod", "fedstat", "rosstat"})
PUBLIC_MARTS = ("monitoring_2_0", "construction_operational", "kvartirografia", "rasprodannost",
                "emiss_34118", "escrow_manual", "vvod_static")
FEDSTAT_IDS = frozenset({"43246", "57824", "31074_часть1", "31074_часть2", "34118_часть1", "34118_часть2"})


def validate_status(status, started, *, previous_id=None):
    require(status.get("run_id") and status["run_id"] != previous_id, "public-source status is stale")
    require(status.get("status") == "success" and status.get("current_stage") == "finished"
            and status.get("marts_ok") is True and status.get("processed_ok") is True
            and not status.get("failures") and not status.get("status_write_errors") and not status.get("error"),
            "public-source run did not finish successfully")
    for key in ("sources_requested", "successes"):
        values = status.get(key) or []
        require(len(values) == len(PUBLIC_SOURCES) and set(values) == PUBLIC_SOURCES,
                "public-source status must confirm exactly all six sources")
    # The orchestrator writes local timestamps to whole seconds.
    first = datetime.fromisoformat(str(status.get("started_at"))).timestamp()
    last = datetime.fromisoformat(str(status.get("finished_at"))).timestamp()
    require(math.floor(started) <= first <= last <= time.time() + 1, "public-source status predates this run")


def validate_receipts(access, started, *, required_ids=None):
    import pandas as pd
    from pipeline.download_provenance import receipt_path
    from pipeline.file_utils import validate_excel_file
    from pipeline.source_records import file_sha256
    from rosstat_checker import PAGE_SOURCES
    rosstat_ids = {item["key"] for entries in PAGE_SOURCES.values() for item in entries}
    required = FEDSTAT_IDS | rosstat_ids if required_ids is None else frozenset(required_ids)
    require(required and required <= FEDSTAT_IDS | rosstat_ids, "unrecognized verification download scope")
    records = {}
    for source in access._raw_files([access.DOWNLOADS_DIR], ["*.xls*"]):
        receipt = receipt_path(source)
        if not receipt.is_file():
            continue
        info = json.loads(receipt.read_text(encoding="utf-8"))
        identifier = info.get("source_id")
        if identifier not in required:
            continue
        require(identifier not in records, "duplicate active downloaded source: " + identifier)
        require(info.get("sha256") == file_sha256(source), "download receipt hash differs: " + identifier)
        downloaded = datetime.fromisoformat(str(info.get("downloaded_at")))
        require(downloaded.tzinfo is not None and downloaded.timestamp() >= math.floor(started)
                and source.stat().st_mtime >= math.floor(started), "download/source predates this run: " + identifier)
        validate_excel_file(source)
        with pd.ExcelFile(source) as workbook:
            require(workbook.sheet_names, "downloaded workbook has no sheets: " + identifier)
            sheets = len(workbook.sheet_names)
        records[identifier] = {"file": source.name, "sha256": info["sha256"], "bytes": source.stat().st_size,
                               "downloaded_at": info["downloaded_at"], "remote_date": info.get("remote_date"), "sheets": sheets}
    require(set(records) == required, "missing fresh verified downloads: " + ", ".join(sorted(required - set(records))))
    return records


def validate_public_sources(access, started):
    from pipeline.data_access import NA_INDICATORS
    from pipeline.source_records import file_sha256
    payloads = {}
    for name in PUBLIC_MARTS:
        files = access.source_files(name)
        require(files, name + " has no source files")
        payloads[name] = access._load_realty_mart(name, files)
    for name in ("monitoring_2_0", "construction_operational", "kvartirografia", "rasprodannost"):
        primary = [path for path in access.source_files(name) if path.name.startswith(name + "_")]
        require(primary and all(path.stat().st_mtime >= math.floor(started) for path in primary),
                name + " contains source files predating this run")
    for key in ("rv", "oks"):
        require_frame(payloads["monitoring_2_0"].get(key), "monitoring/" + key, numeric=("Общая площадь",))
    validate_kvart_sources(access, payloads["kvartirografia"])
    validate_construction_sources(access, payloads["construction_operational"])
    validate_rasprod_sources(access, payloads["rasprodannost"])
    receipts = validate_receipts(access, started)
    validate_economic_sources(access)  # Both dynamic IHS parts and exact consumer provenance.
    economics = {}
    for identifier in ("avg_salary", "ipc", *NA_INDICATORS):
        path = access.DATA_PROCESSED / (identifier + ".pkl")
        require(path.stat().st_mtime >= math.floor(started), identifier + " processed file predates this run")
        frame = access.load_indicator(identifier)
        economics[identifier] = {"rows": len(frame), "sha256": file_sha256(path),
                                 "sources": sorted(set(frame.source_file.astype(str)))}
    require_frame(payloads["emiss_34118"], "emiss_34118")
    require_frame(payloads["escrow_manual"], "escrow_manual")
    require(any(not getattr(frame, "empty", True) for frame in payloads["vvod_static"].values()), "static annual input is empty")
    validate_monitoring_history(access)
    kvart, rasprod = payloads["kvartirografia"], payloads["rasprodannost"]
    return {"downloads": receipts, "economics": economics,
            "monitoring": {key: len(payloads["monitoring_2_0"][key]) for key in ("rv", "oks")},
            "kvart": {region: {"developers": int(kvart["developers"].region_key.eq(region).sum()),
                                "confirmed": int(kvart["apartments_per_dev"].region_key.eq(region).sum())}
                      for region in ("rf", "msk")},
            "rasprod": {region: {"periods": len(rasprod["periods_by_region"][region]),
                                  "latest": rasprod["latest_period_by_region"][region]} for region in ("rf", "msk")}}


def validate_etl_audit(access, started, *, required_ids=None):
    from pipeline.data_access import NA_INDICATORS
    required = {"avg_salary", "ipc", *NA_INDICATORS} if required_ids is None else set(required_ids)
    require(required and required <= {"avg_salary", "ipc", *NA_INDICATORS}, "unrecognized verification ETL scope")
    path = access.DATA_PROCESSED / "etl_audit.jsonl"
    require(path.is_file(), "fresh economics ETL audit is missing")
    entries = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    entries = [entry for entry in entries if datetime.fromisoformat(entry["ts"]).timestamp() >= math.floor(started)]
    relevant = [entry for entry in entries if entry.get("indicator") in required]
    require(len(relevant) == len(required) and {entry["indicator"] for entry in relevant} == required,
            f"economics ETL audit does not confirm exactly {len(required)} fresh indicators")
    run_ids = {entry["run_id"] for entry in relevant}
    require(len(run_ids) == 1 and all(entry.get("status") == "success" for entry in relevant),
            "economics ETL audit contains skipped/failed indicators or mixed runs")
    require(any(entry.get("indicator") == "_run" and entry.get("status") == "finished"
                and entry.get("run_id") in run_ids and not entry.get("error") for entry in entries),
            "economics ETL audit run did not finish")
    for entry in relevant:
        frame = access.load_indicator(entry["indicator"])
        require(entry.get("rows") == len(frame) and set(entry.get("files", [])) == set(frame.source_file.astype(str)),
                "economics ETL audit differs from processed rows/provenance")
    return [{key: entry[key] for key in ("run_id", "indicator", "status", "rows", "files")} for entry in relevant]


def validate_housing_ytd(report, history):
    require(not history.duplicated(["year", "month"]).any(), "operational housing history contains duplicate periods")
    month = report["selection"]["month"]
    source = history.loc[history.month.le(month)].groupby("year").value_thousand_m2.sum().div(1000)
    table = next(item for item in report["tables"] if item["id"] == "housing")
    seen = set()
    for row in table["rows"]:
        year = row["Год"]
        require(year not in seen, "operational housing report contains duplicate years")
        seen.add(year)
        if year == report["currentYear"]:
            continue  # Current year is calculated from the RV registry.
        actual = row["За выбранный период, млн м²"]
        if year in source.index:
            require(finite(actual) and math.isclose(actual, source.loc[year], rel_tol=1e-10, abs_tol=1e-10),
                    "historical housing YTD differs from the named source block")
        else:
            require(actual is None, "missing historical housing months were replaced by a measurement")
    require(set(source.index) - {report["currentYear"]} <= seen, "historical housing source years are absent from the report")


def validate_public_pages(get, housing_history):
    catalogs = {name: get("/api/v1/" + name + "/catalog") for name in
                ("apartments", "sales", "construction", "commissioning/annual", "commissioning/operational", "map", "linear",
                 "economics/salary", "economics/ipc", "economics/accounts")}
    for name in ("apartments", "sales", "construction", "commissioning/annual"):
        require(catalogs[name].get("regions", [{}])[0].get("id") == "msk", name + " does not default to Moscow")
    require(get("/api/v1/apartments").get("region") == "msk", "default apartment report is not Moscow")
    for region in ("msk", "rf"):
        apartments = get("/api/v1/apartments?region=" + region)
        require(apartments.get("apartments") and apartments.get("distribution") and apartments.get("developers"), "apartments lack rows")
        require(any(finite(row.get("count")) for row in apartments["apartments"]), "apartments lack measurements")
        detail = get("/api/v1/apartments/developer?" + urlencode({"region": region, "developer": apartments["developers"][0]["name"]}))
        require(finite(detail.get("summary", {}).get("countThousand")), "apartment details lack measurements")
        sales = get("/api/v1/sales?region=" + region)
        require(len(sales.get("metrics", [])) == 4 and all(finite(row.get("value")) for row in sales["metrics"]), "sales lack four KPIs")
        require(sales.get("charts") and all(chart.get("series") for chart in sales["charts"]), "sales lack charts")
        construction = get("/api/v1/construction?region=" + region)
        require(construction.get("metrics") and all(finite(row.get("value")) for row in construction["metrics"]), "construction lacks measurements")
        require_construction_sales(construction.get("sales"), "construction sales")
        require(construction.get("permits", {}).get("rows"), "construction permits are empty")
        annual = get("/api/v1/commissioning/annual?region=" + region)
        require(annual.get("charts") and all(chart.get("rows") for chart in annual["charts"]), "annual charts are empty")
    operational = get("/api/v1/commissioning/operational")
    require(operational.get("tables") and all(table.get("rows") for table in operational["tables"]), "operational tables are empty")
    require(operational["selection"]["month"] == catalogs["commissioning/operational"]["defaultMonth"], "operational month does not follow its catalog")
    validate_housing_ytd(operational, housing_history)
    require(get("/api/v1/map?only_with_coords=false").get("rows"), "map rows are empty")
    require(get("/api/v1/linear").get("summary"), "linear summary is empty")
    defaults = {}
    for family in ("salary", "ipc", "accounts"):
        catalog = catalogs["economics/" + family]
        report = get("/api/v1/economics/" + family)
        require(report.get("charts") and all(any(finite(point.get("y")) for series in chart.get("series", [])
                for point in series.get("points", [])) for chart in report["charts"]), "economics charts lack measured points: " + family)
        defaults[family] = report["selection"]
        if family != "accounts":
            require(catalog["defaults"]["period"] == report["selection"]["period"] == "month", "economics default is not month")
            key = "region" if family == "salary" else "regions"
            require("моск" in str(report["selection"][key]).casefold(), "economics default is not Moscow")
            control = next(item for item in catalog["controls"] if item["id"] == key)
            for marker in ("моск", "российская федерация"):
                option = next((item for item in control["options"] if marker in item["id"].casefold()), None)
                require(option, "economics catalog lacks RF/Moscow")
                regional = get("/api/v1/economics/" + family + "?" + urlencode({key: option["id"]}))
                require(regional.get("charts") and all(any(finite(point.get("y")) for series in chart["series"]
                            for point in series["points"]) for chart in regional["charts"]), "regional economics has no measured points")
        else:
            # Resolve values from this source generation's controls rather than
            # assuming region labels or transmitting synthetic query values.
            controls = [item for item in catalog["controls"] if (item["id"].startswith("block") and item["id"].endswith("_regions"))
                        or item["id"] in {"structure_region", "index_region"}]
            require(controls, "accounts catalog has no regional controls")
            for marker in ("моск", "российская федерация"):
                selection = {}
                for control in controls:
                    option = next((item for item in control["options"] if marker in item["id"].casefold()), None)
                    require(option, "accounts catalog lacks RF/Moscow")
                    selection[control["id"]] = option["id"]
                regional = get("/api/v1/economics/accounts?" + urlencode(selection))
                require(regional.get("charts") and all(any(finite(point.get("y")) for series in chart["series"]
                            for point in series["points"]) for chart in regional["charts"]), "regional accounts have no measured points")
    return defaults


def confirm_full_publication_denied(access, status, started):
    from docker.publication import require_success
    denied = {}
    try:
        require_success(status, None, started)
    except RuntimeError as exc:
        denied["status_gate"] = str(exc)
    require("status_gate" in denied, "six sources were accepted as a complete production run")
    require(not access.source_files("erzrf_cards") and not access.source_files("erzrf_top"), "public container contains ERZ fixtures/data")
    try:
        validate_sources(access)
    except RuntimeError as exc:
        require(any(name + " has no source files" in str(exc) for name in ("erzrf_cards", "erzrf_top")),
                "full production coverage failed for an unrelated reason: " + str(exc))
        denied["coverage_gate"] = str(exc)
    require("coverage_gate" in denied, "six sources were accepted as full production coverage")
    return denied


def guard(root):
    require(os.environ.get("PARSER_LIVE_PUBLIC_SMOKE") == "1" and Path("/.dockerenv").is_file()
            and root == Path("/app") and Path.cwd() == root, "live-public runner needs a disposable /app Docker container")
    require(os.environ.get("TDM_DISABLED") == "1" and os.environ.get("PARALLEL_LIMIT") == "1"
            and os.environ.get("KVART_PER_DEV") == "1", "live-public environment is not fully scoped")
    for name in (".env", "config/erzrf.json", "config/telegram.json", "config/google-service-account.json"):
        require(not (root / name).exists(), "live-public container contains private configuration")
    require(not list((root / "downloads").glob("*.xls*")) and not list((root / "data/processed").glob("*.pkl"))
            and not (root / "data/marts/realty/manifest.json").exists(), "live-public container is not fresh")
    for directory in ("data/raw/realty/nashdom", "data/raw/realty/erzrf"):
        require(not list((root / directory).glob("*.xlsx")) and not list((root / directory).glob("*.json")),
                "live-public container contains dynamic source data")


def versions():
    return {"commit": os.environ.get("PARSER_GIT_SHA"), "python": platform.python_version(),
            "os_release": Path("/etc/os-release").read_text(encoding="utf-8"),
            **{name: subprocess.check_output([name, "--version"], text=True).strip() for name in ("chromium", "chromedriver")}}


def main():
    root = Path("/app")
    require(os.environ.get("PARSER_LIVE_PUBLIC_SMOKE") == "1" and Path("/.dockerenv").is_file()
            and Path.cwd() == root, "live-public runner needs a disposable /app Docker container")
    report_dir = root / "logs/live-public"
    report_dir.mkdir(parents=True, exist_ok=True)
    summary = {"scope": "six public sources; full production publication forbidden", "success": False,
               "started_at": datetime.now(timezone.utc).isoformat(), "stages": []}
    code, started = 1, time.time()
    try:
        guard(root)
        summary["versions"] = versions()
        require('VERSION_ID="12"' in summary["versions"]["os_release"], "production updater is not Debian 12")
        from docker.publication import seed
        seed(root)  # Approved tracked manual/static production seeds only.
        environment = {**os.environ, "PARSER_REQUIRE_REALTY_MARTS": "0"}
        commands = ([sys.executable, "scripts/update_realty.py", *sorted(PUBLIC_SOURCES), "--no-notify", "--retries", "0", "--full-rasprod-history"],
                    [sys.executable, "-m", "pipeline.build_realty_marts", "--strict"],
                    [sys.executable, "-m", "pipeline.build_realty_marts", "--check", "--strict"])
        with (report_dir / "commands.log").open("w", encoding="utf-8") as output:
            for command in commands:
                print("Live-public stage:", " ".join(command), flush=True)
                with subprocess.Popen(command, cwd=root, env=environment, stdout=subprocess.PIPE,
                                      stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace") as process:
                    for line in process.stdout:
                        output.write(line)
                        output.flush()
                        print(line, end="", flush=True)
                    returncode = process.wait()
                summary["stages"].append({"command": command[1:], "returncode": returncode})
                require(returncode == 0, "live-public stage failed; see commands.log")
        from pipeline.data_access import DataAccess, DataContext
        status = json.loads((root / "data/processed/realty_update_status.json").read_text(encoding="utf-8"))
        validate_status(status, started)
        summary["run"] = {key: status.get(key) for key in ("run_id", "started_at", "finished_at", "successes", "failures")}
        access = DataAccess(DataContext(root, downloads=root / "downloads", use_marts=True, require_marts=True))
        summary["sources"] = validate_public_sources(access, started)
        summary["etl"] = validate_etl_audit(access, started)
        summary["full_publication_denied"] = confirm_full_publication_denied(access, status, started)
        from fastapi.testclient import TestClient
        from backend.app import create_app
        os.environ["PARSER_REQUIRE_REALTY_MARTS"] = "1"
        api_stats = []
        summary["api"] = api_stats
        with TestClient(create_app()) as client:
            def get(route):
                response = client.get(route)
                parsed = urlsplit(route)
                stat = {"route": parsed.path, "status": response.status_code,
                        "regions": {key: value for key, value in parse_qs(parsed.query).items()
                                    if key in {"region", "regions", "structure_region", "index_region"} or key.endswith("_regions")}}
                api_stats.append(stat)
                require(response.status_code == 200, "public API failed: " + urlsplit(route).path)
                payload = response.json()
                stat.update({
                    "rows": len(payload.get("rows", [])), "charts": len(payload.get("charts", [])),
                    "points": sum(len(series.get("points", [])) for chart in payload.get("charts", []) for series in chart.get("series", []))})
                return payload
            summary["defaults"] = validate_public_pages(get, access.load_monitoring_operational_history()["housing_monthly"])
        summary["success"], code = True, 0
    except Exception as exc:
        summary["error"] = type(exc).__name__ + ": " + str(exc)[:1000]
        print(summary["error"], flush=True)
    finally:
        status_file = root / "data/processed/realty_update_status.json"
        if status_file.is_file():
            try:
                current = json.loads(status_file.read_text(encoding="utf-8"))
                summary["run"] = {key: current.get(key) for key in
                                  ("run_id", "status", "started_at", "finished_at", "successes", "failures", "current_stage")}
            except (OSError, ValueError) as exc:
                summary["status_read_error"] = type(exc).__name__
        audit = root / "data/processed/etl_audit.jsonl"
        if audit.is_file():
            shutil.copy2(audit, report_dir / "etl_audit.jsonl")
        summary["finished_at"] = datetime.now(timezone.utc).isoformat()
        (report_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print("Live-public verification:", "PASS" if summary["success"] else "FAIL", flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
