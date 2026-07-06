"""Журнал обновлений ETL и состояние realty-витрин."""
from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

from app.audit import (
    PROJECT_ROOT,
    last_run_summary,
    last_success_per_indicator,
    load_realty_update_status,
    load_runs,
    realty_marts_status,
)
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
        last = {}
    else:
        last = last_run_summary(df)
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Последний запуск", last["ts"].strftime("%d.%m.%Y %H:%M") if last.get("ts") is not None else "—")
        c2.metric("✅ Успехов", last.get("success", 0))
        c3.metric("⏭ Без изменений", last.get("skip", 0))
        c4.metric("❌ Ошибок", last.get("error", 0))
        if last.get("duration_sec") is not None:
            st.caption(f"Длительность: {last['duration_sec']} c · run_id={last.get('run_id', '')}")

    st.divider()

    # ─── Последний прогон update_realty.py ────────────────────────
    st.subheader("Последний realty-прогон")
    realty_status = load_realty_update_status()
    if not realty_status:
        st.info("Нет `data/processed/realty_update_status.json`. Запустите `scripts\\update_realty.py`.")
    else:
        finished_at = pd.to_datetime(realty_status.get("finished_at"), errors="coerce")
        updated_at = pd.to_datetime(realty_status.get("updated_at"), errors="coerce")
        duration_sec = realty_status.get("duration_sec")
        successes = realty_status.get("successes") or []
        failures = realty_status.get("failures") or []
        requested = realty_status.get("sources_requested") or []
        completed_sources = realty_status.get("completed_sources") or []
        pending_sources = realty_status.get("pending_sources") or []
        current_stage = realty_status.get("current_stage") or ""
        status_warnings = []
        if "status" not in realty_status:
            status_warnings.append("legacy status: нет поля `status`")
        if "updated_at" not in realty_status:
            status_warnings.append("нет heartbeat `updated_at`")
        log_file = realty_status.get("log_file")
        if isinstance(log_file, str) and log_file and not (PROJECT_ROOT / log_file).is_file():
            status_warnings.append(f"лог не найден: {log_file}")
        elif log_file is not None and not isinstance(log_file, str):
            status_warnings.append("поле `log_file` не строка")
        status_error = realty_status.get("error") or ""
        run_status = str(realty_status.get("status") or "").lower()
        if not run_status:
            run_status = "failed" if failures or realty_status.get("marts_ok") is False else "success"
        marts_selected = realty_status.get("marts_selected")
        marts_changed_aliases = realty_status.get("marts_changed_aliases") or []
        marts_repair_selected = realty_status.get("marts_repair_selected") or []
        diff = realty_status.get("diff") or {}
        status_label = {
            "running": "в работе",
            "success": "успех",
            "failed": "ошибка",
            "interrupted": "прерван",
        }.get(run_status, run_status or "—")
        heartbeat_age_min = None
        if not pd.isna(updated_at):
            heartbeat_age = pd.Timestamp.now(tz=updated_at.tz) - updated_at
            heartbeat_age_min = heartbeat_age.total_seconds() / 60
        stale_running = run_status == "running" and (
            heartbeat_age_min is None or heartbeat_age_min > 360
        )
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Статус", "возможно завис" if stale_running else status_label)
        c2.metric("Завершён", finished_at.strftime("%d.%m.%Y %H:%M") if not pd.isna(finished_at) else "—")
        c3.metric("Источники", f"{len(successes)}/{len(requested)}")
        c4.metric("Ошибок", len(failures))
        c5.metric("Новых/изм.", len(diff.get("added", [])) + len(diff.get("changed", [])))
        if status_warnings:
            st.warning("Проблемы status-файла: " + "; ".join(status_warnings))
        if status_error:
            st.error("Ошибка realty-прогона: " + str(status_error))
        if run_status == "running":
            if stale_running:
                st.warning("Realty-прогон давно не обновлял heartbeat. Проверьте лог и процессы Python/Chrome.")
            else:
                st.info("Realty-прогон сейчас выполняется или был прерван до финальной записи статуса.")
            if completed_sources or pending_sources:
                parts = [f"готово: {len(completed_sources)}/{len(requested)}"]
                if current_stage:
                    parts.append(f"этап: {current_stage}")
                if pending_sources:
                    parts.append("ожидает: " + ", ".join(map(str, pending_sources)))
                if heartbeat_age_min is not None:
                    parts.append(f"heartbeat: {heartbeat_age_min:.0f} мин назад")
                st.caption(" · ".join(parts))
        elif run_status == "interrupted":
            st.warning("Последний realty-прогон был прерван.")
        elif run_status == "failed":
            st.error("Последний realty-прогон завершился с ошибкой.")
        try:
            duration_min = round(float(duration_sec) / 60, 1) if duration_sec is not None else None
        except (TypeError, ValueError):
            duration_min = None
        if duration_min is not None:
            st.caption(
                f"Длительность: {duration_min} мин · "
                f"log={realty_status.get('log_file', '—')}"
            )
        if marts_selected is None:
            st.caption("Realty-витрины: полный rebuild")
        elif marts_selected:
            st.caption("Realty-витрины: " + ", ".join(map(str, marts_selected)))
        else:
            st.caption("Realty-витрины: сборка не требовалась")
        if marts_changed_aliases:
            st.caption("Причина rebuild: " + ", ".join(map(str, marts_changed_aliases)))
        if marts_repair_selected:
            st.caption("Repair: " + ", ".join(map(str, marts_repair_selected)))
        if failures:
            st.error("Ошибки источников: " + ", ".join(map(str, failures)))

    st.divider()

    # ─── Быстрые realty-витрины ─────────────────────────────────
    st.subheader("Realty-витрины сайта")
    marts = realty_marts_status()
    if marts.empty:
        st.info("Нет `data/marts/realty/manifest.json`. Запустите `scripts\\build_realty_marts.bat`.")
    else:
        has_errors = (marts["status"] == "error").any()
        has_stale = (marts["status"] == "stale").any()
        c1, c2, c3, c4 = st.columns(4)
        built_at = pd.to_datetime(marts["built_at"], errors="coerce").max()
        c1.metric("Собраны", built_at.strftime("%d.%m.%Y %H:%M") if not pd.isna(built_at) else "—")
        c2.metric("Витрин", len(marts))
        c3.metric("Проблем", int((marts["status"].isin(["error", "stale"])).sum()))
        c4.metric("Строк", int(marts["rows"].dropna().sum()) if "rows" in marts else 0)
        status_label = {
            "ok": "✅ ok",
            "stale": "🟡 stale",
            "error": "❌ error",
        }
        view = pd.DataFrame({
            "Витрина": marts["mart"],
            "Статус": marts["status"].map(lambda s: status_label.get(s, str(s))),
            "Строк": marts["rows"],
            "Источников": marts["sources"],
            "Сборка, c": marts["duration_sec"],
            "Свежий source": pd.to_datetime(
                marts["latest_source_mtime"], errors="coerce"
            ).dt.strftime("%d.%m.%Y %H:%M"),
            "Ошибка": marts["error"],
        })
        st.dataframe(view, width="stretch", hide_index=True)
        if has_errors:
            st.error("Есть ошибки сборки realty-витрин. Сайт откатится на raw-чтение, но страницы будут медленнее.")
        elif has_stale:
            st.warning("Есть устаревшие realty-витрины: raw-файл свежее mart. Сайт откатится на raw-чтение для этих страниц.")

    st.divider()

    # ─── Сводка последних запусков по показателям ─────────────────
    st.subheader("По показателям")
    per_ind = last_success_per_indicator(df) if not df.empty else pd.DataFrame()
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

    if df.empty:
        st.caption("Нет событий за выбранный период.")
    else:
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
    if df.empty or not last:
        st.code("(пусто)", language="markdown")
    else:
        last_entries = df[df["run_id"] == last["run_id"]].to_dict("records")
        st.code(format_summary(last_entries) or "(пусто)", language="markdown")
    st.caption(
        "Чтобы реально отправлять — создайте `config/telegram.json` с полями `token` и `chat_id`. "
        "Без файла модуль ничего не шлёт."
    )


main()
