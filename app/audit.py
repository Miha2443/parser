"""Чтение audit log для UI: группировка по запускам, агрегаты."""
from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd

from pipeline.audit import read_audit


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


def latest_data_badge() -> str:
    """Строка для шапки страниц: «Данные на ДД.ММ.ГГГГ ЧЧ:ММ»."""
    df = load_runs()
    if df.empty:
        return ""
    successes = df[(df["status"] == "success") & (df["indicator"] != "_run")]
    if successes.empty:
        return ""
    ts = successes["ts"].max()
    if pd.isna(ts):
        return ""
    return f"Данные на {ts.strftime('%d.%m.%Y %H:%M')}"
