"""Run a strict live Fedstat 34118 download and save a timestamped log."""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    log_dir = ROOT / "_to_delete"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"fedstat_34118_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")

    cmd = [
        sys.executable,
        str(ROOT / "fedstat_checker.py"),
        "--only=34118",
        "--force",
    ]

    print(f"Log: {log_path}")
    print("Command:", " ".join(cmd))
    print()

    with log_path.open("w", encoding="utf-8", errors="replace") as log:
        log.write(f"Log: {log_path}\n")
        log.write("Command: " + " ".join(cmd) + "\n\n")
        log.flush()

        proc = subprocess.Popen(
            cmd,
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            print(line, end="")
            log.write(line)
        code = proc.wait()
        log.write(f"\nExit code: {code}\n")

    print()
    print(f"Exit code: {code}")
    print(f"Saved log: {log_path}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
