"""Fast smoke-checks for realty dashboard data loaders.

This validates that the current app loaders can read the materialized marts
and return non-empty structures for the heavy dashboard pages. It does not
start Streamlit, Selenium, or download anything.
"""
from __future__ import annotations

import logging
import os
import contextlib
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ["PARSER_REQUIRE_REALTY_MARTS"] = "1"

logging.getLogger("streamlit").setLevel(logging.ERROR)
logging.getLogger("streamlit.runtime.caching.cache_data_api").setLevel(logging.ERROR)
logging.disable(logging.CRITICAL)

from app import data_access as da  # noqa: E402
from app.audit import realty_marts_status  # noqa: E402


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _load(fn):
    with contextlib.redirect_stderr(io.StringIO()):
        return fn()


def main() -> int:
    marts = _load(realty_marts_status)
    _require(not marts.empty, "realty marts manifest is empty or missing")
    bad = marts[marts["status"].isin(["error", "stale"])]
    _require(bad.empty, "bad realty marts: " + ", ".join(bad["mart"].astype(str)))

    monitoring = _load(da.load_monitoring_2_0)
    _require(len(monitoring.get("rv", [])) > 0, "monitoring rv is empty")
    _require(len(monitoring.get("oks", [])) > 0, "monitoring oks is empty")

    kvart = _load(da.load_kvartirografia)
    _require(len(kvart.get("developers", [])) > 0, "kvartirografia developers is empty")

    rasprod = _load(da.load_rasprodannost)
    _require(len(rasprod.get("periods") or []) > 0, "rasprodannost periods is empty")
    _require(len(rasprod.get("developers", [])) > 0, "rasprodannost developers is empty")

    erz_top = _load(da.load_erzrf_top)
    _require(bool(erz_top.get("obyem_stroitelstva")), "erzrf top construction data is empty")

    cards = _load(da.load_erzrf_cards)
    _require(len(cards) > 0, "erzrf cards are empty")

    print("realty marts smoke: ok")
    print(
        "rows:",
        f"monitoring.rv={len(monitoring.get('rv', []))}",
        f"kvart.developers={len(kvart.get('developers', []))}",
        f"rasprod.developers={len(rasprod.get('developers', []))}",
        f"erz.cards={len(cards)}",
    )
    return 0


if __name__ == "__main__":
    with contextlib.redirect_stderr(io.StringIO()):
        raise SystemExit(main())
