"""Fast checks for atomic realty mart manifest writes."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline import build_realty_marts as brm  # noqa: E402


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "manifest.json"
        brm._write_json_atomic({"version": 1}, target)
        _require(json.loads(target.read_text(encoding="utf-8"))["version"] == 1, "initial manifest write failed")

        original_write_text = Path.write_text
        try:
            def write_bad_json(path_self, *args, **kwargs):
                if str(path_self).endswith(".tmp"):
                    return original_write_text(path_self, "{bad-json", encoding="utf-8")
                return original_write_text(path_self, *args, **kwargs)

            Path.write_text = write_bad_json
            try:
                brm._write_json_atomic({"version": 2}, target)
            except json.JSONDecodeError:
                pass
            else:
                raise AssertionError("bad temporary manifest should fail")
        finally:
            Path.write_text = original_write_text

        _require(json.loads(target.read_text(encoding="utf-8"))["version"] == 1, "failed write replaced old manifest")
        _require(not (Path(tmp) / "manifest.json.tmp").exists(), "temporary manifest was not removed")

    print("realty manifest atomic write checks: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
