"""Download receipts and read-only provenance for rows already in a data mart.

Remote publication dates, filenames and ETL timestamps are not download dates.
Legacy files without a verified receipt deliberately retain an unknown date.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

import pandas as pd

from pipeline.source_records import file_sha256
from pipeline.state_utils import write_json_atomic


def receipt_path(path: Path) -> Path:
    # Keep metadata outside *.xls* globs used by the ETL.
    return path.parent / ".download_receipts" / f"{path.name}.json"


def record_download(path: Path, *, source_id: str = "", remote_date=None) -> None:
    path = Path(path)
    write_json_atomic(receipt_path(path), {
        "downloaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sha256": file_sha256(path),
        "source_id": str(source_id),
        "remote_date": remote_date,
    })


def record_successful_download(function):
    """Cover every successful return, including fallback and chunk downloads."""
    @wraps(function)
    def wrapped(indicator_id, save_dir, **kwargs):
        result = function(indicator_id, save_dir, **kwargs)
        if result:
            record_download(Path(result), source_id=indicator_id,
                            remote_date=kwargs.get("remote_date"))
        return result
    return wrapped


def _downloaded_at(path: Path):
    try:
        receipt = json.loads(receipt_path(path).read_text(encoding="utf-8"))
        stamp = pd.Timestamp(receipt["downloaded_at"])
        if pd.isna(stamp) or stamp.tzinfo is None:
            return None
        if receipt["sha256"] != file_sha256(path):
            return None
        return stamp
    except (OSError, ValueError, KeyError, TypeError):
        return None


def source_provenance(df: pd.DataFrame, downloads: Path) -> pd.DataFrame:
    """Inspect exact source_file names, never substitute a newer wildcard match."""
    columns = ["Исходный файл", "Скачан (МСК)", "Дата файла (МСК)", "Данные по", "Статус"]
    if df.empty or "source_file" not in df:
        return pd.DataFrame(columns=columns)
    rows = []
    for name, group in df.dropna(subset=["source_file"]).groupby("source_file", sort=True):
        name = str(name)
        path = Path(downloads) / name
        # source_file is a basename, not arbitrary file access supplied by a CSV.
        valid_name = Path(name).name == name and name not in {".", ".."}
        exists = valid_name and path.is_file()
        downloaded = _downloaded_at(path) if exists else None
        modified = pd.Timestamp(path.stat().st_mtime, unit="s", tz="UTC") if exists else None
        status = "Дата скачивания не зафиксирована" if exists else "Исходный файл не найден"
        if downloaded is not None:
            status = "Скачивание подтверждено"
            # Replacing a file with the same name must not freshen old processed rows.
            parsed = pd.to_datetime(group.get("loaded_at", pd.Series(dtype=str)), errors="coerce")
            if not parsed.dropna().empty:
                parsed = parsed.max()
                if parsed.tzinfo is None:
                    parsed = parsed.tz_localize("Europe/Moscow")
                if downloaded > parsed:
                    status = "Файл скачан после обработки; требуется ETL"
        period = "—"
        if "year" in group:
            years = pd.to_numeric(group["year"], errors="coerce")
            if years.notna().any():
                year = int(years.max())
                period = str(year)
                if "month" in group:
                    months = pd.to_numeric(group.loc[years.eq(year), "month"], errors="coerce")
                    months = months[months.between(1, 12)]
                    if months.notna().any():
                        period = f"{int(months.max()):02d}.{year}"
        def display(stamp):
            return stamp.tz_convert("Europe/Moscow").strftime("%d.%m.%Y %H:%M") if stamp is not None else "неизвестно"
        rows.append([str(path), display(downloaded), display(modified), period, status])
    return pd.DataFrame(rows, columns=columns)


def download_summary(provenance: pd.DataFrame) -> str:
    if provenance.empty:
        return "Дата скачивания неизвестна"
    values = provenance["Скачан (МСК)"]
    known = values[values.ne("неизвестно")]
    suffix = "; требуется ETL" if provenance["Статус"].str.contains("требуется ETL").any() else ""
    if len(known) != len(values):
        file_dates = provenance["Дата файла (МСК)"]
        file_dates = pd.to_datetime(file_dates[file_dates.ne("неизвестно")], format="%d.%m.%Y %H:%M")
        prefix = ""
        if not file_dates.empty:
            start, end = file_dates.min(), file_dates.max()
            label = start.strftime("%d.%m.%Y %H:%M")
            if start != end:
                label += " — " + end.strftime("%d.%m.%Y %H:%M")
            prefix = f"Дата файлов (МСК): {label}; "
        return prefix + f"дата скачивания неизвестна для {len(values) - len(known)} из {len(values)} файлов" + suffix
    dates = pd.to_datetime(known, format="%d.%m.%Y %H:%M")
    start, end = dates.min(), dates.max()
    label = start.strftime("%d.%m.%Y %H:%M")
    if start != end:
        label += " — " + end.strftime("%d.%m.%Y %H:%M")
    return f"Файлы скачаны (МСК): {label}{suffix}"
