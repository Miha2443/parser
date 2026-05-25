"""Страница «ВВП / ВРП / ВДС» — национальные счета: Москва vs Россия.

Окно данных — с 2011 г. Шесть компактных блоков друг под другом:
  1. Годовой ВВП РФ / ВРП Москвы (трлн руб) — столбики.
  2. На душу населения (млн руб) — столбики.
  3. Индекс физ. объёма ВВП/ВРП (%) — линии.
  4. Индекс физ. объёма на душу (%) — линии.
  5. Структура ВДС: доля выбранных отраслей в общем объёме (красным) vs остальное.
  6. Индекс физ. объёма ВДС по выбранным отраслям (%) + линия «Всего».

Блоки 1-4 умеют показывать Москву, Россию или оба сразу. Выбор отраслей в
блоках 5-6 сохраняется при переключении региона/режима.
"""
from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from app.components.export import chart_download_button, table_download_buttons
from app.data_access import latest_loaded_at, load_national_accounts

st.set_page_config(page_title="ВВП/ВРП/ВДС — Аналитика Москвы", page_icon="📈", layout="wide")

MSK = "Москва"
RF = "Российская Федерация"
MSK_COLOR = "#c8102e"
RF_COLOR = "#1f4e79"
REST_COLOR = "#c8d2dc"
DEFAULT_INDUSTRY = "Строительство"


def _persistent_industry_select(sections: list[str], *, key: str, label: str) -> list[str]:
    """Мультиселект отраслей с сохранением выбора между перезапусками.

    Ключ стабилен (не зависит от региона/режима), поэтому выбор не слетает.
    Значения, которых нет в текущем списке (другой регион), отсеиваются.
    """
    fallback = [DEFAULT_INDUSTRY] if DEFAULT_INDUSTRY in sections else sections[:1]
    if key not in st.session_state:
        st.session_state[key] = fallback
    else:
        valid = [v for v in st.session_state[key] if v in sections]
        st.session_state[key] = valid or fallback
    return st.multiselect(label, options=sections, key=key)


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

    view = st.radio(
        "Регион", ["Вместе", MSK, "Россия"], horizontal=True, key=f"{key}_view",
        label_visibility="collapsed",
    )
    if view == MSK:
        data = data[data["Показатель"] == msk_label]
    elif view == "Россия":
        data = data[data["Показатель"] == rf_label]
    if data.empty:
        st.info("Нет данных.")
        return

    data = data.sort_values(["Показатель", "year"])
    color_map = {msk_label: MSK_COLOR, rf_label: RF_COLOR}
    common = dict(
        x="year", y="value", color="Показатель",
        color_discrete_map=color_map,
        labels={"year": "Год", "value": yaxis, "Показатель": ""},
    )
    texttempl = f"%{{y:,.{decimals}f}}"
    if kind == "bar":
        fig = px.bar(data, barmode="group", **common)
        fig.update_traces(texttemplate=texttempl, textposition="outside",
                          textfont_size=10, cliponaxis=False)
    else:
        fig = px.line(data, markers=True, **common)
        fig.update_traces(mode="lines+markers+text", texttemplate=texttempl,
                          textposition="top center", textfont_size=9, cliponaxis=False)
    fig.update_layout(
        title=title, yaxis_title=yaxis, legend_title="",
        margin=dict(t=46, b=24), height=300,
        xaxis=dict(tickmode="linear", dtick=2),
    )
    st.plotly_chart(fig, width="stretch")

    with st.expander("Данные и выгрузка"):
        chart_download_button(fig, name=key, key=f"{key}_png")
        pivot = data.pivot_table(index="Показатель", columns="year", values="value", aggfunc="first")
        pivot.columns = [str(int(c)) for c in pivot.columns]
        st.dataframe(pivot.style.format(f"{{:,.{decimals}f}}", na_rep="—"), width="stretch")
        table_download_buttons(
            data.rename(columns={"value": yaxis}), name=key, key_prefix=key
        )


def _structure_block(df: pd.DataFrame, *, metric: str, region: str, key: str) -> None:
    """Блок 5: один столбец на год = весь объём ВДС; красным — доля выбранных отраслей."""
    sub = df[(df["metric"] == metric) & (df["region"] == region) & (df["view"] != "Всего")].copy()
    if sub.empty:
        st.info("Нет данных для выбранного региона.")
        return

    sections = sorted(sub["view"].unique())
    chosen = _persistent_industry_select(sections, key="b5_industries", label="Отрасли (разделы ОКВЭД)")
    if not chosen:
        st.info("Выберите хотя бы одну отрасль.")
        return

    is_share = metric == "vds_structure"
    unit = "%" if is_share else sub["unit"].iloc[0]
    total = sub.groupby("year")["value"].sum().rename("total")
    selected = sub[sub["view"].isin(chosen)].groupby("year")["value"].sum().rename("selected")
    agg = pd.concat([total, selected], axis=1).fillna(0.0).reset_index()
    agg["rest"] = (agg["total"] - agg["selected"]).clip(lower=0)
    agg["pct"] = (agg["selected"] / agg["total"] * 100).where(agg["total"] > 0, 0.0)

    if is_share:
        red_text = [f"{p:.0f}%" for p in agg["pct"]]
        top_text = ["100%"] * len(agg)
    else:
        red_text = [f"{v:,.0f}<br>{p:.0f}%" for v, p in zip(agg["selected"], agg["pct"])]
        top_text = [f"{t:,.0f}" for t in agg["total"]]

    fig = go.Figure()
    fig.add_bar(
        x=agg["year"], y=agg["selected"], name="Выбранные отрасли",
        marker_color=MSK_COLOR, text=red_text,
        textposition="inside", insidetextanchor="middle",
        textfont=dict(color="white", size=10),
    )
    fig.add_bar(
        x=agg["year"], y=agg["rest"], name="Остальные отрасли",
        marker_color=REST_COLOR,
    )
    for year, top, txt in zip(agg["year"], agg["total"], top_text):
        fig.add_annotation(x=year, y=top, text=txt, showarrow=False, yshift=9,
                           font=dict(size=10, color="#333"))
    headroom = float(agg["total"].max()) * 1.15 if len(agg) else 1.0
    fig.update_layout(
        barmode="stack", height=340, legend_title="",
        margin=dict(t=30, b=24),
        xaxis=dict(tickmode="linear", dtick=1, title="Год"),
        yaxis=dict(title=unit, range=[0, headroom]),
    )
    st.plotly_chart(fig, width="stretch")

    with st.expander("Данные и выгрузка"):
        chart_download_button(fig, name=key, key=f"{key}_png")
        show = agg.rename(columns={
            "year": "Год", "total": "Всего", "selected": "Выбрано",
            "rest": "Остальные", "pct": "Доля выбранного, %",
        })
        st.dataframe(
            show.style.format({
                "Всего": "{:,.1f}", "Выбрано": "{:,.1f}",
                "Остальные": "{:,.1f}", "Доля выбранного, %": "{:.1f}",
            }),
            width="stretch", hide_index=True,
        )
        table_download_buttons(show, name=key, key_prefix=key)


def _industry_index_block(df: pd.DataFrame, *, region: str, key: str) -> None:
    """Блок 6: индекс физобъёма ВДС по выбранным отраслям + линия «Всего»."""
    sub = df[(df["metric"] == "vds_index") & (df["region"] == region)].copy()
    if sub.empty:
        st.info("Нет данных для выбранного региона.")
        return

    sections = sorted([v for v in sub["view"].unique() if v != "Всего"])
    chosen = _persistent_industry_select(sections, key="b6_industries", label="Отрасли (разделы ОКВЭД)")
    if not chosen:
        st.info("Выберите хотя бы одну отрасль.")
        return

    plot = sub[sub["view"].isin(chosen)].sort_values(["view", "year"])
    fig = px.line(
        plot, x="year", y="value", color="view", markers=True,
        labels={"year": "Год", "value": "%", "view": "Отрасль"},
    )
    fig.update_traces(mode="lines+markers+text", texttemplate="%{y:.0f}",
                      textposition="top center", textfont_size=9, cliponaxis=False)

    total = sub[sub["view"] == "Всего"].sort_values("year")
    if not total.empty:
        fig.add_scatter(
            x=total["year"], y=total["value"], name="Всего по всем отраслям",
            mode="lines", line=dict(color="#444", dash="dash", width=2),
        )
    fig.update_layout(height=340, legend_title="", margin=dict(t=30, b=24),
                      yaxis_title="%", xaxis=dict(tickmode="linear", dtick=1))
    st.plotly_chart(fig, width="stretch")

    with st.expander("Данные и выгрузка"):
        chart_download_button(fig, name=key, key=f"{key}_png")
        views = chosen + (["Всего"] if not total.empty else [])
        full = sub[sub["view"].isin(views)]
        pivot = full.pivot_table(index="view", columns="year", values="value", aggfunc="first")
        pivot.columns = [str(int(c)) for c in pivot.columns]
        st.dataframe(pivot.style.format("{:,.1f}", na_rep="—"), width="stretch")
        table_download_buttons(
            full[["year", "view", "region", "value", "unit"]].rename(columns={"value": "значение"}),
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
    df = df[df["year"] >= 2011].copy()
    st.caption(f"Обновлено: {latest_loaded_at(df)} · с 2011 г. · Источники: Росстат (национальные счета), Мосстат (ВРП)")

    st.subheader("1. Годовой объём (трлн руб)")
    _two_region_chart(
        df, msk_metric="vrp_total", rf_metric="gdp_total",
        msk_scale=1e-6, rf_scale=1e-3,
        title="ВРП Москвы и ВВП России, трлн руб (в текущих ценах)",
        yaxis="трлн руб", msk_label="ВРП Москвы", rf_label="ВВП России",
        decimals=1, key="na_block1", kind="bar",
    )

    st.subheader("2. На душу населения (млн руб)")
    _two_region_chart(
        df, msk_metric="vrp_per_capita", rf_metric="gdp_pc_total",
        msk_scale=1e-6, rf_scale=1e-6,
        title="ВРП/ВВП на душу населения, млн руб",
        yaxis="млн руб", msk_label="Москва (ВРП на душу)", rf_label="Россия (ВВП на душу)",
        decimals=2, key="na_block2", kind="bar",
    )

    st.subheader("3. Индекс физического объёма (% к пред. году)")
    _two_region_chart(
        df, msk_metric="vrp_index", rf_metric="gdp_index",
        msk_scale=1.0, rf_scale=1.0,
        title="Индекс физического объёма ВРП/ВВП, % к предыдущему году",
        yaxis="%", msk_label="ВРП Москвы", rf_label="ВВП России",
        decimals=1, key="na_block3",
    )

    st.subheader("4. Индекс физического объёма на душу (% к пред. году)")
    _two_region_chart(
        df, msk_metric="vrp_per_capita_index", rf_metric="gdp_pc_index",
        msk_scale=1.0, rf_scale=1.0,
        title="Индекс физического объёма на душу населения, % к предыдущему году",
        yaxis="%", msk_label="Москва (на душу)", rf_label="Россия (на душу)",
        decimals=1, key="na_block4",
    )

    st.subheader("5. Структура ВДС: доля выбранных отраслей")
    c1, c2 = st.columns(2)
    with c1:
        region5 = st.radio("Регион", [MSK, RF], key="b5_region", horizontal=True)
    with c2:
        mode5 = st.radio("Показатель", ["В рублях", "Доля, %"], key="b5_mode", horizontal=True)
    metric5 = "vds_value" if mode5 == "В рублях" else "vds_structure"
    _structure_block(df, metric=metric5, region=region5, key=f"na_block5_{region5}_{metric5}")

    st.subheader("6. Индекс физического объёма ВДС по отраслям (%)")
    region6 = st.radio("Регион", [MSK, RF], key="b6_region", horizontal=True)
    _industry_index_block(df, region=region6, key=f"na_block6_{region6}")


main()
