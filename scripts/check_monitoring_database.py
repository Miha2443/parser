"""Failure, provenance, publication and read-load checks for the shadow DB pilot.

Always runs fixtures in temporary databases. --source additionally imports raw
XLSX into the requested shadow DB and compares every record with the shared
loader, raw cell nulls and the current trusted mart (if requested).
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import asdict
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import statistics
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.database import MonitoringDatabase, PublicationConflict
from pipeline.database.repository import AREA_FIELDS, RECORD_FIELDS, canonical_json


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def expect(error_type, action):
    try:
        action()
    except error_type:
        return
    raise AssertionError(f"Expected {error_type.__name__}")


def fixture(raw_label="v1", *, amount="0.10"):
    raw_hash = hashlib.sha256(raw_label.encode()).hexdigest()
    records = []
    for row, uin in [(2, "shared-uin"), (3, "shared-uin"), (4, None)]:
        identity = hashlib.sha256(json.dumps([raw_hash, "Реестр РВ", row], ensure_ascii=False,
                                             separators=(",", ":")).encode()).hexdigest()
        record = {field: None for field in RECORD_FIELDS}
        record.update(
            source_sheet="Реестр РВ", source_row=row, source_record_id=identity, registry="rv",
            uin=uin, developer="Застройщик с длинным названием", year=2026,
            raw_total_area_missing=int(row == 4), raw_residential_area_missing=1,
            raw_total_area_invalid=0, raw_residential_area_invalid=0,
            normalized_payload_json="{}", raw_payload_json="{}",
        )
        record.update({field: amount for field in AREA_FIELDS})
        record["total_area_m2"] = None if row == 4 else format(Decimal(amount) * (row - 1), "f")
        record["residential_area_m2"] = None
        records.append(record)
    return {"raw_file": {"sha256": raw_hash, "path": raw_label + ".xlsx", "size_bytes": 123},
            "normalizer_version": "fixture-v1", "records": records}


def fixture_checks() -> list[str]:
    passed = []
    with tempfile.TemporaryDirectory(prefix="monitoring-db-") as directory:
        root = Path(directory)
        database = MonitoringDatabase(root / "pilot.sqlite")
        database.initialize()
        state0 = database.active_state()
        one = database.stage_version(**fixture())
        repeated = database.stage_version(**fixture())
        require(repeated["idempotent"] and one["version_id"] == repeated["version_id"], "Idempotence failed")
        revised_normalizer = fixture()
        revised_normalizer["normalizer_version"] = "fixture-v2"
        revised = database.stage_version(**revised_normalizer)
        require(revised["version_id"] != one["version_id"], "New normalizer did not create an immutable version")
        require(database.stage_version(**revised_normalizer)["idempotent"], "Repeated raw/new normalizer failed")
        p1 = database.publish(one["version_id"], expected=state0)
        result = database.query()
        require(result["summary"]["record_count"] == 3 and result["summary"]["missing_uin"] == 1,
                "Repeated/empty UIN rows lost")
        require(result["summary"]["total_area_m2"] == "0.30", "Decimal arithmetic is not exact")
        require(result["provenance"]["raw_sha256"] == fixture()["raw_file"]["sha256"], "Provenance lost")
        require(database.query(filters={"uin": "shared-uin"})["summary"]["record_count"] == 2, "UIN became unique")
        passed += ["idempotent import", "empty and duplicate UIN retained", "exact decimal 0.10+0.20 and preserved NULL"]
        passed.append("same raw file with a new normalizer version")
        expect(ValueError, lambda: database.stage_version(**{**fixture("empty"), "records": []}))
        expect(ValueError, lambda: database.stage_version(**fixture(amount="-1")))
        expect(ValueError, lambda: database.stage_version(**fixture(amount="NaN")))
        expect(ValueError, lambda: database.stage_version(**fixture(amount="0.20")))
        bad = fixture("bad")
        bad["records"][0]["source_row"] = 22
        expect(ValueError, lambda: database.stage_version(**bad))
        passed.append("invalid/empty/nondeterministic/source-mismatch versions rejected")
        two = database.stage_version(**fixture("v2", amount="0.20"))

        def fail():
            raise RuntimeError("injected before active pointer switch")

        expect(RuntimeError, lambda: database.publish(two["version_id"], expected=p1, before_switch=fail))
        require(database.active_state() == p1, "Failure changed active publication")
        with closing(database.connect(readonly=True)) as connection:
            require(connection.execute("SELECT COUNT(*) FROM publications").fetchone()[0] == 1,
                    "Failed publication left partial metadata")
        passed.append("failure before pointer switch rolls back publication")
        with database.snapshot() as reader:
            old = reader.query()
            p2 = database.publish(two["version_id"], expected=p1)
            require(reader.query() == old, "Reader snapshot changed during publication")
            require(database.query()["summary"]["total_area_m2"] == "0.60", "New reader missed publication")
        expect(PublicationConflict, lambda: database.publish(one["version_id"], expected=p1))
        require(database.query(publication_id=p1.publication_id)["summary"]["total_area_m2"] == "0.30",
                "Pinned historical query failed")
        back = database.rollback(p1.publication_id, expected=p2)
        require(back.publication_id == p1.publication_id and back.generation > p2.generation, "Rollback failed")
        expect(PublicationConflict, lambda: database.publish(two["version_id"], expected=p1))
        passed += ["WAL reader snapshot while writer publishes", "historical query", "CAS conflict and ABA protection", "rollback"]
        three = database.stage_version(**fixture("v3", amount="0.30"))

        def compete(version_id):
            try:
                return database.publish(version_id, expected=back)
            except PublicationConflict:
                return None

        with ThreadPoolExecutor(max_workers=2) as executor:
            winners = list(executor.map(compete, [two["version_id"], three["version_id"]]))
        require(sum(winner is not None for winner in winners) == 1, "Concurrent CAS allowed two winners")
        passed.append("concurrent writers: one CAS winner")
        with closing(database.connect()) as connection:
            expect(sqlite3.IntegrityError, lambda: connection.execute("UPDATE monitoring_records SET uin='changed'"))
            expect(sqlite3.IntegrityError, lambda: connection.execute("DELETE FROM monitoring_records"))
            expect(sqlite3.IntegrityError, lambda: connection.execute("UPDATE dataset_versions SET record_count=1"))
            expect(sqlite3.IntegrityError, lambda: connection.execute("INSERT OR REPLACE INTO dataset_versions SELECT * FROM dataset_versions LIMIT 1"))
            expect(sqlite3.IntegrityError, lambda: connection.execute("INSERT OR REPLACE INTO raw_files SELECT * FROM raw_files LIMIT 1"))
            expect(sqlite3.IntegrityError, lambda: connection.execute("INSERT OR REPLACE INTO sources SELECT * FROM sources LIMIT 1"))
            require(connection.execute("PRAGMA foreign_key_check").fetchall() == [], "FK integrity failed")
            require(connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "SQLite integrity failed")
            require({row[0] for row in connection.execute("SELECT DISTINCT typeof(total_area_m2) FROM monitoring_records")} == {"text", "null"},
                    "Area values stored as binary numbers")
        passed.append("immutable records/versions and integrity constraints")
        backup = root / "backup.sqlite"
        restored = root / "restored.sqlite"
        database.backup(backup)
        MonitoringDatabase(backup).backup(restored)
        require(MonitoringDatabase(restored).query() == database.query(), "Backup restore changed query")
        expect(FileExistsError, lambda: database.backup(backup))
        passed.append("SQLite online backup and restore to a new file")
        expect(ValueError, lambda: database.query(filters={"registry; DROP TABLE sources;": "rv"}))
        expect(ValueError, lambda: database.query(limit=0))
        require(database.query(filters={"developer": "absent"})["summary"]["record_count"] == 0,
                "Empty query is invalid")
        passed.append("query filter validation and empty result")
        from pipeline.database.import_monitoring import decimal_area_sums
        large = fixture("large-decimal")
        large["records"][0]["total_area_m2"] = "999999999999999999999999999999"
        expected_sum = "999999999999999999999999999999.20"
        require(decimal_area_sums(large["records"])["total_area_m2"] == expected_sum,
                "Validation used the ambient Decimal precision")
        large_version = database.stage_version(**large)
        large_publication = database.publish(large_version["version_id"], expected=database.active_state())
        require(database.query(publication_id=large_publication.publication_id)["summary"]["total_area_m2"] == expected_sum,
                "SQL decimal aggregate differs for 30-digit values")
        passed.append("30-digit decimal validation and SQL aggregate agree")
    return passed


def compare_real(database, prepared, publication_id):
    result = {}
    with database.snapshot(publication_id) as snapshot:
        for registry in ("rv", "oks"):
            query = snapshot.query(filters={"registry": registry})
            expected = prepared.validation
            summary = query["summary"]
            require(summary["record_count"] == expected["row_counts"][registry], "Real row count differs")
            require(summary["missing_uin"] == expected["missing_uin"][registry], "Real UIN nulls differ")
            for key, count in expected["raw_area_missing"][registry].items():
                require(summary[key] == count, "Raw null count differs")
            for field in AREA_FIELDS:
                require(Decimal(summary[field]) == Decimal(expected["decimal_sums"][registry][field]),
                        "Real decimal sum differs: " + field)
            result[registry] = summary
        # Every typed field and both payloads must survive insertion exactly.
        stored = snapshot.connection.execute(
            "SELECT " + ",".join(RECORD_FIELDS) + " FROM monitoring_records WHERE version_id=? ORDER BY source_sheet,source_row",
            (snapshot.version["version_id"],),
        ).fetchall()
        expected_rows = sorted(prepared.records, key=lambda row: (row["source_sheet"], row["source_row"]))
        require([dict(row) for row in stored] == expected_rows, "Not all raw-normalized rows round-tripped")
    return result


def compare_mart(database, publication_id):
    from pipeline.data_access import DataAccess, DataContext
    from pipeline.database.import_monitoring import AREA_COLUMNS
    import pandas as pd
    mart = DataAccess(DataContext(root=ROOT, use_marts=True, require_marts=True)).load_monitoring_2_0()
    comparisons = {}
    for registry in ("rv", "oks"):
        frame = mart[registry]
        query = database.query(publication_id=publication_id, filters={"registry": registry})
        require(query["summary"]["record_count"] == len(frame), "Mart row count differs")
        differences = {}
        for field, column in AREA_COLUMNS.items():
            mart_sum = Decimal(str(pd.to_numeric(frame[column], errors="coerce").fillna(0).sum()))
            difference = Decimal(query["summary"][field]) - mart_sum
            require(abs(difference) < Decimal("0.000001"), "Mart area sum differs beyond float tolerance")
            differences[field] = str(difference)
        comparisons[registry] = {"rows": len(frame), "db_minus_mart_m2": differences,
                                 "tolerance_m2": "0.000001", "reason": "mart uses pandas binary float summation"}
    return comparisons


def read_load(database, publication_id):
    measurements = []
    developers = [r["developer"] for r in database.query(publication_id=publication_id)["rows"] if r["developer"]]
    filters = [{}, {"registry": "rv"}, {"registry": "oks"}, {"year_from": 2022, "year_to": 2026}]
    if developers:
        filters.append({"developer": developers[0]})
    for readers in (1, 5, 20):
        per_reader = max(5, math.ceil(20 / readers))

        def worker(index):
            durations = []
            for i in range(per_reader):
                start = time.perf_counter()
                result = database.query(publication_id=publication_id, filters=filters[(index + i) % len(filters)], limit=50)
                require(result["publication_id"] == publication_id, "Load query publication changed")
                durations.append(time.perf_counter() - start)
            return durations

        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=readers) as executor:
            durations = [duration for batch in executor.map(worker, range(readers)) for duration in batch]
        measurements.append({"readers": readers, "queries": len(durations), "errors": 0,
                             "median_sec": statistics.median(durations),
                             "p95_sec": sorted(durations)[math.ceil(.95 * len(durations)) - 1],
                             "max_sec": max(durations), "wall_sec": time.perf_counter() - started,
                             "cache": "mixed local OS/SQLite; includes exact decimal aggregation and row serialization"})
    return measurements


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--database", type=Path, default=ROOT / "outputs/audit/monitoring.sqlite")
    parser.add_argument("--output", type=Path, default=ROOT / "outputs/audit/monitoring_database_checks.json")
    parser.add_argument("--skip-load", action="store_true", help="Defer read load until an uncontended measurement window")
    parser.add_argument("--load-only", action="store_true", help="Measure published database without importing again")
    parser.add_argument("--verify-existing", action="store_true", help="Compare existing publication to mart and verify real backup/restore")
    args = parser.parse_args()
    report = {"fixture_checks": fixture_checks(), "sqlite_version": sqlite3.sqlite_version}
    if args.source:
        from pipeline.database.import_monitoring import prepare_monitoring
        database = MonitoringDatabase(args.database)
        database.initialize()
        expected = database.active_state()
        started = time.perf_counter()
        prepared = prepare_monitoring(args.source, root=ROOT)
        report["prepare_sec"] = time.perf_counter() - started
        started = time.perf_counter()
        imported = database.stage_version(**asdict(prepared))
        report["stage_sec"] = time.perf_counter() - started
        publication = database.publish(imported["version_id"], expected=expected)
        report["import"] = imported
        report["publication"] = asdict(publication)
        report["comparison"] = compare_real(database, prepared, publication.publication_id)
        report["repeat_import"] = database.stage_version(**asdict(prepared))
        require(report["repeat_import"]["idempotent"], "Real repeated import is not idempotent")
        if not args.skip_load:
            report["read_load"] = read_load(database, publication.publication_id)
        report["database_size_bytes"] = args.database.stat().st_size
    if args.load_only:
        database = MonitoringDatabase(args.database)
        publication = database.active_state()
        require(publication.publication_id is not None, "No publication for load test")
        report["publication"] = asdict(publication)
        report["read_load"] = read_load(database, publication.publication_id)
    if args.verify_existing:
        database = MonitoringDatabase(args.database)
        database.initialize()
        publication = database.active_state()
        require(publication.publication_id is not None, "No publication to verify")
        report["publication"] = asdict(publication)
        report["mart_comparison"] = compare_mart(database, publication.publication_id)
        backup = args.database.with_name(f"monitoring.backup.{publication.publication_id[:12]}.sqlite")
        restored = args.database.with_name(f"monitoring.restored.{publication.publication_id[:12]}.sqlite")
        if not backup.exists():
            database.backup(backup)
        if not restored.exists():
            MonitoringDatabase(backup).backup(restored)
        for candidate in (backup, restored):
            check = MonitoringDatabase(candidate)
            require(check.query(publication_id=publication.publication_id) == database.query(publication_id=publication.publication_id),
                    "Real backup/restore query differs")
            with closing(check.connect(readonly=True)) as connection:
                require(connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "Real backup integrity failed")
                require(connection.execute("PRAGMA foreign_key_check").fetchall() == [], "Real backup FK check failed")
        report["real_backup_restore"] = {"backup": str(backup), "restored": str(restored), "status": "passed"}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
