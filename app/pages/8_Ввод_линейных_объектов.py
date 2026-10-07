"""Ввод линейных объектов: квартальный план и факт ДСТИ."""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from app.components.design import COLORS, apply_theme, page_header, style_plotly
from app.components.export import chart_data_expander, table_download_buttons
from app.linear_objects import latest_source, period_summary, read_linear_objects, source_date


st.set_page_config(page_title="Ввод линейных объектов — Аналитика Москвы", layout="wide")
apply_theme()
page_header("Ввод линейных объектов")


def fmt(value: object, unit: str = "") -> str:
    if value is None or pd.isna(value):
        return "—"
    digits = 1 if unit == "км" else 0
    return f"{float(value):,.{digits}f}".replace(",", " ").replace(".", ",")


source = latest_source()
if source is None:
    st.warning("Файл ДСТИ не найден. Положите Excel-отчёт в `data/raw/realty/linear_objects/`.")
    st.stop()
try:
    data = read_linear_objects(source)
except (OSError, ValueError) as exc:
    st.error(f"Не удалось прочитать показатели 1.1–1.4 из файла ДСТИ: {exc}")
    st.stop()

st.caption(f"Отчёт на {source_date(source):%d.%m.%Y}")
years = sorted(data["year"].unique(), reverse=True)
col_year, col_quarter, col_mode = st.columns([1, 1, 2])
with col_year:
    year = st.selectbox("Год", years, key="linear_year")
available_quarters = sorted(data.loc[data["year"].eq(year), "quarter"].unique())
complete = [quarter for quarter in available_quarters
            if data[data["year"].eq(year) & data["quarter"].eq(quarter)]["fact"].notna().all()]
default_quarter = complete[-1] if complete else available_quarters[-1]
with col_quarter:
    quarter = st.selectbox("Квартал", available_quarters,
                           index=available_quarters.index(default_quarter),
                           format_func=lambda value: f"{value} квартал", key="linear_quarter")
with col_mode:
    mode = st.radio("Расчёт периода", ["За квартал", "С начала года"], horizontal=True, key="linear_mode")
cumulative = mode == "С начала года"
summary = period_summary(data, year, quarter, cumulative)
period_text = f"1–{quarter} кварталы {year}" if cumulative and quarter > 1 else f"{quarter} квартал {year}"
st.subheader(f"План и факт · {period_text}")
if summary["fact"].isna().any():
    st.info("Для выбранного периода факт ещё не указан в отчёте. Пустые значения не считаются нулём.")

metric_columns = st.columns(2)
for index, (_, item) in enumerate(summary.iterrows()):
    with metric_columns[index % 2]:
        with st.container(border=True):
            st.markdown(f"#### {item['indicator']}")
            plan, fact, percent = item["plan"], item["fact"], item["percent"]
            metrics = st.columns(3)
            metrics[0].metric("План", f"{fmt(plan, item['unit'])} {item['unit']}" if pd.notna(plan) else "—")
            metrics[1].metric("Факт", f"{fmt(fact, item['unit'])} {item['unit']}" if pd.notna(fact) else "—")
            metrics[2].metric("Выполнение плана", f"{fmt(percent, 'км')}%" if pd.notna(percent) else "—")
            chart = go.Figure()
            if pd.notna(plan):
                chart.add_bar(x=[plan], y=["План"], orientation="h", marker_color="#A9B9C8",
                              text=[fmt(plan, item["unit"])], textposition="outside", name="План")
            if pd.notna(fact):
                chart.add_bar(x=[fact], y=["Факт"], orientation="h", marker_color=COLORS["red"],
                              text=[fmt(fact, item["unit"])], textposition="outside", name="Факт")
            chart.update_layout(
                height=155, showlegend=False, margin=dict(l=62, r=40, t=10, b=25),
                xaxis=dict(title=item["unit"], rangemode="tozero"),
                yaxis=dict(categoryorder="array", categoryarray=["Факт", "План"]),
            )
            style_plotly(chart, height=155)
            st.plotly_chart(chart, use_container_width=True, key=f"linear_{item['code']}_{year}_{quarter}_{mode}")

st.subheader("Динамика по кварталам")
indicators = summary.set_index("code")["indicator"].to_dict()
selected_code = st.selectbox("Показатель", list(indicators),
                             format_func=lambda code: indicators[code], key="linear_indicators")
trend = pd.concat(
    [period_summary(data, year, current, cumulative) for current in available_quarters],
    ignore_index=True,
)
trend = trend[trend["code"].eq(selected_code)].copy()
trend["Период"] = trend["quarter"].map(lambda value: f"{value} кв.")
trend_fig = go.Figure()
for col, title, color in [("plan", "План", "#A9B9C8"), ("fact", "Факт", COLORS["red"])]:
    trend_fig.add_bar(
        x=trend["Период"], y=trend[col], name=title, marker_color=color,
        text=[fmt(value, trend.iloc[0]["unit"]) if pd.notna(value) else "" for value in trend[col]],
        textposition="outside",
    )
trend_fig.update_layout(
    barmode="group", height=380, margin=dict(l=50, r=30, t=20, b=65),
    yaxis_title=trend.iloc[0]["unit"], legend=dict(orientation="h", y=-0.2),
)
style_plotly(trend_fig, height=380)
st.plotly_chart(trend_fig, use_container_width=True, key=f"linear_trend_{selected_code}_{year}_{mode}")
export = trend[["year", "quarter", "plan", "fact", "percent"]].rename(columns={
    "year": "Год", "quarter": "Квартал", "plan": "План", "fact": "Факт", "percent": "Выполнение плана, %",
})
chart_data_expander(trend_fig, export, name=f"linear_{selected_code}_{year}", key="linear_trend_data")

with st.expander("Все показатели и скачивание", expanded=False):
    all_periods = pd.concat(
        [period_summary(data, year, current, cumulative) for current in available_quarters],
        ignore_index=True,
    )
    shown = all_periods.rename(columns={
        "indicator": "Показатель", "unit": "Единица", "quarter": "Квартал",
        "plan": "План", "fact": "Факт", "percent": "Выполнение плана, %",
    })[["Показатель", "Единица", "Квартал", "План", "Факт", "Выполнение плана, %"]]
    st.dataframe(shown, hide_index=True, use_container_width=True)
    table_download_buttons(shown, name=f"linear_objects_{year}", key_prefix="linear_all")

with st.expander("Исходный файл и дата", expanded=False):
    st.write(source.name)
    st.write(f"Дата отчёта: {source_date(source):%d.%m.%Y}. Показатели 1.1–1.4.")
