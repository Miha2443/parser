"""Smoke-check the offline realty ETL path without touching data/processed."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline import orchestrator  # noqa: E402
from pipeline.audit import AuditRun  # noqa: E402
from pipeline.registry import INDICATORS  # noqa: E402


EXPECTED_MIN_ROWS = {
    "realty_monitoring_2_0": 1000,
    "realty_rasprodannost": 1000,
    "realty_kvartirografia": 100,
    "realty_erzrf_top_rf": 100,
    "realty_erzrf_top_msk": 100,
    "realty_erzrf_cards": 100,
    "realty_escrow_manual": 100,
}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    original_processed = orchestrator.DATA_PROCESSED
    try:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            processed = tmp_root / "processed"
            processed.mkdir()
            orchestrator.DATA_PROCESSED = processed
            audit = AuditRun(path=tmp_root / "audit.jsonl")

            indicators = [ind for ind in INDICATORS if ind.id in EXPECTED_MIN_ROWS]
            _require(len(indicators) == len(EXPECTED_MIN_ROWS), "not all expected realty indicators are registered")

            for indicator in indicators:
                orchestrator._process_one(indicator, audit, download=False)
            summary = audit.finalize()
            _require(summary["error"] == 0, f"offline realty ETL had {summary['error']} error(s)")
            _require(summary["skip"] == 0, f"offline realty ETL had {summary['skip']} skipped indicator(s)")
            _require(summary["success"] == len(EXPECTED_MIN_ROWS), "offline realty ETL did not process every indicator")

            for indicator_id, min_rows in EXPECTED_MIN_ROWS.items():
                path = processed / f"{indicator_id}.pkl"
                _require(path.exists(), f"missing processed pickle: {path.name}")
                frame = pd.read_pickle(path)
                _require(len(frame) >= min_rows, f"{path.name} has too few rows: {len(frame)}")
    finally:
        orchestrator.DATA_PROCESSED = original_processed

    print("orchestrator realty offline smoke: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
