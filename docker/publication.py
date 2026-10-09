"""Copy, validate and atomically publish immutable data for Compose.

Only updater writes /runtime. Host update.sh serializes publication and restart.
Old releases are intentionally retained: running backends resolve live once.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

TREES = ("downloads", "data/processed", "data/marts", "data/derived",
         "data/raw/realty", "logs")
SOURCES = {"monitoring", "rasprod", "kvart", "construction", "erz-top",
           "erz-cards", "fedstat", "rosstat"}
EXCLUDED = {"_archive", "archive", "archives", "__pycache__", "temp", "tmp"}


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
        if (item.name.casefold() in EXCLUDED or item.name.startswith("~$")
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
    from pipeline.build_realty_marts import _specs
    from fastapi.testclient import TestClient
    from backend.app import create_app
    from docker.readiness import ROUTES

    access = DataAccess(DataContext.from_environment())
    for spec in _specs(access):
        getattr(access, spec.loader_name)()  # Enforces source sets, size and mtime_ns.
    with TestClient(create_app()) as client:
        for route in ROUTES:
            response = client.get(route)
            if response.status_code != 200:
                raise RuntimeError(f"Candidate API failed: {route}: {response.status_code}")
            if route == "/api/v1/catalog" and not response.json()["controls"]["developers"]:
                raise RuntimeError("Candidate catalog has no developers")


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
            or path.parts[1] in {"..", "."} or releases.is_symlink()
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


def transaction(runtime, action):
    pending = runtime / "pending.json"
    if not pending.exists():
        return
    record = json.loads(pending.read_text(encoding="utf-8"))
    if action == "rollback":
        swap(runtime, record["previous"])
        return  # Keep journal until the restored backend becomes ready.
    elif live_target(runtime) not in {record["next"], record["previous"]}:
        raise RuntimeError("Cannot acknowledge another release")
    pending.unlink()


def run_action(action, root, runtime):
    if action in {"rollback", "ack"}:
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
        seed(root)
        before = read_status(root).get("run_id")
        started = time.time()
        subprocess.run([sys.executable, "-u", "scripts/update_realty.py", "all", "--retries", "0"],
                       cwd=root, check=True)
        require_success(read_status(root), before, started)
        publish(root, runtime)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["update", "seed", "publish", "validate-api", "rollback", "ack"])
    args = parser.parse_args()
    root, runtime = Path("/app"), Path("/runtime")
    if args.action == "validate-api":
        validate_api()
    else:
        with mutation_lock(root):
            run_action(args.action, root, runtime)


if __name__ == "__main__":
    main()
