"""Download one IPC part to an isolated folder, without state changes or ETL.

Usage: python scripts/diagnose_fedstat_ipc.py --part 2
The log, export, and download receipt remain together under outputs/.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime
from pathlib import Path
import os
import subprocess
import sys
import traceback

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class Tee:
    def __init__(self, console, log):
        self.console, self.log = console, log

    def write(self, text):
        self.console.write(text)
        self.log.write(text)
        self.flush()
        return len(text)

    def flush(self):
        self.console.flush()
        self.log.flush()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--part", type=int, choices=(1, 2), default=2,
                        help="IPC part; default 2 includes recent years")
    parser.add_argument("--worker-dir", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    folder = args.worker_dir or (ROOT / "outputs" / f"fedstat_ipc_{datetime.now():%Y%m%d_%H%M%S_%f}")
    log_path = folder / "diagnostic.log"
    if args.worker_dir is None:
        folder.mkdir(parents=True, exist_ok=False)
        process = subprocess.Popen([
            sys.executable, "-u", str(Path(__file__).resolve()),
            "--part", str(args.part), "--worker-dir", str(folder),
        ], cwd=ROOT, start_new_session=os.name != "nt")
        try:
            return process.wait(timeout=180)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            # Kill only this diagnostic's process tree, including its Chrome.
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               capture_output=True, timeout=15,
                               creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                import signal
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=15)
            message = "STOPPED: diagnostic exceeded 180 seconds or was interrupted; no success confirmed."
            print(message)
            with log_path.open("a", encoding="utf-8") as log:
                log.write(message + "\n")
            print(f"Log: {log_path}")
            return 2

    import fedstat_checker as fc
    source = f"31074_часть{args.part}"
    with log_path.open("w", encoding="utf-8") as log, \
            redirect_stdout(Tee(sys.stdout, log)), redirect_stderr(Tee(sys.stderr, log)):
        print(f"Source: {source}; page: https://www.fedstat.ru/indicator/31074")
        print(f"Log: {log_path}")
        try:
            revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                      capture_output=True, text=True, timeout=5).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            revision = ""
        print(f"Checkout: {ROOT}; Git HEAD: {revision or 'unavailable'}")
        print("One export attempt with transport fallbacks; no state updates, ETL or notifications.")
        driver = None
        try:
            driver = fc.create_driver(download_dir=folder)
            date = fc.get_last_update_date(driver, source)
            print(f"Remote passport date: {date or 'unavailable'}")
            path = fc.download_excel(source, folder, remote_date=date, driver=driver)
            if path is None:
                print("FAILED: no verified Excel file downloaded.")
                return 2
            from pipeline.parsers.fedstat_ipc import parse
            frame = parse(path)
            if frame.empty:
                print("FAILED: Excel contains no parseable IPC observations.")
                return 2
            latest = frame[["year", "month"]].sort_values(["year", "month"]).iloc[-1]
            print(f"IPC observations: {len(frame)}; latest period: {int(latest['year'])}-{int(latest['month']):02d}")
            print(f"SUCCESS: {path}; {path.stat().st_size} bytes")
            return 0
        except Exception:
            traceback.print_exc()
            return 2
        finally:
            if driver is not None:
                driver.quit()


if __name__ == "__main__":
    raise SystemExit(main())
