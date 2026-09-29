"""Offline regression checks for complete/partial Fedstat update status."""
from __future__ import annotations

import sys
import tempfile
from contextlib import ExitStack, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import fedstat_checker as fc
from pipeline.downloaders import fedstat as dl
from pipeline.registry import Indicator


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _check(root: Path, *, pipeline: bool, dates: dict, failed: set,
           expected_ok: bool, only: bool = False, direct: bool = False) -> None:
    initial = {src: "01.07.2026" for src in dates}
    downloads = root / "downloads"
    downloads.mkdir(exist_ok=True)
    driver = Mock()
    calls = []

    def fake_download(src, save_dir, *, remote_date=None, driver=None):
        calls.append(src)
        _require(remote_date == dates[src], "download must receive the source date")
        if src in failed:
            return None
        path = save_dir / f"{src}_new.xls"
        path.write_bytes(b"offline fixture")
        return path

    indicator = Indicator("fixture", "test", "test", "", "fedstat",
                          list(dates), "unused", ["*.xls"])
    with ExitStack() as stack:
        stack.enter_context(patch.multiple(
            fc, DOWNLOAD_DIR=downloads, STATE_FILE=root / "state.json",
            INDICATORS={src: src for src in dates},
            DEFAULT_INDICATOR_USAGE={src: "offline fixture" for src in dates},
            DIRECT_DOWNLOAD_ON_DATE_FAILURE=direct,
        ))
        stack.enter_context(patch.multiple(dl, DOWNLOADS_DIR=downloads, ROOT=root))
        stack.enter_context(patch.object(fc, "create_driver", return_value=driver))
        stack.enter_context(patch.object(fc, "load_state", return_value=initial.copy()))
        saved = stack.enter_context(patch.object(fc, "save_state"))
        stack.enter_context(patch.object(fc, "get_last_update_date",
                                       side_effect=lambda _driver, src: dates[src]))
        stack.enter_context(patch.object(fc, "download_excel", side_effect=fake_download))
        stack.enter_context(patch.object(fc, "_should_direct_fallback", return_value=True))
        archive = stack.enter_context(patch.object(dl, "_archive_old"))
        stack.enter_context(redirect_stdout(StringIO()))
        if pipeline:
            try:
                result = dl.fetch(indicator)
            except RuntimeError as exc:
                _require(not expected_ok, f"unexpected failure: {exc}")
                for src in dates:
                    if dates[src] is None or src in failed:
                        _require(src in str(exc), "failure must identify the source")
            else:
                _require(expected_ok, "incomplete update must raise, not return skipped")
                _require(result["skipped"] == (not calls), "unchanged/skipped contract")
                _require(len(result["new_files"]) == len(calls), "successful downloads returned")
            if not expected_ok or any(date == initial[src] for src, date in dates.items()):
                archive.assert_not_called()
            elif calls:
                archive.assert_called_once()
        else:
            files, ok = fc.run(only_ids=list(dates) if only else None)
            _require(ok == expected_ok, "run must fail on any incomplete source")
            _require(len(files) == len(set(calls) - failed), "successful files remain available")

        driver.quit.assert_called_once()
        saved.assert_called_once()
        expected_state = initial.copy()
        for src in calls:
            if src not in failed and dates[src] is not None:
                expected_state[src] = dates[src]
        _require(saved.call_args.args[0] == expected_state,
                 "only confirmed dated downloads may advance state")


def main() -> int:
    cases = [
        ({"one": "01.07.2026", "two": "02.07.2026"}, {"two"}, False),
        ({"one": "01.07.2026", "two": None}, set(), False),
        ({"one": "01.07.2026", "two": "01.07.2026"}, set(), True),
        ({"one": "02.07.2026", "two": "02.07.2026"}, set(), True),
        ({"one": "02.07.2026", "two": "01.07.2026"}, set(), True),
        ({"one": "02.07.2026", "two": "02.07.2026"}, {"two"}, False),
        ({"one": "02.07.2026", "two": None}, set(), False),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for pipeline, only in ((False, False), (False, True), (True, False)):
            for dates, failed, ok in cases:
                _check(root, pipeline=pipeline, only=only, dates=dates,
                       failed=failed, expected_ok=ok)
        for failed, ok in ((set(), True), ({"two"}, False)):
            _check(root, pipeline=False, dates={"one": "01.07.2026", "two": None},
                   failed=failed, expected_ok=ok, direct=True)

        # Exercise the actual caller contract without writing an audit or parsing data.
        from pipeline import orchestrator
        audit = Mock()
        downloader = Mock()
        downloader.fetch.side_effect = RuntimeError("Fedstat: two: failed download")
        indicator = Indicator("fixture", "test", "test", "", "fedstat",
                              ["two"], "unused", ["*.xls"])
        with patch.object(orchestrator, "_downloader", return_value=downloader):
            orchestrator._process_one(indicator, audit, download=True)
        audit.error.assert_called_once()
        _require(audit.error.call_args.kwargs["stage"] == "download", "audit error stage")
        audit.skip.assert_not_called()
        downloader.find_files.assert_not_called()

    print("fedstat status checks: ok (23 update scenarios + caller contract)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
