"""Compile project Python entrypoints used by the dashboard/update flow.

This is intentionally lightweight: it catches syntax errors and invalid module
files without importing Streamlit pages or starting network/Selenium work.
"""
from __future__ import annotations

import compileall
import py_compile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent

DIRS = [
    ROOT / "app",
    ROOT / "pipeline",
]

FILES = [
    ROOT / "fedstat_checker.py",
    ROOT / "nashdom_checker.py",
    ROOT / "rosstat_checker.py",
    ROOT / "erzrf_checker.py",
]


def main() -> int:
    ok = True
    for directory in DIRS:
        if directory.exists():
            ok = compileall.compile_dir(
                directory,
                quiet=1,
                force=False,
                maxlevels=10,
            ) and ok

    for path in sorted((ROOT / "scripts").glob("*.py")) + FILES:
        if not path.exists():
            continue
        try:
            py_compile.compile(str(path), doraise=True)
        except py_compile.PyCompileError as exc:
            ok = False
            print(f"compile error: {path.relative_to(ROOT)}")
            print(exc.msg)

    if not ok:
        return 1
    print("python compile checks: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
