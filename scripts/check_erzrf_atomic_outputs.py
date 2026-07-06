"""Fast checks for atomic ERZRF JSON and XLSX writes."""
from __future__ import annotations

import json
import sys
import tempfile
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import erzrf_checker as ec  # noqa: E402


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _write_valid_workbook(path: Path, version: int) -> None:
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        pd.DataFrame([{"version": version}]).to_excel(writer, sheet_name="cards", index=False)


def main() -> int:
    original_state = ec.STATE_FILE
    original_download_dir = ec.DOWNLOAD_DIR
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)

        json_target = base / "top_developers_rf.json"
        ec._write_json_atomic(json_target, {"version": 1})
        _require(json.loads(json_target.read_text(encoding="utf-8"))["version"] == 1, "initial JSON write failed")

        original_write_text = Path.write_text
        try:
            def write_bad_json(path_self, *args, **kwargs):
                if str(path_self).endswith(".tmp"):
                    return original_write_text(path_self, "{bad-json", encoding="utf-8")
                return original_write_text(path_self, *args, **kwargs)

            Path.write_text = write_bad_json
            try:
                ec._write_json_atomic(json_target, {"version": 2})
            except json.JSONDecodeError:
                pass
            else:
                raise AssertionError("bad temporary JSON should fail")
        finally:
            Path.write_text = original_write_text

        _require(json.loads(json_target.read_text(encoding="utf-8"))["version"] == 1, "failed JSON write replaced old file")
        _require(not (base / "top_developers_rf.json.tmp").exists(), "temporary JSON was not removed")

        xlsx_target = base / "cards.xlsx"
        ec._write_excel_atomic(xlsx_target, lambda path: _write_valid_workbook(path, version=1))
        frame = pd.read_excel(xlsx_target, sheet_name="cards")
        _require(int(frame.loc[0, "version"]) == 1, "initial XLSX write failed")

        def write_bad_workbook(path: Path) -> None:
            path.write_bytes(b"not an xlsx")

        try:
            ec._write_excel_atomic(xlsx_target, write_bad_workbook)
        except Exception:  # noqa: BLE001
            pass
        else:
            raise AssertionError("bad temporary workbook should fail")

        frame = pd.read_excel(xlsx_target, sheet_name="cards")
        _require(int(frame.loc[0, "version"]) == 1, "failed XLSX write replaced old file")
        _require(not (base / "cards.tmp.xlsx").exists(), "temporary XLSX was not removed")

        try:
            ec.STATE_FILE = base / "state" / "erzrf_state.json"
            ec.save_state({"ok": True})
            _require(ec.load_state() == {"ok": True}, "save_state/load_state should round-trip")

            ec.STATE_FILE.write_text("{bad-json", encoding="utf-8")
            with redirect_stdout(StringIO()):
                recovered = ec.load_state()
            _require(recovered == {}, "bad state should recover as empty dict")
            _require(not ec.STATE_FILE.exists(), "bad state should be moved aside")
            _require((ec.STATE_FILE.parent / "erzrf_state.json.bad").is_file(), "bad state backup missing")
            (ec.STATE_FILE.parent / "erzrf_state.json.bad").write_text("previous bad", encoding="utf-8")

            ec.STATE_FILE.write_text("{bad-json-again", encoding="utf-8")
            with redirect_stdout(StringIO()):
                recovered = ec.load_state()
            _require(recovered == {}, "second bad state should recover as empty dict")
            _require(
                (ec.STATE_FILE.parent / "erzrf_state.json.bad").read_text(encoding="utf-8") == "previous bad",
                "existing bad state backup should not be overwritten",
            )
            _require((ec.STATE_FILE.parent / "erzrf_state.json.1.bad").is_file(), "suffixed bad state backup missing")

            ec.DOWNLOAD_DIR = base / "erzrf"
            ec.DOWNLOAD_DIR.mkdir()
            (ec.DOWNLOAD_DIR / "top_developers_rf_20260701.json").write_text(
                json.dumps({"developers": [{"name": "valid-old"}]}),
                encoding="utf-8",
            )
            (ec.DOWNLOAD_DIR / "top_developers_rf_20260702.json").write_text("{bad-json", encoding="utf-8")
            with redirect_stdout(StringIO()):
                developers = ec._load_top_developers()
            _require(developers == [{"name": "valid-old"}], "top developers loader should fall back to previous valid JSON")

            (ec.DOWNLOAD_DIR / "top_developers_rf_20260703.json").write_text(
                json.dumps({"developers": []}),
                encoding="utf-8",
            )
            with redirect_stdout(StringIO()):
                developers = ec._load_top_developers()
            _require(developers == [{"name": "valid-old"}], "top developers loader should skip empty latest JSON")
        finally:
            ec.STATE_FILE = original_state
            ec.DOWNLOAD_DIR = original_download_dir

    print("erzrf atomic output checks: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
