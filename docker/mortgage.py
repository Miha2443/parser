"""Independent mortgage collection and admission into immutable releases.

Mortgage is a new supplemental source, not a new prerequisite for serving the
eight existing providers. Failure never authorizes a claim of provider outage.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys


RELATIVE = Path("data/raw/realty/nashdom/mortgage_history.json")


def _bounded_run(command, *, cwd, check, timeout):
    process = subprocess.Popen(command, cwd=cwd, start_new_session=os.name != "nt")
    try:
        code = process.wait(timeout=timeout)
    except BaseException:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)],
                           check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if process.poll() is None:
            process.kill()
        process.wait()
        raise
    if check and code:
        raise subprocess.CalledProcessError(code, command)
    return subprocess.CompletedProcess(command, code)


def collect(root, *, runner=None):
    runner = runner or _bounded_run
    try:
        result = runner([sys.executable, "-u", "-m", "pipeline.downloaders.nashdom_mortgage"],
                        cwd=Path(root), check=False,
                        timeout=int(os.environ.get("MORTGAGE_UPDATE_TIMEOUT", "1800")))
        status = {"status": {0: "success", 2: "partial"}.get(result.returncode, "failed"),
                  "returncode": result.returncode}
    except subprocess.TimeoutExpired:
        status = {"status": "failed", "reason": "collection_timeout"}
    except (OSError, ValueError) as exc:
        status = {"status": "failed", "reason": "collector_start_failed", "errorClass": type(exc).__name__}
    status["checkedAt"] = datetime.now(timezone.utc).isoformat()
    print("Mortgage supplemental collection: " + str(status), flush=True)
    return status


def _path(root):
    root = Path(root)
    path = root / RELATIVE
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise RuntimeError("Unsafe mortgage source path")
    return path


def _verified(root):
    from backend.mortgage_service import MortgageService
    from pipeline.data_access import DataContext
    path = _path(root)
    if not path.is_file():
        return None
    service = MortgageService(DataContext(Path(root), use_marts=False))
    catalog = service.catalog()
    if {row["id"] for row in catalog["regions"] if row["periods"]} != {"rf", "msk"}:
        raise ValueError("Mortgage source lacks Russia or Moscow history")
    for region in ("rf", "msk"):
        monthly = service.report(region=region)["monthly"]
        if not any(value is not None for row in monthly for segment in ("all", "primary", "secondary")
                   for value in row[segment].values()):
            raise ValueError("Mortgage source has no measured values for " + region)
    return {"path": path, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def select_source(candidate, previous=None):
    """Use a verified current history, otherwise the last verified snapshot.

    Only the fixed mortgage file inside the private candidate may be replaced
    or removed. Working data and previous immutable releases are never edited.
    """
    selected, origin, error = None, None, None
    for name, root in (("current", candidate), ("previous", previous)):
        if root is None:
            continue
        try:
            verified = _verified(root)
        except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
            error = type(exc).__name__
            continue
        if verified:
            selected, origin = verified, name
            break
    destination = _path(candidate)
    if selected and origin == "previous":
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(selected["path"], destination)
    elif selected is None:
        destination.unlink(missing_ok=True)
    if selected:
        return {"state": "available" if origin == "current" else "retained", "selection": origin,
                "file": RELATIVE.as_posix(), "sha256": selected["sha256"],
                "mtime_ns": destination.stat().st_mtime_ns,
                "reason": "verified_mortgage_history" if origin == "current" else "previous_verified_history",
                "currentError": error}
    return {"state": "unavailable", "selection": None, "file": None,
            "reason": "mortgage_history_missing_or_invalid", "currentError": error}


def verify_snapshot(root, declaration):
    if not isinstance(declaration, dict) or declaration.get("state") not in {"available", "retained", "unavailable"}:
        raise RuntimeError("Invalid mortgage publication declaration")
    if declaration.get("state") == "unavailable":
        if _path(root).exists():
            raise RuntimeError("Unavailable mortgage source unexpectedly contains a history file")
        return
    verified = _verified(root)
    if (not verified or declaration.get("file") != RELATIVE.as_posix()
            or verified["sha256"] != declaration.get("sha256")
            or verified["path"].stat().st_mtime_ns != declaration.get("mtime_ns")):
        raise RuntimeError("Published mortgage history differs from its verified source")
