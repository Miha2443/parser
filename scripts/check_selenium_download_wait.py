"""Fast checks for Selenium download waiting logic without launching Chrome."""
from __future__ import annotations

import sys
import tempfile
import threading
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.selenium_utils import wait_for_download  # noqa: E402


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def test_waits_for_stable_final_file() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        download_dir = Path(tmp)
        (download_dir / "old.xlsx").write_bytes(b"old")
        before = set(download_dir.glob("*"))

        def writer() -> None:
            temp = download_dir / "report.xlsx.crdownload"
            final = download_dir / "report.xlsx"
            temp.write_bytes(b"partial")
            time.sleep(0.08)
            final.write_bytes(b"part1")
            temp.unlink()
            time.sleep(0.08)
            final.write_bytes(b"done")

        thread = threading.Thread(target=writer)
        thread.start()
        result = wait_for_download(
            download_dir,
            before_snapshot=before,
            timeout=3,
            poll_interval=0.03,
            stable_for=0.12,
        )
        thread.join(timeout=1)

        _require(result == download_dir / "report.xlsx", "did not return final download")
        _require(result.read_bytes() == b"done", "returned before final file stabilized")


def test_ignores_temp_files_from_before_snapshot() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        download_dir = Path(tmp)
        (download_dir / "stale.crdownload").write_bytes(b"old temp")
        before = set(download_dir.glob("*"))
        expected = download_dir / "new.xlsx"
        expected.write_bytes(b"new")

        result = wait_for_download(
            download_dir,
            before_snapshot=before,
            timeout=1,
            poll_interval=0.02,
            stable_for=0,
        )

        _require(result == expected, "old temp file blocked a new completed download")


def test_returns_newest_completed_file_deterministically() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        download_dir = Path(tmp)
        before = set(download_dir.glob("*"))
        older = download_dir / "a.xlsx"
        newer = download_dir / "b.xlsx"
        older.write_bytes(b"a")
        time.sleep(0.02)
        newer.write_bytes(b"b")

        result = wait_for_download(
            download_dir,
            before_snapshot=before,
            timeout=1,
            poll_interval=0.02,
            stable_for=0,
        )

        _require(result == newer, "did not return newest completed download")


def test_timeout_on_temp_only() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        download_dir = Path(tmp)
        before = set(download_dir.glob("*"))
        (download_dir / "unfinished.xlsx.crdownload").write_bytes(b"partial")

        result = wait_for_download(
            download_dir,
            before_snapshot=before,
            timeout=0.15,
            poll_interval=0.03,
            stable_for=0,
        )

        _require(result is None, "temporary-only download should time out")


def main() -> int:
    test_waits_for_stable_final_file()
    test_ignores_temp_files_from_before_snapshot()
    test_returns_newest_completed_file_deterministically()
    test_timeout_on_temp_only()
    print("selenium download wait checks: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
