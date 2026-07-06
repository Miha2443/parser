"""Validate the machine-readable status written by scripts/update_realty.py.

The check is intentionally lightweight and offline. In default mode it warns
about legacy or incomplete status files but only fails on malformed data. Use
``--strict`` when the status file itself should be treated as a deployment gate.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from update_realty import SOURCE_MAP


ROOT = Path(__file__).resolve().parent.parent
STATUS_FILE = ROOT / "data" / "processed" / "realty_update_status.json"
VALID_STATUSES = {"running", "success", "failed", "interrupted"}
STALE_RUNNING_MIN = 360


def _parse_dt(value: Any, field: str, failures: list[str]) -> datetime | None:
    if not value:
        return None
    if not isinstance(value, str):
        failures.append(f"{field} is not a string")
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        failures.append(f"{field} is not ISO datetime: {value!r}")
        return None


def _list_field(data: dict[str, Any], field: str, failures: list[str]) -> list[str]:
    value = data.get(field, [])
    if not isinstance(value, list):
        failures.append(f"{field} is not a list")
        return []
    return [str(item) for item in value]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="fail on legacy status shape, missing log, or stale running heartbeat",
    )
    args = parser.parse_args()

    if not STATUS_FILE.exists():
        print("realty update status: missing (ok before first run)")
        return 0

    failures: list[str] = []
    warnings: list[str] = []
    try:
        data = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: cannot read {STATUS_FILE.relative_to(ROOT)}: {exc}")
        return 1
    if not isinstance(data, dict):
        print("ERROR: realty update status is not an object")
        return 1

    status = data.get("status")
    if status is None:
        warnings.append("legacy status has no 'status' field")
        status = "failed" if data.get("failures") or data.get("marts_ok") is False else "success"
    status = str(status).lower()
    if status not in VALID_STATUSES:
        failures.append(f"unknown status: {status!r}")

    requested = _list_field(data, "sources_requested", failures)
    successes = _list_field(data, "successes", failures)
    failures_sources = _list_field(data, "failures", failures)
    known_sources = set(SOURCE_MAP)
    unknown_sources = sorted(set(requested + successes + failures_sources) - known_sources)
    if unknown_sources:
        failures.append(f"unknown source alias(es): {', '.join(unknown_sources)}")
    outside_requested = sorted((set(successes) | set(failures_sources)) - set(requested))
    if outside_requested:
        failures.append(f"completed source(s) not requested: {', '.join(outside_requested)}")

    started_at = _parse_dt(data.get("started_at"), "started_at", failures)
    updated_at = _parse_dt(data.get("updated_at"), "updated_at", failures)
    finished_at = _parse_dt(data.get("finished_at"), "finished_at", failures)
    if updated_at is None:
        warnings.append("status has no updated_at heartbeat")
    if status == "running":
        if finished_at is not None:
            failures.append("running status must not have finished_at")
        if updated_at is not None:
            age_min = (datetime.now(updated_at.tzinfo) - updated_at).total_seconds() / 60
            if age_min > STALE_RUNNING_MIN:
                warnings.append(f"running heartbeat is stale: {age_min:.0f} min")
    elif finished_at is None:
        warnings.append(f"{status} status has no finished_at")
    if started_at and finished_at and finished_at < started_at:
        failures.append("finished_at is earlier than started_at")
    if status == "success" and failures_sources:
        failures.append("success status has failed sources")
    if status == "failed" and not failures_sources and not data.get("error") and data.get("marts_ok") is not False:
        warnings.append("failed status has no failures/error/marts_ok=false detail")

    log_file = data.get("log_file")
    if isinstance(log_file, str) and log_file:
        log_path = ROOT / log_file
        if not log_path.is_file():
            warnings.append(f"log_file does not exist: {log_file}")
    elif log_file is not None:
        failures.append("log_file is not a string")

    if failures:
        for item in failures:
            print(f"ERROR: {item}")
    if warnings:
        for item in warnings:
            print(f"WARNING: {item}")

    if failures or (args.strict and warnings):
        print(f"realty update status check failed: {len(failures)} error(s), {len(warnings)} warning(s)")
        return 1
    print(f"realty update status: ok ({status}, {len(warnings)} warning(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
