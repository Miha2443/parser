"""Страница «Индексы потребительских цен»."""
from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from app.components.design import COLORS, apply_theme, page_header, style_plotly
from app.components.export import chart_download_button, table_download_buttons
from app.data_access import (
    MONTH_NAMES_RU,
    QUARTER_NAMES_RU,
    latest_loaded_at,
    load_ipc,
    month_label,
    quarter_label,
)

st.set_page_config(page_title="ИПЦ — Аналитика Москвы", layout="wide")
apply_theme()

REGION_COLORS = {"Москва": COLORS["red"], "Российская Федерация": COLORS["blue"]}

PERIOD_TYPE_BY_LABEL = {
    "К предыдущему месяцу": "month_to_month",
    "С начала года к АППГ": "ytd_to_yago",
}
LABEL_BY_PERIOD_TYPE = {v: k for k, v in PERIOD_TYPE_BY_LABEL.items()}


def render_year_view(df: pd.DataFrame, regions: list[str]) -> None:
    """Годовой ИПЦ = декабрьский «с начала года к АППГ» = индекс декабрь/декабрь."""
    annual = df[(df["period_type"] == "ytd_to_yago") & (df["month"] == 12)].copy()
    annual = annual[annual["region"].isin(regions)].sort_values(["year", "region"])
    if annual.empty:
        st.info("Нет данных за выбранные регионы.")
        return
    annual["_label"] = annual["value"].map(lambda v: f"{v:.2f}")
    fig = px.bar(
        annual,
        x="year",
        y="value",
        color="region",
        barmode="group",
        color_discrete_map=REGION_COLORS,
        text="_label",
        labels={"year": "Год", "value": "% к АППГ", "region": "Регион"},
    )
    fig.update_traces(textposition="outside", cliponaxis=False)
    fig.update_layout(
        title="ИПЦ — декабрь к декабрю предыдущего года",
        xaxis=dict(tickmode="linear", dtick=1),
        yaxis_title="%",
        legend_title="",
        margin=dict(t=60, b=40),
        height=390,
    )
    style_plotly(fig, height=390)
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


def _timeseries_chart(
    sub: pd.DataFrame,
    *,
    color_col: str,
    color_map: dict[str, str],
    title: str,
    show_labels: bool,
) -> "px.line":
    sub = sub.sort_values(["_period_sort"]).copy()
    periods_ordered = (
        sub[["_period_sort", "period"]]
        .drop_duplicates()
        .sort_values("_period_sort")["period"]
        .tolist()
    )
    sub["_label"] = sub["value"].map(lambda v: f"{v:.2f}")
    fig = px.line(
        sub,
        x="period",
        y="value",
        color=color_col,
        markers=True,
        color_discrete_map=color_map,
        text="_label" if show_labels else None,
        labels={"period": "", "value": "%", color_col: ""},
    )
    if show_labels:
        fig.update_traces(textposition="top center")
    fig.update_xaxes(
        categoryorder="array", categoryarray=periods_ordered, tickangle=-45
    )
    fig.update_layout(
        title=title,
        yaxis_title="%",
        legend_title="",
        margin=dict(t=60, b=80),
        height=390,
    )
    return fig


def render_quarter_view(df: pd.DataFrame, regions: list[str], period_type: str) -> None:
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
    sub["period"] = sub.apply(lambda r: quarter_label(r["year"], r["quarter"]), axis=1)
    sub["_period_sort"] = sub["year"] * 10 + sub["quarter"]

    n_periods = len(sub[["year", "quarter"]].drop_duplicates())
    show_labels = n_periods <= 40
    fig = _timeseries_chart(
        sub,
        color_col="region",
        color_map=REGION_COLORS,
        title=f"ИПЦ по кварталам — {LABEL_BY_PERIOD_TYPE[period_type]}",
        show_labels=show_labels,
    )
    style_plotly(fig, height=390)
    st.plotly_chart(fig, width="stretch")
    chart_download_button(fig, name=f"ipc_quarter_{period_type}", key="ipc_q_png")

    pivot = sub.pivot_table(index="period", columns="region", values="value", aggfunc="first")
    pivot = pivot.reindex(sub.sort_values("_period_sort")["period"].unique())
    st.dataframe(pivot.style.format("{:.2f}", na_rep="—"), width="stretch")
    table_download_buttons(
        sub[["year", "quarter", "period", "region", "value"]].rename(
            columns={"value": "ипц_%", "period": "период"}
        ),
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
    sub["period"] = sub.apply(lambda r: month_label(r["year"], r["month"]), axis=1)
    sub["_period_sort"] = sub["year"] * 100 + sub["month"]

    n_periods = len(sub[["year", "month"]].drop_duplicates())
    show_labels = n_periods <= 40
    fig = _timeseries_chart(
        sub,
        color_col="region",
        color_map=REGION_COLORS,
        title=f"ИПЦ по месяцам — {LABEL_BY_PERIOD_TYPE[period_type]}",
        show_labels=show_labels,
    )
    style_plotly(fig, height=390)
    st.plotly_chart(fig, width="stretch")
    chart_download_button(fig, name=f"ipc_month_{period_type}", key="ipc_m_png")

    pivot = sub.pivot_table(index="period", columns="region", values="value", aggfunc="first")
    pivot = pivot.reindex(sub.sort_values("_period_sort")["period"].unique())
    st.dataframe(pivot.style.format("{:.2f}", na_rep="—"), width="stretch")
    table_download_buttons(
        sub[["year", "month", "period", "region", "value"]].rename(
            columns={"value": "ипц_%", "period": "период"}
        ),
        name=f"ipc_month_{period_type}",
        key_prefix="ipc_month",
    )


def main() -> None:
    df = load_ipc()
    if df.empty:
        st.warning(
            "Файлы ИПЦ не найдены в `data/processed/prices_ipc.pkl`. "
            "Запустите `py pipeline/run_etl.py`."
        )
        return

    page_header(
        "Индексы потребительских цен",
        f"Обновлено: {latest_loaded_at(df)} · Источник: fedstat.ru, индикатор 31074 (части 1 и 2)",
    )

    c1, c2 = st.columns([1.2, 2.0])
    with c1:
        period = st.radio("Период", options=["Год", "Квартал", "Месяц"])
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
        st.divider()
        render_year_view(df, regions=regions)
        return

    period_type_label = st.radio(
        "Тип индекса",
        options=list(PERIOD_TYPE_BY_LABEL.keys()),
        horizontal=True,
    )
    period_type = PERIOD_TYPE_BY_LABEL[period_type_label]

    st.divider()
    if period == "Квартал":
        render_quarter_view(df, regions=regions, period_type=period_type)
    else:
        render_month_view(df, regions=regions, period_type=period_type)


main()
