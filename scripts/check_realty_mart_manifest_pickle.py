"""Fast checks for realty mart pickle validation."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.build_realty_marts import _pickle_load_error  # noqa: E402


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        good = base / "good.pkl"
        bad = base / "bad.pkl"
        pd.to_pickle({"frame": pd.DataFrame({"x": [1]})}, good)
        bad.write_bytes(b"not a pickle")

        _require(_pickle_load_error(good) is None, "valid pickle should pass")
        _require(_pickle_load_error(bad) is not None, "bad pickle should fail")

    print("realty mart pickle checks: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
