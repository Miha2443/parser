"""Страница «ВВП / ВРП / ВДС» — национальные счета: Москва vs Россия.

Окно данных — с 2011 г. Шесть компактных блоков друг под другом:
  1. ВРП Москвы и ВВП России (трлн руб) — столбики.
  2. На душу населения (млн руб/чел) — столбики.
  3. Индекс физ. объёма ВРП/ВВП (%) — линии.
  4. Индекс физ. объёма на душу (%) — линии.
  5. Структура ВРП по отраслям (млрд руб / доля, %) — выбранные отрасли разным
     цветом, остальное — серым.
  6. Индекс физ. объёма ВДС по выбранным отраслям (%) + линия «Всего» (по галочке).

Блоки 1-4: выбор регионов (Москва / РФ) мультиселектом. Выбор отраслей в
блоках 5-6 сохраняется при переключении региона/режима.
"""
from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from app.components.design import COLORS, SERIES, apply_theme, page_header, style_plotly
from app.components.export import chart_download_button, table_download_buttons
from app.data_access import dataset_download_summary, show_dataset_sources, load_national_accounts

st.set_page_config(page_title="ВРП и ВВП — Аналитика Москвы", layout="wide")
apply_theme()

MSK = "Москва"
RF = "Российская Федерация"
MSK_COLOR = COLORS["red"]
RF_COLOR = COLORS["blue"]
REST_COLOR = COLORS["neutral"]
INDUSTRY_PALETTE = SERIES
DEFAULT_INDUSTRY = "Строительство"
# Разделители для plotly: дробная часть — запятая, разряды — неразрывный пробел.
RU_SEPARATORS = ", "


def ru_num(value: float, decimals: int = 0) -> str:
    """Русский формат числа: разряды через пробел, дробная часть через запятую (1 234,56)."""
    try:
        s = f"{float(value):,.{decimals}f}"
    except (TypeError, ValueError):
        return str(value)
    return s.replace(",", " ").replace(".", ",")


def _ru_fmt(decimals: int):
    return lambda v: ru_num(v, decimals)


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
    title: str | None,
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

    regions = st.multiselect(
        "Регионы", options=[MSK, RF], default=[MSK, RF],
        key=f"{key}_regions", label_visibility="collapsed",
    )
    keep = ([msk_label] if MSK in regions else []) + ([rf_label] if RF in regions else [])
    data = data[data["Показатель"].isin(keep)]
    if data.empty:
        st.info("Выберите хотя бы один регион.")
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
        yaxis_title=yaxis, legend_title="",
        margin=dict(t=46 if title else 24, b=24), height=300,
        xaxis=dict(tickmode="linear", dtick=2),
        separators=RU_SEPARATORS,
    )
    if title:
        fig.update_layout(title=title)
    style_plotly(fig, height=280)
    st.plotly_chart(fig, width="stretch")

    with st.expander("Данные и выгрузка"):
        chart_download_button(fig, name=key, key=f"{key}_png")
        pivot = data.pivot_table(index="Показатель", columns="year", values="value", aggfunc="first")
        pivot.columns = [str(int(c)) for c in pivot.columns]
        st.dataframe(pivot.style.format(_ru_fmt(decimals), na_rep="—"), width="stretch")
        table_download_buttons(
            data.rename(columns={"value": yaxis}), name=key, key_prefix=key
        )


def _structure_block(df: pd.DataFrame, *, metric: str, region: str, key: str) -> None:
    """Блок 5: столбец на год = весь объём ВДС; выбранные отрасли — цветом, остальное — серым."""
    sub = df[(df["metric"] == metric) & (df["region"] == region) & (df["view"] != "Всего")].copy()
    if sub.empty:
        st.info("Нет данных для выбранного региона.")
        return

    # В выбор — только отрасли действующего ОКВЭД2 (2016+). Старые названия
    # ОКВЭД-2007 (доли Москвы 2011-2015) в список не выводим, но в сумме по
    # году они учитываются и оседают в «Остальные» — итог остаётся 100%.
    sections = sorted(sub.loc[sub["year"] >= 2016, "view"].unique())
    if not sections:
        sections = sorted(sub["view"].unique())
    chosen = _persistent_industry_select(sections, key="b5_industries", label="Отрасли (разделы ОКВЭД)")
    if not chosen:
        st.info("Выберите хотя бы одну отрасль.")
        return

    is_share = metric == "vds_structure"
    if is_share:
        sub["val"] = sub["value"]
        unit_label = "%"
    else:
        scale = 1e-6 if sub["unit"].iloc[0] == "млн руб" else 1e-3
        sub["val"] = sub["value"] * scale
        unit_label = "трлн руб"

    total = sub.groupby("year")["val"].sum()
    sel = sub[sub["view"].isin(chosen)].sort_values(["year", "view"]).copy()
    rest = (total - sel.groupby("year")["val"].sum().reindex(total.index).fillna(0)).clip(lower=0)

    fig = px.bar(
        sel, x="year", y="val", color="view",
        color_discrete_sequence=INDUSTRY_PALETTE,
        labels={"year": "Год", "val": unit_label, "view": "Отрасль"},
    )
    bar_tt = "%{y:.0f}%" if is_share else "%{y:,.1f}"
    fig.update_traces(texttemplate=bar_tt, textposition="inside",
                      insidetextanchor="middle", textfont_size=9)
    rest_df = rest.reset_index()
    rest_df.columns = ["year", "val"]
    fig.add_bar(x=rest_df["year"], y=rest_df["val"], name="Остальные отрасли",
                marker_color=REST_COLOR)
    tot_df = total.reset_index()
    tot_df.columns = ["year", "val"]
    for year, top in zip(tot_df["year"], tot_df["val"]):
        txt = "100%" if is_share else ru_num(top, 1)
        fig.add_annotation(x=year, y=top, text=txt, showarrow=False, yshift=9,
                           font=dict(size=10, color="#333"))
    headroom = float(total.max()) * 1.15 if len(total) else 1.0
    fig.update_layout(
        barmode="stack", height=360, legend_title="", margin=dict(t=24, b=24),
        xaxis=dict(tickmode="linear", dtick=1, title="Год"),
        yaxis=dict(title=unit_label, range=[0, headroom]),
        uniformtext_minsize=7, uniformtext_mode="hide",
        separators=RU_SEPARATORS,
    )
    style_plotly(fig, height=320)
    st.plotly_chart(fig, width="stretch")

    with st.expander("Данные и выгрузка"):
        chart_download_button(fig, name=key, key=f"{key}_png")
        disp = sel.pivot_table(index="view", columns="year", values="val", aggfunc="first")
        disp.loc["Остальные отрасли"] = rest
        disp.loc["Всего"] = total
        disp.columns = [str(int(c)) for c in disp.columns]
        st.dataframe(disp.style.format(_ru_fmt(1), na_rep="—"), width="stretch")
        table_download_buttons(
            sel[["year", "view", "region", "val"]].rename(columns={"val": unit_label}),
            name=key, key_prefix=key,
        )


def _industry_index_block(df: pd.DataFrame, *, region: str, key: str, show_total: bool) -> None:
    """Блок 6: индекс физобъёма ВДС по выбранным отраслям + линия «Всего» (по галочке)."""
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
    if show_total and not total.empty:
        fig.add_scatter(
            x=total["year"], y=total["value"], name="Всего по всем отраслям",
            mode="lines", line=dict(color="#444", dash="dash", width=2),
        )
    fig.update_layout(height=340, legend_title="", margin=dict(t=24, b=24),
                      yaxis_title="%", xaxis=dict(tickmode="linear", dtick=1),
                      separators=RU_SEPARATORS)
    style_plotly(fig, height=310)
    st.plotly_chart(fig, width="stretch")

    with st.expander("Данные и выгрузка"):
        chart_download_button(fig, name=key, key=f"{key}_png")
        views = chosen + (["Всего"] if not total.empty else [])
        full = sub[sub["view"].isin(views)]
        pivot = full.pivot_table(index="view", columns="year", values="value", aggfunc="first")
        pivot.columns = [str(int(c)) for c in pivot.columns]
        st.dataframe(pivot.style.format(_ru_fmt(1), na_rep="—"), width="stretch")
        table_download_buttons(
            full[["year", "view", "region", "value", "unit"]].rename(columns={"value": "значение"}),
            name=key, key_prefix=key,
        )


def main() -> None:
    df = load_national_accounts()
    if df.empty:
        st.warning(
            "Витрины национальных счётов не найдены. Скачайте файлы "
            "(`py rosstat_checker.py`) и запустите `py pipeline/orchestrator.py --skip-download`."
        )
        return
    df = df[df["year"] >= 2011].copy()
    page_header(
        "ВРП и ВВП",
        f"{dataset_download_summary(df)} · с 2011 г. · Источники: Росстат (национальные счета), Мосстат (ВРП)",
    )
    st.subheader("1. ВРП Москвы и ВВП России, трлн руб")
    _two_region_chart(
        df, msk_metric="vrp_total", rf_metric="gdp_total",
        msk_scale=1e-6, rf_scale=1e-3,
        title="В годовом выражении в текущих ценах",
        yaxis="трлн руб", msk_label="ВРП Москвы", rf_label="ВВП России",
        decimals=1, key="na_block1", kind="bar",
    )

    st.subheader("2. ВРП Москвы и ВВП РФ на душу населения, млн руб/чел")
    _two_region_chart(
        df, msk_metric="vrp_per_capita", rf_metric="gdp_pc_total",
        msk_scale=1e-6, rf_scale=1e-6,
        title="В годовом выражении в текущих ценах",
        yaxis="млн руб/чел", msk_label="Москва (ВРП на душу)", rf_label="Россия (ВВП на душу)",
        decimals=2, key="na_block2", kind="bar",
    )

    st.subheader("3. Индекс физического объема ВРП, % к пред. году")
    _two_region_chart(
        df, msk_metric="vrp_index", rf_metric="gdp_index",
        msk_scale=1.0, rf_scale=1.0,
        title=None,
        yaxis="%", msk_label="ВРП Москвы", rf_label="ВВП России",
        decimals=1, key="na_block3",
    )

    st.subheader("4. Индекс физического объема ВРП на душу населения, % к пред. году")
    _two_region_chart(
        df, msk_metric="vrp_per_capita_index", rf_metric="gdp_pc_index",
        msk_scale=1.0, rf_scale=1.0,
        title=None,
        yaxis="%", msk_label="Москва (на душу)", rf_label="Россия (на душу)",
        decimals=1, key="na_block4",
    )

    st.subheader("5. Структура ВРП, трлн")
    c1, c2 = st.columns(2)
    with c1:
        region5 = st.radio("Регион", [MSK, RF], key="b5_region", horizontal=True)
    with c2:
        mode5 = st.radio("Показатель", ["В рублях", "Доля, %"], key="b5_mode", horizontal=True)
    metric5 = "vds_value" if mode5 == "В рублях" else "vds_structure"
    _structure_block(df, metric=metric5, region=region5, key=f"na_block5_{region5}_{metric5}")

    st.subheader("6. Индекс физического объема ВРП по отраслям, %")
    c3, c4 = st.columns(2)
    with c3:
        region6 = st.radio("Регион", [MSK, RF], key="b6_region", horizontal=True)
    with c4:
        show_total6 = st.checkbox("Показывать «Всего по всем отраслям»", value=True, key="b6_show_total")
    _industry_index_block(df, region=region6, key=f"na_block6_{region6}", show_total=show_total6)

    st.divider()
    show_dataset_sources(df)


main()
