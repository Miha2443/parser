"""Fast checks for nashdom downloader/run contracts without network or Selenium."""
from __future__ import annotations

import contextlib
import io
import os
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import nashdom_checker as nc  # noqa: E402
from pipeline.downloaders import nashdom as nashdom_downloader  # noqa: E402
from pipeline.registry import Indicator  # noqa: E402


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _write_monitoring_workbook(path: Path, names: list[str]) -> None:
    import openpyxl

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Реестр РВ"
    sheet.append(["Группа компаний", "УИН", "Год ввода по Мосстату", "Отрасли",
                  "Группировка", "Общая площадь", "Жилая площадь"])
    for name in names:
        sheet.append([name, "component", 2026, "Жилые объекты", "Жилье", 100, 70])
    oks = workbook.create_sheet("Реестр ОКС")
    oks.append(["Группа компаний", "УИН", "Назначение", "Общая площадь", "Жилая площадь"])
    if names:
        oks.append([names[0], "construction", "Жилье", 100, 70])
    workbook.save(path)


def test_monitoring_devs_prefers_filename_date_with_fallback() -> None:
    original_download_dir = nc.DOWNLOAD_DIR
    with tempfile.TemporaryDirectory() as tmp:
        try:
            nc.DOWNLOAD_DIR = Path(tmp)
            old_file = nc.DOWNLOAD_DIR / "monitoring_2_0_20260701.xlsx"
            latest_valid = nc.DOWNLOAD_DIR / "monitoring_2_0_20260702.xlsx"
            _write_monitoring_workbook(old_file, ["Old Dev"])
            _write_monitoring_workbook(latest_valid, ["Latest Dev"])

            os.utime(old_file, (2_000_000_000, 2_000_000_000))
            os.utime(latest_valid, (1_000_000_000, 1_000_000_000))
            with contextlib.redirect_stdout(io.StringIO()):
                names = nc._load_monitoring_devs()
            _require(names == ["Latest Dev"], "monitoring devs should prefer filename date over mtime")

            corrupt_latest = nc.DOWNLOAD_DIR / "monitoring_2_0_20260703.xlsx"
            corrupt_latest.write_text("not xlsx", encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                names = nc._load_monitoring_devs()
            _require(names == ["Latest Dev"], "monitoring devs should fall back to previous valid workbook")
        finally:
            nc.DOWNLOAD_DIR = original_download_dir


def test_monitoring_same_day_helpers() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "monitoring_2_0_20260706.xlsx"
        content = b"PK\x03\x04same content"
        digest = nc._sha256_bytes(content)

        target.write_bytes(content)
        entry = {
            "filename": target.name,
            "size_bytes": len(content),
            "sha256": digest,
        }
        _require(
            nc._monitoring_same_day_unchanged(
                state_entry=entry,
                target=target,
                content_sha256=digest,
                size_bytes=len(content),
            ),
            "same-day monitoring file should be unchanged",
        )
        _require(
            not nc._monitoring_same_day_unchanged(
                state_entry={**entry, "sha256": "bad"},
                target=target,
                content_sha256=digest,
                size_bytes=len(content),
            ),
            "hash mismatch should be treated as changed",
        )
        _require(
            not nc._monitoring_same_day_unchanged(
                state_entry={**entry, "size_bytes": "bad"},
                target=target,
                content_sha256=digest,
                size_bytes=len(content),
            ),
            "bad stored size should be treated as changed",
        )


def test_monitoring_fetch_force_overrides_same_day_skip() -> None:
    original_download_dir = nc.DOWNLOAD_DIR
    original_get = nc.requests.get
    previous_force = os.environ.get("NASHDOM_FORCE")

    class Response:
        url = "https://docs.google.com/export"
        status_code = 200
        content = b""
        headers = {
            "content-type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        }

    with tempfile.TemporaryDirectory() as tmp:
        try:
            nc.DOWNLOAD_DIR = Path(tmp)
            fixture = nc.DOWNLOAD_DIR / "fixture.xlsx"
            _write_monitoring_workbook(fixture, ["Developer"])
            Response.content = fixture.read_bytes()
            nc.requests.get = lambda *args, **kwargs: Response()
            state: dict = {}
            os.environ.pop("NASHDOM_FORCE", None)

            with contextlib.redirect_stdout(io.StringIO()):
                files, ok = nc.fetch_monitoring_2_0(state)
            _require(ok and len(files) == 1, "first monitoring fetch should write file")

            with contextlib.redirect_stdout(io.StringIO()):
                files, ok = nc.fetch_monitoring_2_0(state)
            _require(ok and files == [], "unchanged monitoring fetch should skip")

            os.environ["NASHDOM_FORCE"] = "1"
            with contextlib.redirect_stdout(io.StringIO()):
                files, ok = nc.fetch_monitoring_2_0(state)
            _require(ok and len(files) == 1, "force monitoring fetch should rewrite")
        finally:
            nc.DOWNLOAD_DIR = original_download_dir
            nc.requests.get = original_get
            if previous_force is None:
                os.environ.pop("NASHDOM_FORCE", None)
            else:
                os.environ["NASHDOM_FORCE"] = previous_force


def test_run_contract_allows_successful_skip() -> None:
    original_funcs = nc.SOURCE_FUNCS
    original_download_dir = nc.DOWNLOAD_DIR
    original_state_file = nc.STATE_FILE
    with tempfile.TemporaryDirectory() as tmp:
        tmp_root = Path(tmp)
        changed_file = tmp_root / "changed.xlsx"
        changed_file.write_bytes(b"x")

        def changed(state: dict):
            state["changed"] = True
            return [changed_file]

        def skipped(state: dict):
            state["skipped"] = True
            return [], True

        def failed(state: dict):
            state["failed"] = True
            return [], False

        try:
            nc.DOWNLOAD_DIR = tmp_root / "raw"
            nc.STATE_FILE = tmp_root / "state.json"
            nc.SOURCE_FUNCS = {
                "changed": changed,
                "skipped": skipped,
                "failed": failed,
            }
            with contextlib.redirect_stdout(io.StringIO()):
                files, ok = nc.run(only=["changed", "skipped"])
            _require(ok, "changed + successful skip should be ok")
            _require(files == [changed_file], "run should return only real new files")

            with contextlib.redirect_stdout(io.StringIO()):
                _, ok = nc.run(only=["failed"])
            _require(not ok, "explicit failed source should fail run")
        finally:
            nc.SOURCE_FUNCS = original_funcs
            nc.DOWNLOAD_DIR = original_download_dir
            nc.STATE_FILE = original_state_file


def test_pipeline_wrapper_unpacks_run_result() -> None:
    original_run = nc.run
    original_load_state = nc.load_state
    indicator = Indicator(
        id="test",
        section="test",
        title="test",
        unit="",
        source="nashdom",
        source_ids=["monitoring_2_0"],
        parser="",
        file_patterns=[],
    )

    try:
        expected = [Path("data/raw/realty/nashdom/test.xlsx")]

        def ok_run(only=None):
            return expected, True

        def failed_run(only=None):
            return [], False

        nc.load_state = lambda: {"monitoring_2_0": {"report_date": "06.07.2026"}}
        nc.run = ok_run
        result = nashdom_downloader.fetch(indicator)
        _require(result["new_files"] == expected, "wrapper should unpack nc.run files")
        _require(result["skipped"] is False, "wrapper should mark non-empty files as not skipped")

        nc.run = failed_run
        try:
            nashdom_downloader.fetch(indicator)
        except RuntimeError:
            pass
        else:
            raise AssertionError("wrapper should raise on failed nc.run")
    finally:
        nc.run = original_run
        nc.load_state = original_load_state


def main() -> int:
    test_monitoring_devs_prefers_filename_date_with_fallback()
    test_monitoring_same_day_helpers()
    test_monitoring_fetch_force_overrides_same_day_skip()
    test_run_contract_allows_successful_skip()
    test_pipeline_wrapper_unpacks_run_result()
    print("nashdom contract checks: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
