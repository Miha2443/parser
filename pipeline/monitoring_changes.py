"""Row-level change log for successive Monitoring 2.0 workbooks."""
from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import pandas as pd


SHEETS = ("Реестр РВ", "Реестр ОКС")


def _text(value) -> str:
    if value is None or pd.isna(value):
        return ""
    return " ".join(str(value).replace("\xa0", " ").split())


def _normalized(value) -> str:
    return _text(value).casefold()


def _first(record: pd.Series, names: tuple[str, ...]) -> str:
    for name in names:
        if name in record.index:
            value = _text(record.get(name))
            if value:
                return value
    return ""


def snapshot_monitoring(path: Path) -> dict:
    items: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    row_counts: dict[str, int] = {}
    with pd.ExcelFile(path) as workbook:
        for sheet in SHEETS:
            if sheet not in workbook.sheet_names:
                continue
            frame = workbook.parse(sheet_name=sheet)
            row_counts[sheet] = len(frame)
            for _, record in frame.iterrows():
                uin = _first(record, ("УИН",))
                document = _first(record, ("№ РВ", "№РС", "Разрешение на строительство"))
                address = _first(record, ("Адрес", "Строительный адрес"))
                object_name = _first(record, ("Наименование объекта", "Коммерческое наименование", "Коммерческое название"))
                if not any((uin, document, address, object_name)):
                    continue
                # UIN/document are stable business identifiers. For older rows
                # without them, fall back to address + object name.
                identity = (
                    sheet,
                    _normalized(uin or document),
                    _normalized(document if uin else f"{address}|{object_name}"),
                )
                items[identity].append({
                    "sheet": sheet,
                    "uin": uin,
                    "document": document,
                    "object": object_name,
                    "address": address,
                })
    return {"items": dict(items), "row_counts": row_counts}


def compare_monitoring_snapshots(
    old: dict,
    new: dict,
    *,
    previous_file: str,
    current_file: str,
    previous_sha256: str = "",
    current_sha256: str = "",
) -> dict:
    """Return added/removed business rows between two workbook snapshots."""
    old_counts = Counter({key: len(value) for key, value in old["items"].items()})
    new_counts = Counter({key: len(value) for key, value in new["items"].items()})
    added: list[dict] = []
    removed: list[dict] = []
    for key, count in (new_counts - old_counts).items():
        added.extend(new["items"][key][-count:])
    for key, count in (old_counts - new_counts).items():
        removed.extend(old["items"][key][-count:])
    event_id = hashlib.sha256(
        f"{previous_file}|{current_file}|{previous_sha256}|{current_sha256}".encode("utf-8")
    ).hexdigest()[:20]
    return {
        "event_id": event_id,
        "detected_at": datetime.now().isoformat(timespec="seconds"),
        "previous_file": previous_file,
        "current_file": current_file,
        "previous_rows": old["row_counts"],
        "current_rows": new["row_counts"],
        "added_count": len(added),
        "removed_count": len(removed),
        "added": added,
        "removed": removed,
    }


def compare_monitoring_files(previous: Path, current: Path) -> dict:
    """Return added/removed business rows between two validated workbooks."""
    def digest(path: Path) -> str:
        checksum = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                checksum.update(chunk)
        return checksum.hexdigest()

    return compare_monitoring_snapshots(
        snapshot_monitoring(previous),
        snapshot_monitoring(current),
        previous_file=previous.name,
        current_file=current.name,
        previous_sha256=digest(previous),
        current_sha256=digest(current),
    )


def append_change_event(path: Path, event: dict) -> bool:
    """Append one unique non-empty event to the durable JSONL journal."""
    if not event.get("added_count") and not event.get("removed_count"):
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    event_id = event.get("event_id")
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                if json.loads(line).get("event_id") == event_id:
                    return False
            except (json.JSONDecodeError, AttributeError):
                continue
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(event, ensure_ascii=False) + "\n")
    return True
