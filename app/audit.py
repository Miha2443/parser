"""Чтение audit log для UI: группировка по запускам, агрегаты."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from pipeline.audit import read_audit

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REALTY_MARTS_MANIFEST = PROJECT_ROOT / "data" / "marts" / "realty" / "manifest.json"


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


def realty_marts_status() -> pd.DataFrame:
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
        latest_source = None
        for source in sources:
            if isinstance(source, dict):
                ts = pd.to_datetime(source.get("mtime"), errors="coerce")
                if not pd.isna(ts) and (latest_source is None or ts > latest_source):
                    latest_source = ts

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

        rows.append({
            "mart": mart,
            "status": "error" if info.get("error") else "ok",
            "rows": row_count,
            "cols": col_count,
            "built_at": pd.to_datetime(info.get("built_at"), errors="coerce")
            if info.get("built_at") else manifest_built_at,
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
