"""Offline, staged repair of monitoring processed data; never runs the ETL.

prepare writes only into a new evidence directory. publish replaces exactly one
production pickle after compare-and-swap checks and a verified backup. rollback
only replaces the exact published version, preserving a concurrent newer file.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import socket
import sys
import uuid

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import openpyxl
import pandas as pd

from pipeline.data_access import DataAccess, DataContext
from pipeline.orchestrator import _deduplicate_processed
from pipeline.parsers import nashdom_monitoring_2_0 as parser
from pipeline.source_records import file_sha256

TARGET = "data/processed/realty_monitoring_2_0.pkl"
MART = "data/marts/realty/monitoring_2_0.pkl"
# Independent explicit source-cell contract: no parser numeric/identity helpers.
CELL_METRICS = {
    "Общая площадь": ("total_area_m2", "м²"),
    "Жилая площадь": ("living_area_m2", "м²"),
    "Количество квартир": ("apartments_count", "шт"),
    "Количество апартаментов": ("apartments_count", "шт"),
    "Места в ДОУ": ("kindergarten_places", "мест"),
    "Места в СОШ": ("school_places", "мест"),
    "Посещения в смену в поликлиниках": ("clinic_visits_per_shift", "посещений/смена"),
    "Койки в больницах": ("hospital_beds", "коек"),
    "Машиномест": ("parking_places", "мест"),
    "Кол-во рабочих мест": ("jobs_count", "шт"),
    "Номерной фонд": ("hotel_rooms", "номеров"),
}
PROVENANCE = ["source_row", "source_file_sha256", "source_record_id"]


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


@contextmanager
def offline():
    """Block accidental Python socket traffic; do not call collectors/notifiers."""
    previous = os.environ.get("TDM_DISABLED")
    os.environ["TDM_DISABLED"] = "1"
    connect, connect_ex, create = socket.socket.connect, socket.socket.connect_ex, socket.create_connection

    def denied(*args, **kwargs):
        raise RuntimeError("Network is forbidden during monitoring recovery")

    socket.socket.connect = socket.socket.connect_ex = socket.create_connection = denied
    try:
        yield
    finally:
        socket.socket.connect, socket.socket.connect_ex, socket.create_connection = connect, connect_ex, create
        if previous is None:
            os.environ.pop("TDM_DISABLED", None)
        else:
            os.environ["TDM_DISABLED"] = previous


def fingerprint(path):
    stat = path.stat()
    return {"sha256": file_sha256(path), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def inputs(root):
    # Same authoritative directory/pattern as registry realty_monitoring_2_0.
    return sorted((root / "data/raw/realty/nashdom").glob("monitoring_2_0_*.xlsx"))


def protected_inventory(root):
    paths = []
    for directory in ("data", "state", "nashdom"):
        base = root / directory
        if base.exists():
            paths.extend(p for p in base.rglob("*") if p.is_file() and p.name != ".etl.lock")
    paths.extend(p for p in (root / "logs").glob("*.json") if p.is_file())
    return {p.relative_to(root).as_posix(): fingerprint(p) for p in sorted(paths)}


def write_json(path, value):
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def independent_cells(path):
    digest = file_sha256(path)
    result = {}
    skipped = {}
    with path.open("rb") as stream:
        book = openpyxl.load_workbook(stream, read_only=True, data_only=True)
        try:
            for sheet in book:
                rows = sheet.iter_rows(values_only=True)
                header = next(rows, ())
                if sheet.title not in {"Реестр ОКС", "Реестр РВ"} and not {"УИН", "Общая площадь"}.issubset(header):
                    continue
                require(len(header) == len(set(header)), f"Duplicate Excel headers: {path.name}/{sheet.title}")
                positions = [(i, name) for i, name in enumerate(header) if name in CELL_METRICS]
                for row_number, values in enumerate(rows, 2):
                    identity = hashlib.sha256(json.dumps([digest, sheet.title, row_number], ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
                    for column, metric in positions:
                        raw = values[column] if column < len(values) else None
                        if raw is None or raw == "":
                            continue
                        try:
                            value = float(str(raw).replace("\xa0", "").replace(" ", "").replace(",", "."))
                        except (ValueError, TypeError):
                            key = sheet.title + "/" + metric
                            skipped[key] = skipped.get(key, 0) + 1
                            continue
                        require(math.isfinite(value), f"Nonfinite source metric: {sheet.title}/{row_number}/{metric}")
                        view, unit = CELL_METRICS[metric]
                        result[(identity, metric)] = (sheet.title, row_number, value, view, unit)
        finally:
            book.close()
    return result, skipped


def reconcile_cells(frame, path):
    expected, skipped = independent_cells(path)
    actual = {}
    digest = file_sha256(path)
    for row in frame.itertuples(index=False):
        require(row.source_file_sha256 == digest, "Wrong provenance file digest")
        require(row.source_file == path.name, "Wrong source filename")
        key = (row.source_record_id, row.metric_column)
        require(key not in actual, "Duplicate source cell after parsing")
        actual[key] = (row.source_sheet, row.source_row, row.value, row.view, row.unit)
    require(actual == expected, f"Independent source-cell reconciliation failed: {path.name}")
    return {"independent_numeric_cells": len(expected), "exact_cell_matches": len(actual), "skipped_nonnumeric_cells_by_sheet_metric": skipped}


def summaries(frame):
    result = []
    for key, group in frame.groupby(["source_file", "source_sheet", "metric_column"], dropna=False, sort=True):
        result.append(dict(zip(["source_file", "source_sheet", "metric_column"], key)) | {
            "rows": len(group), "sum_decimal": str(sum((Decimal(str(value)) for value in group["value"]), Decimal(0)))})
    return result


def compare_mart(root, latest):
    require((root / MART).is_file(), "Existing monitoring mart is required for scoped recovery")
    existing = pd.read_pickle(root / MART)
    fresh = DataAccess(DataContext(root, use_marts=False)).load_monitoring_2_0(source_path=latest)
    require(existing.keys() == fresh.keys(), "Monitoring mart payload differs; separate review needed")
    for key in fresh:
        if isinstance(fresh[key], pd.DataFrame):
            pd.testing.assert_frame_equal(existing[key], fresh[key])
        else:
            require(existing[key] == fresh[key], f"Monitoring mart {key} differs; separate review needed")
    example = existing["rv"]
    example = example[(example["УИН"] == "HG8891-10-0002-001") & (example["source_sheet"] == "Лист4")]
    return {"exact_payload_equality": True, "rebuild_needed": False,
            "rv_rows": len(fresh["rv"]), "oks_rows": len(fresh["oks"]),
            "example_component_rows": len(example), "example_total_area": float(example["Общая площадь"].sum())}


def prepare(root, run, expected_latest_rows=None):
    root, run = root.resolve(), run.resolve()
    require(not run.is_relative_to(root / "data") and not run.is_relative_to(root / "state"), "Evidence directory must be outside production data/state")
    require(not run.exists(), "Use a new evidence directory")
    require(not (root / "state/.etl.lock").exists(), "ETL lock exists; recovery refuses to overlap")
    before = protected_inventory(root)
    selected = inputs(root)
    require(bool(selected), "No authoritative monitoring inputs")
    legacy = pd.read_pickle(root / TARGET)
    require(set(legacy["source_file"]).issubset({p.name for p in selected}), "A legacy source file is missing from authoritative raw inputs")
    run.mkdir(parents=True)
    stage = run / "raw"
    stage.mkdir()
    frames, source_reports = [], []
    for source in selected:
        copy = stage / source.name
        shutil.copy2(source, copy)
        require(fingerprint(copy) == before[source.relative_to(root).as_posix()], "Staged raw differs from source")
        frame = parser.parse(copy)
        check = reconcile_cells(frame, copy)
        deduped = _deduplicate_processed(frame)
        require(len(frame) == len(deduped), "Distinct source metrics unexpectedly collapsed")
        frames.append(deduped)
        source_reports.append({"path": source.relative_to(root).as_posix(), **fingerprint(source), **check,
                               "legacy_rows": int(legacy["source_file"].eq(source.name).sum()), "candidate_rows": len(deduped)})
        print(json.dumps(source_reports[-1], ensure_ascii=False), flush=True)
    candidate = _deduplicate_processed(pd.concat(frames, ignore_index=True))
    require(len(candidate) == sum(map(len, frames)), "Cross-version metrics collapsed")
    require(len(_deduplicate_processed(pd.concat([candidate, candidate], ignore_index=True))) == len(candidate), "Repeated import is not idempotent")
    old_columns = [c for c in legacy.columns if c not in ["loaded_at", *PROVENANCE]]
    joined = legacy[old_columns].merge(candidate[old_columns].drop_duplicates(), how="left", on=old_columns, indicator=True)
    require(joined["_merge"].eq("both").all(), "Some legacy records are absent or changed; review required")
    latest = max(selected, key=lambda p: p.stat().st_mtime_ns)
    latest_frame = candidate[candidate["source_file"] == latest.name]
    if expected_latest_rows is not None:
        require(len(latest_frame) == expected_latest_rows, "Latest snapshot differs from expected metric count")
    example = latest_frame[(latest_frame["uin"] == "HG8891-10-0002-001") & (latest_frame["source_sheet"] == "Лист4") & (latest_frame["metric_column"] == "Общая площадь")]
    mart_check = compare_mart(root, stage / latest.name)
    pd.to_pickle(candidate, run / "candidate.pkl")
    pd.testing.assert_frame_equal(candidate, pd.read_pickle(run / "candidate.pkl"))
    fallback = sorted((root / "nashdom").glob("monitoring_2_0_*.xlsx"))
    report = {"schema_version": 1, "phase": "prepared", "root": str(root), "target": TARGET,
              "prepared_at": datetime.now(timezone.utc).isoformat(), "offline": True, "TDM_DISABLED": "1",
              "candidate_sha256": file_sha256(run / "candidate.pkl"), "target_before": before[TARGET],
              "sources": source_reports, "excluded_fallback": [{"path": p.relative_to(root).as_posix(), **fingerprint(p), "reason": "Outside authoritative ETL file pattern; retained unchanged"} for p in fallback],
              "legacy_rows": len(legacy), "candidate_rows": len(candidate), "all_legacy_records_preserved": True,
              "restored_rows_in_existing_snapshots": sum(s["candidate_rows"] - s["legacy_rows"] for s in source_reports if s["legacy_rows"]),
              "added_rows_from_previously_missing_snapshots": sum(s["candidate_rows"] for s in source_reports if not s["legacy_rows"]),
              "latest_source": latest.name, "latest_rows": len(latest_frame),
              "latest_example": {"uin": "HG8891-10-0002-001", "source_sheet": "Лист4", "source_rows": example["source_row"].tolist(), "rows": len(example), "total_area": float(example["value"].sum())},
              "mart": mart_check, "legacy_summaries": summaries(legacy), "candidate_summaries": summaries(candidate),
              "protected_before": before, "planned_production_files": [TARGET]}
    require(protected_inventory(root) == before, "Production changed during isolated preparation")
    write_json(run / "reconciliation.json", report)
    return report


@contextmanager
def exclusive_etl_lock(root):
    path = root / "state/.etl.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    # Do not adopt, delete, or expire another process's lock.
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    content = f"token={token}\npid={os.getpid()}\noperation=monitoring_recovery\n"
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
        yield
    finally:
        if path.exists() and path.read_text(encoding="utf-8") == content:
            path.unlink()


def atomic_copy(source, target):
    temporary = target.with_name(target.name + ".recovery-" + uuid.uuid4().hex + ".tmp")
    try:
        shutil.copy2(source, temporary)
        require(file_sha256(temporary) == file_sha256(source), "Temporary copy failed integrity check")
        pd.read_pickle(temporary)
        with temporary.open("rb+") as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def read_report(root, run):
    report = json.loads((run / "reconciliation.json").read_text(encoding="utf-8"))
    require(Path(report["root"]).resolve() == root.resolve(), "Evidence belongs to another root")
    require(report["target"] == TARGET and report["planned_production_files"] == [TARGET], "Unexpected recovery target")
    return report


def publish(root, run, *, after_replace=None):
    root, run = root.resolve(), run.resolve()
    report = read_report(root, run)
    candidate, target, backup = run / "candidate.pkl", root / TARGET, run / "backup.pkl"
    require(file_sha256(candidate) == report["candidate_sha256"], "Candidate changed after reconciliation")
    frame = pd.read_pickle(candidate)
    require(len(frame) == report["candidate_rows"], "Candidate row count changed")
    require(not (run / "publication.json").exists(), "This recovery was already published or attempted; inspect its evidence")
    with exclusive_etl_lock(root):
        require(protected_inventory(root) == report["protected_before"], "Production changed since preparation")
        require([p.relative_to(root).as_posix() for p in inputs(root)] == [s["path"] for s in report["sources"]], "Raw source set changed")
        require(not backup.exists(), "Backup already exists; inspect previous attempt")
        shutil.copy2(target, backup)
        require(fingerprint(backup) == report["target_before"], "Backup integrity check failed")
        pd.read_pickle(backup)
        journal = {"phase": "backup_verified", "target": TARGET, "before_sha256": file_sha256(backup), "candidate_sha256": report["candidate_sha256"]}
        write_json(run / "publication.json", journal)
        replaced = False
        try:
            require(fingerprint(target) == report["target_before"], "Target changed immediately before publication")
            atomic_copy(candidate, target)
            replaced = True
            if after_replace:
                after_replace()
            require(file_sha256(target) == report["candidate_sha256"], "Published file failed integrity check")
            pd.testing.assert_frame_equal(frame, pd.read_pickle(target))
            after = protected_inventory(root)
            changed = sorted(key for key in set(after) | set(report["protected_before"]) if after.get(key) != report["protected_before"].get(key))
            require(changed == [TARGET], f"Unexpected production changes: {changed}")
            journal.update(phase="published", published_at=datetime.now(timezone.utc).isoformat(), changed_production_files=changed, rows=len(frame), backup_sha256=file_sha256(backup), after_sha256=file_sha256(target))
            write_json(run / "publication.json", journal)
        except BaseException as exc:
            if replaced:
                # A concurrent writer's version is never overwritten by rollback.
                if file_sha256(target) == report["candidate_sha256"]:
                    atomic_copy(backup, target)
                    require(fingerprint(target) == report["target_before"], "Automatic rollback failed integrity check")
                    journal["phase"] = "failed_rolled_back"
                else:
                    journal["phase"] = "failed_concurrent_target_preserved"
            else:
                journal["phase"] = "failed_before_replace"
            journal["error"] = f"{type(exc).__name__}: {exc}"
            write_json(run / "publication.json", journal)
            raise
    return journal


def rollback(root, run):
    root, run = root.resolve(), run.resolve()
    report = read_report(root, run)
    backup, target = run / "backup.pkl", root / TARGET
    require(fingerprint(backup) == report["target_before"], "Backup changed; refusing rollback")
    with exclusive_etl_lock(root):
        require(file_sha256(target) == report["candidate_sha256"], "Target is not this recovery version; refusing rollback")
        atomic_copy(backup, target)
        require(fingerprint(target) == report["target_before"], "Rollback integrity check failed")
        result = {"phase": "rolled_back", "target": TARGET, "restored_sha256": file_sha256(target), "rolled_back_at": datetime.now(timezone.utc).isoformat()}
        write_json(run / "rollback.json", result)
    return result


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("action", choices=["prepare", "publish", "rollback"])
    cli.add_argument("--root", type=Path, default=ROOT)
    cli.add_argument("--run-dir", type=Path, required=True)
    cli.add_argument("--expected-latest-rows", type=int)
    args = cli.parse_args()
    with offline():
        if args.action == "prepare":
            result = prepare(args.root, args.run_dir, args.expected_latest_rows)
            result = {key: result[key] for key in ("phase", "legacy_rows", "candidate_rows", "restored_rows_in_existing_snapshots", "added_rows_from_previously_missing_snapshots", "latest_rows", "mart")}
        else:
            result = {"publish": publish, "rollback": rollback}[args.action](args.root, args.run_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
