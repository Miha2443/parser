"""Квартирография — детальная карточка по одному девелоперу.

Выбираешь регион + девелопера → видишь его карточку:
- Имя + место в рейтинге (по площади)
- KPI: квартиры, площадь
- Pie chart распределения по комнатности
- Сравнение с другими девелоперами (опционально)
"""
from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from app.components.design import COLORS, apply_theme, page_header, style_plotly
from app.components.export import chart_data_expander
from app.data_access import load_kvartirografia

st.set_page_config(page_title="Квартирография — Девелопер — Аналитика", layout="wide")
apply_theme()

COLOR_1K = COLORS["green"]
COLOR_2K = COLORS["blue"]
COLOR_3K = COLORS["amber"]
COLOR_4K = COLORS["red"]
ROOM_COLORS = [COLOR_1K, COLOR_2K, COLOR_3K, COLOR_4K]


def ru_num(value, digits=0):
    if value is None or pd.isna(value):
        return "—"
    try:
        s = f"{float(value):,.{digits}f}"
    except (TypeError, ValueError):
        return str(value)
    return s.replace(",", " ").replace(".", ",")


data = load_kvartirografia()
if data["developers"].empty:
    st.warning(
        "Нет данных. Положи `kvartirografia_<date>.json` в "
        "`data/raw/realty/nashdom/` или в `nashdom/` в корне репо."
    )
    st.stop()

page_header("Квартирография — по девелоперу")

region_map = {"rf": "Российская Федерация", "msk": "Город Москва"}
available = sorted(
    [r for r in data["regions_available"] if r in region_map],
    key=lambda value: 0 if value == "msk" else 1,
)
if not available:
    st.error("В данных нет регионов rf/msk")
    st.stop()

cols_filt = st.columns([2, 4, 2])
with cols_filt[0]:
    sel_reg = st.radio(
        "Регион",
        available,
        format_func=lambda x: region_map[x],
        key="dev_region",
        horizontal=True,
    )

devs_all = data["developers"]
devs = devs_all[devs_all["region_key"] == sel_reg].copy()
devs = devs.sort_values("площадь_тыс_м²_num", ascending=False).reset_index(drop=True)
devs["place"] = devs.index + 1

with cols_filt[1]:
    if devs.empty:
        st.info("Нет девелоперов для этого региона")
        st.stop()
    options = devs["наименование"].tolist()
    sel_dev = st.selectbox(
        "Девелопер",
        options,
        key="dev_select",
        help="Поиск по началу названия",
    )
with cols_filt[2]:
    st.markdown(
        f"<div style='padding-top:30px;color:{COLORS['muted']};'>"
        f"Отчёт на <b>{data['report_date']}</b></div>",
        unsafe_allow_html=True,
    )

row = devs[devs["наименование"] == sel_dev].iloc[0]

# === Карточка ===
st.markdown(f"### {row['наименование']}")
st.write(f"Место по объёму строительства в регионе: **{int(row['place'])}** из {len(devs)}")

kpi_cols = st.columns(4)
kpi_cols[0].metric("Квартиры", f"{ru_num(row['квартиры_тыс_шт_num'], 1)} тыс. шт")
kpi_cols[1].metric("Площадь", f"{ru_num(row['площадь_тыс_м²_num'])} тыс. м²")

# Расчёт средней площади квартиры
qty = row["квартиры_тыс_шт_num"]
area = row["площадь_тыс_м²_num"]
if qty and area and qty > 0:
    avg_area = (area * 1000) / (qty * 1000)  # м² на квартиру
    kpi_cols[2].metric("Ср. площадь квартиры", f"{ru_num(avg_area, 1)} м²")
    reference_rows = data["apartments"]
    reference_lines = []
    for region_key, label in [("msk", "Москва"), ("rf", "РФ")]:
        reference = reference_rows[reference_rows["region_key"].eq(region_key) & reference_rows["тип"].eq("Все квартиры")]
        if not reference.empty:
            item = reference.iloc[0]
            reference_qty = pd.to_numeric(item["количество_шт_num"], errors="coerce")
            reference_area = pd.to_numeric(item["площадь_тыс_м²_num"], errors="coerce")
            if pd.notna(reference_qty) and reference_qty > 0 and pd.notna(reference_area):
                reference_lines.append(f"{label}: **{ru_num(reference_area * 1000 / reference_qty, 1)} м²**")
    if reference_lines:
        kpi_cols[2].markdown("Средняя по всем квартирам — " + " · ".join(reference_lines))
kpi_cols[3].metric(
    "Доля рынка региона",
    f"{ru_num(row['площадь_тыс_м²_num'] / devs['площадь_тыс_м²_num'].sum() * 100, 2)}%",
)

# === Распределение по комнатности ===
st.markdown("#### Структура портфеля по комнатности")
chart_l, chart_r = st.columns([2, 3])

shares = {
    "1 комн": row["доля_1комн_%_num"],
    "2 комн": row["доля_2комн_%_num"],
    "3 комн": row["доля_3комн_%_num"],
    "4+ комн": row["доля_4+комн_%_num"],
}
shares = {k: v for k, v in shares.items() if v is not None}
shares_df = pd.DataFrame([{"Тип": k, "Доля, %": v} for k, v in shares.items()])

with chart_l:
    if shares:
        fig = go.Figure(go.Pie(
            labels=list(shares.keys()),
            values=list(shares.values()),
            marker=dict(colors=ROOM_COLORS[:len(shares)]),
            textinfo="label+percent",
            textposition="inside",
            insidetextorientation="radial",
            hovertemplate="%{label}: %{value}%<extra></extra>",
            sort=False,
        ))
        fig.update_layout(
            margin=dict(l=18, r=18, t=16, b=32), height=340, showlegend=False,
        )
        style_plotly(fig, height=340)
        st.plotly_chart(fig, use_container_width=True)
        chart_data_expander(
            fig, shares_df, name=f"rooms_{sel_dev}", key="developer_rooms"
        )
    else:
        st.info("Нет данных по комнатности")

with chart_r:
    st.dataframe(
        shares_df, hide_index=True, use_container_width=True,
        column_config={
            "Доля, %": st.column_config.ProgressColumn(
                format="%d%%", min_value=0, max_value=100,
            ),
        },
    )

# === Сравнение с топ-10 ===
st.markdown("#### Сравнение с топ-10 девелоперов региона")
top10 = devs.head(10).copy()
# Если выбранный не в топ-10 — добавить отдельно
if sel_dev not in top10["наименование"].values:
    extra = devs[devs["наименование"] == sel_dev]
    top10 = pd.concat([top10, extra], ignore_index=True)

compare_df = pd.DataFrame({
    "Девелопер": top10["наименование"],
    "Место": top10["place"],
    "Площадь, тыс. м²": top10["площадь_тыс_м²_num"],
    "1 комн, %": top10["доля_1комн_%_num"],
    "2 комн, %": top10["доля_2комн_%_num"],
    "3 комн, %": top10["доля_3комн_%_num"],
    "4+ комн, %": top10["доля_4+комн_%_num"],
})

# Подсветка выбранной строки
def highlight_selected(row):
    return ["background-color: #EAF2F8" if row["Девелопер"] == sel_dev else "" for _ in row]

st.dataframe(
    compare_df,
    hide_index=True,
    use_container_width=True,
    column_config={
        "Место": st.column_config.NumberColumn(format="%d"),
        "Площадь, тыс. м²": st.column_config.NumberColumn(format="%d"),
        "1 комн, %": st.column_config.ProgressColumn(format="%d%%", min_value=0, max_value=100),
        "2 комн, %": st.column_config.ProgressColumn(format="%d%%", min_value=0, max_value=100),
        "3 комн, %": st.column_config.ProgressColumn(format="%d%%", min_value=0, max_value=100),
        "4+ комн, %": st.column_config.ProgressColumn(format="%d%%", min_value=0, max_value=100),
    },
)
