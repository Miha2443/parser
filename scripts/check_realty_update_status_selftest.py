"""Self-checks for scripts/check_realty_update_status.py."""
from __future__ import annotations

import json
import tempfile
from contextlib import redirect_stdout
from datetime import datetime
from io import StringIO
from pathlib import Path

from check_realty_update_status import check_status_file


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _check(path: Path, *, strict: bool, quiet_warnings: bool = False) -> int:
    with redirect_stdout(StringIO()):
        return check_status_file(path, strict=strict, quiet_warnings=quiet_warnings)


def main() -> int:
    now = datetime.now().isoformat(timespec="seconds")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)

        ok_status = root / "ok.json"
        _write(ok_status, {
            "status": "success",
            "started_at": now,
            "updated_at": now,
            "finished_at": now,
            "sources_requested": ["monitoring"],
            "successes": ["monitoring"],
            "failures": [],
            "completed_sources": ["monitoring"],
            "pending_sources": [],
            "marts_ok": True,
        })
        if _check(ok_status, strict=True) != 0:
            raise AssertionError("valid status should pass strict mode")

        legacy_status = root / "legacy.json"
        _write(legacy_status, {
            "started_at": now,
            "finished_at": now,
            "sources_requested": ["monitoring"],
            "successes": ["monitoring"],
            "failures": [],
            "marts_ok": True,
        })
        if _check(legacy_status, strict=False) != 0:
            raise AssertionError("legacy status should pass non-strict mode")
        if _check(legacy_status, strict=False, quiet_warnings=True) != 0:
            raise AssertionError("legacy status should pass quiet non-strict mode")
        if _check(legacy_status, strict=True) == 0:
            raise AssertionError("legacy status should fail strict mode")

        bad_status = root / "bad.json"
        _write(bad_status, {
            "status": "success",
            "started_at": now,
            "updated_at": now,
            "finished_at": now,
            "sources_requested": ["monitoring"],
            "successes": ["unknown-source"],
            "failures": [],
        })
        if _check(bad_status, strict=False) == 0:
            raise AssertionError("unknown source should fail")

        bad_progress = root / "bad_progress.json"
        _write(bad_progress, {
            "status": "running",
            "started_at": now,
            "updated_at": now,
            "finished_at": None,
            "sources_requested": ["monitoring", "rasprod"],
            "successes": ["monitoring"],
            "failures": [],
            "completed_sources": [],
            "pending_sources": ["monitoring", "rasprod"],
        })
        if _check(bad_progress, strict=False) == 0:
            raise AssertionError("inconsistent progress fields should fail")

    print("realty update status selftest: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
