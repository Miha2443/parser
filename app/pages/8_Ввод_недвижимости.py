"""Ввод недвижимости — годовые значения (Москва / РФ), 2011→2026.

Агрегатная страница (без выбора застройщика). 3 секции:
  1. Ввод недвижимости — жильё + нежильё (Москва из vvod / РФ из Stroi_111).
  2. Жилая недвижимость — ① МКД/ИЖС (Москва vvod / РФ ЕМИСС 34118),
     ② бюджет/небюджет (monitoring 2.0), ③ реновация (ФОНД РЕНОВАЦИИ).
  3. Нежилая недвижимость — ① нежильё/нежилые-в-жилье (vvod),
     ② бюджет/небюджет (monitoring 2.0), ③ разбивка по отраслям (vvod + прочее).

Единицы — млн м². Переключатель Москва/РФ управляет блоком 1 и
подграфиком 2①; остальные подграфики — всегда Москва.
"""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from app.components.design import COLORS, apply_theme, page_header, style_plotly
from app.data_access import (
    load_vvod_static,
    load_emiss_34118,
    load_emiss_34118_periods,
    load_monitoring_2_0,
    load_monitoring_2011_2026_static,
    month_label,
    monitoring_by_year,
    quarter_label,
)

st.set_page_config(page_title="Ввод недвижимости — Аналитика Москвы", layout="wide")
apply_theme()

# Палитра
C_ZH = COLORS["green"]       # жильё / МКД
C_NZH = "#7B8794"            # нежильё
C_IZHS = COLORS["amber"]     # ИЖС
C_NEBUDG = COLORS["cyan"]    # небюджет
C_BUDG = COLORS["blue"]      # бюджет
C_NZH_IN = COLORS["teal"]    # нежилые в жилье
C_MOP = COLORS["amber"]      # МОП
BRANCH_COLORS = [COLORS["blue"], COLORS["cyan"], COLORS["green"], COLORS["amber"], "#A8B5C2"]


def ru_num(value, digits=1):
    if value is None or pd.isna(value):
        return "—"
    try:
        s = f"{float(value):,.{digits}f}"
    except (TypeError, ValueError):
        return str(value)
    return s.replace(",", " ").replace(".", ",")


def render_stacked(df: pd.DataFrame, series: list[tuple[str, str, str]],
                   *, year_from: int, year_to: int, key: str,
                   totals: bool = True, height: int = 360) -> None:
    """Стэк-бары по годам с фиксированной шириной столбцов.

    df: DataFrame с колонкой 'year' + колонками из series.
    series: список (колонка, подпись, цвет).
    Годы дополняются до [year_from, year_to] нулями — столбцы одной ширины,
    подписи лет горизонтальные (xaxis.type='category').
    """
    years = list(range(year_from, year_to + 1))
    xs = [str(y) for y in years]
    if df is None or df.empty:
        d = pd.DataFrame({"year": years})
        for col, _, _ in series:
            d[col] = 0.0
    else:
        d = (df.set_index("year").reindex(years).reset_index())
    for col, _, _ in series:
        if col not in d.columns:
            d[col] = 0.0
    d = d.fillna(0.0)

    fig = go.Figure()
    for col, label, color in series:
        vals = d[col].astype(float)
        fig.add_trace(go.Bar(
            x=xs, y=vals, name=label, marker_color=color,
            text=[ru_num(v) if v > 0 else "" for v in vals],
            textposition="inside",
            hovertemplate="<b>" + label + "</b><br>%{x}: %{y:.1f} млн м²<extra></extra>",
        ))
    if totals and len(series) > 1:
        tot = d[[c for c, _, _ in series]].sum(axis=1)
        fig.add_trace(go.Scatter(
            x=xs, y=tot, mode="text",
            text=[ru_num(v) if v > 0 else "" for v in tot],
            textposition="top center", textfont=dict(size=11, color=COLORS["ink"]),
            showlegend=False, hoverinfo="skip",
        ))
        y_top = float(tot.max()) * 1.15 if tot.max() > 0 else 1
    else:
        y_top = float(d[[c for c, _, _ in series]].max().max()) * 1.15 or 1

    fig.update_layout(
        barmode="stack", height=height,
        margin=dict(l=52, r=18, t=28, b=72),
        yaxis=dict(title="млн м²", range=[0, y_top]),
        xaxis=dict(type="category", categoryorder="array", categoryarray=xs,
                   tickmode="array", tickvals=xs, ticktext=xs, tickangle=0),
        bargap=0.25,
        legend=dict(orientation="h", y=-0.22, x=1, xanchor="right"),
    )
    style_plotly(fig, height=height)
    st.plotly_chart(fig, use_container_width=True, key=key)


def _emiss_delta_from_ytd(df: pd.DataFrame, months: list[int]) -> pd.DataFrame:
    src = df[df["month"].isin(months)].copy()
    if src.empty:
        return src
    rows = []
    for _, g in src.sort_values(["year", "month"]).groupby("year"):
        prev = {"МКД": 0.0, "ИЖС": 0.0}
        for _, row in g.iterrows():
            new = row.copy()
            for col in ("МКД", "ИЖС"):
                value = float(row.get(col, 0) or 0)
                new[col] = max(value - prev[col], 0.0)
                prev[col] = value
            rows.append(new)
    return pd.DataFrame(rows)


def render_period_bars(df: pd.DataFrame, *, period: str, key: str, height: int = 330) -> None:
    if df is None or df.empty:
        st.info("Нет данных ЕМИСС 34118 для выбранного периода.")
        return
    if period == "Квартал":
        src = df[df["period_type"].isin(["ytd", "year"]) & df["month"].isin([3, 6, 9, 12])].copy()
        src = _emiss_delta_from_ytd(src, [3, 6, 9, 12])
        src["period"] = src.apply(lambda r: quarter_label(r["year"], r["quarter"]), axis=1)
        src["_sort"] = src["year"] * 10 + src["quarter"]
    else:
        direct = df[df["period_type"] == "month"].copy()
        if direct.empty:
            ytd = df[df["period_type"].isin(["ytd", "year"])].copy()
            src = _emiss_delta_from_ytd(ytd, list(range(1, 13)))
        else:
            src = direct
        src["period"] = src.apply(lambda r: month_label(r["year"], r["month"]), axis=1)
        src["_sort"] = src["year"] * 100 + src["month"]
    if src.empty:
        st.info("В выгрузке 34118 пока нет строк для выбранного периода.")
        return
    src = src.sort_values("_sort")
    periods = src["period"].tolist()
    fig = go.Figure()
    for col, label, color in [("МКД", "МКД", C_ZH), ("ИЖС", "ИЖС", C_IZHS)]:
        vals = pd.to_numeric(src[col], errors="coerce").fillna(0)
        fig.add_trace(go.Bar(
            x=periods,
            y=vals,
            name=label,
            marker_color=color,
            text=[ru_num(v) if v > 0 else "" for v in vals],
            textposition="outside",
            hovertemplate="<b>" + label + "</b><br>%{x}: %{y:.1f} млн м²<extra></extra>",
        ))
    ymax = float(pd.to_numeric(src[["МКД", "ИЖС"]].max(axis=1), errors="coerce").max() or 1)
    fig.update_layout(
        barmode="group",
        height=height,
        margin=dict(l=52, r=18, t=28, b=86),
        yaxis=dict(title="млн м²", range=[0, ymax * 1.22]),
        xaxis=dict(type="category", categoryorder="array", categoryarray=periods, tickangle=-45),
        legend=dict(orientation="h", y=-0.28, x=1, xanchor="right"),
    )
    style_plotly(fig, height=height)
    st.plotly_chart(fig, use_container_width=True, key=key)


def _sum_range(df: pd.DataFrame, col: str, y0: int, y1: int) -> float:
    if df is None or df.empty or col not in df.columns:
        return 0.0
    m = (df["year"] >= y0) & (df["year"] <= y1)
    return float(pd.to_numeric(df.loc[m, col], errors="coerce").fillna(0).sum())


RENOVATION_VALUES = {
    2017: 0.3,
    2018: 0.2,
    2019: 0.2,
    2020: 0.4,
    2021: 1.2,
    2022: 0.8,
    2023: 1.2,
    2024: 1.3,
    2025: 2.3,
}


MOP_VALUES = {
    2011: 1.2,
    2012: 1.3,
    2013: 1.8,
    2014: 1.4,
    2015: 1.8,
    2016: 1.7,
    2017: 2.1,
    2018: 2.3,
    2019: 3.3,
    2020: 3.2,
    2021: 4.7,
    2022: 3.9,
    2023: 4.5,
    2024: 4.1,
    2025: 4.9,
}


def _upsert_year(df: pd.DataFrame, row: dict) -> pd.DataFrame:
    row_df = pd.DataFrame([row])
    if df is None or df.empty:
        return row_df
    return pd.concat([df, row_df], ignore_index=True).drop_duplicates(subset=["year"], keep="last")


def _budget_split_with_2026(static_df: pd.DataFrame, live_2026: pd.DataFrame) -> pd.DataFrame:
    cols = ["year", "Небюджет", "Бюджет"]
    base = pd.DataFrame(columns=cols)
    if static_df is not None and not static_df.empty:
        base = static_df[static_df["year"].between(2011, 2025)].copy()
        base = base[[c for c in cols if c in base.columns]]
    if live_2026 is not None and not live_2026.empty:
        base = pd.concat([base, live_2026[cols]], ignore_index=True)
    return base.drop_duplicates(subset=["year"], keep="last").sort_values("year")


def _monitoring_mop_row(rv: pd.DataFrame, year: int) -> dict | None:
    required = {"Год ввода по Мосстату", "category_моп"}
    if rv is None or rv.empty or not required.issubset(set(rv.columns)):
        return None
    part = rv[pd.to_numeric(rv["Год ввода по Мосстату"], errors="coerce") == year]
    if part.empty:
        return None
    value = float(pd.to_numeric(part["category_моп"], errors="coerce").fillna(0).sum()) / 1e6
    return {"year": year, "МОП": value} if value > 0 else None


def _monitoring_nonres_row(rv: pd.DataFrame, year: int) -> dict | None:
    required = {
        "Год ввода по Мосстату",
        "category_нежилое_отдельное",
        "category_нежилое_в_жилом",
    }
    if rv is None or rv.empty or not required.issubset(set(rv.columns)):
        return None
    part = rv[pd.to_numeric(rv["Год ввода по Мосстату"], errors="coerce") == year]
    if part.empty:
        return None
    standalone = float(pd.to_numeric(part["category_нежилое_отдельное"], errors="coerce").fillna(0).sum()) / 1e6
    in_housing = float(pd.to_numeric(part["category_нежилое_в_жилом"], errors="coerce").fillna(0).sum()) / 1e6
    if standalone == 0 and in_housing == 0:
        return None
    return {
        "year": year,
        "нежильё": standalone,
        "нежилые_в_жилье": in_housing,
        "общая": standalone + in_housing,
    }


def _monitoring_nonres_branch_row(rv: pd.DataFrame, year: int) -> dict | None:
    nonres = _monitoring_nonres_row(rv, year)
    if not nonres or rv is None or rv.empty or "Отрасли" not in rv.columns:
        return None
    part = rv[pd.to_numeric(rv["Год ввода по Мосстату"], errors="coerce") == year].copy()
    if "category_нежилое_отдельное" not in part.columns:
        return None
    area = pd.to_numeric(part["category_нежилое_отдельное"], errors="coerce").fillna(0)
    industries = part["Отрасли"].fillna("").astype(str).str.casefold()

    def sum_if(mask) -> float:
        return float(area[mask].sum()) / 1e6

    offices = sum_if(industries.str.contains("административно-деловые", regex=False))
    social = sum_if(
        industries.str.contains("доу", regex=False)
        | industries.str.contains("образователь", regex=False)
        | industries.str.contains("лечебно", regex=False)
        | industries.str.contains("спортивно", regex=False)
        | industries.str.contains("культовые", regex=False)
    )
    industry = sum_if(
        industries.str.contains("производ", regex=False)
        | industries.str.contains("пром", regex=False)
    )
    hotels = sum_if(industries.str.contains("гостини", regex=False))
    return {
        "year": year,
        "офисы": offices,
        "соц": social,
        "пром": industry,
        "гостиницы": hotels,
    }


# ── Данные ──
vvod = load_vvod_static()
emiss = load_emiss_34118()
emiss_periods = load_emiss_34118_periods()
mon = load_monitoring_2_0()
monitoring_static = load_monitoring_2011_2026_static()
rv = mon.get("rv", pd.DataFrame())

page_header("Ввод недвижимости")

if all(v.empty for v in vvod.values()) and emiss.empty:
    st.warning("Нет данных. Положи vvod.xlsx / Stroi_111_2025.xls в "
               "data/raw/realty/vvod/")
    st.stop()

region = st.radio("Регион", ["Москва", "РФ"], horizontal=True, key="vvod_region")
is_msk = region == "Москва"
emiss_period = "Год"
if not is_msk:
    emiss_period = st.radio("Период ЕМИСС 34118", ["Год", "Квартал", "Месяц"], horizontal=True)

YF, YT = 2011, 2026

# ============================================================
# Блок 1. Ввод недвижимости — годовые значения
# ============================================================
st.markdown("### Ввод недвижимости — годовые значения")

if is_msk:
    b1 = vvod["msk_total"][["year", "жильё"]].copy()
    mop = pd.DataFrame([{"year": year, "МОП": value} for year, value in MOP_VALUES.items()])
    mop26 = _monitoring_mop_row(rv, 2026)
    if mop26:
        mop = _upsert_year(mop, mop26)
    b1 = b1.merge(mop, on="year", how="outer")
    nonres_split = vvod["msk_nonres"][["year", "нежильё", "нежилые_в_жилье"]].copy()
    b1 = b1.merge(nonres_split, on="year", how="outer")
    # 2026 — из monitoring 2.0: жилье по жилой площади, нежилье по общей.
    zh26 = monitoring_by_year(
        rv,
        gruppirovka="Жилье",
        value_col="Жилая площадь",
        year_from=2026,
        year_to=2026,
    )
    v_zh = float(zh26["value"].iloc[0]) if not zh26.empty else float("nan")
    nonres26 = _monitoring_nonres_row(rv, 2026)
    if not pd.isna(v_zh) or nonres26:
        row26 = {"year": 2026, "жильё": v_zh}
        if nonres26:
            row26.update({
                "нежильё": nonres26["нежильё"],
                "нежилые_в_жилье": nonres26["нежилые_в_жилье"],
            })
        if mop26:
            row26["МОП"] = mop26["МОП"]
        b1 = _upsert_year(b1, row26)
else:
    b1 = vvod["rf_total"][["year", "жильё", "нежильё"]].copy()

c_chart, c_txt = st.columns([3, 1])
with c_chart:
    b1_series = [("жильё", "Жильё", C_ZH), ("нежильё", "Нежильё", C_NZH)]
    if is_msk:
        b1_series = [
            ("жильё", "Жильё", C_ZH),
            ("МОП", "МОП", C_MOP),
            ("нежилые_в_жилье", "Нежилье в жилье", C_NZH_IN),
            ("нежильё", "Нежилье", C_NZH),
        ]
    render_stacked(b1, b1_series, year_from=YF, year_to=YT, key="b1", height=320)
with c_txt:
    for (y0, y1) in [(2011, 2025), (2011, 2026)]:
        zh = _sum_range(b1, "жильё", y0, y1)
        mop_sum = _sum_range(b1, "МОП", y0, y1) if is_msk else 0.0
        nzh = _sum_range(b1, "нежильё", y0, y1)
        nzh_in = _sum_range(b1, "нежилые_в_жилье", y0, y1) if is_msk else 0.0
        has26 = (not is_msk) and y1 == 2026
        mop_line = f"- {ru_num(mop_sum)} млн м² МОП\n" if is_msk else ""
        nzh_in_line = f"- {ru_num(nzh_in)} млн м² нежилья в жилье\n" if is_msk else ""
        st.markdown(
            f"**В {y0}-{y1}** введено\n\n"
            f"- {ru_num(zh)} млн м² жилья\n"
            f"{mop_line}"
            f"{nzh_in_line}"
            f"- {ru_num(nzh)} млн м² нежилья\n"
            f"- **{ru_num(zh + mop_sum + nzh_in + nzh)} млн м² недвижимости**"
            + ("\n\n*(2026 по РФ — нет данных)*" if has26 else "")
        )
        st.markdown("")

# ============================================================
# Блок 2. Жилая недвижимость — годовые значения
# ============================================================
st.markdown("### Жилая недвижимость — годовые значения")

# ① МКД / ИЖС  (Москва vvod / РФ ЕМИСС 34118)
st.markdown("**① МКД и ИЖС**" + ("" if is_msk else " — РФ (ЕМИСС 34118)"))
b2_1 = vvod["msk_residential"].copy() if is_msk else emiss.copy()
if is_msk:
    mkd26 = monitoring_by_year(
        rv,
        gruppirovka="Жилье",
        value_col="Жилая площадь",
        year_from=2026,
        year_to=2026,
    )
    if not mkd26.empty:
        b2_1 = pd.concat(
            [b2_1, pd.DataFrame([{"year": 2026, "МКД": float(mkd26["value"].iloc[0]), "ИЖС": 0.0}])],
            ignore_index=True,
        ).drop_duplicates(subset=["year"], keep="last")
c1, c2 = st.columns([3, 1])
with c1:
    if not is_msk and emiss_period != "Год":
        render_period_bars(emiss_periods, period=emiss_period, key="b2_1_period", height=330)
    else:
        render_stacked(b2_1, [("МКД", "МКД", C_ZH), ("ИЖС", "ИЖС", C_IZHS)],
                       year_from=YF, year_to=YT, key="b2_1", height=300)
with c2:
    for (y0, y1) in [(2011, 2025), (2011, 2026)]:
        st.markdown(
            f"**Σ {y0}-{y1}**\n\n"
            f"- МКД: {ru_num(_sum_range(b2_1, 'МКД', y0, y1))} млн м²\n"
            f"- ИЖС: {ru_num(_sum_range(b2_1, 'ИЖС', y0, y1))} млн м²")
        st.markdown("")

if is_msk:
    # ② Бюджет / Небюджет (monitoring 2.0, Москва)
    st.markdown("**② Бюджетное и небюджетное (Москва, мониторинг 2.0)**")
    nb = monitoring_by_year(
        rv,
        gruppirovka="Жилье",
        istochnik=["Внебюджет"],
        value_col="Жилая площадь",
        year_from=2026,
        year_to=2026,
    ).rename(columns={"value": "Небюджет"})
    bd = monitoring_by_year(rv, gruppirovka="Жилье",
                            istochnik=["Городской бюджет", "Федеральный бюджет"],
                            value_col="Жилая площадь",
                            year_from=2026,
                            year_to=2026).rename(columns={"value": "Бюджет"})
    live_b2_2 = pd.merge(nb, bd, on="year", how="outer")
    b2_2 = _budget_split_with_2026(monitoring_static["residential_budget_split"], live_b2_2)
    c1, c2 = st.columns([3, 1])
    with c1:
        render_stacked(b2_2, [("Небюджет", "Небюджет", C_NEBUDG), ("Бюджет", "Бюджет", C_BUDG)],
                       year_from=YF, year_to=YT, key="b2_2", height=300)
    with c2:
        for (y0, y1) in [(2011, 2025), (2011, 2026)]:
            st.markdown(
                f"**Σ {y0}-{y1}**\n\n"
                f"- Небюджет: {ru_num(_sum_range(b2_2, 'Небюджет', y0, y1))} млн м²\n"
                f"- Бюджет: {ru_num(_sum_range(b2_2, 'Бюджет', y0, y1))} млн м²")
            st.markdown("")

    # ③ Реновация (ФОНД РЕНОВАЦИИ, Москва), 2017-
    st.markdown("**③ Ввод по реновации (Москва)**")
    b2_3 = pd.DataFrame(
        [{"year": year, "Реновация": value} for year, value in RENOVATION_VALUES.items()]
    )
    ren26 = monitoring_by_year(
        rv,
        gk="ФОНД РЕНОВАЦИИ",
        gruppirovka="Жилье",
        value_col="Жилая площадь",
        year_from=2026,
        year_to=2026,
    )
    if not ren26.empty:
        b2_3 = _upsert_year(b2_3, {"year": 2026, "Реновация": float(ren26["value"].iloc[0])})
    c1, c2 = st.columns([3, 1])
    with c1:
        render_stacked(b2_3, [("Реновация", "Реновация", C_ZH)],
                       year_from=2017, year_to=YT, key="b2_3", height=280, totals=False)
    with c2:
        for (y0, y1) in [(2017, 2025), (2017, 2026)]:
            st.markdown(f"**Σ {y0}-{y1}**: {ru_num(_sum_range(b2_3, 'Реновация', y0, y1))} млн м²")
            st.markdown("")

if not is_msk:
    st.stop()

# ============================================================
# Блок 3. Нежилая недвижимость — годовые значения (всегда Москва)
# ============================================================
st.markdown("### Нежилая недвижимость — годовые значения")
st.caption("Все подграфики ниже — по Москве.")

# ① нежильё + нежилые в жилье (vvod)
st.markdown("**① Нежильё и нежилые в жилье**")
b3_1 = vvod["msk_nonres"].copy()
nonres26 = _monitoring_nonres_row(rv, 2026)
if nonres26:
    b3_1 = _upsert_year(b3_1, nonres26)
c1, c2 = st.columns([3, 1])
with c1:
    render_stacked(b3_1, [("нежильё", "Нежильё", C_NZH),
                          ("нежилые_в_жилье", "Нежилые в жилье", C_NZH_IN)],
                   year_from=YF, year_to=YT, key="b3_1", height=300)
with c2:
    for (y0, y1) in [(2011, 2025), (2011, 2026)]:
        st.markdown(
            f"**Σ {y0}-{y1}**\n\n"
            f"- Нежильё: {ru_num(_sum_range(b3_1, 'нежильё', y0, y1))} млн м²\n"
            f"- В жилье: {ru_num(_sum_range(b3_1, 'нежилые_в_жилье', y0, y1))} млн м²")
        st.markdown("")

# ② Бюджет / Небюджет (monitoring нежильё)
st.markdown("**② Бюджетное и небюджетное (мониторинг 2.0)**")
nb3 = monitoring_by_year(
    rv,
    istochnik=["Внебюджет"],
    value_col="category_нежилое_отдельное",
    year_from=2026,
    year_to=2026,
).rename(columns={"value": "Небюджет"})
bd3 = monitoring_by_year(
    rv,
    istochnik=["Городской бюджет", "Федеральный бюджет"],
    value_col="category_нежилое_отдельное",
    year_from=2026,
    year_to=2026,
).rename(columns={"value": "Бюджет"})
live_b3_2 = pd.merge(nb3, bd3, on="year", how="outer")
b3_2 = _budget_split_with_2026(monitoring_static["nonres_budget_split"], live_b3_2)
c1, c2 = st.columns([3, 1])
with c1:
    render_stacked(b3_2, [("Небюджет", "Небюджет", C_NEBUDG), ("Бюджет", "Бюджет", C_BUDG)],
                   year_from=YF, year_to=YT, key="b3_2", height=300)
with c2:
    for (y0, y1) in [(2011, 2025), (2011, 2026)]:
        st.markdown(
            f"**Σ {y0}-{y1}**\n\n"
            f"- Небюджет: {ru_num(_sum_range(b3_2, 'Небюджет', y0, y1))} млн м²\n"
            f"- Бюджет: {ru_num(_sum_range(b3_2, 'Бюджет', y0, y1))} млн м²")
        st.markdown("")

# ③ Разбивка по отраслям + прочее (vvod)
st.markdown("**③ Разбивка по отраслям**")
br = vvod["msk_nonres_branches"].copy()
branch26 = _monitoring_nonres_branch_row(rv, 2026)
if branch26:
    br = _upsert_year(br, branch26)
if not br.empty:
    # прочее = общая нежилое (3①) − (офисы+соц+пром+гостиницы)
    tot = b3_1[["year", "общая"]] if not b3_1.empty else pd.DataFrame()
    br = br.merge(tot, on="year", how="left")
    branch_cols = ["офисы", "соц", "пром", "гостиницы"]
    br["прочее"] = (br.get("общая", 0).fillna(0)
                    - br[branch_cols].sum(axis=1)).clip(lower=0)
    series3 = [("офисы", "Офисы", BRANCH_COLORS[0]),
               ("соц", "Соц. объекты", BRANCH_COLORS[1]),
               ("пром", "Промышленные", BRANCH_COLORS[2]),
               ("гостиницы", "Гостиницы", BRANCH_COLORS[3]),
               ("прочее", "Прочее", BRANCH_COLORS[4])]
    c1, c2 = st.columns([3, 1])
    with c1:
        render_stacked(br, series3, year_from=YF, year_to=YT, key="b3_3", height=300)
    with c2:
        for (y0, y1) in [(2011, 2025), (2011, 2026)]:
            st.markdown(
                f"**Σ {y0}-{y1}**\n\n"
                f"- Офисы: {ru_num(_sum_range(br, 'офисы', y0, y1))} млн м²\n"
                f"- Соц.: {ru_num(_sum_range(br, 'соц', y0, y1))} млн м²\n"
                f"- Пром.: {ru_num(_sum_range(br, 'пром', y0, y1))} млн м²\n"
                f"- Гостиницы: {ru_num(_sum_range(br, 'гостиницы', y0, y1))} млн м²\n"
                f"- Прочее: {ru_num(_sum_range(br, 'прочее', y0, y1))} млн м²"
            )
            st.markdown("")
else:
    st.info("Нет данных по отраслям нежилой недвижимости.")
