"""Fast checks for the ERZRF downloader/orchestrator contract."""
from __future__ import annotations

import sys
import tempfile
import types
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.downloaders import erzrf as dl  # noqa: E402


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _install_fake_erzrf(runs: dict[tuple[str, ...], tuple[list[Path], bool]], state: dict):
    fake = types.ModuleType("erzrf_checker")

    def run(only=None):
        return runs[tuple(only or [])]

    fake.run = run
    fake.load_state = lambda: state
    sys.modules["erzrf_checker"] = fake
    return fake


def main() -> int:
    original_module = sys.modules.get("erzrf_checker")
    original_data_raw = dl.DATA_RAW
    original_state_dir = dl.STATE_DIR
    try:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            raw = base / "data" / "raw"
            state_dir = base / "state"
            raw.mkdir(parents=True)
            state_dir.mkdir()
            dl.DATA_RAW = raw
            dl.STATE_DIR = state_dir

            top_file = raw / "realty" / "erzrf" / "top_test.xlsx"
            cards_file = raw / "realty" / "erzrf" / "cards" / "cards_test.xlsx"
            cards_file.parent.mkdir(parents=True)
            top_file.parent.mkdir(parents=True, exist_ok=True)
            top_file.write_bytes(b"top")
            cards_file.write_bytes(b"cards")

            dl._RAN_KEYS.clear()
            _install_fake_erzrf(
                {
                    ("top",): ([top_file], True),
                    ("cards",): ([cards_file], True),
                },
                {"erzrf_top": {"last_run": "2026-07-06T12:00:00"}},
            )
            indicator = SimpleNamespace(source_ids=["top_rf", "cards"], file_patterns=[])
            result = dl.fetch(indicator)
            _require(result["new_files"] == [top_file, cards_file], "new_files should be a flat Path list")
            _require(all(isinstance(p, Path) for p in result["new_files"]), "new_files should contain only Path values")
            _require(result["new_date"] == "2026-07-06", "new_date should come from ERZRF state")

            dl._RAN_KEYS.clear()
            _install_fake_erzrf(
                {("top",): ([top_file], False)},
                {"erzrf_top": {"last_run": "2026-07-06T12:00:00"}},
            )
            try:
                dl.fetch(SimpleNamespace(source_ids=["top_rf"], file_patterns=[]))
            except RuntimeError as exc:
                _require("erzrf source failed: top" in str(exc), "failure should name the ERZRF source")
            else:
                raise AssertionError("ok=False should fail the downloader")
            _require("top" not in dl._RAN_KEYS, "failed source should not be marked as already run")
    finally:
        dl.DATA_RAW = original_data_raw
        dl.STATE_DIR = original_state_dir
        dl._RAN_KEYS.clear()
        if original_module is None:
            sys.modules.pop("erzrf_checker", None)
        else:
            sys.modules["erzrf_checker"] = original_module

    print("erzrf downloader contract checks: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
