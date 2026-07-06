"""Чтение audit log для UI: группировка по запускам, агрегаты."""
from __future__ import annotations

import json
import logging
import contextlib
import io
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from pipeline.audit import read_audit

logging.getLogger("streamlit").setLevel(logging.ERROR)
logging.getLogger("streamlit.runtime.caching.cache_data_api").setLevel(logging.ERROR)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REALTY_MARTS_MANIFEST = PROJECT_ROOT / "data" / "marts" / "realty" / "manifest.json"
REALTY_UPDATE_STATUS = PROJECT_ROOT / "data" / "processed" / "realty_update_status.json"
REALTY_UPDATE_STATUSES = {"running", "success", "failed", "interrupted"}
REALTY_RUNNING_STALE_MIN = 360


def load_runs() -> pd.DataFrame:
    """Возвращает DataFrame с одной строкой на (run_id, indicator), плюс служебные строки `_run`."""
    rows = read_audit()
    if not rows:
        return pd.DataFrame(columns=["run_id", "ts", "indicator", "status"])
    df = pd.DataFrame(rows)
    df["ts"] = pd.to_datetime(df["ts"], errors="coerce")
    return df


def last_run_summary(df: pd.DataFrame) -> dict[str, Any]:
    """Сводка по последнему запуску. Пустой dict если данных нет."""
    if df.empty:
        return {}
    last_run_id = df.sort_values("ts").iloc[-1]["run_id"]
    sub = df[df["run_id"] == last_run_id]
    finished = sub[sub["indicator"] == "_run"]
    indicators = sub[sub["indicator"] != "_run"]
    return {
        "run_id": last_run_id,
        "ts": sub["ts"].max(),
        "duration_sec": float(finished["duration_sec"].iloc[0]) if not finished.empty else None,
        "success": int((indicators["status"] == "success").sum()),
        "skip": int((indicators["status"] == "skip").sum()),
        "error": int((indicators["status"] == "error").sum()),
        "entries": indicators.to_dict("records"),
    }


def last_success_per_indicator(df: pd.DataFrame) -> pd.DataFrame:
    """По одной строке на показатель — последний успешный запуск + статус последнего любого."""
    if df.empty:
        return pd.DataFrame()
    indicators = df[df["indicator"] != "_run"].copy()
    # Последний любой запуск по каждому показателю
    last_any = indicators.sort_values("ts").groupby("indicator").tail(1)
    # Последний успешный
    last_ok = (
        indicators[indicators["status"] == "success"]
        .sort_values("ts")
        .groupby("indicator")
        .tail(1)[["indicator", "ts", "rows", "new_date"]]
        .rename(columns={"ts": "last_success_ts", "new_date": "last_success_date"})
    )
    out = last_any.merge(last_ok, on="indicator", how="left")
    return out.sort_values("indicator").reset_index(drop=True)


def load_realty_marts_manifest() -> dict[str, Any]:
    if not REALTY_MARTS_MANIFEST.exists():
        return {}
    try:
        return json.loads(REALTY_MARTS_MANIFEST.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def load_realty_update_status() -> dict[str, Any]:
    """Последний machine-readable статус `scripts/update_realty.py`."""
    if not REALTY_UPDATE_STATUS.exists():
        return {}
    try:
        data = json.loads(REALTY_UPDATE_STATUS.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def realty_update_status_summary(status: dict[str, Any]) -> dict[str, Any]:
    """Normalized dashboard-facing summary for data/processed/realty_update_status.json."""
    if not status:
        return {}
    warnings: list[str] = []
    run_status = str(status.get("status") or "").lower()
    updated_at = pd.to_datetime(status.get("updated_at"), errors="coerce")
    if not run_status:
        warnings.append("legacy")
        if pd.isna(updated_at):
            run_status = "unknown"
        else:
            run_status = "failed" if status.get("failures") or status.get("marts_ok") is False else "success"
    if pd.isna(updated_at):
        warnings.append("no heartbeat")

    log_file = status.get("log_file")
    if isinstance(log_file, str) and log_file and not (PROJECT_ROOT / log_file).is_file():
        warnings.append("log missing")
    elif log_file is not None and not isinstance(log_file, str):
        warnings.append("bad log_file")

    heartbeat_age_min = None
    if not pd.isna(updated_at):
        heartbeat_age = pd.Timestamp.now(tz=updated_at.tz) - updated_at
        heartbeat_age_min = heartbeat_age.total_seconds() / 60
    stale_running = run_status == "running" and (
        heartbeat_age_min is None or heartbeat_age_min > REALTY_RUNNING_STALE_MIN
    )
    label = {
        "running": "в работе",
        "success": "успех",
        "failed": "ошибка",
        "interrupted": "прерван",
        "unknown": "нет статуса",
    }.get(run_status, run_status or "—")
    if stale_running:
        label = "возможно завис"
    return {
        "status": run_status,
        "label": label,
        "warnings": warnings,
        "stale_running": stale_running,
        "heartbeat_age_min": heartbeat_age_min,
        "error": status.get("error") or "",
    }


def _current_realty_sources(mart: str) -> list[Path]:
    """Current raw files for a mart. Used only for freshness diagnostics."""
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            from app import data_access as da  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return []
    try:
        mapping = {
            "kvartirografia": lambda: da._raw_files(da.KVART_PATHS, ["kvartirografia_*.json"]),
            "monitoring_2_0": lambda: da._raw_files(da.MONITORING_PATHS, ["monitoring_2_0_*.xlsx"]),
            "erzrf_top": lambda: da._raw_files(da.ERZRF_PATHS, ["top_*.xlsx", "top_developers_*.json"]),
            "erzrf_cards": lambda: da._raw_files(da.ERZRF_PATHS, ["cards_*.xlsx"], recursive=True),
            "escrow_manual": lambda: da._raw_files(da.ESCROW_PATHS, ["*.xlsx"]),
            "rasprodannost": lambda: da._raw_files(da.RASPROD_PATHS, ["rasprodannost_*.xlsx"]),
            "vvod_static": lambda: da._raw_files([p for p in da.VVOD_PATHS if p.exists()], ["*.xls*", "*.txt"]),
            "emiss_34118": lambda: (
                da._raw_files([p for p in da.VVOD_PATHS if p.exists()], ["emiss_34118_base.xls"])
                + da._raw_files([PROJECT_ROOT / "downloads"], ["*Введено в действие общей площади жилых домов*.xls*"])
            ),
        }
        getter = mapping.get(mart)
        return getter() if getter else []
    except Exception:  # noqa: BLE001
        return []


def _latest_manifest_source_mtime(sources: list) -> pd.Timestamp | None:
    latest = None
    for source in sources:
        if isinstance(source, dict):
            ts = pd.to_datetime(source.get("mtime"), errors="coerce")
            if not pd.isna(ts) and (latest is None or ts > latest):
                latest = ts
    return latest


def _latest_current_source_mtime(mart: str) -> pd.Timestamp | None:
    latest = None
    for path in _current_realty_sources(mart):
        try:
            ts = pd.to_datetime(datetime.fromtimestamp(path.stat().st_mtime), errors="coerce")
        except OSError:
            continue
        if not pd.isna(ts) and (latest is None or ts > latest):
            latest = ts
    return latest


def realty_marts_status(*, live_check: bool = False) -> pd.DataFrame:
    """One row per realty mart from data/marts/realty/manifest.json."""
    manifest = load_realty_marts_manifest()
    marts = manifest.get("marts") if isinstance(manifest, dict) else {}
    if not isinstance(marts, dict) or not marts:
        return pd.DataFrame(columns=[
            "mart", "status", "rows", "cols", "built_at", "duration_sec",
            "sources", "latest_source_mtime", "error",
        ])

    manifest_built_at = pd.to_datetime(manifest.get("built_at"), errors="coerce")
    rows: list[dict[str, Any]] = []
    for mart, info in sorted(marts.items()):
        if not isinstance(info, dict):
            continue
        summary = info.get("summary") if isinstance(info.get("summary"), dict) else {}
        sources = info.get("sources") if isinstance(info.get("sources"), list) else []
        latest_source = (
            _latest_current_source_mtime(str(mart))
            if live_check
            else _latest_manifest_source_mtime(sources)
        )
        if latest_source is None and live_check:
            latest_source = _latest_manifest_source_mtime(sources)

        row_count = None
        col_count = None
        if isinstance(summary.get("rows"), int):
            row_count = summary.get("rows")
        if summary.get("type") == "dataframe":
            row_count = summary.get("rows")
            col_count = summary.get("cols")
        elif row_count is None and isinstance(summary.get("frames"), dict):
            frame_rows = [
                v.get("rows") for v in summary["frames"].values()
                if isinstance(v, dict) and isinstance(v.get("rows"), int)
            ]
            row_count = sum(frame_rows) if frame_rows else None

        built_at = pd.to_datetime(info.get("built_at"), errors="coerce") \
            if info.get("built_at") else manifest_built_at
        if info.get("error"):
            status = "error"
        elif (
            latest_source is not None
            and not pd.isna(latest_source)
            and not pd.isna(built_at)
            and latest_source > built_at
        ):
            status = "stale"
        else:
            status = "ok"

        rows.append({
            "mart": mart,
            "status": status,
            "rows": row_count,
            "cols": col_count,
            "built_at": built_at,
            "duration_sec": info.get("duration_sec"),
            "sources": len(sources),
            "latest_source_mtime": latest_source,
            "error": info.get("error", ""),
        })
    return pd.DataFrame(rows)


def latest_data_badge() -> str:
    """Строка для шапки страниц: «Данные сайта на ДД.ММ.ГГГГ ЧЧ:ММ»."""
    candidates = []
    df = load_runs()
    if not df.empty:
        successes = df[(df["status"] == "success") & (df["indicator"] != "_run")]
        if not successes.empty:
            candidates.append(successes["ts"].max())

    manifest = load_realty_marts_manifest()
    if manifest:
        marts = manifest.get("marts") if isinstance(manifest.get("marts"), dict) else {}
        mart_dates = [
            pd.to_datetime(info.get("built_at"), errors="coerce")
            for info in marts.values()
            if isinstance(info, dict) and info.get("built_at")
        ]
        candidates.extend(mart_dates)
        if not mart_dates:
            candidates.append(pd.to_datetime(manifest.get("built_at"), errors="coerce"))

    candidates = [ts for ts in candidates if not pd.isna(ts)]
    if not candidates:
        return ""
    ts = max(candidates)
    if pd.isna(ts):
        return ""
    return f"Данные сайта на {ts.strftime('%d.%m.%Y %H:%M')}"
