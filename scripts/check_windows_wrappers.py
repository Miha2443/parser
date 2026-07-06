"""Validate Windows batch wrappers without executing their payloads."""
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent

UTF8_WRAPPERS = [
    ROOT / "setup.bat",
    ROOT / "start.bat",
    ROOT / "tdm_test.bat",
    ROOT / "update.bat",
    ROOT / "scripts" / "update_all.bat",
    ROOT / "scripts" / "update_realty.bat",
    ROOT / "scripts" / "update_realty_scheduled.bat",
    ROOT / "scripts" / "validate_realty_dashboard.bat",
]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def main() -> int:
    failures: list[str] = []
    for path in UTF8_WRAPPERS:
        rel = path.relative_to(ROOT)
        if not path.is_file():
            failures.append(f"{rel}: missing")
            continue
        text = _read(path)
        lower = text.lower()
        if 'set "pythonioencoding=utf-8"' not in lower:
            failures.append(f"{rel}: missing PYTHONIOENCODING=utf-8")
        if 'set "pythonutf8=1"' not in lower:
            failures.append(f"{rel}: missing PYTHONUTF8=1")
        if "cd /d %~dp0" in lower:
            failures.append(f"{rel}: unquoted cd /d %~dp0")

    if failures:
        for failure in failures:
            print(f"ERROR: {failure}")
        return 1
    print("windows wrapper checks: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
