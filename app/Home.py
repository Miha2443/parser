"""Главная страница дашборда."""
from __future__ import annotations

import streamlit as st

from app.audit import latest_data_badge
from app.data_access import latest_loaded_at, load_ipc, load_salary

st.set_page_config(
    page_title="Аналитика Москвы — дашборд",
    page_icon="📊",
    layout="wide",
)

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
