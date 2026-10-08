"""Run a strict live Fedstat 34118 download and save a timestamped log."""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def _latest_part_file(part: str) -> Path | None:
    downloads = ROOT / "downloads"
    if not downloads.exists():
        return None
    files = [
        path
        for path in downloads.glob("*")
        if path.is_file() and f"34118_{part}" in path.name
    ]
    if not files:
        return None
    return max(files, key=lambda path: path.stat().st_mtime)


def _validate_downloaded_parts(log) -> int:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    import fedstat_checker as fc  # noqa: PLC0415

    ok = True
    print("\nPost-check downloaded 34118 files:")
    log.write("\nPost-check downloaded 34118 files:\n")
    for part in ("часть1", "часть2"):
        indicator_id = f"34118_{part}"
        path = _latest_part_file(part)
        if path is None:
            ok = False
            message = f"  ❌ {indicator_id}: file not found in downloads"
        elif fc._validate_34118_file(indicator_id, path):
            message = f"  ✅ {indicator_id}: {path}"
        else:
            ok = False
            message = f"  ❌ {indicator_id}: validation failed for {path}"
        print(message)
        log.write(message + "\n")
    return 0 if ok else 2


def main() -> int:
    log_dir = ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"fedstat_34118_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONUNBUFFERED", "1")

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
        if code == 0:
            post_code = _validate_downloaded_parts(log)
            if post_code != 0:
                code = post_code
                log.write(f"Post-check exit code: {post_code}\n")

    print()
    print(f"Exit code: {code}")
    print(f"Saved log: {log_path}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
