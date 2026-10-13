"""Permanent source history. Copy and index first; remove an active file last.

The archive index is JSONL, protected by an OS file lock and a thread lock.
Every archived file also has an independent metadata sidecar. Nothing purges
history. Downloads are retired only by verified source receipts or an exact,
state-confirmed legacy filename family, never by dashboard wildcard patterns.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import threading
import uuid

from pipeline.download_provenance import receipt_path, record_download
from pipeline.file_utils import validate_excel_file
from pipeline.source_records import file_sha256
from pipeline.state_utils import write_json_atomic

_LOCKS = {}
_LOCKS_GUARD = threading.Lock()


@contextmanager
def _lock(directory):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / ".history.lock"
    with _LOCKS_GUARD:
        lock = _LOCKS.setdefault(str(path.resolve()), threading.RLock())
    with lock, path.open("a+b") as handle:
        if os.name == "nt":
            import msvcrt
            if handle.seek(0, 2) == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def _relative(path, root):
    path = Path(path).resolve()
    return path.relative_to(root).as_posix() if path.is_relative_to(root) else str(path)


def _read_json(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _records(archive):
    try:
        with (archive / "index.jsonl").open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    record = json.loads(line)
                    if isinstance(record, dict):
                        yield record
                except ValueError:
                    continue  # A crash-truncated line cannot select an active file.
    except FileNotFoundError:
        return


def _check_archive(root, archive):
    if not archive.resolve().is_relative_to(root):
        raise ValueError("Archive must stay inside the declared project root")


def validate_workbook(path):
    validate_excel_file(path)
    import pandas as pd
    try:
        with pd.ExcelFile(path) as book:
            if not book.sheet_names:
                raise ValueError("Downloaded workbook contains no sheets")
    except Exception as exc:
        raise ValueError(f"Invalid downloaded workbook: {path}") from exc


def confirmed_download(path, *, source_id, remote_date):
    """A saved date alone cannot confirm a missing or corrupted active file."""
    path = Path(path)
    if not path.is_file() or path.is_symlink():
        return False
    try:
        validate_workbook(path)
        receipt = _read_json(receipt_path(path))
        return (receipt.get("source_id") == source_id and receipt.get("remote_date") == remote_date
                and receipt.get("sha256") == file_sha256(path))
    except (OSError, ValueError):
        return False


def _archive_locked(source, *, root, archive, indicator_key, reason, move, deduplicate, category=None):
    if source.is_symlink() or not source.is_file() or source.resolve().is_relative_to(archive.resolve()):
        raise ValueError(f"Refusing unsafe archive source: {source}")
    digest, size = file_sha256(source), source.stat().st_size
    original = _relative(source, root)
    if deduplicate:
        for record in _records(archive):
            if (record.get("indicator_key") == indicator_key and record.get("original_path") == original
                    and record.get("sha256") == digest):
                stored = Path(record["archive_path"])
                stored = stored if stored.is_absolute() else root / stored
                if stored.resolve().is_relative_to(archive.resolve()) and stored.is_file() and file_sha256(stored) == digest:
                    if move:
                        source.unlink()
                    return record
    now = datetime.now(timezone.utc)
    group = re.sub(r"[^\w.-]+", "_", category or indicator_key).strip(".") or "source"
    destination = archive / now.strftime("%Y-%m-%d") / group
    destination.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    for attempt in range(1000):
        bucket = destination / (token if attempt == 0 else f"{token}_{attempt}")
        try:
            bucket.mkdir()
            break
        except FileExistsError:
            continue
    else:
        raise OSError("Cannot allocate a collision-free archive directory")
    target = bucket / source.name
    temporary = bucket / ".copy.tmp"
    try:
        shutil.copy2(source, temporary)
        if temporary.stat().st_size != size or file_sha256(temporary) != digest or file_sha256(source) != digest:
            raise OSError(f"Source changed during archival: {source}")
        with temporary.open("r+b") as handle:
            os.fsync(handle.fileno())
        temporary.replace(target)
        record = {"archived_at": now.isoformat(timespec="microseconds"), "original_path": original,
                  "archive_path": _relative(target, root), "indicator_key": indicator_key,
                  "sha256": digest, "size_bytes": size, "reason": reason,
                  "operation": "move" if move else "copy"}
        receipt = _read_json(receipt_path(source))
        if receipt.get("sha256") == digest:
            record["download_receipt"] = receipt
        metadata = bucket / "archive-record.json"
        with metadata.open("x", encoding="utf-8") as handle:
            json.dump(record, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        # One locked append; durable metadata/data already exist before unlink.
        with (archive / "index.jsonl").open("a+b") as handle:
            if handle.seek(0, 2):
                handle.seek(-1, 2)
                if handle.read(1) != b"\n":
                    handle.write(b"\n")
            handle.write((json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        if move:
            source.unlink()
        return record
    finally:
        temporary.unlink(missing_ok=True)


def archive_file(path, *, root, indicator_key, reason, archive_root=None, move=False, deduplicate=False, category=None):
    """Archive an explicit file; returns its durable index/sidecar record.

    On copy/index failure the active source remains. Hash deduplication is only
    for the same source key AND original path, not arbitrary equal downloads.
    """
    root = Path(root).resolve()
    archive = Path(archive_root) if archive_root is not None else root / "data/raw/_archive"
    _check_archive(root, archive)
    if not Path(path).resolve().is_relative_to(root):
        raise ValueError("Archive source must stay inside the declared project root")
    with _lock(archive):
        return _archive_locked(Path(path), root=root, archive=archive, indicator_key=indicator_key,
                               reason=reason, move=move, deduplicate=deduplicate, category=category)


def date_stamp(value):
    for pattern in ("%d.%m.%Y", "%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(str(value), pattern).strftime("%Y%m%d")
        except ValueError:
            pass
    return None


def history_for(downloads):
    """Keep isolated/diagnostic downloads isolated too; honor configured roots."""
    downloads = Path(downloads).resolve()
    configured = Path(os.environ["PARSER_ROOT"]).resolve() if os.environ.get("PARSER_ROOT") else None
    if configured and downloads == Path(os.environ.get("PARSER_DOWNLOADS", configured / "downloads")).resolve():
        root = configured
    else:
        root = downloads.parent if downloads.name == "downloads" else downloads
    return SourceHistory(root, downloads)


class SourceHistory:
    def __init__(self, root, downloads=None):
        self.root = Path(root).resolve()
        self.downloads = Path(downloads).resolve() if downloads is not None else self.root / "downloads"
        if not self.downloads.is_relative_to(self.root):
            raise ValueError("Downloads must stay inside the declared project root")
        self.archive = self.root / "data/raw/_archive"

    def install(self, candidate, target, *, indicator_key, remote_date=None, validate=validate_workbook):
        """Validate a staged complete file, save a same-name predecessor, replace."""
        candidate, target = Path(candidate), Path(target)
        if candidate.resolve() == target.resolve() or not target.resolve().is_relative_to(self.downloads):
            raise ValueError("Install requires a separate candidate and a downloads target")
        validate(candidate)
        _check_archive(self.root, self.archive)
        with _lock(self.archive):
            previous = None
            if target.exists():
                previous = _archive_locked(target, root=self.root, archive=self.archive, indicator_key=indicator_key,
                                reason="before_confirmed_replacement", move=False, deduplicate=True)
            target.parent.mkdir(parents=True, exist_ok=True)
            candidate.replace(target)
            try:
                record_download(target, source_id=indicator_key.split(":", 1)[-1], remote_date=remote_date)
            except BaseException:
                if previous:
                    backup = Path(previous["archive_path"])
                    backup = backup if backup.is_absolute() else self.root / backup
                    restoration = target.with_name(target.name + f".{uuid.uuid4().hex}.restore")
                    shutil.copy2(backup, restoration)
                    restoration.replace(target)
                    if previous.get("download_receipt"):
                        write_json_atomic(receipt_path(target), previous["download_receipt"])
                    else:
                        receipt_path(target).unlink(missing_ok=True)
                else:
                    target.unlink(missing_ok=True)
                    receipt_path(target).unlink(missing_ok=True)
                raise
        return target

    def retire(self, indicator_key, current, *, remote_date=None, legacy_names=(), migrate_legacy=False):
        """Retire only this confirmed key; untouched unknown/neighboring files stay.

        Legacy matching uses an exact canonical suffix and a known saved date,
        or exact filenames explicitly supplied from the previous source state.
        A receipt with a different key/hash always vetoes legacy inference.
        """
        current = Path(current)
        if not current.is_file() or current.is_symlink() or current.parent.resolve() != self.downloads:
            return []
        # Collectors may pass ./downloads/name while iterdir yields absolute
        # paths. Compare the same canonical identity without accepting a leaf
        # symlink or a file outside this downloads directory.
        current = current.resolve()
        if current.parent != self.downloads:
            return []
        try:
            validate_workbook(current)
        except (OSError, ValueError):
            return []
        source_id = indicator_key.split(":", 1)[-1]
        stamp = date_stamp(remote_date)
        current_receipt = _read_json(receipt_path(current))
        current_digest = file_sha256(current)
        verified = (current_receipt.get("source_id") == source_id
                    and current_receipt.get("sha256") == current_digest
                    and current_receipt.get("remote_date") == remote_date)
        if receipt_path(current).exists() and not verified:
            return []
        legacy = migrate_legacy and stamp and current.name.startswith(stamp + "_")
        if not verified and not legacy:
            return []
        suffix = current.name[9:] if re.match(r"^\d{8}_", current.name) else None
        records = []
        _check_archive(self.root, self.archive)
        with _lock(self.archive):
            # The file/receipt may have been replaced while waiting for another
            # installer. Never retire files based on a stale confirmation.
            if (current.is_symlink() or current.parent.resolve() != self.downloads
                    or _read_json(receipt_path(current)) != current_receipt or file_sha256(current) != current_digest):
                return []
            for old in self.downloads.iterdir():
                if old == current or not old.is_file() or old.is_symlink() or old.suffix.lower() not in {".xls", ".xlsx"}:
                    continue
                receipt = _read_json(receipt_path(old))
                old_verified = (receipt.get("source_id") == source_id and receipt.get("sha256") == file_sha256(old))
                if receipt_path(old).exists() and not old_verified:
                    continue
                if old_verified and current_receipt.get("downloaded_at") and receipt.get("downloaded_at", "") > current_receipt["downloaded_at"]:
                    continue
                legacy_match = (legacy and suffix and re.match(r"^\d{8}_", old.name)
                                and old.name[9:] == suffix and old.name[:8] <= stamp)
                if not old_verified and not legacy_match and old.name not in legacy_names:
                    continue
                # Explicit legacy names still require the state-confirmed current.
                if not old_verified and not (verified or legacy):
                    continue
                records.append(_archive_locked(old, root=self.root, archive=self.archive,
                    indicator_key=indicator_key, reason="superseded_confirmed_source", move=True, deduplicate=True))
                receipt_path(old).unlink(missing_ok=True)
        return records


def migrate_downloads(root):
    """Offline, conservative migration using saved state and exact current names."""
    import fedstat_checker as fedstat
    import rosstat_checker as rosstat
    root = Path(root).resolve()
    history = SourceHistory(root)
    result = []
    for collector, family in ((fedstat, "fedstat"), (rosstat, "rosstat")):
        state_path = root / f"state/{family}_state.json"
        state = _read_json(state_path if state_path.exists() else root / f"{family}_state.json")
        for key, value in state.items():
            if family == "fedstat":
                if key not in fedstat.INDICATORS or not date_stamp(value):
                    continue
                name, remote = fedstat.current_filename(key, value), value
            else:
                if key not in {item["key"] for items in rosstat.PAGE_SOURCES.values() for item in items} or not isinstance(value, dict):
                    continue
                name, remote = value.get("filename", ""), value.get("date")
            if not name or Path(name).name != name or not date_stamp(remote):
                continue
            result.extend(history.retire(f"{family}:{key}", history.downloads / name,
                                         remote_date=remote, migrate_legacy=True))
    return result


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["migrate"])
    parser.add_argument("--root", type=Path, default=Path(os.environ.get("PARSER_ROOT", ".")))
    args = parser.parse_args()
    print(f"Archived {len(migrate_downloads(args.root))} confidently mapped obsolete downloads; unknown files retained.")
