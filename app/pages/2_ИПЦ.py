"""Страница «Индексы потребительских цен»."""
from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from app.components.export import chart_download_button, table_download_buttons
from app.data_access import MONTH_NAMES_RU, QUARTER_NAMES_RU, latest_loaded_at, load_ipc

st.set_page_config(page_title="ИПЦ — Аналитика Москвы", page_icon="💰", layout="wide")

REGION_COLORS = {"Москва": "#1f77b4", "Российская Федерация": "#ff7f0e"}

PERIOD_LABEL = {
    "month_to_month": "К предыдущему месяцу",
    "ytd_to_yago": "С начала года к АППГ",
}


def render_year_view(df: pd.DataFrame, regions: list[str]) -> None:
    """Годовой ИПЦ = декабрьский «с начала года к АППГ» = индекс декабрь/декабрь."""
    annual = df[(df["period_type"] == "ytd_to_yago") & (df["month"] == 12)].copy()
    annual = annual[annual["region"].isin(regions)].sort_values(["year", "region"])
    if annual.empty:
        st.info("Нет данных за выбранные регионы.")
        return
    fig = px.bar(
        annual,
        x="year",
        y="value",
        color="region",
        barmode="group",
        color_discrete_map=REGION_COLORS,
        text="value",
        labels={"year": "Год", "value": "% к АППГ", "region": "Регион"},
    )
    fig.update_traces(texttemplate="%{text:.2f}", textposition="outside", cliponaxis=False)
    fig.update_layout(
        title="ИПЦ — декабрь к декабрю предыдущего года",
        xaxis=dict(tickmode="linear", dtick=1),
        yaxis_title="%",
        legend_title="",
        margin=dict(t=60, b=40),
    )
    st.plotly_chart(fig, width="stretch")
    chart_download_button(fig, name="ipc_year", key="ipc_year_png")

    pivot = annual.pivot_table(index="region", columns="year", values="value", aggfunc="first")
    pivot.columns = [str(int(c)) for c in pivot.columns]
    st.dataframe(pivot.style.format("{:.2f}", na_rep="—"), width="stretch")
    table_download_buttons(
        annual[["year", "region", "value"]].rename(columns={"value": "ипц_%"}),
        name="ipc_year",
        key_prefix="ipc_year",
    )


def render_quarter_view(df: pd.DataFrame, regions: list[str], period_type: str) -> None:
    """Квартальный ИПЦ: значение на последний месяц квартала.

    - month_to_month → индекс марта/июня/сентября/декабря к пред. месяцу
    - ytd_to_yago    → индекс с начала года к АППГ на конец квартала
    """
    last_month_in_q = {1: 3, 2: 6, 3: 9, 4: 12}
    sub = df[df["period_type"] == period_type].copy()
    sub["_q_last_m"] = sub["quarter"].map(last_month_in_q)
    sub = sub[sub["month"] == sub["_q_last_m"]]
    sub = sub[sub["region"].isin(regions)]
    if sub.empty:
        st.info("Нет данных для выбранных параметров.")
        return
    chosen = st.multiselect(
        "Кварталы",
        options=[1, 2, 3, 4],
        default=[1, 2, 3, 4],
        format_func=lambda q: QUARTER_NAMES_RU[q - 1],
    )
    if not chosen:
        st.info("Выберите хотя бы один квартал.")
        return
    sub = sub[sub["quarter"].isin(chosen)].copy()
    sub["период"] = sub["quarter"].map(lambda q: QUARTER_NAMES_RU[q - 1])
    sub["линия"] = sub["region"] + " — " + sub["период"]
    sub = sub.sort_values(["region", "quarter", "year"])

    fig = px.line(
        sub,
        x="year",
        y="value",
        color="линия",
        markers=True,
        labels={"year": "Год", "value": "%"},
    )
    fig.update_layout(
        title=f"ИПЦ по кварталам — {PERIOD_LABEL[period_type]}",
        xaxis=dict(tickmode="linear", dtick=1),
        yaxis_title="%",
        legend_title="",
        margin=dict(t=60, b=40),
    )
    st.plotly_chart(fig, width="stretch")
    chart_download_button(fig, name=f"ipc_quarter_{period_type}", key="ipc_q_png")

    pivot = (
        sub.assign(строка=lambda d: d["region"] + " — " + d["период"])
        .pivot_table(index="строка", columns="year", values="value", aggfunc="first")
    )
    pivot.columns = [str(int(c)) for c in pivot.columns]
    st.dataframe(pivot.style.format("{:.2f}", na_rep="—"), width="stretch")
    table_download_buttons(
        sub[["year", "quarter", "период", "region", "value"]].rename(columns={"value": "ипц_%"}),
        name=f"ipc_quarter_{period_type}",
        key_prefix="ipc_quarter",
    )


def render_month_view(df: pd.DataFrame, regions: list[str], period_type: str) -> None:
    sub = df[(df["period_type"] == period_type) & (df["region"].isin(regions))].copy()
    if sub.empty:
        st.info("Нет данных для выбранных параметров.")
        return
    chosen = st.multiselect(
        "Месяцы",
        options=list(range(1, 13)),
        default=list(range(1, 13)),
        format_func=lambda m: MONTH_NAMES_RU[m - 1],
    )
    if not chosen:
        st.info("Выберите хотя бы один месяц.")
        return
    sub = sub[sub["month"].isin(chosen)].copy()
    sub["месяц"] = sub["month"].map(lambda m: MONTH_NAMES_RU[m - 1])
    sub["линия"] = sub["region"] + " — " + sub["месяц"]
    sub = sub.sort_values(["region", "month", "year"])

    fig = px.line(
        sub,
        x="year",
        y="value",
        color="линия",
        markers=True,
        labels={"year": "Год", "value": "%"},
    )
    fig.update_layout(
        title=f"ИПЦ по месяцам — {PERIOD_LABEL[period_type]}",
        xaxis=dict(tickmode="linear", dtick=1),
        yaxis_title="%",
        legend_title="",
        margin=dict(t=60, b=40),
    )
    st.plotly_chart(fig, width="stretch")
    chart_download_button(fig, name=f"ipc_month_{period_type}", key="ipc_m_png")

    pivot = (
        sub.assign(строка=lambda d: d["region"] + " — " + d["месяц"])
        .pivot_table(index="строка", columns="year", values="value", aggfunc="first")
    )
    pivot.columns = [str(int(c)) for c in pivot.columns]
    pivot = pivot.reindex(
        sorted(pivot.index, key=lambda s: (s.split(" — ")[0], MONTH_NAMES_RU.index(s.split(" — ")[1])))
    )
    st.dataframe(pivot.style.format("{:.2f}", na_rep="—"), width="stretch")
    table_download_buttons(
        sub[["year", "month", "месяц", "region", "value"]].rename(columns={"value": "ипц_%"}),
        name=f"ipc_month_{period_type}",
        key_prefix="ipc_month",
    )


def main() -> None:
    st.title("💰 Индексы потребительских цен")
    df = load_ipc()
    if df.empty:
        st.warning(
            "Файлы ИПЦ не найдены в `data/processed/prices_ipc.parquet`. "
            "Запустите `python pipeline/run_etl.py`."
        )
        return

    st.caption(f"Обновлено: {latest_loaded_at(df)} · Источник: fedstat.ru, индикатор 31074 (части 1 и 2)")

    c1, c2 = st.columns([1.2, 2.0])
    with c1:
        period = st.radio("Период", options=["Год", "Квартал", "Месяц"], horizontal=False)
    with c2:
        regions = st.multiselect(
            "Регионы",
            options=sorted(df["region"].unique()),
            default=sorted(df["region"].unique()),
        )

    if not regions:
        st.info("Выберите хотя бы один регион.")
        return

    if period == "Год":
        # для года тип индекса фиксирован — декабрь к декабрю
        st.divider()
        render_year_view(df, regions=regions)
        return

    period_type_label = st.radio(
        "Тип индекса",
        options=["К предыдущему месяцу", "С начала года к АППГ"],
        horizontal=True,
    )
    period_type = (
        "month_to_month" if period_type_label == "К предыдущему месяцу" else "ytd_to_yago"
    )

    st.divider()
    if period == "Квартал":
        render_quarter_view(df, regions=regions, period_type=period_type)
    else:
        render_month_view(df, regions=regions, period_type=period_type)


main()
