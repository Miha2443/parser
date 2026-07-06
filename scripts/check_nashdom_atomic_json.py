"""Fast checks for atomic JSON writes in nashdom checker."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import nashdom_checker as nc  # noqa: E402


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    original_state = nc.STATE_FILE
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        target = base / "checkpoint.json"
        nc._write_json_atomic(target, [{"version": 1}])
        _require(json.loads(target.read_text(encoding="utf-8"))[0]["version"] == 1, "initial JSON write failed")

        original_write_text = Path.write_text
        try:
            def write_bad_json(path_self, *args, **kwargs):
                if str(path_self).endswith(".tmp"):
                    return original_write_text(path_self, "{bad-json", encoding="utf-8")
                return original_write_text(path_self, *args, **kwargs)

            Path.write_text = write_bad_json
            try:
                nc._write_json_atomic(target, [{"version": 2}])
            except json.JSONDecodeError:
                pass
            else:
                raise AssertionError("bad temporary JSON should fail")
        finally:
            Path.write_text = original_write_text

        _require(json.loads(target.read_text(encoding="utf-8"))[0]["version"] == 1, "failed write replaced old JSON")
        _require(not (base / "checkpoint.json.tmp").exists(), "temporary JSON was not removed")

        try:
            nc.STATE_FILE = base / "state" / "nashdom_state.json"
            nc.save_state({"ok": True})
            _require(nc.load_state() == {"ok": True}, "save_state/load_state should round-trip")
        finally:
            nc.STATE_FILE = original_state

    print("nashdom atomic JSON checks: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
