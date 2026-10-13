"""Copy, validate and atomically publish immutable data for Compose.

Only updater writes /runtime. Host update.sh serializes publication and restart.
Old releases are retained; archive moves require proof of the backend's live release.
"""
from __future__ import annotations

import argparse
import hashlib
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

TREES = ("downloads", "data/processed", "data/marts", "data/derived",
         "data/raw/realty", "logs")
from docker.coverage import SOURCES
EXCLUDED = {"_archive", "archive", "archives", "__pycache__", "temp", "tmp",
            ".fedstat_resume", "_debug"}


@contextmanager
def mutation_lock(root):
    import fcntl  # Updater runs on Linux; lock spans the complete download/build/copy.
    (root / "state").mkdir(parents=True, exist_ok=True)
    with (root / "state/.container-publication.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def copy_tree(source: Path, target: Path, *, seed=False):
    target.mkdir(parents=True, exist_ok=True)
    if not source.exists():
        return
    for item in source.iterdir():
        if (item.name.casefold() in EXCLUDED or item.name.startswith(("~$", ".source-staging-"))
                or item.suffix.casefold() in {".tmp", ".part", ".crdownload", ".download"}):
            continue
        if item.is_symlink():
            raise RuntimeError(f"Refusing mutable data symlink: {item}")
        destination = target / item.name
        if item.is_dir():
            copy_tree(item, destination, seed=seed)
        elif not seed or not destination.exists():
            shutil.copy2(item, destination)
            if item.stat().st_mtime_ns != destination.stat().st_mtime_ns:
                raise RuntimeError(f"Filesystem cannot preserve nanosecond timestamps: {item}")


def seed(root: Path, source=Path("/opt/parser-seed")):
    for directory in ("downloads", "data", "state", "logs"):
        (root / directory).mkdir(parents=True, exist_ok=True)
    copy_tree(source / "data", root / "data", seed=True)


def read_status(root):
    try:
        return json.loads((root / "data/processed/realty_update_status.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def require_success(status, previous_id, started):
    if (not status.get("run_id") or status["run_id"] == previous_id
            or status.get("status") != "success" or not status.get("finished_at")
            or status.get("current_stage") != "finished"
            or set(status.get("sources_requested", [])) != SOURCES
            or set(status.get("successes", [])) != SOURCES
            or status.get("failures") or status.get("status_write_errors")
            or status.get("error") or status.get("marts_ok") is not True
            or status.get("processed_ok") is not True):
        raise RuntimeError("Current complete all-source run did not succeed; refusing publication")
    if datetime.fromisoformat(status["started_at"]).timestamp() < started - 2:
        raise RuntimeError("Update status predates this invocation")


def validate(root):
    # Fresh subprocesses are essential: pipeline.paths is initialized at import.
    environment = {**os.environ, "PARSER_ROOT": str(root),
                   "PARSER_DOWNLOADS": str(root / "downloads"),
                   "PARSER_REQUIRE_REALTY_MARTS": "1", "PARSER_USE_REALTY_MARTS": "1"}
    subprocess.run([sys.executable, "-m", "pipeline.build_realty_marts", "--check", "--strict"],
                   env=environment, check=True)
    subprocess.run([sys.executable, "-m", "docker.publication", "validate-api"],
                   env=environment, check=True)


def validate_api():
    from pipeline.data_access import DataAccess, DataContext
    from docker.coverage import validate_sources, validate_pages
    from fastapi.testclient import TestClient
    from backend.app import create_app
    access = DataAccess(DataContext.from_environment())
    validate_sources(access)
    with TestClient(create_app()) as client:
        def get(route):
            response = client.get(route)
            if response.status_code != 200:
                raise RuntimeError(f"Candidate API failed: {route}: {response.status_code}")
            return response.json()
        validate_pages(get)


def live_target(runtime):
    live = runtime / "live"
    if not live.is_symlink():
        if live.exists():
            raise RuntimeError("runtime/live must be a relative symlink")
        return ""
    target = os.readlink(live)
    require_target(runtime, target)
    return target


def require_target(runtime, target):
    path = Path(target)
    releases = runtime / "releases"
    selected = runtime / path
    if (path.is_absolute() or len(path.parts) != 2 or path.parts[0] != "releases"
            or path.parts[1] in {"..", "."} or not re.fullmatch(r"[A-Za-z0-9_.-]+", path.parts[1]) or releases.is_symlink()
            or selected.is_symlink() or not selected.is_dir()
            or selected.resolve().parent != releases.resolve()):
        raise RuntimeError("Unsafe release target")


def swap(runtime, target):
    live_target(runtime)
    if target:
        require_target(runtime, target)
        temporary = runtime / (".live-" + uuid.uuid4().hex)
        temporary.symlink_to(target, target_is_directory=True)
        os.replace(temporary, runtime / "live")
    elif (runtime / "live").is_symlink():
        (runtime / "live").unlink()


def publish(root, runtime):
    runtime.mkdir(parents=True, exist_ok=True)
    previous = live_target(runtime)
    if (runtime / "pending.json").exists():
        raise RuntimeError("Unfinished restart transaction; run docker/update.sh for recovery")
    releases = runtime / "releases"
    releases.mkdir(exist_ok=True)
    identifier = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:12]
    candidate = releases / (".building-" + identifier)
    candidate.mkdir()
    try:
        for relative in TREES:
            copy_tree(root / relative, candidate / relative)
        validate(candidate)
        final = releases / identifier
        candidate.rename(final)
        # Persist recovery metadata before switching, never prune an old release.
        temporary = runtime / ".pending-next"
        temporary.write_text(json.dumps({"previous": previous, "next": f"releases/{identifier}"}), encoding="utf-8")
        os.replace(temporary, runtime / "pending.json")
        swap(runtime, f"releases/{identifier}")
        print(f"Published releases/{identifier}; backend restart pending", flush=True)
    finally:
        if candidate.exists():
            shutil.rmtree(candidate)


def recovery_info(runtime):
    """Read and validate every transaction boundary before any recovery mutation."""
    pending = runtime / "pending.json"
    if pending.is_symlink():
        raise RuntimeError("Recovery journal must be a regular file")
    if not pending.exists():
        return None
    record = json.loads(pending.read_text(encoding="utf-8"))
    if (not isinstance(record, dict) or set(record) != {"previous", "next"}
            or not isinstance(record["previous"], str) or not isinstance(record["next"], str)
            or record["previous"] == record["next"]):
        raise RuntimeError("Invalid recovery journal; inspect runtime/pending.json")
    require_target(runtime, record["next"])
    if record["previous"]:
        require_target(runtime, record["previous"])
    live = live_target(runtime)
    if live not in {record["next"], record["previous"]}:
        raise RuntimeError("Recovery journal does not match live release; no mutation allowed")
    return {**record, "live": live}


def transaction(runtime, action):
    record = recovery_info(runtime)
    if record is None:
        return
    pending = runtime / "pending.json"
    if action == "rollback":
        if not record["previous"]:
            raise RuntimeError("No previous immutable release; refusing destructive rollback")
        swap(runtime, record["previous"])
        return  # Keep journal until the restored backend becomes ready.
    if action == "activate-next":
        swap(runtime, record["next"])
        return
    if action != "ack":
        raise RuntimeError("Unknown recovery action")
    if not record["live"]:
        raise RuntimeError("Cannot acknowledge an unpublished bootstrap release")
    if live_target(runtime) == record["next"]:
        # Retain the explicit rollback reserve even if newer failed releases exist.
        from docker.release_history import _write_record
        _write_record(runtime / "previous-good.json", {
            "current": record["next"], "previous": record["previous"],
        })
    pending.unlink()


def run_action(action, root, runtime):
    if action == "archive":
        from docker.release_history import archive_releases
        archive_releases(runtime, keep=int(os.environ.get("RELEASE_KEEP", "3")))
    elif action == "recovery-info":
        record = recovery_info(runtime)
        if record is None:
            raise RuntimeError("No recovery journal")
        # Validated tokens only; '-' represents the empty bootstrap predecessor.
        print(" ".join(record[key] or "-" for key in ("previous", "next", "live")), flush=True)
    elif action in {"rollback", "ack", "activate-next"}:
        transaction(runtime, action)
    elif action == "seed":
        seed(root)
    elif action == "publish":
        # Explicit import of already-built data; host restart/ack still required.
        publish(root, runtime)
    else:
        import time
        if (runtime / "pending.json").exists():
            raise RuntimeError("Restart transaction pending; use docker/update.sh for recovery")
        fingerprints = []
        for name in ("nashdom_checker.py", "erzrf_checker.py", "fedstat_checker.py"):
            fingerprints.append(f"{name}={hashlib.sha256((root / name).read_bytes()).hexdigest()[:12]}")
        print("Updater image source hashes: " + " ".join(fingerprints), flush=True)
        seed(root)
        before = read_status(root).get("run_id")
        started = time.time()
        subprocess.run([sys.executable, "-u", "scripts/update_realty.py", "all", "--retries", "0"],
                       cwd=root, check=True)
        require_success(read_status(root), before, started)
        publish(root, runtime)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["update", "seed", "publish", "validate-api", "rollback", "ack", "archive", "recovery-info", "activate-next"])
    args = parser.parse_args()
    root, runtime = Path("/app"), Path("/runtime")
    if args.action == "validate-api":
        validate_api()
    else:
        with mutation_lock(root):
            run_action(args.action, root, runtime)


if __name__ == "__main__":
    main()
