"""Fast checks for fedstat/rosstat state recovery."""
from __future__ import annotations

import sys
import tempfile
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import fedstat_checker as fc  # noqa: E402
import rosstat_checker as rc  # noqa: E402
import update_realty as ur  # noqa: E402


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _check_checker_state(module, filename: str, payload: dict) -> None:
    original_state = module.STATE_FILE
    try:
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / filename
            module.STATE_FILE = state_file

            module.save_state(payload)
            _require(module.load_state() == payload, f"{filename} should round-trip")

            original_write_text = Path.write_text
            try:
                def write_bad_json(path_self, *args, **kwargs):
                    if str(path_self).endswith(".tmp"):
                        return original_write_text(path_self, "{bad-json", encoding="utf-8")
                    return original_write_text(path_self, *args, **kwargs)

                Path.write_text = write_bad_json
                try:
                    module.save_state({"broken": True})
                except ValueError:
                    pass
                else:
                    raise AssertionError(f"{filename} bad temporary write should fail")
            finally:
                Path.write_text = original_write_text

            _require(module.load_state() == payload, f"{filename} failed write should keep old state")
            _require(not (state_file.parent / f"{filename}.tmp").exists(), f"{filename}.tmp should be removed")

            state_file.write_text("{bad-json", encoding="utf-8")
            with redirect_stdout(StringIO()):
                recovered = module.load_state()
            _require(recovered == {}, f"{filename} should recover corrupt JSON as empty dict")
            _require(not state_file.exists(), f"{filename} corrupt state should be moved aside")
            _require((state_file.parent / f"{filename}.bad").is_file(), f"{filename}.bad backup missing")

            (state_file.parent / f"{filename}.bad").write_text("previous bad", encoding="utf-8")
            state_file.write_text("{bad-json-again", encoding="utf-8")
            with redirect_stdout(StringIO()):
                recovered = module.load_state()
            _require(recovered == {}, f"{filename} second corrupt JSON should recover")
            _require(
                (state_file.parent / f"{filename}.bad").read_text(encoding="utf-8") == "previous bad",
                f"{filename}.bad should not be overwritten",
            )
            _require((state_file.parent / f"{filename}.1.bad").is_file(), f"{filename}.1.bad backup missing")
    finally:
        module.STATE_FILE = original_state


def _check_update_realty_site_dates() -> None:
    original_root = ur.ROOT
    try:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ur.ROOT = root
            (root / "state").mkdir()
            (root / "fedstat_state.json").write_text("{bad-json", encoding="utf-8")
            (root / "rosstat_state.json").write_text(
                '{"vvp_god": {"date": "01.02.2026", "filename": "vvp.xlsx"}}',
                encoding="utf-8",
            )

            with redirect_stdout(StringIO()):
                site_dates = ur.collect_site_dates()

            _require("fedstat" not in site_dates, "corrupt fedstat state should be omitted from summary")
            _require(
                site_dates.get("rosstat", {}).get("vvp_god", {}).get("date") == "01.02.2026",
                "valid rosstat state should stay in summary",
            )
            _require((root / "fedstat_state.json.bad").is_file(), "update_realty should quarantine bad fedstat state")
    finally:
        ur.ROOT = original_root


def main() -> int:
    _check_checker_state(fc, "fedstat_state.json", {"33648": "01.02.2026"})
    _check_checker_state(
        rc,
        "rosstat_state.json",
        {"vvp_god": {"date": "01.02.2026", "filename": "vvp.xlsx"}},
    )
    _check_update_realty_site_dates()
    print("stat state recovery checks: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
