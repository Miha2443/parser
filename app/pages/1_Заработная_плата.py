"""Страница «Заработная плата» — реализация эскиза дашборда."""
from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from app.components.export import chart_download_button, table_download_buttons
from app.data_access import MONTH_NAMES_RU, QUARTER_NAMES_RU, latest_loaded_at, load_salary

st.set_page_config(page_title="Заработная плата — Аналитика Москвы", page_icon="💼", layout="wide")

INDUSTRY_COLORS = {"Строительство": "#1f77b4", "Всего": "#ff7f0e"}


def aggregate_year(df: pd.DataFrame) -> pd.DataFrame:
    """Годовое значение = YTD за декабрь (январь-декабрь)."""
    annual = df[(df["period_type"] == "ytd") & (df["month"] == 12)].copy()
    return annual[["year", "view", "region", "value"]]


def aggregate_quarter(df: pd.DataFrame, ytd: bool) -> pd.DataFrame:
    """Кварталы.

    ytd=False — среднее месячных значений в квартале.
    ytd=True  — YTD на последний месяц квартала (значение «с начала года»).
    """
    if ytd:
        last_month_in_q = {1: 3, 2: 6, 3: 9, 4: 12}
        out = df[df["period_type"] == "ytd"].copy()
        out["__q_last_month"] = out["quarter"].map(last_month_in_q)
        out = out[out["month"] == out["__q_last_month"]]
        return out[["year", "quarter", "view", "region", "value"]]
    monthly = df[df["period_type"] == "month"].copy()
    agg = (
        monthly.groupby(["year", "quarter", "view", "region"], as_index=False)["value"]
        .mean()
        .round(2)
    )
    return agg


def aggregate_month(df: pd.DataFrame, ytd: bool) -> pd.DataFrame:
    pt = "ytd" if ytd else "month"
    sub = df[df["period_type"] == pt].copy()
    return sub[["year", "month", "view", "region", "value"]]


def render_year_view(df: pd.DataFrame, region: str, views: list[str]) -> None:
    annual = aggregate_year(df)
    annual = annual[(annual["region"] == region) & (annual["view"].isin(views))]
    if annual.empty:
        st.info("Нет данных для выбранных параметров.")
        return

    annual = annual.sort_values(["year", "view"])
    fig = px.bar(
        annual,
        x="year",
        y="value",
        color="view",
        barmode="group",
        color_discrete_map=INDUSTRY_COLORS,
        labels={"year": "Год", "value": "руб.", "view": "Отрасль"},
        text="value",
    )
    fig.update_traces(texttemplate="%{text:,.0f}", textposition="outside", cliponaxis=False)
    fig.update_layout(
        title=f"Среднемесячная заработная плата (год, январь–декабрь). {region}",
        xaxis=dict(tickmode="linear", dtick=1),
        yaxis_title="руб.",
        legend_title="",
        margin=dict(t=60, b=40),
    )
    st.plotly_chart(fig, width="stretch")
    chart_download_button(fig, name=f"zp_year_{region}", key="zp_year_png")

    pivot = (
        annual.pivot_table(index="view", columns="year", values="value", aggfunc="first")
        .reindex(views)
    )
    pivot.columns = [str(int(c)) for c in pivot.columns]
    st.dataframe(
        pivot.style.format("{:,.1f}", na_rep="—"),
        width="stretch",
    )
    table_download_buttons(
        annual.rename(columns={"value": "значение_руб"}),
        name=f"zp_year_{region}",
        key_prefix="zp_year",
    )


def render_quarter_view(df: pd.DataFrame, region: str, views: list[str], ytd: bool) -> None:
    quarters = aggregate_quarter(df, ytd=ytd)
    quarters = quarters[(quarters["region"] == region) & (quarters["view"].isin(views))]
    if quarters.empty:
        st.info("Нет данных для выбранных параметров.")
        return
    all_quarters = sorted(quarters["quarter"].unique().tolist())
    chosen = st.multiselect(
        "Кварталы",
        options=all_quarters,
        default=all_quarters,
        format_func=lambda q: QUARTER_NAMES_RU[q - 1],
    )
    if not chosen:
        st.info("Выберите хотя бы один квартал.")
        return
    quarters = quarters[quarters["quarter"].isin(chosen)].copy()
    quarters["период"] = quarters["quarter"].map(lambda q: QUARTER_NAMES_RU[q - 1])
    quarters["линия"] = quarters["view"] + " — " + quarters["период"]
    quarters = quarters.sort_values(["view", "quarter", "year"])

    fig = px.line(
        quarters,
        x="year",
        y="value",
        color="линия",
        markers=True,
        labels={"year": "Год", "value": "руб."},
    )
    suffix = "с начала года" if ytd else "среднее за квартал"
    fig.update_layout(
        title=f"Заработная плата по кварталам ({suffix}). {region}",
        xaxis=dict(tickmode="linear", dtick=1),
        yaxis_title="руб.",
        legend_title="",
        margin=dict(t=60, b=40),
    )
    st.plotly_chart(fig, width="stretch")
    chart_download_button(fig, name=f"zp_quarter_{region}_{'ytd' if ytd else 'mean'}", key="zp_q_png")

    pivot = (
        quarters.assign(строка=lambda d: d["view"] + " — " + d["период"])
        .pivot_table(index="строка", columns="year", values="value", aggfunc="first")
        .sort_index()
    )
    pivot.columns = [str(int(c)) for c in pivot.columns]
    st.dataframe(pivot.style.format("{:,.1f}", na_rep="—"), width="stretch")
    table_download_buttons(
        quarters[["year", "quarter", "период", "view", "region", "value"]]
        .rename(columns={"value": "значение_руб"}),
        name=f"zp_quarter_{region}",
        key_prefix="zp_quarter",
    )


def render_month_view(df: pd.DataFrame, region: str, views: list[str], ytd: bool) -> None:
    monthly = aggregate_month(df, ytd=ytd)
    monthly = monthly[(monthly["region"] == region) & (monthly["view"].isin(views))]
    if monthly.empty:
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
    monthly = monthly[monthly["month"].isin(chosen)].copy()
    monthly["месяц"] = monthly["month"].map(lambda m: MONTH_NAMES_RU[m - 1])
    monthly["линия"] = monthly["view"] + " — " + monthly["месяц"]
    monthly = monthly.sort_values(["view", "month", "year"])

    fig = px.line(
        monthly,
        x="year",
        y="value",
        color="линия",
        markers=True,
        labels={"year": "Год", "value": "руб."},
    )
    suffix = "с начала года" if ytd else "за месяц"
    fig.update_layout(
        title=f"Заработная плата по месяцам ({suffix}). {region}",
        xaxis=dict(tickmode="linear", dtick=1),
        yaxis_title="руб.",
        legend_title="",
        margin=dict(t=60, b=40),
    )
    st.plotly_chart(fig, width="stretch")
    chart_download_button(fig, name=f"zp_month_{region}_{'ytd' if ytd else 'm'}", key="zp_m_png")

    pivot = (
        monthly.assign(строка=lambda d: d["view"] + " — " + d["месяц"])
        .pivot_table(index="строка", columns="year", values="value", aggfunc="first")
    )
    pivot.columns = [str(int(c)) for c in pivot.columns]
    pivot = pivot.reindex(
        sorted(pivot.index, key=lambda s: (s.split(" — ")[0], MONTH_NAMES_RU.index(s.split(" — ")[1])))
    )
    st.dataframe(pivot.style.format("{:,.1f}", na_rep="—"), width="stretch")
    table_download_buttons(
        monthly[["year", "month", "месяц", "view", "region", "value"]]
        .rename(columns={"value": "значение_руб"}),
        name=f"zp_month_{region}",
        key_prefix="zp_month",
    )


def main() -> None:
    st.title("💼 Среднемесячная заработная плата")
    df = load_salary()
    if df.empty:
        st.warning(
            "Файл `data/processed/employment_salary.parquet` не найден. "
            "Запустите `python pipeline/run_etl.py`."
        )
        return

    st.caption(f"Обновлено: {latest_loaded_at(df)} · Источник: fedstat.ru, индикатор 57824")

    # ─── Селекторы ─────────────────────────────────────────────────────────
    top = st.container()
    with top:
        c1, c2, c3 = st.columns([1.2, 1.2, 1.6])
        with c1:
            region = st.radio(
                "Регион",
                options=sorted(df["region"].unique()),
                index=sorted(df["region"].unique()).index("Москва")
                if "Москва" in df["region"].unique()
                else 0,
                horizontal=False,
            )
        with c2:
            period = st.radio("Период", options=["Год", "Квартал", "Месяц"], horizontal=False)
        with c3:
            views = st.multiselect(
                "Отрасль",
                options=["Строительство", "Всего"],
                default=["Строительство", "Всего"],
            )

    if not views:
        st.info("Выберите хотя бы одну отрасль.")
        return

    ytd = False
    if period in ("Месяц", "Квартал"):
        ytd_mode = st.radio(
            "Значение",
            options=[f"За {period.lower()}", "С начала года"],
            horizontal=True,
        )
        ytd = ytd_mode == "С начала года"

    st.divider()

    if period == "Год":
        render_year_view(df, region=region, views=views)
    elif period == "Квартал":
        render_quarter_view(df, region=region, views=views, ytd=ytd)
    else:
        render_month_view(df, region=region, views=views, ytd=ytd)


main()
