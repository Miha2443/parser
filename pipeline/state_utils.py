"""Helpers for resilient parser state files."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def unique_bad_state_path(path: Path) -> Path:
    """Return a non-existing backup path for a corrupt state file."""
    first = path.with_name(f"{path.name}.bad")
    if not first.exists():
        return first
    for i in range(1, 1000):
        candidate = path.with_name(f"{path.name}.{i}.bad")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"cannot allocate bad state backup path for {path}")


def load_json_state(path: Path, *, label: str = "state") -> dict[str, Any]:
    """Load a JSON object state file, quarantining corrupt files."""
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError) as exc:
        bad = unique_bad_state_path(path)
        try:
            path.replace(bad)
            print(f"  WARNING: corrupt {label} state {path} ({exc}); moved to {bad}")
        except OSError:
            print(f"  WARNING: corrupt {label} state {path} ({exc}); continuing with empty state")
        return {}


def write_json_atomic(path: Path, payload: Any) -> None:
    """Write JSON through a validated temporary file and atomic replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    try:
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        json.loads(tmp.read_text(encoding="utf-8"))
        tmp.replace(path)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
