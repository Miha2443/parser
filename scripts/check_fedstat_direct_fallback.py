"""Fast check for fedstat direct Excel fallback when the indicator page hangs."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import fedstat_checker as fc  # noqa: E402


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class DummyDriver:
    def __init__(self) -> None:
        self.closed = False

    def quit(self) -> None:
        self.closed = True


def main() -> int:
    original_download_dir = fc.DOWNLOAD_DIR
    original_state_file = fc.STATE_FILE
    original_indicators = fc.INDICATORS
    original_create_driver = fc.create_driver
    original_get_last_update_date = fc.get_last_update_date
    original_download_excel = fc.download_excel
    original_direct = fc.DIRECT_DOWNLOAD_ON_DATE_FAILURE

    try:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fc.DOWNLOAD_DIR = root / "downloads"
            fc.STATE_FILE = root / "fedstat_state.json"
            fc.INDICATORS = {"57824": "salary"}
            fc.DIRECT_DOWNLOAD_ON_DATE_FAILURE = True

            driver = DummyDriver()
            fc.create_driver = lambda download_dir=None: driver
            fc.get_last_update_date = lambda _driver, _indicator_id: None

            def fake_download(indicator_id, save_dir, *, remote_date=None, driver=None):
                _require(indicator_id == "57824", "fallback should download requested indicator")
                _require(remote_date is None, "fallback should not invent a remote passport date")
                _require(driver is not None, "fallback should pass browser cookies")
                save_dir.mkdir(parents=True, exist_ok=True)
                path = save_dir / "57824_direct.xls"
                path.write_bytes(b"xls")
                return path

            fc.download_excel = fake_download

            files, ok = fc.run(force=False)

            _require(ok, "direct fallback should make fedstat run successful")
            _require(len(files) == 1, "direct fallback should return downloaded file")
            _require(files[0].name == "57824_direct.xls", "unexpected fallback file")
            _require(driver.closed, "driver should be closed")
    finally:
        fc.DOWNLOAD_DIR = original_download_dir
        fc.STATE_FILE = original_state_file
        fc.INDICATORS = original_indicators
        fc.create_driver = original_create_driver
        fc.get_last_update_date = original_get_last_update_date
        fc.download_excel = original_download_excel
        fc.DIRECT_DOWNLOAD_ON_DATE_FAILURE = original_direct

    print("fedstat direct fallback checks: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
