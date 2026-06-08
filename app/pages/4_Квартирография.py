"""Квартирография — дашборд в стиле наш.дом.рф.

Layout повторяет оригинальную страницу:
- Селектор региона (РФ / Москва) вверху
- 4 блока:
  · Типы квартир (5 строк × 2 показателя)
  · Распределение квартир по площади (гистограмма)
  · Объём строительства по девелоперам (таблица)
  · Объём строительства по регионам (таблица)
"""
from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from app.data_access import load_kvartirografia

st.set_page_config(page_title="Квартирография — Аналитика Москвы", layout="wide")

# Цветовая палитра наш.дом.рф
COLOR_1K = "#8BC540"   # green — 1 комн
COLOR_2K = "#4EC3E0"   # blue — 2 комн
COLOR_3K = "#E4E7E8"   # light gray — 3 комн
COLOR_4K = "#072833"   # dark navy — 4+ комн


def ru_num(value, digits=0):
    if value is None or pd.isna(value):
        return "—"
    try:
        s = f"{float(value):,.{digits}f}"
    except (TypeError, ValueError):
        return str(value)
    return s.replace(",", " ").replace(".", ",")


data = load_kvartirografia()
if data["apartments"].empty:
    st.warning(
        "Нет данных. Положи `kvartirografia_<date>.json` в "
        "`data/raw/realty/nashdom/` или в `nashdom/` в корне репо."
    )
    st.stop()

st.title("Квартирография жилищного строительства")

cols_top = st.columns([3, 2])
with cols_top[0]:
    region_map = {"rf": "Российская Федерация", "msk": "Город Москва"}
    available = [r for r in data["regions_available"] if r in region_map]
    if not available:
        st.error("В данных нет регионов rf/msk")
        st.stop()
    sel = st.radio(
        "Регион",
        available,
        format_func=lambda x: region_map[x],
        horizontal=True,
        key="kvart_region",
    )
with cols_top[1]:
    st.markdown(f"<div style='padding-top:30px;color:#7A8386;'>"
                f"Отчёт по данным на <b>{data['report_date']}</b></div>",
                unsafe_allow_html=True)

# === Блок 1: типы квартир + распределение ===
b1_left, b1_right = st.columns(2)

with b1_left:
    st.subheader("Типы квартир")
    apartments = data["apartments"]
    df = apartments[apartments["region_key"] == sel].copy()
    if df.empty:
        st.info("Нет данных для региона")
    else:
        df_show = pd.DataFrame({
            "Тип квартир": df["тип"],
            "Количество квартир, шт.": df["количество_шт_num"].apply(lambda v: ru_num(v)),
            "Площадь, тыс. м²": df["площадь_тыс_м²_num"].apply(lambda v: ru_num(v)),
        })
        st.dataframe(df_show, hide_index=True, use_container_width=True)

with b1_right:
    st.subheader("Распределение квартир по площади, м²")
    distribution = data["distribution"]
    df = distribution[distribution["region_key"] == sel].copy()
    if df.empty:
        st.info("Нет данных")
    else:
        fig = px.bar(
            df, x="диапазон", y="доля_num",
            text=df["доля_num"].apply(lambda v: f"{int(v)}%" if v is not None else ""),
            color_discrete_sequence=[COLOR_1K],
        )
        fig.update_traces(textposition="outside")
        fig.update_layout(
            margin=dict(l=0, r=0, t=10, b=0), height=350,
            yaxis_title="", xaxis_title="", showlegend=False,
        )
        fig.update_yaxes(visible=False)
        st.plotly_chart(fig, use_container_width=True)


# === Блок 2 + 3: таблицы по девелоперам и регионам ===
def render_top_table(df: pd.DataFrame, name_label: str, top_n: int = 50):
    """Рендерит таблицу с цветными процентами комнатности."""
    if df.empty:
        st.info("Нет данных")
        return
    df_sorted = df.sort_values("площадь_тыс_м²_num", ascending=False).head(top_n)
    # st.dataframe с column_config для прогресс-баров
    df_show = pd.DataFrame({
        name_label: df_sorted["наименование"],
        "Квартиры, тыс. шт": df_sorted["квартиры_тыс_шт_num"],
        "Площадь, тыс. м²": df_sorted["площадь_тыс_м²_num"],
        "1 комн, %": df_sorted["доля_1комн_%_num"],
        "2 комн, %": df_sorted["доля_2комн_%_num"],
        "3 комн, %": df_sorted["доля_3комн_%_num"],
        "4+ комн, %": df_sorted["доля_4+комн_%_num"],
    })
    st.dataframe(
        df_show,
        hide_index=True,
        use_container_width=True,
        height=min(38 * (len(df_show) + 1), 600),
        column_config={
            "Квартиры, тыс. шт": st.column_config.NumberColumn(format="%.1f"),
            "Площадь, тыс. м²": st.column_config.NumberColumn(format="%d"),
            "1 комн, %": st.column_config.ProgressColumn(
                format="%d%%", min_value=0, max_value=100,
            ),
            "2 комн, %": st.column_config.ProgressColumn(
                format="%d%%", min_value=0, max_value=100,
            ),
            "3 комн, %": st.column_config.ProgressColumn(
                format="%d%%", min_value=0, max_value=100,
            ),
            "4+ комн, %": st.column_config.ProgressColumn(
                format="%d%%", min_value=0, max_value=100,
            ),
        },
    )


b2_left, b2_right = st.columns(2)

with b2_left:
    devs = data["developers"]
    df = devs[devs["region_key"] == sel].copy()
    st.subheader(f"Объём строительства по девелоперам ({len(df)})")
    render_top_table(df, "Девелопер")

with b2_right:
    regs = data["regions"]
    df = regs[regs["region_key"] == sel].copy()
    st.subheader(f"Объём строительства по регионам ({len(df)})")
    render_top_table(df, "Регион")
