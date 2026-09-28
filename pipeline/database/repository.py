"""SQLite shadow pilot with immutable versions and compare-and-swap publication.

One writer / local filesystem only. Decimal columns intentionally use TEXT;
query APIs return decimal strings with explicit units and row-level semantics.
"""
from __future__ import annotations

from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Iterable


AREA_FIELDS = (
    "total_area_m2", "residential_area_m2", "category_residential_m2",
    "category_common_m2", "category_nonres_in_res_m2", "category_nonres_other_m2",
)
RECORD_FIELDS = (
    "source_sheet", "source_row", "source_record_id", "registry", "uin", "developer",
    "object_name", "address", "okrug", "district", "permit_number", "grouping", "purpose",
    "funding_source", "year", *AREA_FIELDS, "raw_total_area_missing",
    "raw_residential_area_missing", "raw_total_area_invalid", "raw_residential_area_invalid",
    "normalized_payload_json", "raw_payload_json",
)
ALLOWED_FILTERS = {"registry", "developer", "uin", "okrug", "year_from", "year_to"}


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def decimal_text(value) -> str:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Not a decimal: {value!r}") from exc
    if not result.is_finite():
        raise ValueError(f"Non-finite decimal: {value!r}")
    if len(result.as_tuple().digits) > 30 or result.as_tuple().exponent < -18 or result.adjusted() > 30:
        raise ValueError("Decimal exceeds the pilot's 30 digit / 18 fractional digit contract")
    return format(result, "f")


class _DecimalSum:
    def __init__(self):
        self.value = Decimal(0)

    def step(self, value):
        if value is not None:
            with localcontext() as context:
                context.prec = 60
                self.value += Decimal(value)

    def finalize(self):
        return format(self.value, "f")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class PublicationState:
    publication_id: str | None
    generation: int


class PublicationConflict(RuntimeError):
    pass


class MonitoringDatabase:
    def __init__(self, path: Path | str):
        self.path = Path(path).resolve()

    def connect(self, *, readonly: bool = False) -> sqlite3.Connection:
        if readonly:
            uri = self.path.as_uri() + "?mode=ro"
            connection = sqlite3.connect(uri, uri=True, timeout=10, isolation_level=None)
        else:
            connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")
        if not readonly:
            connection.execute("PRAGMA synchronous=FULL")
        connection.create_aggregate("decimal_sum", 1, _DecimalSum)
        return connection

    def initialize(self) -> None:
        if sqlite3.sqlite_version_info < (3, 37, 0):
            raise RuntimeError("The pilot requires SQLite 3.37 or newer for STRICT tables")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as connection:
            tables = connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            if tables:
                names = {r[0] for r in tables}
                if "schema_metadata" not in names:
                    raise ValueError("Refusing to initialize an unrelated existing database")
                versions = connection.execute("SELECT version FROM schema_metadata").fetchall()
                if [r[0] for r in versions] != [1]:
                    raise ValueError("Unsupported database schema")
                raw_columns = {row[1] for row in connection.execute("PRAGMA table_info(raw_files)")}
                record_columns = {row[1] for row in connection.execute("PRAGMA table_info(monitoring_records)")}
                if "workbook_metadata_json" not in raw_columns or not set(RECORD_FIELDS) <= record_columns:
                    raise ValueError("Existing draft schema is incompatible; use an explicit migration or new database")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.executescript(Path(__file__).with_name("schema.sql").read_text(encoding="utf-8"))

    def active_state(self) -> PublicationState:
        with closing(self.connect(readonly=True)) as connection:
            row = connection.execute("SELECT publication_id, generation FROM active_publication WHERE slot='monitoring'").fetchone()
            return PublicationState(*row)

    def stage_version(
        self, *, raw_file: dict, normalizer_version: str, records: Iterable[dict],
        validation: dict | None = None,
    ) -> dict:
        records = [dict(record) for record in records]
        if not records:
            raise ValueError("Empty versions cannot replace a valid publication")
        raw_id = raw_file["sha256"]
        if len(raw_id) != 64 or any(c not in "0123456789abcdef" for c in raw_id):
            raise ValueError("Invalid raw SHA-256")
        identities = set()
        for record in records:
            if set(record) != set(RECORD_FIELDS):
                raise ValueError(f"Invalid record fields: {set(record) ^ set(RECORD_FIELDS)}")
            identity = (record["source_sheet"], record["source_row"])
            if identity in identities:
                raise ValueError(f"Duplicate source row: {identity}")
            identities.add(identity)
            expected_id = hashlib.sha256(json.dumps(
                [raw_id, *identity], ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")).hexdigest()
            if record["source_record_id"] != expected_id:
                raise ValueError(f"Source identity does not match file/sheet/row: {identity}")
            for field in AREA_FIELDS:
                if record[field] is None and field in {"total_area_m2", "residential_area_m2"}:
                    continue
                record[field] = decimal_text(record[field])
                if Decimal(record[field]) < 0:
                    raise ValueError(f"Negative area in {identity}: {field}")
            for field, prefix in (("total_area_m2", "raw_total_area"), ("residential_area_m2", "raw_residential_area")):
                is_unknown = bool(record[prefix + "_missing"] or record[prefix + "_invalid"])
                if is_unknown != (record[field] is None):
                    raise ValueError(f"Missing/invalid source area must remain NULL: {identity}, {field}")
            for field in ("normalized_payload_json", "raw_payload_json"):
                json.loads(record[field])
        records.sort(key=lambda r: (r["source_sheet"], r["source_row"]))
        records_hash = digest(records)
        version_id = digest(["monitoring", raw_id, normalizer_version])
        validation = {**(validation or {}), "record_count": len(records), "records_sha256": records_hash}
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute("SELECT * FROM dataset_versions WHERE version_id=?", (version_id,)).fetchone()
            if existing:
                if existing["records_sha256"] != records_hash or existing["record_count"] != len(records):
                    raise ValueError("Same raw/normalizer identity produced different rows")
                connection.rollback()
                return {"version_id": version_id, "record_count": len(records), "idempotent": True}
            if not connection.execute("SELECT 1 FROM sources WHERE source_id='monitoring'").fetchone():
                connection.execute("INSERT INTO sources VALUES ('monitoring', 'Мониторинг 2.0', 'Москва')")
            if not connection.execute("SELECT 1 FROM raw_files WHERE raw_file_id=?", (raw_id,)).fetchone():
                connection.execute("INSERT INTO raw_files VALUES (?, 'monitoring', ?, ?, ?, ?)",
                                   (raw_id, str(raw_file["path"]), raw_file["size_bytes"],
                                    canonical_json(raw_file.get("workbook_metadata", {})), utc_now()))
            fields = ("version_id", "raw_file_id", *RECORD_FIELDS)
            connection.executemany(
                f"INSERT INTO monitoring_records ({','.join(fields)}) VALUES ({','.join('?' for _ in fields)})",
                [(version_id, raw_id, *(record[k] for k in RECORD_FIELDS)) for record in records],
            )
            count = connection.execute("SELECT COUNT(*) FROM monitoring_records WHERE version_id=?", (version_id,)).fetchone()[0]
            if count != len(records):
                raise ValueError("Stored row count mismatch")
            connection.execute("INSERT INTO dataset_versions VALUES (?, 'monitoring', ?, ?, ?, ?, ?, ?)",
                               (version_id, raw_id, normalizer_version, len(records), records_hash,
                                canonical_json(validation), utc_now()))
            connection.commit()
            return {"version_id": version_id, "record_count": count, "idempotent": False}
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def publish(self, version_id: str, *, expected: PublicationState, before_switch=None) -> PublicationState:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            current = PublicationState(*connection.execute(
                "SELECT publication_id, generation FROM active_publication WHERE slot='monitoring'"
            ).fetchone())
            if current != expected:
                raise PublicationConflict(f"Publication changed: expected {expected}, found {current}")
            version = connection.execute("SELECT * FROM dataset_versions WHERE version_id=?", (version_id,)).fetchone()
            if not version:
                raise ValueError("Unknown version")
            count = connection.execute("SELECT COUNT(*) FROM monitoring_records WHERE version_id=?", (version_id,)).fetchone()[0]
            if not count or count != version["record_count"]:
                raise ValueError("Version failed validation before publication")
            publication_id = digest(["publication", version_id])
            exists = connection.execute("SELECT 1 FROM publications WHERE publication_id=?", (publication_id,)).fetchone()
            if not exists:
                connection.execute("INSERT INTO publication_members VALUES (?, 'monitoring', ?)", (publication_id, version_id))
                connection.execute("INSERT INTO publications VALUES (?, ?)", (publication_id, utc_now()))
            if before_switch is not None:
                before_switch()
            if current.publication_id == publication_id:
                connection.commit()
                return current
            connection.execute("UPDATE active_publication SET publication_id=?, generation=generation+1 WHERE slot='monitoring'", (publication_id,))
            connection.commit()
            return PublicationState(publication_id, current.generation + 1)
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def rollback(self, publication_id: str, *, expected: PublicationState) -> PublicationState:
        with closing(self.connect(readonly=True)) as connection:
            row = connection.execute("SELECT version_id FROM publication_members WHERE publication_id=?", (publication_id,)).fetchone()
        if row is None:
            raise ValueError("Unknown rollback publication")
        return self.publish(row[0], expected=expected)

    @contextmanager
    def snapshot(self, publication_id: str | None = None):
        connection = self.connect(readonly=True)
        try:
            connection.execute("BEGIN")
            if publication_id is None:
                publication_id = connection.execute("SELECT publication_id FROM active_publication WHERE slot='monitoring'").fetchone()[0]
            row = connection.execute(
                "SELECT v.*, p.created_at AS published_at FROM publication_members m "
                "JOIN dataset_versions v USING(version_id) JOIN publications p USING(publication_id) "
                "WHERE m.publication_id=? AND m.dataset='monitoring'", (publication_id,),
            ).fetchone()
            if row is None:
                raise ValueError("No published monitoring dataset")
            yield MonitoringSnapshot(connection, publication_id, dict(row))
        finally:
            connection.rollback()
            connection.close()

    def query(self, **kwargs) -> dict:
        publication_id = kwargs.pop("publication_id", None)
        with self.snapshot(publication_id) as snapshot:
            return snapshot.query(**kwargs)

    def backup(self, destination: Path | str) -> None:
        destination = Path(destination)
        if destination.exists():
            raise FileExistsError("Backup destination must be new")
        destination.parent.mkdir(parents=True, exist_ok=True)
        source = self.connect(readonly=True)
        target = sqlite3.connect(destination)
        try:
            source.backup(target)
            if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Backup integrity check failed")
        finally:
            source.close()
            target.close()


class MonitoringSnapshot:
    def __init__(self, connection, publication_id: str, version: dict):
        self.connection = connection
        self.publication_id = publication_id
        self.version = version

    def query(self, *, filters: dict | None = None, limit: int = 100, offset: int = 0) -> dict:
        filters = dict(filters or {})
        if set(filters) - ALLOWED_FILTERS:
            raise ValueError(f"Unsupported filters: {set(filters) - ALLOWED_FILTERS}")
        if not isinstance(limit, int) or not 1 <= limit <= 1000 or not isinstance(offset, int) or offset < 0:
            raise ValueError("limit must be 1..1000 and offset >= 0")
        where = ["version_id=?"]
        params = [self.version["version_id"]]
        for key, value in filters.items():
            if key in {"year_from", "year_to"}:
                if not isinstance(value, int):
                    raise ValueError("Year bounds must be integers")
                where.append("year " + (">=" if key == "year_from" else "<=") + " ?")
            else:
                if not isinstance(value, str):
                    raise ValueError("Text filters must be strings")
                if key == "registry" and value not in {"rv", "oks"}:
                    raise ValueError("registry must be rv or oks")
                where.append(key + " = ?")
            params.append(value)
        clause = " AND ".join(where)
        summary = dict(self.connection.execute(
            "SELECT COUNT(*) AS record_count, SUM(uin IS NULL) AS missing_uin, "
            "COALESCE(SUM(raw_total_area_missing),0) AS raw_total_area_missing, "
            "COALESCE(SUM(raw_residential_area_missing),0) AS raw_residential_area_missing, "
            "COALESCE(SUM(raw_total_area_invalid),0) AS raw_total_area_invalid, "
            "COALESCE(SUM(raw_residential_area_invalid),0) AS raw_residential_area_invalid, "
            + ", ".join(f"COALESCE(decimal_sum({field}),'0') AS {field}" for field in AREA_FIELDS)
            + " FROM monitoring_records WHERE " + clause, params,
        ).fetchone())
        summary["missing_uin"] = summary["missing_uin"] or 0
        rows = self.connection.execute(
            "SELECT " + ",".join(k for k in RECORD_FIELDS if not k.endswith("payload_json"))
            + " FROM monitoring_records WHERE " + clause + " ORDER BY source_sheet,source_row LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
        validation = json.loads(self.version["validation_json"])
        return {
            "publication_id": self.publication_id, "version_id": self.version["version_id"],
            "filters": filters, "limit": limit, "offset": offset,
            "units": {field: "m2 (decimal string)" for field in AREA_FIELDS},
            "grain": "source sheet row; UIN is not unique; area sums are sums of rows, not verified unique objects",
            "provenance": {"raw_sha256": self.version["raw_file_id"],
                           "normalizer_version": self.version["normalizer_version"],
                           "publication_created_at": self.version["published_at"],
                           "source_dates": validation.get("source_dates", {})},
            "quality_warnings": validation.get("quality_warnings", []),
            "summary": summary, "rows": [dict(row) for row in rows],
        }
