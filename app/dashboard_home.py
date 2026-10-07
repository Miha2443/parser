"""Содержимое главной страницы дашборда."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from app.audit import (
    latest_data_badge,
    load_realty_update_status,
    realty_marts_status,
    realty_source_freshness_lines,
    realty_update_status_summary,
)
from app.components.design import apply_theme, page_header

st.set_page_config(
    page_title="Аналитика Москвы — дашборд",
    page_icon="▦",
    layout="wide",
)
apply_theme()

# === Сайдбар: свежесть данных + ручная перезагрузка ===
with st.sidebar:
    st.markdown("### Свежесть данных")
    marts = realty_marts_status()
    src_freshness = realty_source_freshness_lines(marts)
    if not marts.empty:
        built_at = marts["built_at"].max()
        errors = int((marts["status"] == "error").sum())
        stale = int((marts["status"] == "stale").sum())
        status_text = "ошибка" if errors else "устарело" if stale else "актуально"
        built_text = built_at.strftime("%d.%m.%Y %H:%M") if not pd.isna(built_at) else "—"
        src_freshness.append(f"Витрины сайта: {built_text} ({status_text})")

    realty_run = load_realty_update_status()
    if realty_run:
        summary = realty_update_status_summary(realty_run)
        status = summary.get("status")
        status_warnings = summary.get("warnings") or []
        failures = realty_run.get("failures") or []
        status_label = summary.get("label") or "—"
        state_text = "ошибка" if status in {"failed", "interrupted"} or summary.get("stale_running") else "проверить" if status_warnings else "актуально"
        suffix = f", ошибок: {len(failures)}" if failures else ""
        if status_warnings:
            suffix += f" ({', '.join(status_warnings)})"
        src_freshness.append(f"Realty-прогон: {status_label} ({state_text}){suffix}")
    st.markdown("\n".join(f"- {s}" for s in src_freshness))

    st.markdown("---")
    if st.button("Перезагрузить кеш", use_container_width=True,
                 help="Сбросить кеш данных (полезно после обновления выгрузок)"):
        st.cache_data.clear()
        st.success("Кеш сброшен")
        st.rerun()

badge = latest_data_badge()
page_header(
    "Аналитика Москвы",
    f"Обновление данных: {badge}." if badge else None,
)

st.markdown("<h2 class='ma-home-section-title'>Рынок недвижимости</h2>", unsafe_allow_html=True)
realty_cols = st.columns(3)
with realty_cols[0]:
    with st.container(border=True, height=220):
        st.markdown("### Текущее строительство")
        st.page_link("pages/0_Текущее_строительство.py", label="Оперативные данные →")
        st.page_link("pages/4_Квартирография.py", label="Квартирография →")
        st.page_link("pages/9_Карта_объектов.py", label="Карта объектов →")
with realty_cols[1]:
    with st.container(border=True, height=220):
        st.markdown("### Ввод недвижимости")
        st.page_link("pages/8_Ввод_недвижимости_оперативные.py", label="Оперативные данные →")
        st.page_link("pages/8_Ввод_недвижимости.py", label="Годовые данные →")
        st.page_link("pages/8_Ввод_линейных_объектов.py", label="Линейные объекты →")
with realty_cols[2]:
    with st.container(border=True, height=220):
        st.markdown("### Профиль застройщика")
        st.page_link("pages/7_Профиль_застройщика.py", label="Профиль →")
        st.page_link("pages/5_Квартирография_по_девелоперу.py", label="Квартирография по застройщику →")

st.markdown("<h2 class='ma-home-section-title'>Данные Мосстата / Росстата</h2>", unsafe_allow_html=True)
stat_cols = st.columns(3)
with stat_cols[0]:
    with st.container(border=True):
        st.markdown("### ВВП и ВРП")
        st.page_link("pages/3_ВРП_и_ВВП.py", label="Открыть →")
with stat_cols[1]:
    with st.container(border=True):
        st.markdown("### ИПЦ")
        st.page_link("pages/2_ИПЦ.py", label="Открыть →")
with stat_cols[2]:
    with st.container(border=True):
        st.markdown("### Заработная плата")
        st.page_link("pages/1_Заработная_плата.py", label="Открыть →")

st.markdown("### 3–4. Сервис")
service_cols = st.columns(2)
with service_cols[0]:
    st.page_link("pages/8_Отправка_в_TDM.py", label="Отправка в TDM →")
with service_cols[1]:
    st.page_link("pages/99_Обновления.py", label="Журнал обновлений →")
