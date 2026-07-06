"""Fast self-checks for strict realty mart loading mode."""
from __future__ import annotations

import os
import logging
import json
import sys
import tempfile
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logging.getLogger("streamlit").setLevel(logging.ERROR)
logging.getLogger("streamlit.runtime.caching.cache_data_api").setLevel(logging.ERROR)
logging.disable(logging.CRITICAL)

from app import data_access as da  # noqa: E402


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _must_raise(fn, exc_type: type[BaseException], label: str) -> None:
    try:
        fn()
    except exc_type:
        return
    raise AssertionError(f"{label}: expected {exc_type.__name__}")


def _write_manifest(mart_dir: Path, marts: dict) -> None:
    (mart_dir / "manifest.json").write_text(
        json.dumps({"marts": marts}, ensure_ascii=False),
        encoding="utf-8",
    )


def main() -> int:
    original_marts = da.DATA_MARTS_REALTY
    previous_require = os.environ.get("PARSER_REQUIRE_REALTY_MARTS")
    previous_use = os.environ.get("PARSER_USE_REALTY_MARTS")
    with tempfile.TemporaryDirectory() as tmp:
        mart_dir = Path(tmp) / "marts"
        raw_dir = Path(tmp) / "raw"
        mart_dir.mkdir()
        raw_dir.mkdir()
        raw_file = raw_dir / "source.xlsx"
        raw_file.write_bytes(b"raw")

        try:
            da.DATA_MARTS_REALTY = mart_dir
            os.environ["PARSER_REQUIRE_REALTY_MARTS"] = "1"
            os.environ["PARSER_USE_REALTY_MARTS"] = "1"

            _must_raise(
                lambda: da._load_realty_mart("missing", []),
                FileNotFoundError,
                "missing strict mart",
            )

            good = mart_dir / "good.pkl"
            pd.to_pickle({"ok": pd.DataFrame({"x": [1]})}, good)
            _must_raise(
                lambda: da._load_realty_mart("good", []),
                RuntimeError,
                "missing strict manifest",
            )
            (mart_dir / "manifest.json").write_text("{bad-json", encoding="utf-8")
            _must_raise(
                lambda: da._load_realty_mart("good", []),
                RuntimeError,
                "bad strict manifest",
            )
            _write_manifest(mart_dir, {})
            _must_raise(
                lambda: da._load_realty_mart("good", []),
                RuntimeError,
                "missing strict manifest entry",
            )
            _write_manifest(mart_dir, {"good": {"file": str(good)}})
            loaded = da._load_realty_mart("good", [])
            _require("ok" in loaded, "valid mart should load")

            stale = mart_dir / "stale.pkl"
            pd.to_pickle({"stale": pd.DataFrame()}, stale)
            _write_manifest(mart_dir, {
                "good": {"file": str(good)},
                "stale": {"file": str(stale)},
            })
            os.utime(stale, (1, 1))
            _must_raise(
                lambda: da._load_realty_mart("stale", [raw_file]),
                RuntimeError,
                "stale strict mart",
            )

            bad = mart_dir / "bad.pkl"
            bad.write_bytes(b"not a pickle")
            _write_manifest(mart_dir, {
                "good": {"file": str(good)},
                "stale": {"file": str(stale)},
                "bad": {"file": str(bad)},
            })
            _must_raise(
                lambda: da._load_realty_mart("bad", []),
                RuntimeError,
                "bad strict mart",
            )

            error_marked = mart_dir / "error_marked.pkl"
            pd.to_pickle({"old": pd.DataFrame({"x": [1]})}, error_marked)
            _write_manifest(mart_dir, {
                "good": {"file": str(good)},
                "stale": {"file": str(stale)},
                "bad": {"file": str(bad)},
                "error_marked": {
                    "file": str(error_marked),
                    "error": "synthetic",
                },
            })
            _must_raise(
                lambda: da._load_realty_mart("error_marked", []),
                RuntimeError,
                "manifest error strict mart",
            )
            os.environ["PARSER_REQUIRE_REALTY_MARTS"] = "0"
            _require(
                da._load_realty_mart("error_marked", []) is None,
                "manifest error should force fallback in normal mode",
            )
            os.environ["PARSER_REQUIRE_REALTY_MARTS"] = "1"

            os.environ["PARSER_USE_REALTY_MARTS"] = "0"
            _must_raise(
                lambda: da._load_realty_mart("good", []),
                RuntimeError,
                "disabled strict marts",
            )
        finally:
            da.DATA_MARTS_REALTY = original_marts
            if previous_require is None:
                os.environ.pop("PARSER_REQUIRE_REALTY_MARTS", None)
            else:
                os.environ["PARSER_REQUIRE_REALTY_MARTS"] = previous_require
            if previous_use is None:
                os.environ.pop("PARSER_USE_REALTY_MARTS", None)
            else:
                os.environ["PARSER_USE_REALTY_MARTS"] = previous_use

    print("realty mart require checks: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
