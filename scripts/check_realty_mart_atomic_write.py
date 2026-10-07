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

    original_root = brm.ROOT
    try:
        with tempfile.TemporaryDirectory() as tmp:
            brm.ROOT = Path(tmp)
            raw = brm.ROOT / "data" / "raw" / "realty" / "nashdom"
            raw.mkdir(parents=True)
            for name in (
                "monitoring_2_0_20260702.xlsx.crdownload",
                "rasprodannost_20260702.xlsx.download",
                "kvartirografia_20260702.xlsx.part",
                "manifest.json.tmp",
            ):
                (raw / name).write_bytes(b"partial")
            (raw / "monitoring_2_0_20260702.xlsx").write_bytes(b"complete")

            tmp_names = sorted(path.name for path in brm._tmp_files())
            _require(
                tmp_names == [
                    "kvartirografia_20260702.xlsx.part",
                    "manifest.json.tmp",
                    "monitoring_2_0_20260702.xlsx.crdownload",
                    "rasprodannost_20260702.xlsx.download",
                ],
                "temporary realty download suffixes were not detected",
            )
    finally:
        brm.ROOT = original_root

    print("realty mart atomic write checks: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
