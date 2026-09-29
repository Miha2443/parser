"""Главная страница дашборда."""
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
from app.data_access import dataset_download_summary, load_ipc, load_salary

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
    ("Единый дашборд по экономическим показателям, рынку недвижимости "
     "и состоянию обновления источников.")
    + (f" Обновление данных: {badge}." if badge else ""),
)

salary = load_salary()
ipc = load_ipc()

summary_cols = st.columns(4)
summary_cols[0].metric("Разделов", "10")
summary_cols[1].metric("Регионы в зарплатах", len(salary["region"].unique()) if not salary.empty else 0)
summary_cols[2].metric("Регионы в ИПЦ", len(ipc["region"].unique()) if not ipc.empty else 0)
summary_cols[3].metric("Формат", "BI")

st.markdown("### Доступные показатели")


def _feature_card(index: str, title: str, body: str, meta: list[str]) -> None:
    meta_html = "<br>".join(meta)
    st.markdown(
        f"""
        <div class="ma-feature-card">
          <div class="ma-feature-index">{index}</div>
          <h3>{title}</h3>
          <p>{body}</p>
          <div class="ma-feature-meta">{meta_html}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


col1, col2, col3 = st.columns(3)
with col1:
    _feature_card(
        "01 / Экономика",
        "Заработная плата",
        "Среднемесячная номинальная начисленная зарплата по Москве и РФ с разрезом по отраслям.",
        [
            f"<b>Регионы:</b> {', '.join(sorted(salary['region'].unique())) if not salary.empty else '—'}",
            f"<b>Отрасли:</b> {', '.join(sorted(salary['view'].unique())) if not salary.empty else '—'}",
            f"{dataset_download_summary(salary)}",
        ],
    )
    st.page_link("pages/1_Заработная_плата.py", label="Открыть раздел →")

with col2:
    _feature_card(
        "02 / Цены",
        "ИПЦ",
        "Индексы потребительских цен на товары и услуги: год, квартал и месяц.",
        [
            f"<b>Регионы:</b> {', '.join(sorted(ipc['region'].unique())) if not ipc.empty else '—'}",
            f"{dataset_download_summary(ipc)}",
        ],
    )
    st.page_link("pages/2_ИПЦ.py", label="Открыть раздел →")

with col3:
    _feature_card(
        "03 / Нацсчета",
        "ВРП и ВВП",
        "ВВП России, ВРП Москвы и валовая добавленная стоимость по отраслям.",
        [
            "<b>Период:</b> с 2011 года",
            "<b>Состав:</b> 6 аналитических блоков",
        ],
    )
    st.page_link("pages/3_ВРП_и_ВВП.py", label="Открыть раздел →")

st.markdown("### Недвижимость и девелопмент")
realty_cols = st.columns(3)
with realty_cols[0]:
    _feature_card(
        "04 / Жильё",
        "Карта объектов",
        "Введённые и строящиеся объекты мониторинга на карте Москвы с фильтром по застройщику.",
        ["<b>Слой:</b> РВ + ОКС", "<b>Фильтры:</b> статус, год, округ"],
    )
    st.page_link("pages/9_Карта_объектов.py", label="Открыть карту →")
with realty_cols[1]:
    _feature_card(
        "05 / Жильё",
        "Квартирография",
        "Структура квартир, распределение по площади и рейтинги девелоперов/регионов.",
        ["<b>Срезы:</b> Москва / РФ", "<b>Формат:</b> таблицы + графики"],
    )
    st.page_link("pages/4_Квартирография.py", label="Открыть раздел →")
with realty_cols[2]:
    _feature_card(
        "06 / Девелоперы",
        "Профиль застройщика",
        "Карточка группы компаний: ввод, строительство, рейтинги, переносы и эскроу.",
        ["<b>Источники:</b> 5 витрин", "<b>Режим:</b> выбор застройщика"],
    )
    st.page_link("pages/7_Профиль_застройщика.py", label="Открыть раздел →")
realty_cols = st.columns(3)
with realty_cols[0]:
    _feature_card(
        "07 / Продажи",
        "Распроданность",
        "KPI распроданности и стройготовности жилья по периодам и сегментам.",
        ["<b>Период:</b> помесячно", "<b>Срезы:</b> регионы и сегменты"],
    )
    st.page_link("pages/6_Распроданность.py", label="Открыть раздел →")
with realty_cols[1]:
    _feature_card(
        "08 / Ввод",
        "Ввод недвижимости",
        "Годовые значения по жилой и нежилой недвижимости, бюджету и отраслям.",
        ["<b>Период:</b> 2011-2026", "<b>Срез:</b> Москва / РФ"],
    )
    st.page_link("pages/8_Ввод_недвижимости.py", label="Открыть раздел →")

st.markdown("---")
st.page_link("pages/99_Обновления.py", label="Журнал обновлений ETL")
st.markdown("---")
st.markdown(
    """
**Как пользоваться**
1. Выберите раздел в боковом меню или по ссылке выше. На телефоне меню открывается кнопкой в верхней панели.
2. Внутри раздела переключатель **Год / Квартал / Месяц** меняет тип графика и таблицы.
3. Для месяца и квартала можно выбрать конкретные периоды через `+`-меню.
4. Данные и графики экспортируются кнопками под таблицами.
"""
)
