"""Fast checks for atomic realty mart pickle writes."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline import build_realty_marts as brm  # noqa: E402


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "mart.pkl"
        brm._write_pickle_atomic({"version": 1}, target)
        _require(pd.read_pickle(target)["version"] == 1, "initial atomic write failed")

        original_to_pickle = brm.pd.to_pickle
        try:
            def write_bad_temp(value, path):
                Path(path).write_bytes(b"not a pickle")

            brm.pd.to_pickle = write_bad_temp
            try:
                brm._write_pickle_atomic({"version": 2}, target)
            except RuntimeError:
                pass
            else:
                raise AssertionError("bad temporary pickle should fail")
        finally:
            brm.pd.to_pickle = original_to_pickle

        _require(pd.read_pickle(target)["version"] == 1, "failed write replaced old mart")
        _require(not (Path(tmp) / "mart.pkl.tmp").exists(), "temporary pickle was not removed")

    print("realty mart atomic write checks: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
