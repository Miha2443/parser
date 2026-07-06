"""Fast checks for TDM realty file listing."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.tdm_files import list_tdm_realty_files  # noqa: E402


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _write(path: Path, content: bytes, mtime: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    os.utime(path, (mtime, mtime))


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "realty"
        _write(root / "nashdom" / "older.xlsx", b"xlsx", 100)
        _write(root / "nashdom" / "newer.json", b"{}", 200)
        _write(root / "nashdom" / "partial.xlsx.crdownload", b"partial", 300)
        _write(root / "nashdom" / "scratch.tmp", b"tmp", 400)
        _write(root / "nashdom" / "notes.txt", b"text", 500)
        _write(root / "_archive" / "2026-07-01" / "nashdom" / "archived.xlsx", b"xlsx", 600)

        options = list_tdm_realty_files(root)
        _require([option.rel for option in options] == [
            "nashdom\\newer.json",
            "nashdom\\older.xlsx",
        ], "TDM files should be active, supported, and newest first")
        _require("newer.json" in options[0].label, "TDM file label should include relative path")
        _require("(01.01.1970 03:03," in options[0].label, "TDM file label should include mtime")

    print("tdm file listing checks: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
