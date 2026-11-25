"""Admit an operator's public DOM.RF capture into a PRIVATE release candidate.

No network requests, browser/session data, fabricated history, or relaxed gates.
Each source is rolled back independently if its ordinary raw contract fails.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import uuid
from zipfile import ZipFile

from pipeline.source_availability import SOURCES, source_files, outcome

NAME = "domrf-2026-11-25"
RAW = Path("data/raw/realty/nashdom")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def load_capture(root):
    """The image seed remains accessible when /app/data is a host mount."""
    for directory in (Path(root) / "data/imports", Path("/opt/parser-seed/data/imports")):
        manifest = directory / (NAME + ".json")
        if not manifest.is_file():
            continue
        declaration = json.loads(manifest.read_text(encoding="utf-8"))
        if declaration.get("schemaVersion") != 1 or declaration.get("archive") != NAME + ".zip":
            raise ValueError("Unsupported DOM.RF import manifest")
        archive = directory / declaration["archive"]
        if archive.is_symlink() or digest(archive) != declaration.get("sha256"):
            raise ValueError("DOM.RF archive checksum mismatch")
        with ZipFile(archive) as z:
            names = z.namelist()
            if len(names) != len(set(names)) or sum(i.file_size for i in z.infolist()) > 100 * 1024 * 1024:
                raise ValueError("Invalid DOM.RF archive size/population")
            metadata = json.loads(z.read("metadata.json"))
            if metadata.get("schemaVersion") != 1 or metadata.get("captureDate") != "2026-11-25":
                raise ValueError("Unsupported DOM.RF capture")
            expected = metadata.get("files", {})
            if not isinstance(expected, dict) or set(names) != set(expected) | {"metadata.json"}:
                raise ValueError("DOM.RF archive manifest differs from its files")
            result = {}
            for name, checksum in expected.items():
                if Path(name).is_absolute() or ".." in Path(name).parts or "\\" in name:
                    raise ValueError("Unsafe DOM.RF archive path")
                body = z.read(name)
                if hashlib.sha256(body).hexdigest() != checksum:
                    raise ValueError("DOM.RF member checksum mismatch: " + name)
                result[name] = body if name.endswith(".xlsx") else json.loads(body)
            result["metadata"] = metadata
            return result
    raise FileNotFoundError("DOM.RF import seed is missing; rebuild updater from current Git")


def write_json(path, value):
    from pipeline.state_utils import write_json_atomic
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(Path(path), value)


def stamp_file(path, captured_at, source_id):
    from pipeline.download_provenance import receipt_path
    instant = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
    if instant.tzinfo is None:
        raise ValueError("Capture timestamp has no timezone")
    os.utime(path, (instant.timestamp(), instant.timestamp()))
    write_json(receipt_path(path), {"downloaded_at": instant.isoformat(), "sha256": digest(path),
               "source_id": source_id, "capture_method": "ordinary Yandex public report; offline import",
               "imported_at": now()})


def import_monitoring(candidate, capture):
    from pipeline.monitoring_changes import compare_monitoring_files, append_change_event
    raw = candidate / RAW
    files = list(raw.glob("monitoring_2_0_*.xlsx"))
    if any(p.name > "monitoring_2_0_20261125.xlsx" for p in files):
        raise ValueError("Server Monitoring 2.0 is newer than this capture")
    target = raw / "monitoring_2_0_20261125.xlsx"
    content = capture["ready_to_import/" + target.name]
    if target.exists() and target.read_bytes() == content:
        return False
    captured = datetime.fromisoformat(capture["metadata"]["monitoringCapturedAt"]).timestamp()
    if any(p.stat().st_mtime > captured for p in files):
        raise ValueError("Server Monitoring 2.0 was downloaded later than this capture")
    previous = max(files, key=lambda p: p.stat().st_mtime_ns) if files else None
    # Compare against the server baseline, never the collector computer's old workbook.
    with tempfile.TemporaryDirectory(dir=candidate, prefix=".source-staging-monitoring-") as directory:
        staged = Path(directory) / target.name
        staged.write_bytes(content)
        event = compare_monitoring_files(previous, staged) if previous else None
        raw.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(staged, target)
    stamp_file(target, capture["metadata"]["monitoringCapturedAt"], "monitoring_2_0")
    if event is not None:
        append_change_event(candidate / "data/processed/monitoring_2_0_changes.jsonl", event)
    return True


def import_construction(candidate, capture):
    from pipeline.downloaders.nashdom_operational_contract import period, validate_current_snapshot
    from pipeline.downloaders.nashdom_operational_history import HistoryStore, validate_bundle_payload
    incoming = capture["ready_to_import/construction_operational_20261125.json"]
    validate_current_snapshot(incoming)
    raw = candidate / RAW
    for path in raw.glob("construction_operational_*.json"):
        if not re.fullmatch(r"construction_operational_\d{8}(?:_.*)?\.json", path.name):
            continue
        old = json.loads(path.read_text(encoding="utf-8-sig"))
        validate_current_snapshot(old)
        if old == incoming:
            continue
        if path.stat().st_mtime > datetime.fromisoformat(incoming["scraped_at"].replace("Z", "+00:00")).timestamp():
            raise ValueError("Server construction/sales snapshot was captured later")
        for block in ("construction", "sales"):
            if period(old[block][0]["report_period"]) > period(incoming[block][0]["report_period"]):
                raise ValueError("Server " + block + " period is newer than this capture")
            old_date = old[block][0].get("report_date")
            if old_date and datetime.strptime(old_date, "%d.%m.%Y") > datetime.strptime(incoming[block][0]["report_date"], "%d.%m.%Y"):
                raise ValueError("Server " + block + " report date is newer than this capture")
    target = raw / "construction_operational_20261125.json"
    if target.exists() and json.loads(target.read_text(encoding="utf-8")) == incoming:
        return False
    raw.mkdir(parents=True, exist_ok=True)
    write_json(target, incoming)
    stamp_file(target, incoming["scraped_at"], "construction_operational")
    history = raw / "construction_history.json"
    if history.exists():
        payload = json.loads(history.read_text(encoding="utf-8"))
        validate_bundle_payload(payload)  # Do not quarantine or replace an invalid baseline.
        store = HistoryStore(raw)
        for block in ("construction", "sales"):
            month = period(incoming[block][0]["report_period"])
            # The canonical rows were normalized from literal full UI response sections.
            # A portable verified bundle accepts these already checked canonical rows.
            store.entries[block][month] = {"block": block, "period": month,
                "collected_at": incoming[block][0]["collected_at"], "rows": incoming[block],
                "origin": "operator public response capture", "capture": incoming["browser_capture"]}
        bounds = {block: max(store.entries[block]) for block in ("construction", "sales")}
        coverage = store.coverage(bounds)
        store.publish(coverage, complete=all(not v["missing_months"] for v in coverage.values()))
        stamp_file(history, incoming["scraped_at"], "construction_operational_history")
    return True


def merge_rasprod(previous, incoming, dynamics):
    """Every retained month must STILL match all four newly captured source series."""
    from pipeline.rasprod_contract import key, validate_entry, validate_history
    from pipeline.downloaders.nashdom_rasprod_api import matches_history
    expected = {}
    for row in dynamics:
        year, month = map(int, row["period"].split("-"))
        identity = row["region"], year, month
        if identity in expected:
            raise ValueError("Duplicate captured rasprod month")
        expected[identity] = {k: row[k] for k in ("squareSumIndex", "soldPercIndex", "soldReadyPercIndex", "readyPercIndex")}
    if {k[0] for k in expected} != {"rf", "msk"}:
        raise ValueError("Rasprod capture lacks both regions")
    values = {}
    for row in previous:
        validate_entry(row)
        identity = key(row)
        if identity in values or identity not in expected:
            raise ValueError("Server rasprod is newer, duplicated or outside the captured population")
        values[identity] = row
    for row in incoming:
        validate_entry(row)
        values[key(row)] = row
    for identity, row in values.items():
        if identity not in expected or not matches_history(row, expected[identity]):
            raise ValueError("Retained rasprod differs from captured dynamics: " + str(identity))
    result = [values[k] for k in sorted(values)]
    validate_history(result, expected)
    return result, set(expected)


def write_rasprod(raw, entries, expected, captured_at):
    """Use the collector's existing ETL workbook schema, not the readable export."""
    import pandas as pd
    from pipeline.rasprod_contract import TABLES
    target = raw / "rasprodannost_20261125.xlsx"
    kpis, sections = [], {k: [] for k in TABLES}
    for entry in entries:
        meta = {"region_key": entry["region_key"], "year": entry["year"], "month": entry["month_num"],
                "month_name": entry["month_name"], "report_period": entry["report_period"]}
        for metric in entry["kpi"]:
            kpis.append({**meta, **{k: metric[k] for k in ("название", "значение", "единица")},
                         **{"прогноз_" + k: v for k, v in metric.get("по_годам", {}).items()}})
        for sheet, section in TABLES.items():
            sections[sheet].extend({**meta, "section": section, **row} for row in entry["tables"][section])
    with pd.ExcelWriter(target, engine="openpyxl") as writer:
        pd.DataFrame(kpis).to_excel(writer, sheet_name="kpi", index=False)
        for sheet, rows in sections.items():
            pd.DataFrame(rows).to_excel(writer, sheet_name=sheet, index=False)
    source = target.with_suffix(".json")
    write_json(source, entries)
    write_json(target.with_suffix(".coverage.json"), {"schema_version": 1, "complete": True,
               "expected": [list(k) for k in sorted(expected)], "confirmed": [list(k) for k in sorted(expected)],
               "json_sha256": digest(source), "xlsx_sha256": digest(target)})
    for path in (target, source, target.with_suffix(".coverage.json")):
        stamp_file(path, captured_at, "rasprodannost")


def import_rasprod(candidate, capture):
    from pipeline.data_access import DataAccess, DataContext
    from docker.coverage import validate_rasprod_sources
    access = DataAccess(DataContext(candidate, downloads=candidate / "downloads", use_marts=False))
    sources = access.source_files("rasprodannost")
    if not sources:
        raise ValueError("Verified server rasprod history is missing; current-only reports cannot replace it")
    validate_rasprod_sources(access, access.load_rasprodannost())
    latest = max(sources, key=lambda p: p.stat().st_mtime_ns)
    old = json.loads(latest.with_suffix(".json").read_text(encoding="utf-8"))
    current = capture["rasprodannost_current_2026-10.json"]
    merged, expected = merge_rasprod(old, current, capture["rasprodannost_dynamics_2020-01_2026-10.json"])
    if old == merged:
        return False
    stamp = capture["ready_to_import/construction_operational_20261125.json"]["scraped_at"]
    if latest.stat().st_mtime > datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp():
        raise ValueError("Server rasprod was captured later; refusing to downgrade")
    write_rasprod(candidate / RAW, merged, expected, stamp)
    return True


def import_mortgage(candidate, capture):
    from pipeline.downloaders.nashdom_mortgage_api import normalize_month
    from pipeline.downloaders.nashdom_mortgage import year_bundle
    from pipeline.mortgage_history import load_published, save_checkpoint, publish_history
    raw = candidate / RAW
    previous = load_published(raw)
    if previous is None:
        raise ValueError("Verified server mortgage baseline is missing")
    incoming = capture["mortgage_current_2026-09.json"]
    if {r["region"] for r in incoming} != {"msk", "rf"} or len(incoming) != 2:
        raise ValueError("Mortgage capture lacks exactly RF and Moscow")
    captured_at = capture["ready_to_import/construction_operational_20261125.json"]["scraped_at"]
    already_imported = []
    for row in incoming:
        exact = normalize_month(row["region"], row["period"], row["rawMetrics"]["exchanges"])
        existing = [v for v in previous["records"] if v["region"] == row["region"] and v["period"] == row["period"]]
        source_time = previous.get("yearSources", {}).get(row["region"], {}).get(row["period"][:4], {}).get("collectedAt")
        already_imported.append(len(existing) == 3 and source_time == captured_at and all(
            {k: v[k] for k in exact["metrics"][v["segment"]]} == exact["metrics"][v["segment"]] for v in existing))
    if all(already_imported):
        return False
    refreshed = []
    for row in incoming:
        region, period = row["region"], row["period"]
        if max(r["period"] for r in previous["records"] if r["region"] == region) > period:
            raise ValueError("Server mortgage has newer months than this capture")
        exact = normalize_month(region, period, row["rawMetrics"]["exchanges"])
        if exact["metrics"] != row["metrics"]:
            raise ValueError("Mortgage capture differs from literal source responses")
        year = int(period[:4])
        by_period = {}
        for old in previous["records"]:
            if old["region"] == region and old["period"].startswith(str(year) + "-"):
                by_period[old["period"]] = normalize_month(region, old["period"], old["sourceEvidence"]["exchanges"])
        by_period[period] = exact
        bundle = year_bundle(region, year, [(int(p[5:]), value) for p, value in sorted(by_period.items())])
        bundle["evidence"] = {"method": "incremental operator capture; older months retain their original raw source evidence",
                              "retainedBaselineCollectedAt": previous["collectedAt"], "refreshedPeriods": [period]}
        bundle["collectedAt"] = captured_at
        old_time = previous.get("yearSources", {}).get(region, {}).get(str(year), {}).get("collectedAt")
        if old_time and datetime.fromisoformat(old_time.replace("Z", "+00:00")) > datetime.fromisoformat(bundle["collectedAt"].replace("Z", "+00:00")):
            raise ValueError("Server mortgage year was captured later")
        save_checkpoint(raw, bundle)
        refreshed.append((region, year))
    available = {r: previous["coverage"]["regions"][r]["availableYears"] for r in ("rf", "msk")}
    result = publish_history(raw, available, prior=previous, requested_years={r: [2026] for r in ("rf", "msk")}, refreshed_years=refreshed)
    return result != previous


def apply(candidate, root, *, allow_unchanged=False):
    """Independent source transactions; only the caller's private candidate changes."""
    candidate, root = Path(candidate), Path(root)
    capture = load_capture(root)
    results = {"kvart": {"state": "retained", "reason": "Сохранена прежняя квартирография: в пакете нет полного независимого DOM-списка девелоперов"}}
    handlers = {"monitoring": import_monitoring, "construction": import_construction,
                "rasprod": import_rasprod, "mortgage": import_mortgage}
    for alias, handler in handlers.items():
        before = set(source_files(candidate, alias)) if alias in SOURCES else {candidate / RAW / "mortgage_history.json"}
        journal = candidate / "data/processed/monitoring_2_0_changes.jsonl"
        if alias == "monitoring" and journal.exists():
            before.add(journal)
        before = {p for p in before if p.exists()}
        with tempfile.TemporaryDirectory(dir=candidate, prefix=".source-staging-domrf-") as folder:
            backup = Path(folder)
            for file in before:
                target = backup / file.relative_to(candidate)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(file, target)
            try:
                changed = handler(candidate, capture)
                if alias in SOURCES:
                    from docker.coverage import validate_source_raw
                    from pipeline.data_access import DataAccess, DataContext
                    validate_source_raw(DataAccess(DataContext(candidate, downloads=candidate / "downloads", use_marts=False)), alias)
                else:
                    from docker.mortgage import _verified
                    if _verified(candidate) is None:
                        raise ValueError("Mortgage source did not validate")
                results[alias] = {"state": "fresh" if changed else "retained", "reason": "Импорт проверенных данных от 25.11.2026" if changed else "Эти данные уже установлены"}
            except (OSError, ValueError, RuntimeError, LookupError, TypeError) as exc:
                after = set(source_files(candidate, alias)) if alias in SOURCES else {candidate / RAW / "mortgage_history.json"}
                if alias == "monitoring":
                    after.add(journal)
                for file in after - before:
                    file.unlink(missing_ok=True)
                for file in before:
                    shutil.copy2(backup / file.relative_to(candidate), file)
                results[alias] = {"state": "retained", "reason": "Прежние данные сохранены: " + str(exc)[:400]}
        print("DOMRF import " + alias + ": " + json.dumps(results[alias], ensure_ascii=False), flush=True)
    (candidate / "logs").mkdir(exist_ok=True)
    write_json(candidate / "logs/domrf-import-2026-11-25.json", results)
    if not allow_unchanged and not any(row["state"] == "fresh" for row in results.values()):
        raise RuntimeError("No new validated DOM.RF data to publish; current site left unchanged. " + json.dumps(results, ensure_ascii=False))
    return results


def publication_metadata(candidate, results):
    """Manual import has its own truthful evidence; it never fakes a collector run."""
    run_id = "manual-domrf-" + uuid.uuid4().hex
    started = now()
    rows = {}
    for alias in sorted(SOURCES):
        files = source_files(candidate, alias)
        result = results.get(alias, {"state": "retained", "reason": "Источник сохранён из действующей публикации; в ручной пакет не входит"})
        fresh = result["state"] == "fresh"
        code = "manual_capture_imported" if fresh else "manual_capture_retained"
        proof = outcome(alias, run_id, "success" if fresh else "failed", phase="manual_import", reason_code=code, reason=result["reason"])
        rows[alias] = {"state": "fresh" if fresh else "retained" if files else "unavailable",
                      "reason_code": code, "reason": result["reason"], "outcome": proof,
                      "files": [str(p.relative_to(candidate)) for p in files],
                      "source_dates": {p.name: datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat() for p in files},
                      "inventory": [{"path": str(p.relative_to(candidate)), "sha256": digest(p), "mtime_ns": p.stat().st_mtime_ns} for p in files]}
    return {"schema_version": 2, "mode": "manual_import", "status": "degraded", "run_id": run_id,
            "run_started_at": started, "run_finished_at": now(), "recorded_at": now(), "sources": rows,
            "optional_sources": {alias: rows[alias] for alias in ("erz-top", "erz-cards")},
            "manual_import": results, "full_marts_built": False}
