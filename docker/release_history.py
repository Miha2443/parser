"""Keep old immutable site releases for inspection without touching live data."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import uuid

RELEASE_NAME = re.compile(r"\d{8}T\d{6}-[0-9a-f]{12}")


def _write_record(path: Path, record: dict) -> None:
    temporary = path.with_name("." + path.name + "-" + uuid.uuid4().hex)
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(record, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def archive_releases(runtime: Path, *, keep: int = 3, probe=None) -> list[str]:
    """Archive only after proving that the backend serves the current live release.

    Caller holds both the host update lock and the container mutation lock.
    Nothing is deleted. The live release and newest ``keep - 1`` other releases
    remain available in releases/. An interrupted transaction blocks all moves.
    """
    from docker.publication import live_target, require_target
    from docker.readiness import probe as default_probe

    if keep < 2:
        raise ValueError("Keep at least two releases for immediate rollback")
    if (runtime / "pending.json").exists():
        raise RuntimeError("Cannot archive releases while publication recovery is pending")
    live = live_target(runtime)
    if not live:
        raise RuntimeError("Cannot archive without a live release")
    # Health includes the pinned resolved path, not merely the live symlink.
    (probe or default_probe)(base="http://backend:8000", expected=str(runtime / live))
    releases = runtime / "releases"
    candidates = []
    for directory in releases.iterdir():
        if not RELEASE_NAME.fullmatch(directory.name):
            continue  # Never touch operator files or interrupted .building directories.
        require_target(runtime, "releases/" + directory.name)
        if directory.name != Path(live).name:
            candidates.append(directory)
    candidates.sort(key=lambda path: path.name, reverse=True)
    protected = {Path(live).name}
    reserve = runtime / "previous-good.json"
    if reserve.exists():
        if reserve.is_symlink():
            raise RuntimeError("Rollback reserve must not be a symlink")
        record = json.loads(reserve.read_text(encoding="utf-8"))
        if record.get("current") == live and record.get("previous"):
            require_target(runtime, record["previous"])
            protected.add(Path(record["previous"]).name)
    for directory in candidates:
        if len(protected) < keep:
            protected.add(directory.name)
    stale = [path for path in candidates if path.name not in protected]
    archive = runtime / "_archive"
    if archive.is_symlink():
        raise RuntimeError("Release archive must not be a symlink")
    archive.mkdir(exist_ok=True)
    history = archive / "releases"
    if history.is_symlink():
        raise RuntimeError("Release archive must not be a symlink")
    history.mkdir(exist_ok=True)
    # A crash after rename can leave a prepared metadata record. The archived
    # directory is already intact; finish only that record, never repeat the move.
    for metadata in history.glob("*.json"):
        if not RELEASE_NAME.fullmatch(metadata.stem):
            continue
        if metadata.is_symlink():
            raise RuntimeError("Release archive record must not be a symlink")
        record = json.loads(metadata.read_text(encoding="utf-8"))
        destination = history / metadata.stem
        if (record.get("status") == "prepared" and destination.is_dir()
                and not destination.is_symlink()
                and not (releases / metadata.stem).exists()
                and record.get("original_path") == f"releases/{metadata.stem}"):
            record["status"] = "archived"
            _write_record(metadata, record)
    # Validate the complete plan before any move, including destination collisions.
    for directory in stale:
        if (history / directory.name).exists() or (history / directory.name).is_symlink():
            raise RuntimeError(f"Release archive already contains {directory.name}")
    moved = []
    for directory in stale:
        destination = history / directory.name
        record_path = history / (directory.name + ".json")
        if record_path.is_symlink():
            raise RuntimeError("Release archive record must not be a symlink")
        if record_path.exists():
            # A prepared record from an interrupted move may be safely retried.
            record = json.loads(record_path.read_text(encoding="utf-8"))
            if record.get("status") != "prepared" or record.get("original_path") != f"releases/{directory.name}":
                raise RuntimeError(f"Release archive record already exists: {record_path.name}")
        else:
            record = {
                "release": directory.name,
                "original_path": f"releases/{directory.name}",
                "archived_path": f"_archive/releases/{directory.name}",
                "archived_at": datetime.now(timezone.utc).isoformat(),
                "live_release_at_archive": live,
                "reason": "superseded site release",
                "status": "prepared",
            }
            _write_record(record_path, record)
        # Same runtime filesystem: rename is atomic and does not modify file times.
        directory.rename(destination)
        record["status"] = "archived"
        _write_record(record_path, record)
        moved.append(directory.name)
        print(f"Archived site release: {record['original_path']} -> {record['archived_path']}", flush=True)
    return moved
