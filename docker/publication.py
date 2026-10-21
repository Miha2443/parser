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
from docker.coverage import SOURCES, OPTIONAL_SOURCES, REQUIRED_SOURCES
EXCLUDED = {"_archive", "archive", "archives", "__pycache__", "temp", "tmp",
            ".fedstat_resume", ".construction_history", ".construction_history.lock",
            ".mortgage_history", ".mortgage_history.lock", "_debug"}


@contextmanager
def mutation_lock(root):
    import fcntl  # Updater runs on Linux; lock spans the complete download/build/copy.
    (root / "state").mkdir(parents=True, exist_ok=True)
    with (root / "state/.container-publication.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


@contextmanager
def bootstrap_baseline(root, runtime):
    """Pin the last working generation before a first collector can overwrite it."""
    if live_target(runtime):
        yield None
        return
    runtime.mkdir(parents=True, exist_ok=True)
    baseline = runtime / (".bootstrap-baseline-" + uuid.uuid4().hex)
    baseline.mkdir()
    try:
        for relative in TREES:
            copy_tree(root / relative, baseline / relative)
        yield baseline
    finally:
        if baseline.is_symlink() or baseline.resolve().parent != runtime.resolve():
            raise RuntimeError("Unsafe bootstrap baseline cleanup")
        shutil.rmtree(baseline)


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


def requested_sources():
    if os.environ.get("UPDATE_ERZ") == "0":
        print("UPDATE_ERZ=0 is deprecated: all eight providers are checked; confirmed outages retain previous data", flush=True)
    return SOURCES


def require_success(status, previous_id, started, *, expected_sources=None, returncode=None):
    from pipeline.source_availability import validate_outcome
    expected = requested_sources() if expected_sources is None else frozenset(expected_sources)
    requested, successes, failures = (status.get(key, []) for key in ("sources_requested", "successes", "failures"))
    valid_lists = all(isinstance(values, list) and all(isinstance(value, str) for value in values)
                      and len(values) == len(set(values)) for values in (requested, successes, failures))
    skipped = status.get("skipped", [])
    if not isinstance(skipped, list) or not all(isinstance(alias, str) for alias in skipped) or len(skipped) != len(set(skipped)):
        raise RuntimeError("Invalid skipped source list")
    expected_error = ""
    if (not status.get("run_id") or status["run_id"] == previous_id
            or status.get("status") != ("degraded" if skipped else "success") or not status.get("finished_at")
            or status.get("current_stage") != "finished"
            or not valid_lists or not REQUIRED_SOURCES <= expected <= SOURCES
            or set(requested) != expected or set(successes) | set(skipped) != expected
            or set(successes) & set(skipped) or failures or status.get("status_write_errors")
            or (status.get("error") or "") != expected_error or status.get("marts_ok") is not True
            or status.get("processed_ok") is not True
            or (returncode is not None and returncode != 0)):
        raise RuntimeError("Current required-source run did not succeed; refusing publication")
    if datetime.fromisoformat(status["started_at"]).timestamp() < started - 2:
        raise RuntimeError("Update status predates this invocation")
    outcomes = status.get("source_outcomes", {})
    if not isinstance(outcomes, dict) or set(outcomes) != expected:
        raise RuntimeError("Source outcome partition is incomplete")
    for alias in expected:
        row = validate_outcome(outcomes[alias], alias, status["run_id"], status["started_at"], status["finished_at"])
        if row["status"] != ("skipped" if alias in skipped else "success"):
            raise RuntimeError("Source outcome contradicts its run partition")


def publication_status(root, status=None, returncode=None, *, full_marts_built=False):
    from pipeline.data_access import DataAccess, DataContext
    access = DataAccess(DataContext(root, downloads=root / "downloads", use_marts=False))
    optional = {}
    for alias, family in (("erz-top", "erzrf_top"), ("erz-cards", "erzrf_cards")):
        files = access.source_files(family)
        state = "unavailable" if not files else "fresh" if status and alias in status.get("successes", []) else "retained"
        optional[alias] = {"state": state, "files": [file.name for file in files],
                           "source_dates": {file.name: datetime.fromtimestamp(file.stat().st_mtime, timezone.utc).isoformat() for file in files}}
    return {"schema_version": 1, "status": "degraded" if any(row["state"] != "fresh" for row in optional.values()) else "success",
            "mode": "collector" if status else "offline_import", "optional_sources": optional,
            "collector_status": status.get("status") if status else None, "collector_returncode": returncode,
            "run_id": status.get("run_id") if status else None,
            "required_successes": sorted(REQUIRED_SOURCES) if status else [],
            "collector_marts_skipped": bool(status), "full_marts_built": full_marts_built,
            "recorded_at": datetime.now(timezone.utc).isoformat()}


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
    from pipeline.source_availability import read_publication, unavailable, route_sources
    source_states = read_publication(access.PROJECT_ROOT).get("sources", {})
    with TestClient(create_app()) as client:
        def get(route):
            response = client.get(route)
            if response.status_code == 503:
                detail = response.json().get("detail", {})
                blocked = route_sources(route.split("?")[0]) & unavailable(access.PROJECT_ROOT)
                if blocked and isinstance(detail, dict) and detail.get("code") == "source_unavailable" and set(detail.get("sources", [])) == blocked:
                    return {"sourceUnavailable": True, "sources": sorted(blocked)}
            if response.status_code != 200:
                raise RuntimeError(f"Candidate API failed: {route}: {response.status_code}")
            return response.json()
        validate_pages(get, source_states=source_states)


def build_candidate(root):
    environment = {**os.environ, "PARSER_ROOT": str(root), "PARSER_DOWNLOADS": str(root / "downloads"),
                   "PARSER_USE_REALTY_MARTS": "0", "PARSER_REQUIRE_REALTY_MARTS": "0"}
    subprocess.run([sys.executable, "-m", "pipeline.build_realty_marts", "--strict"], env=environment, check=True)


def build_candidate_processed(root, sources):
    """Reuse current proven processed inputs; rebuild missing/stale indicators privately."""
    from pipeline.source_availability import indicator_ids
    from pipeline.data_access import DataAccess, DataContext
    from pipeline.registry import by_id
    access = DataAccess(DataContext(root, downloads=root / "downloads", use_marts=False))
    selected = []
    for identifier in sorted(indicator_ids(set(sources) - OPTIONAL_SOURCES)):
        indicator = by_id(identifier)
        target = root / "data/processed" / (identifier + ".pkl")
        # Economic files are genuinely reusable when the existing contribution
        # provenance and download timestamps pass the final coverage contract.
        files = access._raw_files([access.DOWNLOADS_DIR], list(indicator.file_patterns)) if indicator.source in {"fedstat", "rosstat"} else []
        reusable = (files and target.is_file() and all(file.stat().st_mtime_ns <= target.stat().st_mtime_ns for file in files))
        if not reusable:
            selected.append(identifier)
    if selected:
        environment = {**os.environ, "PARSER_ROOT": str(root), "PARSER_DOWNLOADS": str(root / "downloads"),
                       "PARSER_USE_REALTY_MARTS": "0", "PARSER_REQUIRE_REALTY_MARTS": "0", "TDM_DISABLED": "1"}
        subprocess.run([sys.executable, "pipeline/orchestrator.py", "--skip-download", "--no-notify", "--only", *selected],
                       cwd=Path(__file__).resolve().parents[1], env=environment, check=True)


def select_candidate_sources(candidate, status, previous=None):
    from pipeline.source_availability import source_files, indicator_ids, FAMILIES
    from pipeline.data_access import DataAccess, DataContext
    from docker.coverage import validate_source_raw, validate_erz_sources
    from zipfile import BadZipFile
    def access(root):
        return DataAccess(DataContext(root, downloads=root / "downloads", use_marts=False))
    rows = {}
    units = [(alias,) for alias in ("monitoring", "construction", "kvart", "rasprod", "fedstat", "rosstat")]
    units.append(("erz-top", "erz-cards"))
    for aliases in units:
        skipped = [alias for alias in aliases if status["source_outcomes"][alias]["status"] == "skipped"]
        chosen = candidate if not skipped else previous
        reason = "Источник обновлён и проверен"
        code = "collected"
        if skipped:
            reason, code = (status["source_outcomes"][skipped[0]][key] for key in ("reason", "reason_code"))
        if chosen is not None:
            try:
                if len(aliases) == 2:
                    validate_erz_sources(access(chosen))
                    if not skipped:
                        for alias in aliases:
                            if not source_files(chosen, alias):
                                raise RuntimeError("Successful ERZ collector has no source data")
                else:
                    validate_source_raw(access(chosen), aliases[0])
            except (OSError, ValueError, RuntimeError, KeyError, TypeError, BadZipFile, EOFError) as exc:
                if not skipped:
                    raise RuntimeError("Successful collector produced invalid " + ", ".join(aliases) + ": " + str(exc)) from exc
                chosen = None
                reason += "; прежний проверенный набор недоступен: " + str(exc)[:250]
        selected = {alias: source_files(chosen, alias) if chosen else [] for alias in aliases}
        if skipped:
            # Remove only this family's candidate files. Downloads and Nashdom
            # are shared namespaces; another source's files never move here.
            for file in sorted({file for alias in aliases for file in source_files(candidate, alias)}):
                file.unlink()
            for file in sorted({file for files in selected.values() for file in files}):
                destination = candidate / file.relative_to(chosen)
                if not destination.resolve().is_relative_to(candidate.resolve()):
                    raise RuntimeError("Unsafe retained source destination")
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(file, destination)
                if destination.stat().st_mtime_ns != file.stat().st_mtime_ns:
                    raise RuntimeError("Retained source timestamp changed")
            for identifier in indicator_ids(aliases):
                destination = candidate / "data/processed" / (identifier + ".pkl")
                destination.unlink(missing_ok=True)
                source = chosen / "data/processed" / destination.name if chosen else None
                if source and source.is_file():
                    if source.is_symlink() or not source.resolve().is_relative_to(chosen.resolve()):
                        raise RuntimeError("Unsafe retained processed source")
                    shutil.copy2(source, destination)
        for alias in aliases:
            files = source_files(candidate, alias)
            state = "unavailable" if not files else "retained" if skipped else "fresh"
            evidence_alias = skipped[0] if skipped else alias
            rows[alias] = {"state": state, "reason_code": code, "reason": reason,
                           "outage_alias": evidence_alias, "outcome": status["source_outcomes"][evidence_alias],
                           "files": [str(file.relative_to(candidate)) for file in files],
                           "source_dates": {file.name: datetime.fromtimestamp(file.stat().st_mtime, timezone.utc).isoformat() for file in files},
                           "inventory": [{"path": str(file.relative_to(candidate)), "sha256": hashlib.sha256(file.read_bytes()).hexdigest(),
                                          "mtime_ns": file.stat().st_mtime_ns} for file in files]}
    return {"schema_version": 2, "status": "degraded" if any(row["state"] != "fresh" for row in rows.values()) else "success",
            "mode": "collector", "sources": rows, "optional_sources": {alias: rows[alias] for alias in OPTIONAL_SOURCES},
            "run_id": status["run_id"], "run_started_at": status["started_at"], "run_finished_at": status["finished_at"],
            "collector_status": status["status"], "collector_returncode": 0, "full_marts_built": False,
            "collector_marts_skipped": True, "recorded_at": datetime.now(timezone.utc).isoformat()}


def _erz_files(access):
    files = set(access.source_files("erzrf_top") + access.source_files("erzrf_cards"))
    files.update(file.with_suffix(file.suffix + ".provenance.json") for file in list(files)
                 if file.suffix.lower() in {".xlsx", ".xls"} and file.with_suffix(file.suffix + ".provenance.json").is_file())
    return sorted(files)


def select_candidate_erz(candidate, previous=None):
    """Select a verified coherent cohort without mutating working/old data."""
    from pipeline.data_access import DataAccess, DataContext
    from docker.coverage import validate_erz_sources
    from zipfile import BadZipFile
    current = DataAccess(DataContext(candidate, downloads=candidate / "downloads", use_marts=False))
    current_files = _erz_files(current)
    selected, reason, origin = [], "No current ERZ source data", "none"
    for label, root in (("current", candidate), ("previous", previous)):
        if root is None:
            continue
        access = DataAccess(DataContext(root, downloads=root / "downloads", use_marts=False))
        files = _erz_files(access)
        if not files:
            continue
        try:
            if any(file.is_symlink() or not file.resolve().is_relative_to(root.resolve()) for file in files):
                raise ValueError("Unsafe ERZ source path")
            validate_erz_sources(access)
            if len({file.name for file in files}) != len(files):
                raise ValueError("Ambiguous duplicate ERZ filenames")
        except (OSError, ValueError, RuntimeError, KeyError, TypeError, BadZipFile, EOFError) as exc:
            reason = ("Current ERZ rejected: " if label == "current" else reason + "; previous ERZ rejected: ") + str(exc)[:300]
            continue
        selected, origin = files, label
        if label == "current":
            reason = "Current ERZ cohort verified"
        break
    # Stage a copy before clearing: current selection lives inside candidate.
    staging = candidate / ".erz-selection"
    staging.mkdir()
    for file in selected:
        shutil.copy2(file, staging / file.name)
    boundary = candidate.resolve()
    canonical = candidate / "data/raw/realty/erzrf"
    if canonical.is_symlink() or not canonical.resolve().is_relative_to(boundary):
        raise RuntimeError("Unsafe candidate ERZ directory")
    for file in current_files:
        if file.is_symlink() or not file.resolve().is_relative_to(boundary):
            raise RuntimeError("Unsafe candidate ERZ source")
        file.unlink()
    if canonical.exists():
        shutil.rmtree(canonical)  # Verified private candidate namespace above.
    copy_tree(staging, canonical)
    if staging.is_symlink() or not staging.resolve().is_relative_to(boundary):
        raise RuntimeError("Unsafe candidate ERZ staging directory")
    shutil.rmtree(staging)
    # Cached optional ETL pickles cannot substitute for the selected raw cohort.
    from pipeline.registry import INDICATORS
    for indicator in INDICATORS:
        if indicator.source == "erzrf":
            (candidate / "data/processed" / (indicator.id + ".pkl")).unlink(missing_ok=True)
    return {"selection": origin, "reason": reason}


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


def publish(root, runtime, *, run_status=None, collector_returncode=None, baseline=None):
    if run_status is not None:
        require_success(run_status, None, datetime.fromisoformat(run_status["started_at"]).timestamp(),
                        expected_sources=SOURCES, returncode=collector_returncode)
    runtime.mkdir(parents=True, exist_ok=True)
    previous = live_target(runtime)
    if (runtime / "pending.json").exists():
        raise RuntimeError("Unfinished restart transaction; run docker/update.sh for recovery")
    releases = runtime / "releases"
    if releases.is_symlink() or releases.resolve().parent != runtime.resolve():
        raise RuntimeError("Unsafe releases directory")
    releases.mkdir(exist_ok=True)
    identifier = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:12]
    candidate = releases / (".building-" + identifier)
    candidate.mkdir()
    try:
        for relative in TREES:
            copy_tree(root / relative, candidate / relative)
        if run_status and run_status.get("source_outcomes"):
            metadata = select_candidate_sources(candidate, run_status, runtime / previous if previous else baseline)
            (candidate / "data/processed/publication_status.json").write_text(json.dumps(metadata), encoding="utf-8")
            build_candidate_processed(candidate, {alias for alias, row in metadata["sources"].items() if row["state"] != "unavailable"})
            selection = None
        else:
            selection = select_candidate_erz(candidate, runtime / previous if previous else None)
            metadata = publication_status(candidate, run_status, collector_returncode)
            # An offline import can contain newer downloads than its pickles.
            # Repair only the private candidate, then keep the normal proofs.
            build_candidate_processed(candidate, {"fedstat", "rosstat"})
        from docker.mortgage import select_source as select_mortgage
        metadata["supplemental_sources"] = {"mortgage": select_mortgage(candidate, runtime / previous if previous else None)}
        build_candidate(candidate)
        metadata["full_marts_built"] = True
        if selection is not None:
            metadata["erz_selection"] = selection
            for row in metadata["optional_sources"].values():
                if selection["selection"] == "previous" and row["state"] != "unavailable":
                    row["state"] = "retained"
                row["reason"] = selection["reason"]
            metadata["status"] = "degraded" if any(row["state"] != "fresh" for row in metadata["optional_sources"].values()) else "success"
        (candidate / "data/processed/publication_status.json").write_text(json.dumps(metadata), encoding="utf-8")
        print("Candidate sources: " + json.dumps({key: {"state": row["state"], "reason_code": row["reason_code"]}
              for key, row in metadata.get("sources", {}).items()} if "sources" in metadata else selection), flush=True)
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
            if candidate.is_symlink() or candidate.resolve().parent != releases.resolve():
                raise RuntimeError("Unsafe candidate cleanup directory")
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
        # Offline import builds only the normalized private candidate.
        publish(root, runtime)
    elif action == "build-required-processed":
        from scripts.update_realty import select_processed_indicators
        subprocess.run([sys.executable, "pipeline/orchestrator.py", "--skip-download", "--no-notify", "--only",
                        *sorted(select_processed_indicators(sorted(REQUIRED_SOURCES)))], cwd=root, check=True)
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
        expected = requested_sources()
        with bootstrap_baseline(root, runtime) as baseline:
            result = subprocess.run([sys.executable, "-u", "scripts/update_realty.py", *sorted(expected), "--retries", "0", "--collect-only"],
                                    cwd=root, check=False)
            status = read_status(root)
            require_success(status, before, started, expected_sources=expected, returncode=result.returncode)
            from docker.mortgage import collect as collect_mortgage
            collect_mortgage(root)
            publish(root, runtime, run_status=status, collector_returncode=result.returncode, baseline=baseline)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["update", "seed", "publish", "build-required-processed", "validate-api", "rollback", "ack", "archive", "recovery-info", "activate-next"])
    args = parser.parse_args()
    root, runtime = Path("/app"), Path("/runtime")
    if args.action == "validate-api":
        validate_api()
    else:
        with mutation_lock(root):
            run_action(args.action, root, runtime)


if __name__ == "__main__":
    main()
