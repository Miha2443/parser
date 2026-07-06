"""Главная страница дашборда."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from app.audit import latest_data_badge, realty_marts_status
from app.data_access import latest_loaded_at, load_ipc, load_salary

st.set_page_config(
    page_title="Аналитика Москвы — дашборд",
    page_icon="📊",
    layout="wide",
)

# === Сайдбар: свежесть данных + ручная перезагрузка ===
with st.sidebar:
    st.markdown("### 🔄 Свежесть данных")
    realty = Path(__file__).resolve().parent.parent / "data" / "raw" / "realty"
    src_freshness = []
    for name, pattern in [
        ("Мониторинг 2.0", "nashdom/monitoring_2_0_*.xlsx"),
        ("Квартирография", "nashdom/kvartirografia_*.xlsx"),
        ("Распроданность", "nashdom/rasprodannost_*.xlsx"),
        ("ERZRF топ", "erzrf/top_obyem_stroitelstva_rf_*.xlsx"),
        ("ERZRF карточки", "erzrf/cards/cards_*.xlsx"),
        ("Эскроу", "escrow_manual/*.xlsx"),
    ]:
        files = list(realty.glob(pattern))
        files = [f for f in files if "_archive" not in f.parts]
        if not files:
            src_freshness.append(f"❌ {name}: нет файла")
            continue
        latest = max(files, key=lambda p: p.stat().st_mtime)
        mtime = datetime.fromtimestamp(latest.stat().st_mtime)
        days = (datetime.now() - mtime).days
        icon = "🟢" if days <= 1 else "🟡" if days <= 7 else "🟠" if days <= 30 else "🔴"
        src_freshness.append(f"{icon} {name}: {mtime.strftime('%d.%m.%Y')} ({days}д.)")

    marts = realty_marts_status()
    if not marts.empty:
        built_at = marts["built_at"].max()
        errors = int((marts["status"] == "error").sum())
        stale = int((marts["status"] == "stale").sum())
        icon = "🔴" if errors else "🟡" if stale else "🟢"
        built_text = built_at.strftime("%d.%m.%Y %H:%M") if not pd.isna(built_at) else "—"
        src_freshness.append(f"{icon} Витрины сайта: {built_text}")
    st.markdown("\n".join(f"- {s}" for s in src_freshness))

    st.markdown("---")
    if st.button("♻️ Перезагрузить кеш", use_container_width=True,
                 help="Сбросить кеш данных (полезно после обновления выгрузок)"):
        st.cache_data.clear()
        st.success("Кеш сброшен")
        st.rerun()

st.title("📊 Аналитика Москвы")
badge = latest_data_badge()
st.caption(
    ("Тестовая версия дашборда — разделы «Занятость и заработная плата» и «Цены».")
    + (f" · {badge}" if badge else "")
)

salary = load_salary()
ipc = load_ipc()

st.markdown("### Доступные показатели")

col1, col2, col3 = st.columns(3)
with col1:
    with st.container(border=True):
        st.markdown("#### Заработная плата")
        st.write("Среднемесячная номинальная начисленная заработная плата работающих в экономике.")
        st.write(
            f"**Регионы:** {', '.join(sorted(salary['region'].unique())) if not salary.empty else '—'}"
        )
        st.write(
            f"**Отрасли:** {', '.join(sorted(salary['view'].unique())) if not salary.empty else '—'}"
        )
        st.write(f"**Обновлено:** {latest_loaded_at(salary)}")
        st.page_link("pages/1_Заработная_плата.py", label="Открыть раздел →")

with col2:
    with st.container(border=True):
        st.markdown("#### ИПЦ")
        st.write("Индексы потребительских цен на товары и услуги.")
        st.write(
            f"**Регионы:** {', '.join(sorted(ipc['region'].unique())) if not ipc.empty else '—'}"
        )
        st.write(f"**Обновлено:** {latest_loaded_at(ipc)}")
        st.page_link("pages/2_ИПЦ.py", label="Открыть раздел →")

with col3:
    with st.container(border=True):
        st.markdown("#### ВРП и ВВП")
        st.write("ВВП России, ВРП Москвы и валовая добавленная стоимость по отраслям.")
        st.page_link("pages/3_ВРП_и_ВВП.py", label="Открыть раздел →")

st.markdown("---")
st.page_link("pages/99_Обновления.py", label="🔄 Журнал обновлений ETL")
st.markdown("---")
st.markdown(
    """
**Как пользоваться**
1. Выберите раздел в боковом меню или по ссылке выше.
2. Внутри раздела переключатель **Год / Квартал / Месяц** меняет тип графика и таблицы.
3. Для месяца и квартала можно выбрать конкретные периоды через `+`-меню.
4. Данные и графики экспортируются кнопками под таблицами.
"""
)
