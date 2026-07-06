"""Fast self-checks for scripts/update_realty.py run lock behavior."""
from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

import update_realty as ur


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    original_held = ur._REALTY_UPDATE_LOCK_HELD
    try:
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / ".realty_update.lock"
            ur._REALTY_UPDATE_LOCK_HELD = False

            _require(ur.acquire_realty_update_lock(lock), "first lock acquire should succeed")
            first_text = lock.read_text(encoding="utf-8")

            _require(not ur.acquire_realty_update_lock(lock), "fresh lock should block second acquire")
            _require(lock.read_text(encoding="utf-8") == first_text, "fresh lock should not be replaced")

            ur.release_realty_update_lock(lock)
            _require(not lock.exists(), "release should remove lock")

            lock.write_text("stale\n", encoding="utf-8")
            old_time = time.time() - 13 * 3600
            os.utime(lock, (old_time, old_time))

            _require(ur.acquire_realty_update_lock(lock), "stale lock should be replaced")
            _require(lock.read_text(encoding="utf-8") != "stale\n", "stale lock content should be replaced")
            ur.release_realty_update_lock(lock)
    finally:
        ur._REALTY_UPDATE_LOCK_HELD = original_held

    print("update_realty lock checks: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
