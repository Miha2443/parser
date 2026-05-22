"""Журнал обновлений ETL. Читает data/processed/etl_audit.jsonl."""
from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

from app.audit import last_run_summary, last_success_per_indicator, load_runs
from pipeline.notifier import format_summary
from pipeline.registry import INDICATORS

st.set_page_config(page_title="Обновления — Аналитика Москвы", page_icon="🔄", layout="wide")

STATUS_ICON = {"success": "✅", "skip": "⏭", "error": "❌", "info": "ℹ️"}

INDICATOR_TITLES = {ind.id: ind.title for ind in INDICATORS}
INDICATOR_SOURCES = {ind.id: ind.source for ind in INDICATORS}


def main() -> None:
    st.title("🔄 Журнал обновлений ETL")
    df = load_runs()
    if df.empty:
        st.info("Лог `data/processed/etl_audit.jsonl` пуст. Запустите `py pipeline/orchestrator.py`.")
        return

    last = last_run_summary(df)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Последний запуск", last["ts"].strftime("%d.%m.%Y %H:%M") if last.get("ts") is not None else "—")
    c2.metric("✅ Успехов", last.get("success", 0))
    c3.metric("⏭ Без изменений", last.get("skip", 0))
    c4.metric("❌ Ошибок", last.get("error", 0))
    if last.get("duration_sec") is not None:
        st.caption(f"Длительность: {last['duration_sec']} c · run_id={last.get('run_id', '')}")

    st.divider()

    # ─── Сводка последних запусков по показателям ─────────────────
    st.subheader("По показателям")
    per_ind = last_success_per_indicator(df)
    if per_ind.empty:
        st.info("Нет данных по показателям.")
    else:
        view = pd.DataFrame({
            "Показатель": per_ind["indicator"].map(lambda i: INDICATOR_TITLES.get(i, i)),
            "Источник": per_ind["indicator"].map(lambda i: INDICATOR_SOURCES.get(i, "—")),
            "Статус посл. запуска": per_ind["status"].map(lambda s: f"{STATUS_ICON.get(s, '·')} {s}"),
            "Посл. проверка": per_ind["ts"].dt.strftime("%d.%m.%Y %H:%M"),
            "Посл. успех": per_ind["last_success_ts"].dt.strftime("%d.%m.%Y %H:%M"),
            "Дата данных": per_ind.get("last_success_date", pd.Series([""] * len(per_ind))).fillna(""),
            "Строк": per_ind.get("rows", pd.Series([None] * len(per_ind))),
        })
        st.dataframe(view, width="stretch", hide_index=True)

    # ─── Журнал событий ───────────────────────────────────────────
    st.divider()
    st.subheader("Журнал событий")
    period_days = st.selectbox("Период", options=[7, 30, 90], index=1, format_func=lambda d: f"{d} дней")
    only_errors = st.checkbox("Только ошибки", value=False)

    since = datetime.now() - timedelta(days=period_days)
    filt = df[(df["ts"] >= since) & (df["indicator"] != "_run")].copy()
    if only_errors:
        filt = filt[filt["status"] == "error"]
    filt = filt.sort_values("ts", ascending=False)
    if filt.empty:
        st.caption("Нет событий за выбранный период.")
    else:
        show = pd.DataFrame({
            "Время": filt["ts"].dt.strftime("%d.%m.%Y %H:%M:%S"),
            "Показатель": filt["indicator"].map(lambda i: INDICATOR_TITLES.get(i, i)),
            "Статус": filt["status"].map(lambda s: f"{STATUS_ICON.get(s, '·')} {s}"),
            "Сообщение": filt.apply(
                lambda r: r.get("reason") or r.get("error") or r.get("message") or "", axis=1
            ),
            "Строк": filt.get("rows", pd.Series([None] * len(filt))),
        })
        st.dataframe(show, width="stretch", hide_index=True)

        st.download_button(
            "Скачать журнал (CSV)",
            data=show.to_csv(index=False).encode("utf-8-sig"),
            file_name="etl_audit.csv",
            mime="text/csv",
        )

    # ─── Preview Telegram-сообщения ───────────────────────────────
    st.divider()
    st.subheader("Telegram — превью последнего сообщения")
    last_entries = df[df["run_id"] == last["run_id"]].to_dict("records")
    st.code(format_summary(last_entries) or "(пусто)", language="markdown")
    st.caption(
        "Чтобы реально отправлять — создайте `config/telegram.json` с полями `token` и `chat_id`. "
        "Без файла модуль ничего не шлёт."
    )


main()
