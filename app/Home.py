"""Единая точка входа и навигация дашборда."""
from __future__ import annotations

import streamlit as st

from app.components.design import apply_theme


st.set_page_config(
    page_title="Аналитика Москвы",
    page_icon="▦",
    layout="wide",
)

pages = [
    st.Page("dashboard_home.py", title="Главная", icon="🏠", default=True),
    st.Page("pages/0_Текущее_строительство.py", title="Оперативные данные", icon="🏗️"),
    st.Page("pages/4_Квартирография.py", title="Квартирография", icon="🏙️"),
    st.Page("pages/6_Распроданность.py", title="Распроданность", icon="📊"),
    st.Page("pages/9_Карта_объектов.py", title="Карта объектов", icon="🗺️"),
    st.Page("pages/8_Ввод_недвижимости_оперативные.py", title="Оперативный ввод", icon="⏱️"),
    st.Page("pages/8_Ввод_недвижимости.py", title="Годовой ввод", icon="🏘️"),
    st.Page("pages/7_Профиль_застройщика.py", title="Профиль", icon="👤"),
    st.Page("pages/5_Квартирография_по_девелоперу.py", title="Квартирография по застройщику", icon="🏢"),
    st.Page("pages/3_ВРП_и_ВВП.py", title="ВВП и ВРП", icon="📈"),
    st.Page("pages/2_ИПЦ.py", title="ИПЦ", icon="🧮"),
    st.Page("pages/1_Заработная_плата.py", title="Заработная плата", icon="💰"),
    st.Page("pages/8_Отправка_в_TDM.py", title="Отправка в TDM", icon="📤"),
    st.Page("pages/99_Обновления.py", title="Журнал обновлений", icon="🕘"),
]

navigation = st.navigation(pages, position="hidden")
with st.sidebar:
    st.page_link("dashboard_home.py", label="Главная", icon="🏠")
    with st.expander("Рынок недвижимости", expanded=True):
        st.markdown("**Текущее строительство**")
        st.page_link("pages/0_Текущее_строительство.py", label="Оперативные данные", icon="🏗️")
        st.page_link("pages/4_Квартирография.py", label="Квартирография", icon="🏙️")
        st.page_link("pages/9_Карта_объектов.py", label="Карта объектов", icon="🗺️")
        st.page_link("pages/6_Распроданность.py", label="Распроданность", icon="📊")
        st.markdown("**Ввод недвижимости**")
        st.page_link("pages/8_Ввод_недвижимости_оперативные.py", label="Оперативные данные", icon="⏱️")
        st.page_link("pages/8_Ввод_недвижимости.py", label="Годовые данные", icon="🏘️")
        st.markdown("**Профиль застройщика**")
        st.page_link("pages/7_Профиль_застройщика.py", label="Профиль", icon="👤")
        st.page_link("pages/5_Квартирография_по_девелоперу.py", label="Квартирография по застройщику", icon="🏢")
    with st.expander("Мосстат / Росстат", expanded=True):
        st.page_link("pages/3_ВРП_и_ВВП.py", label="ВВП и ВРП", icon="📈")
        st.page_link("pages/2_ИПЦ.py", label="ИПЦ", icon="🧮")
        st.page_link("pages/1_Заработная_плата.py", label="Заработная плата", icon="💰")
    with st.expander("Сервис", expanded=False):
        st.page_link("pages/8_Отправка_в_TDM.py", label="Отправка в TDM", icon="📤")
        st.page_link("pages/99_Обновления.py", label="Журнал обновлений", icon="🕘")
apply_theme()
navigation.run()
