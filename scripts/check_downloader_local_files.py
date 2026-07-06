"""Fast checks for downloader local file discovery."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.downloaders.local_files import list_local_files  # noqa: E402


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        active = root / "realty" / "nashdom" / "monitoring_2_0_20260702.xlsx"
        active.parent.mkdir(parents=True)
        active.write_bytes(b"xlsx")
        (root / "realty" / "nashdom" / "monitoring_2_0_20260702.xlsx.crdownload").write_bytes(b"partial")
        (root / "realty" / "nashdom" / "scratch.tmp").write_bytes(b"tmp")
        (root / "realty" / "nashdom" / "folder.xlsx").mkdir()
        archived = root / "realty" / "_archive" / "2026-07-01" / "nashdom" / "monitoring_2_0_20260701.xlsx"
        archived.parent.mkdir(parents=True)
        archived.write_bytes(b"old")

        files = list_local_files(root, [
            "realty/nashdom/*.xlsx",
            "realty/**/*.xlsx",
            "realty/nashdom/*.tmp",
            "realty/nashdom/*.crdownload",
        ])
        _require(files == [active], f"unexpected local files: {files}")

    print("downloader local file checks: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
