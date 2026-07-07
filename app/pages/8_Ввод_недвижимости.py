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

from app.data_access import (
    load_vvod_static,
    load_emiss_34118,
    load_monitoring_2_0,
    monitoring_by_year,
)

st.set_page_config(page_title="Ввод недвижимости — Аналитика Москвы", layout="wide")

# Палитра
C_ZH = "#8BC540"      # жильё / МКД
C_NZH = "#7A8386"     # нежильё
C_IZHS = "#A8DC74"    # ИЖС
C_NEBUDG = "#4EC3E0"  # небюджет
C_BUDG = "#1f4e79"    # бюджет
C_NZH_IN = "#4EC3E0"  # нежилые в жилье
BRANCH_COLORS = ["#1f4e79", "#4EC3E0", "#8BC540", "#F4A261", "#C8C8C8"]


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
            textposition="top center", textfont=dict(size=11, color="#333"),
            showlegend=False, hoverinfo="skip",
        ))
        y_top = float(tot.max()) * 1.15 if tot.max() > 0 else 1
    else:
        y_top = float(d[[c for c, _, _ in series]].max().max()) * 1.15 or 1

    fig.update_layout(
        barmode="stack", height=height,
        margin=dict(l=0, r=0, t=10, b=0),
        yaxis=dict(title="млн м²", range=[0, y_top]),
        xaxis=dict(type="category", categoryorder="array", categoryarray=xs,
                   tickmode="array", tickvals=xs, ticktext=xs, tickangle=0),
        bargap=0.25,
        legend=dict(orientation="h", y=-0.18),
    )
    st.plotly_chart(fig, use_container_width=True, key=key)


def _sum_range(df: pd.DataFrame, col: str, y0: int, y1: int) -> float:
    if df is None or df.empty or col not in df.columns:
        return 0.0
    m = (df["year"] >= y0) & (df["year"] <= y1)
    return float(pd.to_numeric(df.loc[m, col], errors="coerce").fillna(0).sum())


# ── Данные ──
vvod = load_vvod_static()
emiss = load_emiss_34118()
mon = load_monitoring_2_0()
rv = mon.get("rv", pd.DataFrame())

st.title("Ввод недвижимости")

if all(v.empty for v in vvod.values()) and emiss.empty:
    st.warning("Нет данных. Положи vvod.xlsx / Stroi_111_2025.xls в "
               "data/raw/realty/vvod/")
    st.stop()

region = st.radio("Регион", ["Москва", "РФ"], horizontal=True, key="vvod_region")
is_msk = region == "Москва"

YF, YT = 2011, 2026

# ============================================================
# Блок 1. Ввод недвижимости — годовые значения
# ============================================================
st.markdown("### Ввод недвижимости — годовые значения")

if is_msk:
    b1 = vvod["msk_total"].copy()
    # 2026 — из monitoring 2.0: жилье по жилой площади, нежилье по общей.
    zh26 = monitoring_by_year(
        rv,
        gruppirovka="Жилье",
        value_col="Жилая площадь",
        year_from=2026,
        year_to=2026,
    )
    nzh26 = monitoring_by_year(
        rv,
        gruppirovka="Нежилье",
        value_col="Общая площадь",
        year_from=2026,
        year_to=2026,
    )
    v_zh = float(zh26["value"].iloc[0]) if not zh26.empty else float("nan")
    v_nzh = float(nzh26["value"].iloc[0]) if not nzh26.empty else float("nan")
    if not (pd.isna(v_zh) and pd.isna(v_nzh)):
        b1 = pd.concat([b1, pd.DataFrame(
            [{"year": 2026, "жильё": v_zh, "нежильё": v_nzh}])], ignore_index=True)
else:
    b1 = vvod["rf_total"][["year", "жильё", "нежильё"]].copy()

c_chart, c_txt = st.columns([3, 1])
with c_chart:
    render_stacked(
        b1, [("жильё", "Жильё", C_ZH), ("нежильё", "Нежильё", C_NZH)],
        year_from=YF, year_to=YT, key="b1", height=380)
with c_txt:
    for (y0, y1) in [(2011, 2025), (2011, 2026)]:
        zh = _sum_range(b1, "жильё", y0, y1)
        nzh = _sum_range(b1, "нежильё", y0, y1)
        has26 = (not is_msk) and y1 == 2026
        st.markdown(
            f"**В {y0}-{y1}** введено\n\n"
            f"- {ru_num(zh)} млн м² жилья\n"
            f"- {ru_num(nzh)} млн м² нежилья\n"
            f"- **{ru_num(zh + nzh)} млн м² недвижимости**"
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
    render_stacked(b2_1, [("МКД", "МКД", C_ZH), ("ИЖС", "ИЖС", C_IZHS)],
                   year_from=YF, year_to=YT, key="b2_1", height=340)
with c2:
    for (y0, y1) in [(2011, 2025), (2011, 2026)]:
        st.markdown(
            f"**Σ {y0}-{y1}**\n\n"
            f"- МКД: {ru_num(_sum_range(b2_1, 'МКД', y0, y1))} млн м²\n"
            f"- ИЖС: {ru_num(_sum_range(b2_1, 'ИЖС', y0, y1))} млн м²")
        st.markdown("")

# ② Бюджет / Небюджет (monitoring 2.0, всегда Москва)
st.markdown("**② Бюджетное и небюджетное (Москва, мониторинг 2.0)**")
nb = monitoring_by_year(
    rv,
    gruppirovka="Жилье",
    istochnik=["Внебюджет"],
    value_col="Жилая площадь",
).rename(columns={"value": "Небюджет"})
bd = monitoring_by_year(rv, gruppirovka="Жилье",
                        istochnik=["Городской бюджет", "Федеральный бюджет"],
                        value_col="Жилая площадь").rename(columns={"value": "Бюджет"})
b2_2 = pd.merge(nb, bd, on="year", how="outer")
c1, c2 = st.columns([3, 1])
with c1:
    render_stacked(b2_2, [("Небюджет", "Небюджет", C_NEBUDG), ("Бюджет", "Бюджет", C_BUDG)],
                   year_from=YF, year_to=YT, key="b2_2", height=340)
with c2:
    for (y0, y1) in [(2011, 2025), (2011, 2026)]:
        st.markdown(
            f"**Σ {y0}-{y1}**\n\n"
            f"- Небюджет: {ru_num(_sum_range(b2_2, 'Небюджет', y0, y1))} млн м²\n"
            f"- Бюджет: {ru_num(_sum_range(b2_2, 'Бюджет', y0, y1))} млн м²")
        st.markdown("")

# ③ Реновация (ФОНД РЕНОВАЦИИ, всегда Москва), 2017-
st.markdown("**③ Ввод по реновации (Москва)**")
b2_3 = monitoring_by_year(
    rv,
    gk="ФОНД РЕНОВАЦИИ",
    gruppirovka="Жилье",
    value_col="Жилая площадь",
).rename(columns={"value": "Реновация"})
c1, c2 = st.columns([3, 1])
with c1:
    render_stacked(b2_3, [("Реновация", "Реновация", C_ZH)],
                   year_from=2017, year_to=YT, key="b2_3", height=320, totals=False)
with c2:
    for (y0, y1) in [(2017, 2025), (2017, 2026)]:
        st.markdown(f"**Σ {y0}-{y1}**: {ru_num(_sum_range(b2_3, 'Реновация', y0, y1))} млн м²")
        st.markdown("")

# ============================================================
# Блок 3. Нежилая недвижимость — годовые значения (всегда Москва)
# ============================================================
st.markdown("### Нежилая недвижимость — годовые значения")
st.caption("Все подграфики ниже — по Москве.")

# ① нежильё + нежилые в жилье (vvod)
st.markdown("**① Нежильё и нежилые в жилье**")
b3_1 = vvod["msk_nonres"].copy()
c1, c2 = st.columns([3, 1])
with c1:
    render_stacked(b3_1, [("нежильё", "Нежильё", C_NZH),
                          ("нежилые_в_жилье", "Нежилые в жилье", C_NZH_IN)],
                   year_from=YF, year_to=YT, key="b3_1", height=340)
with c2:
    for (y0, y1) in [(2011, 2025), (2011, 2026)]:
        st.markdown(
            f"**Σ {y0}-{y1}**\n\n"
            f"- Нежильё: {ru_num(_sum_range(b3_1, 'нежильё', y0, y1))} млн м²\n"
            f"- В жилье: {ru_num(_sum_range(b3_1, 'нежилые_в_жилье', y0, y1))} млн м²")
        st.markdown("")

# ② Бюджет / Небюджет (monitoring нежильё)
st.markdown("**② Бюджетное и небюджетное (мониторинг 2.0)**")
nb3 = monitoring_by_year(rv, gruppirovka="Нежилье", istochnik=["Внебюджет"]).rename(columns={"value": "Небюджет"})
bd3 = monitoring_by_year(rv, gruppirovka="Нежилье",
                         istochnik=["Городской бюджет", "Федеральный бюджет"]).rename(columns={"value": "Бюджет"})
b3_2 = pd.merge(nb3, bd3, on="year", how="outer")
c1, c2 = st.columns([3, 1])
with c1:
    render_stacked(b3_2, [("Небюджет", "Небюджет", C_NEBUDG), ("Бюджет", "Бюджет", C_BUDG)],
                   year_from=YF, year_to=YT, key="b3_2", height=340)
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
if not br.empty:
    # прочее = общая нежилое (3①) − (офисы+соц+пром+гостиницы)
    tot = vvod["msk_nonres"][["year", "общая"]] if not vvod["msk_nonres"].empty else pd.DataFrame()
    br = br.merge(tot, on="year", how="left")
    branch_cols = ["офисы", "соц", "пром", "гостиницы"]
    br["прочее"] = (br.get("общая", 0).fillna(0)
                    - br[branch_cols].sum(axis=1)).clip(lower=0)
    series3 = [("офисы", "Офисы", BRANCH_COLORS[0]),
               ("соц", "Соц. объекты", BRANCH_COLORS[1]),
               ("пром", "Промышленные", BRANCH_COLORS[2]),
               ("гостиницы", "Гостиницы", BRANCH_COLORS[3]),
               ("прочее", "Прочее", BRANCH_COLORS[4])]
    render_stacked(br, series3, year_from=YF, year_to=2025, key="b3_3", height=360)
else:
    st.info("Нет данных по отраслям нежилой недвижимости.")
