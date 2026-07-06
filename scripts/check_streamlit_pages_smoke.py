"""Execute Streamlit pages in bare mode to catch top-level runtime errors.

The check runs each page in a separate Python process. This keeps Streamlit
state isolated and avoids starting a browser or server during validation.
"""
from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
APP_ROOT = ROOT / "app"
TIMEOUT_SEC = 60
TAIL_LINES = 45


@dataclass(frozen=True)
class PageResult:
    path: Path
    returncode: int | None
    output: str
    timed_out: bool = False


def _pages() -> list[Path]:
    pages = [APP_ROOT / "Home.py"]
    pages.extend(sorted((APP_ROOT / "pages").glob("*.py")))
    return [page for page in pages if page.exists()]


def _tail(text: str, lines: int = TAIL_LINES) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return ""
    return "\n".join(normalized.splitlines()[-lines:])


def _run_page(path: Path) -> PageResult:
    env = os.environ.copy()
    pythonpath = str(ROOT)
    if env.get("PYTHONPATH"):
        pythonpath += os.pathsep + env["PYTHONPATH"]
    env.update(
        {
            "PYTHONPATH": pythonpath,
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
            "PARSER_USE_REALTY_MARTS": "1",
        }
    )
    try:
        completed = subprocess.run(
            [sys.executable, str(path)],
            cwd=ROOT,
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=TIMEOUT_SEC,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        output = "\n".join(
            part
            for part in [
                exc.stdout.decode("utf-8", "replace")
                if isinstance(exc.stdout, bytes)
                else exc.stdout,
                exc.stderr.decode("utf-8", "replace")
                if isinstance(exc.stderr, bytes)
                else exc.stderr,
            ]
            if part
        )
        return PageResult(path=path, returncode=None, output=output, timed_out=True)

    return PageResult(
        path=path,
        returncode=completed.returncode,
        output="\n".join(part for part in [completed.stdout, completed.stderr] if part),
    )


def main() -> int:
    failures: list[PageResult] = []
    for page in _pages():
        result = _run_page(page)
        if result.timed_out or result.returncode:
            failures.append(result)

    if failures:
        print("streamlit page smoke: failed")
        for failure in failures:
            rel = failure.path.relative_to(ROOT)
            status = "timeout" if failure.timed_out else f"exit {failure.returncode}"
            print(f"\n--- {rel} ({status}) ---")
            tail = _tail(failure.output)
            print(tail or "<no output>")
        return 1

    print(f"streamlit page smoke: ok ({len(_pages())} pages)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
