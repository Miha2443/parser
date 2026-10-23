"""Optional preferential mortgage collection and immutable-source selection."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from docker.mortgage import _bounded_run
from pipeline.downloaders.domrf_preferential import freshness


RELATIVE = Path("data/raw/realty/domrf/preferential_mortgage.json")


def collect(root, *, runner=None):
    runner = runner or _bounded_run
    try:
        result = runner([sys.executable, "-u", "-m", "pipeline.downloaders.domrf_preferential"],
                        cwd=Path(root), check=False,
                        timeout=int(os.environ.get("PREFERENTIAL_MORTGAGE_UPDATE_TIMEOUT", "420")))
        status = {"status": {0: "success", 2: "retained"}.get(result.returncode, "failed"),
                  "returncode": result.returncode}
    except subprocess.TimeoutExpired:
        status = {"status": "failed", "reason": "collection_timeout"}
    except (OSError, ValueError) as exc:
        status = {"status": "failed", "reason": "collector_start_failed", "errorClass": type(exc).__name__}
    status["checkedAt"] = datetime.now(timezone.utc).isoformat()
    print("Preferential mortgage supplemental collection: " + str(status), flush=True)
    return status


def _path(root):
    root = Path(root)
    path = root / RELATIVE
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise RuntimeError("Unsafe preferential mortgage source path")
    return path


def _verified(root):
    path = _path(root)
    if not path.is_file():
        return None
    from pipeline.preferential_mortgage import validate_source
    payload = path.read_bytes()
    document = json.loads(payload)
    validate_source(document)
    return {"path": path, "sha256": hashlib.sha256(payload).hexdigest(),
            "freshness": freshness(document), "sourceDate": document["sourceDate"]}


def select_source(candidate, previous=None):
    """Retain a newer verified release when an old attachment/seed is present."""
    selected, origin, error = None, None, None
    for name, root in (("current", candidate), ("previous", previous)):
        if root is None:
            continue
        try:
            verified = _verified(root)
        except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
            if name == "current":
                error = type(exc).__name__
            continue
        if verified and (selected is None or verified["freshness"] > selected["freshness"]):
            selected, origin = verified, name
    destination = _path(candidate)
    if selected and origin == "previous":
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(selected["path"], destination)
    elif selected is None:
        destination.unlink(missing_ok=True)
    if selected:
        return {"state": "available" if origin == "current" else "retained", "selection": origin,
                "file": RELATIVE.as_posix(), "sha256": selected["sha256"],
                "mtime_ns": destination.stat().st_mtime_ns, "sourceDate": selected["sourceDate"],
                "reason": "verified_preferential_mortgage" if origin == "current" else "previous_verified_source",
                "currentError": error}
    return {"state": "unavailable", "selection": None, "file": None,
            "reason": "preferential_mortgage_missing_or_invalid", "currentError": error}


def verify_snapshot(root, declaration):
    if not isinstance(declaration, dict) or declaration.get("state") not in {"available", "retained", "unavailable"}:
        raise RuntimeError("Invalid preferential mortgage publication declaration")
    if declaration["state"] == "unavailable":
        if _path(root).exists():
            raise RuntimeError("Unavailable preferential mortgage source unexpectedly exists")
        return
    verified = _verified(root)
    if (not verified or declaration.get("file") != RELATIVE.as_posix()
            or verified["sha256"] != declaration.get("sha256")
            or verified["path"].stat().st_mtime_ns != declaration.get("mtime_ns")
            or verified["sourceDate"] != declaration.get("sourceDate")):
        raise RuntimeError("Published preferential mortgage differs from its verified source")
