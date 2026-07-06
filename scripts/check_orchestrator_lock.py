"""Fast checks for orchestrator ETL lock behavior."""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline import orchestrator  # noqa: E402


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    original_lock = orchestrator.ETL_LOCK
    original_held = orchestrator._ETL_LOCK_HELD
    original_token = orchestrator._ETL_LOCK_TOKEN
    try:
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / ".etl.lock"
            orchestrator.ETL_LOCK = lock
            orchestrator._ETL_LOCK_HELD = False
            orchestrator._ETL_LOCK_TOKEN = None

            _require(orchestrator._acquire_lock(), "first lock acquire should succeed")
            first_text = lock.read_text(encoding="utf-8")
            _require("token=" in first_text, "lock should include an owner token")
            _require(not orchestrator._acquire_lock(), "fresh lock should block second acquire")
            _require(lock.read_text(encoding="utf-8") == first_text, "fresh lock should not be replaced")

            orchestrator._release_lock()
            _require(not lock.exists(), "release should remove lock")

            lock.write_text("stale\n", encoding="utf-8")
            old_time = time.time() - 7 * 3600
            os.utime(lock, (old_time, old_time))
            _require(orchestrator._acquire_lock(), "stale lock should be replaced")
            _require(lock.read_text(encoding="utf-8") != "stale\n", "stale lock content should be replaced")
            orchestrator._release_lock()

            _require(orchestrator._acquire_lock(), "owner lock acquire should succeed")
            lock.write_text("token=other-owner\npid=999\n", encoding="utf-8")
            orchestrator._release_lock()
            _require(lock.exists(), "release should not remove another owner's lock")
            _require("other-owner" in lock.read_text(encoding="utf-8"), "other owner's lock should remain intact")
            lock.unlink()
    finally:
        orchestrator.ETL_LOCK = original_lock
        orchestrator._ETL_LOCK_HELD = original_held
        orchestrator._ETL_LOCK_TOKEN = original_token

    print("orchestrator lock checks: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
