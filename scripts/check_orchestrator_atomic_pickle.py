"""Fast checks for atomic processed pickle writes in the orchestrator."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline import orchestrator  # noqa: E402


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "processed.pkl"
        orchestrator._write_pickle_atomic(pd.DataFrame({"version": [1]}), target)
        _require(pd.read_pickle(target).loc[0, "version"] == 1, "initial processed pickle write failed")

        original_to_pickle = orchestrator.pd.to_pickle
        try:
            def write_bad_temp(value, path):
                Path(path).write_bytes(b"not a pickle")

            orchestrator.pd.to_pickle = write_bad_temp
            try:
                orchestrator._write_pickle_atomic(pd.DataFrame({"version": [2]}), target)
            except Exception:  # noqa: BLE001
                pass
            else:
                raise AssertionError("bad temporary pickle should fail")
        finally:
            orchestrator.pd.to_pickle = original_to_pickle

        _require(pd.read_pickle(target).loc[0, "version"] == 1, "failed write replaced old processed pickle")
        _require(not (Path(tmp) / "processed.pkl.tmp").exists(), "temporary processed pickle was not removed")

    print("orchestrator atomic pickle checks: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
