"""Fast checks for atomic realty update status writes."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import update_realty as ur


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "realty_update_status.json"
        ur._write_json_atomic(target, {"status": "old"})
        _require(json.loads(target.read_text(encoding="utf-8"))["status"] == "old", "initial status write failed")

        original_write_text = Path.write_text
        try:
            def write_bad_json(path_self, *args, **kwargs):
                if str(path_self).endswith(".tmp"):
                    return original_write_text(path_self, "{bad-json", encoding="utf-8")
                return original_write_text(path_self, *args, **kwargs)

            Path.write_text = write_bad_json
            try:
                ur._write_json_atomic(target, {"status": "new"})
            except json.JSONDecodeError:
                pass
            else:
                raise AssertionError("bad temporary status should fail")
        finally:
            Path.write_text = original_write_text

        _require(json.loads(target.read_text(encoding="utf-8"))["status"] == "old", "failed write replaced old status")
        _require(not (Path(tmp) / "realty_update_status.json.tmp").exists(), "temporary status was not removed")

    print("realty status atomic write checks: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
