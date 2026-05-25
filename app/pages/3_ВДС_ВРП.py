"""Страница «ВВП / ВРП / ВДС» — национальные счета: Москва vs Россия.

Шесть блоков по эскизу:
  1. Годовой ВВП РФ / ВРП Москвы (трлн руб) — две линии.
  2. На душу населения (млн руб) — две линии.
  3. Индекс физ. объёма ВВП/ВРП (%) — две линии.
  4. Индекс физ. объёма на душу (%) — две линии.
  5. Структура ВДС по отраслям (млрд руб и % к итогу) — с выбором отраслей.
  6. Индекс физ. объёма ВДС по отраслям (%) — с выбором отраслей.
"""
from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from app.components.export import chart_download_button, table_download_buttons
from app.data_access import format_thousands, latest_loaded_at, load_national_accounts

st.set_page_config(page_title="ВВП/ВРП/ВДС — Аналитика Москвы", page_icon="📈", layout="wide")

MSK = "Москва"
RF = "Российская Федерация"
REGION_COLORS = {"Москва": "#c8102e", "Россия": "#1f4e79", RF: "#1f4e79"}


def _two_region_chart(
    df: pd.DataFrame,
    *,
    msk_metric: str,
    rf_metric: str,
    msk_scale: float,
    rf_scale: float,
    title: str,
    yaxis: str,
    msk_label: str,
    rf_label: str,
    decimals: int,
    key: str,
    kind: str = "line",
) -> None:
    msk = df[(df["metric"] == msk_metric) & (df["region"] == MSK)][["year", "value"]].copy()
    msk["value"] = msk["value"] * msk_scale
    msk["Показатель"] = msk_label
    rf = df[(df["metric"] == rf_metric) & (df["region"] == RF)][["year", "value"]].copy()
    rf["value"] = rf["value"] * rf_scale
    rf["Показатель"] = rf_label
    data = pd.concat([msk, rf], ignore_index=True)
    if data.empty:
        st.info("Нет данных.")
        return
    data = data.sort_values(["Показатель", "year"])
    color_map = {msk_label: "#c8102e", rf_label: "#1f4e79"}
    common = dict(
        x="year", y="value", color="Показатель",
        color_discrete_map=color_map,
        labels={"year": "Год", "value": yaxis, "Показатель": ""},
    )
    if kind == "bar":
        fig = px.bar(data, barmode="group", **common)
    else:
        fig = px.line(data, markers=True, **common)
    fig.update_layout(
        title=title, yaxis_title=yaxis, legend_title="",
        margin=dict(t=60, b=40), height=440,
        xaxis=dict(tickmode="linear", dtick=2),
    )
    st.plotly_chart(fig, width="stretch")
    chart_download_button(fig, name=key, key=f"{key}_png")

    pivot = data.pivot_table(index="Показатель", columns="year", values="value", aggfunc="first")
    pivot.columns = [str(int(c)) for c in pivot.columns]
    st.dataframe(pivot.style.format(f"{{:,.{decimals}f}}", na_rep="—"), width="stretch")
    table_download_buttons(
        data.rename(columns={"value": yaxis}), name=key, key_prefix=key
    )


def _industry_block(df: pd.DataFrame, *, metric: str, region: str, key: str) -> None:
    """Блок с выбором отраслей. Для structure/value — stacked bar по годам; для index — линии."""
    sub = df[(df["metric"] == metric) & (df["region"] == region)].copy()
    if sub.empty:
        st.info("Нет данных для выбранного региона.")
        return

    sections = sorted([v for v in sub["view"].unique() if v != "Всего"])
    default = sections[:8] if len(sections) > 8 else sections
    chosen = st.multiselect(
        "Отрасли (разделы ОКВЭД)", options=sections, default=default, key=f"{key}_ms"
    )
    if not chosen:
        st.info("Выберите хотя бы одну отрасль.")
        return
    sub = sub[sub["view"].isin(chosen)].copy()
    unit = sub["unit"].iloc[0]

    if metric == "vds_index":
        sub = sub.sort_values(["view", "year"])
        fig = px.line(
            sub, x="year", y="value", color="view", markers=True,
            labels={"year": "Год", "value": unit, "view": "Отрасль"},
        )
        fig.update_layout(height=520, legend_title="", margin=dict(t=50, b=40),
                          xaxis=dict(tickmode="linear", dtick=1))
    else:
        sub = sub.sort_values(["year", "view"])
        fig = px.bar(
            sub, x="year", y="value", color="view",
            labels={"year": "Год", "value": unit, "view": "Отрасль"},
        )
        fig.update_layout(barmode="stack", height=520, legend_title="",
                          margin=dict(t=50, b=40), xaxis=dict(tickmode="linear", dtick=1))
    st.plotly_chart(fig, width="stretch")
    chart_download_button(fig, name=key, key=f"{key}_png")

    pivot = sub.pivot_table(index="view", columns="year", values="value", aggfunc="first")
    pivot.columns = [str(int(c)) for c in pivot.columns]
    st.dataframe(pivot.style.format("{:,.1f}", na_rep="—"), width="stretch")
    table_download_buttons(
        sub[["year", "view", "region", "value", "unit"]].rename(columns={"value": "значение"}),
        name=key, key_prefix=key,
    )


def main() -> None:
    st.title("📈 ВВП / ВРП / ВДС: Москва и Россия")
    df = load_national_accounts()
    if df.empty:
        st.warning(
            "Витрины национальных счётов не найдены. Скачайте файлы "
            "(`py rosstat_checker.py`) и запустите `py pipeline/orchestrator.py --skip-download`."
        )
        return
    st.caption(f"Обновлено: {latest_loaded_at(df)} · Источники: Росстат (национальные счета), Мосстат (ВРП)")

    st.header("1. Годовой объём (трлн руб)")
    _two_region_chart(
        df, msk_metric="vrp_total", rf_metric="gdp_total",
        msk_scale=1e-6, rf_scale=1e-3,
        title="ВРП Москвы и ВВП России, трлн руб (в текущих ценах)",
        yaxis="трлн руб", msk_label="ВРП Москвы", rf_label="ВВП России",
        decimals=1, key="na_block1", kind="bar",
    )

    st.divider()
    st.header("2. На душу населения (млн руб)")
    _two_region_chart(
        df, msk_metric="vrp_per_capita", rf_metric="gdp_pc_total",
        msk_scale=1e-6, rf_scale=1e-6,
        title="ВРП/ВВП на душу населения, млн руб",
        yaxis="млн руб", msk_label="Москва (ВРП на душу)", rf_label="Россия (ВВП на душу)",
        decimals=2, key="na_block2", kind="bar",
    )

    st.divider()
    st.header("3. Индекс физического объёма (% к пред. году)")
    _two_region_chart(
        df, msk_metric="vrp_index", rf_metric="gdp_index",
        msk_scale=1.0, rf_scale=1.0,
        title="Индекс физического объёма ВРП/ВВП, % к предыдущему году",
        yaxis="%", msk_label="ВРП Москвы", rf_label="ВВП России",
        decimals=1, key="na_block3",
    )

    st.divider()
    st.header("4. Индекс физического объёма на душу (% к пред. году)")
    _two_region_chart(
        df, msk_metric="vrp_per_capita_index", rf_metric="gdp_pc_index",
        msk_scale=1.0, rf_scale=1.0,
        title="Индекс физического объёма на душу населения, % к предыдущему году",
        yaxis="%", msk_label="Москва (на душу)", rf_label="Россия (на душу)",
        decimals=1, key="na_block4",
    )

    st.divider()
    st.header("5. Структура ВДС по отраслям")
    c1, c2 = st.columns([1, 3])
    with c1:
        region5 = st.radio("Регион", [MSK, RF], key="b5_region")
        mode5 = st.radio("Показатель", ["В рублях", "Доля, %"], key="b5_mode")
    with c2:
        metric5 = "vds_value" if mode5 == "В рублях" else "vds_structure"
        _industry_block(df, metric=metric5, region=region5, key=f"na_block5_{region5}_{metric5}")

    st.divider()
    st.header("6. Индекс физического объёма ВДС по отраслям (%)")
    region6 = st.radio("Регион", [MSK, RF], key="b6_region", horizontal=True)
    _industry_block(df, metric="vds_index", region=region6, key=f"na_block6_{region6}")


main()
