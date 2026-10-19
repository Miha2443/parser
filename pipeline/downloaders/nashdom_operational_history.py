"""Resumable monthly operational history. Run with ``python -m ...``.

The registered history bundle is deliberately NOT construction_operational_*:
legacy newest-mtime consumers must continue to see only full daily snapshots.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from pipeline.download_provenance import record_download
from pipeline.downloaders.nashdom_operational_contract import (
    BASE, OPTIONAL_SALES, SALES_FIELDS, AccessVerificationRequired, EmptyReport, ReportError, TransportError, normalize,
    numeric, period, request_url, utc_now,
)
from pipeline.paths import DATA_RAW

BUNDLE_NAME = "construction_history.json"
SCHEMA_VERSION = 1


def collected_instant(value):
    """Compare actual instants without changing the original timestamp text."""
    try:
        stamp = datetime.fromisoformat(value)
        if stamp.tzinfo is None:
            raise ValueError("timezone is required")
        return stamp.astimezone(timezone.utc)
    except (TypeError, ValueError) as exc:
        raise ReportError(f"invalid timezone-aware collection time: {value!r}") from exc


def months(start, end):
    start, end = period(start), period(end)
    year, month = map(int, start.split("-"))
    result = []
    while f"{year:04d}-{month:02d}" <= end:
        result.append(f"{year:04d}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return result


def select_months(start, end, existing, *, recent=3):
    """All gaps remain candidates, including an old month published late."""
    candidates = months(start, end)
    if not candidates:
        return []
    latest = set(candidates[-max(1, recent):])
    # Publish current data early, then backfill chronologically.
    return sorted(latest, reverse=True) + [m for m in candidates if m not in existing and m not in latest]


def _digest(payload):
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                     allow_nan=False).encode("utf-8")).hexdigest()


def atomic_json(path, payload):
    """Unique temporary name, fsync, validate, then atomic replace."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if json.loads(temporary.read_text(encoding="utf-8")) != payload:
            raise ReportError("JSON write verification failed")
        os.replace(temporary, path)
        if os.name != "nt":
            fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def history_lock(raw_dir):
    """OS-owned lock is released even after a killed bootstrap process."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    with (raw_dir / ".construction_history.lock").open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError("another operational history download is active") from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def validate_artifact(artifact):
    if not isinstance(artifact, dict) or artifact.get("schemaVersion") != SCHEMA_VERSION:
        raise ReportError("invalid operational artifact schema")
    block, month = artifact["block"], period(artifact["period"])
    if block not in {"construction", "sales"}:
        raise ReportError("unknown operational artifact block")
    stamp = artifact["collected_at"]
    collected_instant(stamp)
    rows = artifact["rows"]
    report_dates = {row.get("report_date", "") for row in rows}
    if len(report_dates) != 1:
        raise ReportError("artifact has inconsistent block report dates")
    if artifact.get("evidence_kind") == "browser_page":
        from pipeline.downloaders.nashdom_operational_browser import normalize_page
        if "responses" in artifact:
            raise ReportError("mixed API/browser evidence")
        expected = normalize_page(block, month, artifact["observations"], collected_at=stamp)
    elif artifact.get("evidence_kind") in (None, "api_response"):
        if "observations" in artifact:
            raise ReportError("mixed API/browser evidence")
        expected = normalize(block, month, artifact["responses"], collected_at=stamp,
                             report_date=next(iter(report_dates)))
    else:
        raise ReportError("unknown operational evidence kind")
    if rows != expected:
        raise ReportError("stored rows do not match the literal source responses")
    return block, month


def bundle_entries(payload, block):
    """A portable valid bundle remains sufficient when private checkpoints move."""
    grouped = {}
    for row in payload[block]:
        month = period(row["report_period"])
        region = row["region_key"]
        year, num = map(int, month.split("-"))
        stamp = row["collected_at"]
        collected_instant(stamp)
        if row.get("evidence_kind") == "browser_page":
            if row.get("period_verified") is not True or not isinstance(row.get("page_evidence"), dict):
                raise ReportError("bundle has unverified page observation")
            grouped.setdefault(month, []).append(copy.deepcopy(row))
            continue
        if row.get("evidence_kind") not in (None, "api_response") or "page_evidence" in row:
            raise ReportError("bundle has unknown or mixed source evidence")
        if (row.get("period_verified") is not True
                or row.get("source_url") != BASE + request_url(block, month, region)
                or row.get("period_evidence") != {"repYear": year, "repMonth": num}):
            raise ReportError("bundle contains an unverified observation")
        if block == "construction":
            if numeric(row.get("area_thousand_m2")) <= 0:
                raise ReportError("bundle contains an empty construction area")
        else:
            metrics = row["metrics"]
            if set(metrics) != set(SALES_FIELDS):
                raise ReportError("bundle contains incomplete sales metrics")
            for name, value in metrics.items():
                if value is not None or name not in OPTIONAL_SALES:
                    numeric(value)
        grouped.setdefault(month, []).append(copy.deepcopy(row))
    result = {}
    for month, rows in grouped.items():
        keys = [(row["region_key"], row["area_kind"]) for row in rows] if block == "construction" else [row["region_key"] for row in rows]
        expected = {(r, k) for r in ("rf", "msk") for k in ("living", "total")} if block == "construction" else {"rf", "msk"}
        stamps = {collected_instant(row["collected_at"]) for row in rows}
        if len(keys) != len(expected) or set(keys) != expected or len(stamps) != 1:
            raise ReportError("bundle month is partial or duplicate")
        kinds = {row.get("evidence_kind") for row in rows}
        if "browser_page" in kinds:
            if kinds != {"browser_page"}:
                raise ReportError("mixed evidence within block-month")
            from pipeline.downloaders.nashdom_operational_browser import normalize_page
            normalized = normalize_page(block, month, [row["page_evidence"] for row in rows], collected_at=rows[0]["collected_at"])
            if normalized != rows:
                raise ReportError("browser bundle values differ from captured page cells")
        result[month] = {"block": block, "period": month, "rows": rows,
                         "collected_at": rows[0]["collected_at"], "origin": "verified bundle"}
    return result


def validate_bundle_payload(payload):
    """Validate portable observations and truthful coverage, not a target volume."""
    if not isinstance(payload, dict) or payload.get("schemaVersion") != SCHEMA_VERSION:
        raise ReportError("unsupported history bundle schema")
    for block in ("construction", "sales"):
        if not isinstance(payload.get(block), list):
            raise ReportError("invalid history bundle block")
    if payload.get("content_sha256") != _digest({b: payload[b] for b in ("construction", "sales")}):
        raise ReportError("history bundle hash mismatch")
    entries = {b: bundle_entries(payload, b) for b in ("construction", "sales")}
    coverage = payload.get("coverage")
    if not isinstance(coverage, dict) or set(coverage) != set(entries):
        raise ReportError("history bundle has no independent block coverage")
    complete = True
    for block, records in entries.items():
        row = coverage[block]
        if not isinstance(row, dict):
            raise ReportError("invalid history coverage")
        start, end, latest = (period(row.get(key)) for key in ("start", "end", "latest_available"))
        if start < "2020-01" or end > latest:
            raise ReportError("invalid history coverage bounds")
        expected = months(start, end)
        present = set(expected) & records.keys()
        missing = [m for m in expected if m not in present]
        if (type(row.get("expected_months")) is not int or row["expected_months"] != len(expected)
                or type(row.get("collected_months")) is not int or row["collected_months"] != len(present)
                or row.get("missing_months") != missing):
            raise ReportError("history coverage differs from verified observations: " + block)
        if any(month > latest for month in records):
            raise ReportError("history observation exceeds observed source bound")
        complete = complete and bool(expected) and not missing
    if payload.get("history_complete", complete) is not complete:
        raise ReportError("history completeness claim differs from verified coverage")
    if payload.get("complete") is True and not complete:
        raise ReportError("partial history falsely marked complete")
    return entries


class HistoryStore:
    def __init__(self, raw_dir):
        self.raw_dir = Path(raw_dir)
        self.bundle = self.raw_dir / BUNDLE_NAME
        self.private = self.raw_dir / ".construction_history"
        self.entries = {"construction": {}, "sales": {}}
        self.changed = False
        self.recovery_issues = []
        if self.bundle.exists():
            try:
                payload = json.loads(self.bundle.read_text(encoding="utf-8"))
                self.entries = validate_bundle_payload(payload)
            except (ValueError, OSError, AttributeError, KeyError, TypeError) as exc:
                quarantine = self.private / "quarantine"
                quarantine.mkdir(parents=True, exist_ok=True)
                bad = quarantine / (self.bundle.name + "." + uuid.uuid4().hex + ".bad")
                self.bundle.replace(bad)
                self.recovery_issues.append(f"quarantined {bad.name}: {exc}")
                self.changed = True
        # Checkpoint claims are never coverage; use verified rows/source artifacts.
        for path in sorted((self.private / "observations").glob("*.json")):
            try:
                item = json.loads(path.read_text(encoding="utf-8"))
                block, month = validate_artifact(item)
                previous = self.entries[block].get(month)
                if previous is None or collected_instant(item["collected_at"]) > collected_instant(previous["collected_at"]):
                    self.entries[block][month] = item
            except (ValueError, OSError, KeyError, TypeError) as exc:
                self.recovery_issues.append(f"ignored corrupt observation {path.name}: {exc}")
        if self.bundle.exists():
            actual = {b: self.rows(b) for b in self.entries}
            if any(actual[b] != payload[b] for b in self.entries):
                self.changed = True
        elif any(self.entries[b] for b in self.entries):
            self.changed = True

    def rows(self, block):
        return [copy.deepcopy(row) for month in sorted(self.entries[block])
                for row in self.entries[block][month]["rows"]]

    def accept(self, artifact):
        block, month = validate_artifact(artifact)
        name = f"{block}_{month}_{artifact['collected_at'].replace(':', '').replace('+', '_')}_{uuid.uuid4().hex}.json"
        path = self.private / "observations" / name
        atomic_json(path, artifact)
        record_download(path, source_id="construction_operational_history", remote_date=month)
        previous = self.entries[block].get(month)
        if previous is None or collected_instant(artifact["collected_at"]) >= collected_instant(previous["collected_at"]):
            self.entries[block][month] = copy.deepcopy(artifact)
            self.changed = True
        self.prune(self.private / "observations", f"{block}_{month}_*.json", keep=2, receipts=True)
        return path

    def prune(self, directory, pattern, *, keep, receipts=False):
        def retention_key(path):
            if receipts:
                try:
                    item = json.loads(path.read_text(encoding="utf-8"))
                    return collected_instant(item["collected_at"])
                except (ValueError, OSError, KeyError, TypeError):
                    return datetime.min.replace(tzinfo=timezone.utc)
            return path.stat().st_mtime_ns

        candidates = sorted(directory.glob(pattern), key=retention_key, reverse=True)
        for path in candidates[keep:]:
            try:
                path.unlink()
                if receipts:
                    from pipeline.download_provenance import receipt_path
                    receipt_path(path).unlink(missing_ok=True)
            except OSError as exc:
                self.recovery_issues.append(f"private retention cleanup {path.name}: {exc}")

    def coverage(self, bounds, *, start="2020-01", end=None):
        result = {}
        for block in self.entries:
            upper = min(bounds[block], end) if end else bounds[block]
            expected = months(start, upper)
            present = sorted(set(expected) & self.entries[block].keys())
            result[block] = {"start": start, "latest_available": bounds[block], "end": upper,
                             "expected_months": len(expected), "collected_months": len(present),
                             "missing_months": [m for m in expected if m not in self.entries[block]]}
        return result

    def publish(self, coverage, *, complete=False, errors=(), network_collected=False,
                current_publication_error=None):
        rows = {b: self.rows(b) for b in self.entries}
        payload = {"source": "construction_operational_history", "schemaVersion": SCHEMA_VERSION,
                   "scraped_at": max((a["collected_at"] for b in self.entries.values() for a in b.values()),
                                     key=collected_instant, default=""),
                   **rows, "coverage": coverage, "complete": complete, "errors": list(errors),
                   "history_complete": bool(coverage) and all(c["expected_months"] > 0 and not c["missing_months"] for c in coverage.values()),
                   "current_publication_error": current_publication_error,
                   "run_status": "complete" if complete else "partial",
                   "publishable": True,
                   "checked_at": utc_now(), "content_sha256": _digest(rows)}
        if not any(rows.values()):
            return None
        validate_bundle_payload(payload)
        version = self.private / "bundles" / f"history_{uuid.uuid4().hex}.json"
        atomic_json(version, payload)
        atomic_json(self.bundle, payload)
        if network_collected:
            record_download(self.bundle, source_id="construction_operational_history")
        self.prune(self.private / "bundles", "history_*.json", keep=5)
        self.changed = False
        return self.bundle


def publish_current(store, bounds, run_started):
    entries = [store.entries[b].get(bounds[b]) for b in ("construction", "sales")]
    if any(a is None or collected_instant(a["collected_at"]) < collected_instant(run_started) for a in entries):
        return None
    payload = {"source": "construction_operational", "schemaVersion": SCHEMA_VERSION,
               "scraped_at": max((a["collected_at"] for a in entries), key=collected_instant),
               **{b: copy.deepcopy(a["rows"]) for b, a in zip(("construction", "sales"), entries)}}
    from nashdom_checker import _validate_construction_operational_result
    _validate_construction_operational_result(payload)
    # Do not downgrade either block when a source temporarily rolls back.
    current_files = [p for p in store.raw_dir.glob("construction_operational_*.json")
                     if re.fullmatch(r"construction_operational_\d{8}(?:_.*)?\.json", p.name)]
    if current_files:
        latest = max(current_files, key=lambda p: p.stat().st_mtime_ns)
        old = json.loads(latest.read_text(encoding="utf-8"))
        for block in ("construction", "sales"):
            if any(period(row.get("report_period", "")) > bounds[block] for row in old.get(block, [])):
                raise ReportError("refusing to downgrade current " + block)
        atomic_json(store.private / "daily" / (latest.stem + "_" + uuid.uuid4().hex + ".json"), old)
        store.prune(store.private / "daily", "*.json", keep=5)
    target = store.raw_dir / ("construction_operational_" + datetime.now().strftime("%Y%m%d") + ".json")
    atomic_json(target, payload)
    record_download(target, source_id="construction_operational")
    return target


def emit(event):
    print(json.dumps(event, ensure_ascii=True), flush=True)


def _download_history(raw_dir=None, *, start="2020-01", end=None, recent=3,
                     max_periods=None, delay=0.5, client=None, progress=emit):
    """Return only real downloaded paths; partial coverage is never success."""
    raw_dir = Path(raw_dir or DATA_RAW / "realty" / "nashdom")
    start, end = max("2020-01", period(start)), period(end) if end else None
    with history_lock(raw_dir):
        store = HistoryStore(raw_dir)
        from pipeline.downloaders.nashdom_operational_browser import BrowserClient
        with client if client is not None else BrowserClient(raw_dir, delay=delay) as source:
            bounds = source.bounds
            coverage = store.coverage(bounds, start=start, end=end)
            plans = {b: select_months(start, c["end"], store.entries[b], recent=recent)
                     for b, c in coverage.items()}
            # First the independent newest blocks, then the rest of each history.
            jobs = [(b, plans[b].pop(0)) for b in plans if plans[b]]
            jobs += [(b, m) for b in plans for m in plans[b]]
            progress({"event": "plan", "bounds": bounds, "coverage": coverage,
                      "planned_blocks": len(jobs), "recovery_issues": store.recovery_issues})
            files, errors, count, accepted, consecutive_transport = [], [], 0, 0, 0
            current_publication_error = None
            for block, month in jobs:
                if max_periods is not None and count >= max_periods:
                    break
                count += 1
                progress({"event": "fetch", "block": block, "period": month, "attempted": count, "planned": len(jobs)})
                try:
                    artifact = source.collect(block, month)
                    if validate_artifact(artifact) != (block, month):
                        raise ReportError("collected artifact differs from scheduled block/month")
                    store.accept(artifact)
                    accepted += 1
                    consecutive_transport = 0
                    coverage = store.coverage(bounds, start=start, end=end)
                    bundle = store.publish(coverage, errors=errors, network_collected=True,
                                           current_publication_error=current_publication_error)
                    if bundle and bundle not in files:
                        files.append(bundle)
                except (ReportError, OSError, KeyError, TypeError) as exc:
                    errors.append({"block": block, "period": month, "error": str(exc),
                                   "kind": "access_verification" if isinstance(exc, AccessVerificationRequired) else
                                           "source_empty" if isinstance(exc, EmptyReport) else
                                           "transport" if isinstance(exc, TransportError) else "validation_or_io"})
                    progress({"event": "error", **errors[-1]})
                    consecutive_transport = consecutive_transport + 1 if isinstance(exc, TransportError) else 0
                    if isinstance(exc, AccessVerificationRequired) or consecutive_transport >= 3:
                        break
                    continue
                progress({"event": "saved", "block": block, "period": month,
                          "rows": len(artifact["rows"]), "coverage": {
                              b: {"collected_months": c["collected_months"],
                                  "expected_months": c["expected_months"],
                                  "missing_count": len(c["missing_months"])}
                              for b, c in coverage.items()}})
            coverage = store.coverage(bounds, start=start, end=end)
            complete = bool(coverage) and all(c["expected_months"] > 0 and not c["missing_months"] for c in coverage.values())
            history_complete = complete
            complete = complete and not errors and not current_publication_error and count == len(jobs)
            bundle = (store.publish(coverage, complete=complete, errors=errors, network_collected=bool(files),
                                    current_publication_error=current_publication_error)
                      if files or store.changed else store.bundle if store.bundle.exists() else None)
            result = {"new_files": files, "history_file": str(bundle) if bundle else "",
                      "complete": complete, "collected": accepted, "attempted": count,
                      "coverage": coverage, "errors": errors,
                      "history_complete": history_complete,
                      "current_publication_error": current_publication_error,
                      "current_file": "", "planned": len(jobs),
                      "run_status": "complete" if complete else "partial" if accepted else "failed",
                      "stop_reason": "access" if any(e["kind"] == "access_verification" for e in errors) else
                                     "error" if errors else "limit" if count < len(jobs) else "exhausted",
                      "published": bool(files and bundle)}
            progress({"event": "finished", **{k: v for k, v in result.items() if k != "new_files"}})
            return result


def download_history(raw_dir=None, **kwargs):
    """History is independent of the daily snapshot, including on failure."""
    raw_dir = Path(raw_dir or DATA_RAW / "realty" / "nashdom")
    started = utc_now()
    try:
        result = _download_history(raw_dir, **kwargs)
    except Exception as exc:
        atomic_json(raw_dir / ".construction_history" / "last_attempt.json",
                    {"started_at": started, "finished_at": utc_now(), "run_status": "failed",
                     "error": str(exc), "current_snapshot": "unchanged"})
        raise
    atomic_json(raw_dir / ".construction_history" / "last_attempt.json",
                {"started_at": started, "finished_at": utc_now(), "current_snapshot": "unchanged",
                 "bundle_sha256": _digest(json.loads(Path(result["history_file"]).read_text(encoding="utf-8"))) if result["history_file"] else "",
                 **{key: value for key, value in result.items() if key != "new_files"}})
    return result


def verify_published_attempt(raw_dir):
    raw_dir = Path(raw_dir)
    attempt = json.loads((raw_dir / ".construction_history" / "last_attempt.json").read_text(encoding="utf-8"))
    if attempt.get("published") is not True or not attempt.get("collected"):
        raise ReportError("no new verified history bundle was materialized in this attempt")
    payload = json.loads((raw_dir / BUNDLE_NAME).read_text(encoding="utf-8"))
    validate_bundle_payload(payload)
    if attempt.get("bundle_sha256") != _digest(payload):
        raise ReportError("history bundle differs from the last collection attempt")
    return attempt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2020-01")
    parser.add_argument("--end")
    parser.add_argument("--recent-months", type=int, default=3)
    parser.add_argument("--max-periods", type=int, help="Bound a probe by block-months; default is unlimited")
    parser.add_argument("--delay", type=float, default=0.5)
    parser.add_argument("--raw-dir", type=Path, default=DATA_RAW / "realty" / "nashdom")
    parser.add_argument("--verify-published-attempt", action="store_true", help="Verify the last newly collected portable bundle without network access")
    args = parser.parse_args(argv)
    if args.recent_months < 0 or args.delay < 0 or (args.max_periods is not None and args.max_periods < 1):
        parser.error("invalid negative/empty download limit")
    try:
        if args.verify_published_attempt:
            emit({"event": "publishable_attempt_verified", **verify_published_attempt(args.raw_dir)})
            return 0
        result = download_history(args.raw_dir, start=args.start, end=args.end,
                                  recent=args.recent_months, max_periods=args.max_periods, delay=args.delay)
        return 0 if result["complete"] else 2
    except Exception as exc:
        emit({"event": "failed", "complete": False, "error": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
