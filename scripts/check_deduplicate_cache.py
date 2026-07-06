"""Fast checks for content deduplication hash caching."""
from __future__ import annotations

import sys
import tempfile
import time
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline import deduplicate as dd  # noqa: E402


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    original_realty_root = dd.REALTY_ROOT
    original_archive_root = dd.ARCHIVE_ROOT
    original_cache = dict(dd._SHA256_CACHE)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "realty"
            dd.REALTY_ROOT = root
            dd.ARCHIVE_ROOT = root / "_archive"
            dd._SHA256_CACHE.clear()

            active = root / "nashdom" / "monitoring_2_0_20260702.xlsx"
            archive = dd.ARCHIVE_ROOT / "2026-07-01" / "nashdom" / "monitoring_2_0_20260701.xlsx"
            active.parent.mkdir(parents=True)
            archive.parent.mkdir(parents=True)
            active.write_bytes(b"same-content")
            archive.write_bytes(b"same-content")

            duplicate = dd.is_duplicate_of_latest(active)
            _require(duplicate == archive, "duplicate should be detected")
            _require(str(active.resolve()) in dd._SHA256_CACHE, "active hash should be cached")
            _require(str(archive.resolve()) in dd._SHA256_CACHE, "archive hash should be cached")

            first_hash = dd._file_sha256(active)
            second_hash = dd._file_sha256(active)
            _require(second_hash == first_hash, "unchanged hash should be stable")

            time.sleep(0.01)
            active.write_bytes(b"SAME-CONTENT")
            changed_hash = dd._file_sha256(active)
            _require(changed_hash != first_hash, "same-size content change should invalidate hash cache")

            duplicate_active = root / "nashdom" / "monitoring_2_0_20260703.xlsx"
            duplicate_active.write_bytes(b"same-content")
            duplicate_key = str(duplicate_active.resolve())
            with redirect_stdout(StringIO()):
                kept, is_update = dd.deduplicate(duplicate_active, log_prefix="")
            _require(kept == archive, "deduplicate should keep archive duplicate")
            _require(not is_update, "duplicate should not be marked as update")
            _require(not duplicate_active.exists(), "duplicate active file should be removed")
            _require(duplicate_key not in dd._SHA256_CACHE, "removed duplicate hash should be pruned")
            _require(str(archive.resolve()) in dd._SHA256_CACHE, "archive hash should stay cached")
    finally:
        dd.REALTY_ROOT = original_realty_root
        dd.ARCHIVE_ROOT = original_archive_root
        dd._SHA256_CACHE.clear()
        dd._SHA256_CACHE.update(original_cache)

    print("deduplicate cache checks: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
