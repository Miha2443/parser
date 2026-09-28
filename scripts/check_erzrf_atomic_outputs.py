"""Fast checks for atomic ERZRF JSON and XLSX writes."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

import pandas as pd

os.environ["TDM_DISABLED"] = "1"

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


def _write_top_workbook(path: Path, area: int) -> None:
    pd.DataFrame([{"Место": 1, "Наименование, регион": "Fixture developer",
                   "Строится, м²": area}]).to_excel(path, index=False)


def test_card_content_probe() -> None:
    _require(ec._card_html_has_content("<app-org-table><h3>Dev</h3></app-org-table>"), "card probe should detect org table")
    _require(ec._card_html_has_content("<div>Регионы присутствия</div>"), "card probe should detect text markers")
    _require(not ec._card_html_has_content("<html><body>loading</body></html>"), "card probe should reject loading page")


def main() -> int:
    test_card_content_probe()
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

        browser_download = base / "browser.xlsx"
        _write_top_workbook(browser_download, 123)
        _require(ec._validate_downloaded_excel(browser_download, "obyem_stroitelstva"), "valid browser download should pass")
        _require(browser_download.exists(), "valid browser download should be kept")

        invalid_download = base / "login.xlsx"
        invalid_download.write_bytes(b"<html>login</html>")
        with redirect_stdout(StringIO()):
            ok = ec._validate_downloaded_excel(invalid_download, "obyem_stroitelstva")
        _require(not ok, "invalid browser download should fail")
        _require(invalid_download.exists(), "validation must not delete the source")

        final_target = base / "final.xlsx"
        _write_top_workbook(final_target, 100)
        valid_download = base / "download.xlsx"
        _write_top_workbook(valid_download, 200)
        _require(ec._finalize_downloaded_excel(valid_download, final_target, "obyem_stroitelstva"), "valid browser download should finalize")
        _require(pd.read_excel(final_target).loc[0, "Строится, м²"] == 200, "valid browser download did not replace target")
        _require(not valid_download.exists(), "finalized browser download should be moved")

        old_bytes = final_target.read_bytes()
        invalid_download = base / "download-invalid.xlsx"
        invalid_download.write_bytes(b"<html>login</html>")
        with redirect_stdout(StringIO()):
            ok = ec._finalize_downloaded_excel(invalid_download, final_target, "obyem_stroitelstva")
        _require(not ok, "invalid browser download should not finalize")
        _require(final_target.read_bytes() == old_bytes, "invalid browser download replaced target")
        _require(invalid_download.exists(), "invalid staging download should remain until browser cleanup")

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
