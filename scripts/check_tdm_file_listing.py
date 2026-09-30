"""Fast checks for TDM realty file listing."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.tdm_files import (  # noqa: E402
    list_tdm_datasets,
    list_tdm_realty_files,
    prepare_tdm_dataset,
)


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

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write(root / "downloads" / "20260101_Индексы потребительских цен часть1.xls", b"old", 1_700_000_100)
        _write(root / "downloads" / "20260901_Индексы потребительских цен часть1.xls", b"one", 1_700_000_300)
        _write(root / "downloads" / "20260901_Индексы потребительских цен часть2.xls", b"two", 1_700_000_400)
        _write(root / "data" / "raw" / "realty" / "nashdom" / "kvartirografia_20260901.xlsx", b"kv", 1_700_000_500)

        datasets = {option.spec.key: option for option in list_tdm_datasets(root)}
        _require("ipc" in datasets and "kvartirografia" in datasets,
                 "friendly TDM catalog should resolve dashboard datasets")
        _require(len(datasets["ipc"].files) == 2,
                 "IPC should include newest part 1 and part 2")
        _require(datasets["ipc"].files[0].name.startswith("20260901"),
                 "IPC should not include an older duplicate")
        bundle, cleanup = prepare_tdm_dataset(datasets["ipc"], root / "tmp")
        _require(cleanup and bundle.suffix == ".zip" and bundle.is_file(),
                 "multi-file dataset should become one sendable ZIP")
        single, cleanup = prepare_tdm_dataset(datasets["kvartirografia"], root / "tmp")
        _require(not cleanup and single.name.startswith("kvartirografia_"),
                 "single-file dataset should be sent without repacking")

    print("tdm file listing checks: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
