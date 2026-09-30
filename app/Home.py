"""Единая точка входа и навигация дашборда."""
from __future__ import annotations

import streamlit as st

from app.components.design import apply_theme, theme_selector


st.set_page_config(
    page_title="Аналитика Москвы",
    page_icon="▦",
    layout="wide",
)

pages = {
    "Главная": [
        st.Page("dashboard_home.py", title="Главная", icon="🏠", default=True),
    ],
    "Рынок недвижимости": [
        st.Page("pages/0_Текущее_строительство.py", title="Текущее строительство", icon="🏗️"),
        st.Page("pages/4_Квартирография.py", title="Квартирография", icon="🏙️"),
        st.Page("pages/5_Квартирография_по_девелоперу.py", title="Квартиры по девелоперу", icon="🏢"),
        st.Page("pages/6_Распроданность.py", title="Распроданность", icon="📊"),
        st.Page("pages/9_Карта_объектов.py", title="Карта объектов", icon="🗺️"),
        st.Page("pages/8_Ввод_недвижимости.py", title="Ввод недвижимости · год", icon="🏘️"),
        st.Page("pages/7_Профиль_застройщика.py", title="Профиль застройщика", icon="👤"),
    ],
    "Мосстат / Росстат": [
        st.Page("pages/3_ВРП_и_ВВП.py", title="ВВП и ВРП", icon="📈"),
        st.Page("pages/2_ИПЦ.py", title="ИПЦ", icon="🧮"),
        st.Page("pages/1_Заработная_плата.py", title="Заработная плата", icon="💰"),
    ],
    "Сервис": [
        st.Page("pages/8_Отправка_в_TDM.py", title="Отправка в TDM", icon="📤"),
        st.Page("pages/99_Обновления.py", title="Журнал обновлений", icon="🕘"),
    ],
}

navigation = st.navigation(pages, position="sidebar", expanded=True)
with st.sidebar:
    theme_selector()

apply_theme()
navigation.run()
