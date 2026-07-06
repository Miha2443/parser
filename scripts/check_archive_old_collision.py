"""Fast checks for collision-safe realty raw archiving."""
from __future__ import annotations

import os
import sys
import tempfile
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline import archive_old  # noqa: E402


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _write(path: Path, text: str, mtime: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    os.utime(path, (mtime, mtime))


def main() -> int:
    original_realty_root = archive_old.REALTY_ROOT
    original_archive_root = archive_old.ARCHIVE_ROOT
    try:
        with tempfile.TemporaryDirectory() as tmp:
            realty_root = Path(tmp) / "realty"
            archive_root = realty_root / "_archive"
            archive_old.REALTY_ROOT = realty_root
            archive_old.ARCHIVE_ROOT = archive_root

            old_active = realty_root / "nashdom" / "monitoring_2_0_20260701.xlsx"
            new_active = realty_root / "nashdom" / "monitoring_2_0_20260702.xlsx"
            existing_archive = archive_root / "2026-07-01" / "nashdom" / old_active.name

            _write(old_active, "old active", 100)
            _write(new_active, "new active", 200)
            _write(existing_archive, "existing archive", 50)

            with redirect_stdout(StringIO()):
                moved = archive_old.archive_directory("nashdom", keep=1)

            _require(moved == 1, "one stale file should be archived")
            _require(not old_active.exists(), "stale active file should be moved")
            _require(new_active.exists(), "fresh active file should remain")
            _require(existing_archive.read_text(encoding="utf-8") == "existing archive", "existing archive file should remain")

            collision_copy = existing_archive.with_name("monitoring_2_0_20260701_1.xlsx")
            _require(collision_copy.is_file(), "archive collision should create suffixed file")
            _require(collision_copy.read_text(encoding="utf-8") == "old active", "suffixed archive should contain moved file")
    finally:
        archive_old.REALTY_ROOT = original_realty_root
        archive_old.ARCHIVE_ROOT = original_archive_root

    print("archive collision checks: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
