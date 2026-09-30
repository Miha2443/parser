"""Будущий раздел оперативных данных по текущему строительству."""
from __future__ import annotations

import streamlit as st

from app.components.design import apply_theme, page_header


st.set_page_config(page_title="Текущее строительство — Аналитика Москвы", layout="wide")
apply_theme()

page_header(
    "Текущее строительство",
    "Оперативные показатели строящихся объектов. Состав страницы будет добавлен после согласования.",
)

st.info("Раздел подготовлен в навигации. Показатели и визуализации будут добавлены следующим этапом.")
