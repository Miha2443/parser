"""Publication-owned tree history. Caller holds the Docker mutation lock.

The single fsynced journal is the commit point for immutable weekly payloads.
Only acknowledgement of a ready release advances the publication baseline.
Tree acknowledgement stays provisional until its publication journal is cleared.
Late runs close just the latest Sunday, and only from a pre-boundary baseline;
missed weeks are never backfilled from newer data.
"""
from __future__ import annotations

import copy
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import uuid

DIRECTORY = Path("data/derived/construction_tree")
JOURNAL = "construction-tree-history.json"
MOSCOW = timezone(timedelta(hours=3))


def _now(now=None):
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        raise ValueError("History clock must include timezone")
    return value


def _read(path, default=None):
    if path.is_symlink():
        raise RuntimeError("Tree history cannot be a symlink")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return copy.deepcopy(default)


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as output:
            json.dump(value, output, ensure_ascii=True, allow_nan=False, sort_keys=True)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def digest(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, allow_nan=False).encode()).hexdigest()


def _validate_payload(payload):
    if (not isinstance(payload, dict) or payload.get("schemaVersion") != 1
            or payload.get("unit") != "million_m2" or not payload.get("version")
            or not isinstance(payload.get("source"), dict) or not isinstance(payload.get("metrics"), list)
            or len(payload["metrics"]) != 10 or not all(isinstance(m, dict) for m in payload["metrics"])):
        raise RuntimeError("Invalid published construction tree")
    from backend.construction_tree_service import METRICS
    metrics = payload["metrics"]
    if {m.get("id") for m in metrics} != {identifier for identifier, _ in METRICS}:
        raise RuntimeError("Invalid published construction tree metrics")
    for metric in metrics:
        value = metric.get("value")
        if value is not None and (isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value < 0):
            raise RuntimeError("Invalid published tree area")
    return payload


def read_current(root):
    directory = Path(root) / DIRECTORY
    path = directory / "current.json"
    if not path.exists() and not path.is_symlink():
        if (directory / "current-proof.json").exists() or (directory / "publication.json").exists():
            raise RuntimeError("Published construction tree payload is missing")
        return None
    payload = _read(path)
    _validate_payload(payload)
    proof = _read(directory / "current-proof.json")
    if not isinstance(proof, dict) or proof.get("sha256") != digest(payload):
        raise RuntimeError("Construction tree current checksum mismatch")
    return payload


def _validate(history):
    if not isinstance(history, dict) or history.get("schemaVersion") != 1 or not isinstance(history.get("snapshots"), dict):
        raise RuntimeError("Invalid construction tree history")
    for key, row in history["snapshots"].items():
        parsed = date.fromisoformat(key)
        if not isinstance(row, dict) or not isinstance(row.get("snapshot"), dict):
            raise RuntimeError("Invalid weekly tree payload")
        if parsed.weekday() != 6 or row["snapshot"].get("date") != key:
            raise RuntimeError("Invalid weekly tree date")
        payload = {k: v for k, v in row.items() if k != "snapshot"}
        _validate_payload(payload)
        if row["snapshot"].get("sha256") != digest(payload):
            raise RuntimeError("Construction tree snapshot checksum mismatch")
        snapshot = row["snapshot"]
        published = datetime.fromisoformat(snapshot["publishedAt"])
        captured = datetime.fromisoformat(snapshot["capturedAt"])
        boundary = datetime.combine(parsed + timedelta(days=1), time(), MOSCOW)
        if published.tzinfo is None or captured.tzinfo is None or published >= boundary or captured < boundary:
            raise RuntimeError("Snapshot is not a proven pre-Monday publication")
    baseline = history.get("baseline")
    if baseline is not None:
        if not isinstance(baseline, dict):
            raise RuntimeError("Invalid construction tree baseline")
        _validate_payload(baseline["payload"])
        if baseline.get("sha256") != digest(baseline["payload"]):
            raise RuntimeError("Construction tree baseline checksum mismatch")
        if datetime.fromisoformat(baseline["publishedAt"]).tzinfo is None:
            raise RuntimeError("Publication baseline lacks timezone")
    return history


def read_history(root):
    return _validate(_read(Path(root) / DIRECTORY / "history.json", {"schemaVersion": 1, "snapshots": {}}))


def _published_baseline(runtime, target):
    if not target:
        return None
    from docker.publication import require_target
    require_target(Path(runtime), target)
    release = Path(runtime) / target
    current = read_current(release)
    published = _read(release / DIRECTORY / "publication.json")
    if current is None or published is None:
        return None
    if not isinstance(published, dict) or published.get("sha256") != digest(current):
        raise RuntimeError("Recovered tree publication checksum mismatch")
    return {"release": target, "publishedAt": published["publishedAt"], "payload": current, "sha256": digest(current)}


def _journal(runtime):
    path = Path(runtime) / JOURNAL
    history = _read(path)
    if history is not None:
        _validate(history)
    elif path.exists():
        raise RuntimeError("Construction tree journal is null")
    # Removing pending.json is the publication commit point. An interrupted ack
    # may already have written both the new tree journal and previous-good.json;
    # neither can supersede the predecessor until that transaction commits.
    from docker.publication import recovery_info
    pending = recovery_info(Path(runtime))
    if history is not None and pending is None:
        return history
    if pending is not None:
        target = pending["previous"]
    else:
        proof = _read(Path(runtime) / "previous-good.json", {})
        if not isinstance(proof, dict):
            raise RuntimeError("Invalid acknowledged release record")
        target = proof.get("current")
    baseline = _published_baseline(runtime, target)
    if history is None:
        history = read_history(Path(runtime) / target) if target else {"schemaVersion": 1, "snapshots": {}}
    if baseline is not None:
        history["baseline"] = baseline
    else:
        history.pop("baseline", None)
    return _validate(history)


def prepare(runtime, now=None):
    """Call before any collector/offline candidate; retries keep identical bytes."""
    now = _now(now)
    history = _journal(runtime)
    local = now.astimezone(MOSCOW)
    monday = local.date() - timedelta(days=local.weekday())
    sunday = (monday - timedelta(days=1)).isoformat()
    boundary = datetime.combine(monday, time(), MOSCOW)
    baseline = history.get("baseline")
    if baseline and sunday not in history["snapshots"] and datetime.fromisoformat(baseline["publishedAt"]) < boundary:
        payload = copy.deepcopy(_validate_payload(baseline["payload"]))
        payload["snapshot"] = {"date": sunday, "capturedAt": now.isoformat(),
                               "publishedAt": baseline["publishedAt"], "release": baseline["release"],
                               "delayedCapture": local.date() != monday, "sha256": digest(payload)}
        history["snapshots"][sunday] = payload
    _write(Path(runtime) / JOURNAL, history)
    return history


def stage(candidate, runtime):
    """Ignore mutable import history and carry the durable publication archive."""
    history = _journal(runtime)
    destination = Path(candidate) / DIRECTORY
    (destination / "current.json").unlink(missing_ok=True)
    (destination / "current-proof.json").unlink(missing_ok=True)
    (destination / "publication.json").unlink(missing_ok=True)
    from backend.construction_tree_service import ConstructionTreeService
    from backend.profile_service import SourceUnavailable
    from pipeline.data_access import DataContext
    try:
        payload = ConstructionTreeService(DataContext(candidate, use_marts=False)).report()
    except SourceUnavailable:
        payload = None
    _write(destination / "history.json", {"schemaVersion": 1, "snapshots": history["snapshots"]})
    if payload is not None:
        _write(destination / "current.json", payload)
        _write(destination / "current-proof.json", {"sha256": digest(payload)})


def acknowledge(runtime, target, now=None):
    """Prepare a ready release's baseline; pending publication keeps it provisional."""
    now = _now(now)
    # A Sunday build may become ready after midnight Monday. Close its old
    # baseline before advancing, even though collection started on Sunday.
    history = prepare(runtime, now)
    payload = read_current(Path(runtime) / target)
    if payload is not None:
        previous = history.get("baseline", {})
        published_at = previous.get("publishedAt") if previous.get("release") == target else now.isoformat()
        history["baseline"] = {"release": target, "publishedAt": published_at, "payload": payload, "sha256": digest(payload)}
        _write(Path(runtime) / target / DIRECTORY / "publication.json", {"publishedAt": published_at, "sha256": digest(payload)})
    else:
        # Never close a later week using data that was not the current published tree.
        history.pop("baseline", None)
    _write(Path(runtime) / target / DIRECTORY / "history.json", {"schemaVersion": 1, "snapshots": history["snapshots"]})
    _write(Path(runtime) / JOURNAL, history)


def restore_baseline(runtime, target):
    """Rollback keeps the original successful publication time, not retry time."""
    history = _journal(runtime)
    baseline = _published_baseline(runtime, target)
    if baseline is not None:
        history["baseline"] = baseline
    else:
        history.pop("baseline", None)
    _write(Path(runtime) / target / DIRECTORY / "history.json", {"schemaVersion": 1, "snapshots": history["snapshots"]})
    _write(Path(runtime) / JOURNAL, history)
