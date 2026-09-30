"""Распроданность и стройготовность жилья — дашборд.

Источник: наш.дом.рф/аналитика/распроданность-стройготовность.
Данные собираются помесячно с 2020 года по двум регионам (РФ + Москва).

Структура страницы:
1. Шапка: регион + отчётный период (slider по месяцам)
2. 4 KPI карточки с year-by-year прогнозом (Объём строительства /
   Распроданность / Отношение Р/С / Стройготовность)
3. Линейный график: динамика KPI по месяцам с 2020 года
4. 6 таблиц по секциям (ФО / Регионы / Девелоперы / По объёму
   строительства / По населению / Класс недвижимости)
"""
from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from app.components.design import COLORS, apply_theme, page_header, style_plotly
from app.components.export import chart_data_expander
from app.data_access import load_rasprodannost, MONTH_NAMES_RU, MONTH_SHORT_RU

st.set_page_config(page_title="Распроданность — Аналитика Москвы", layout="wide")
apply_theme()

COLOR_VOLUME = COLORS["blue"]
COLOR_SOLD = COLORS["red"]
COLOR_READY = COLORS["green"]
COLOR_RATIO = COLORS["teal"]

KPI_COLORS = {
    "Объем жилищного строительства": COLOR_VOLUME,
    "Распроданность": COLOR_SOLD,
    "Стройготовность": COLOR_READY,
    "Отношение распроданности к стройготовности": COLOR_RATIO,
    "Отношение распроданности к стройготовности ": COLOR_RATIO,
}


def ru_num(value, digits=0):
    if value is None or pd.isna(value):
        return "—"
    try:
        s = f"{float(value):,.{digits}f}"
    except (TypeError, ValueError):
        return str(value)
    return s.replace(",", " ").replace(".", ",")


def period_label(year: int, month: int) -> str:
    return f"{MONTH_NAMES_RU[month-1].capitalize()} {year}"


def period_short(year: int, month: int) -> str:
    return f"{MONTH_SHORT_RU[month-1]} {year % 100:02d}"


data = load_rasprodannost()
if data["kpi"].empty:
    st.warning(
        "Нет данных. Положи `rasprodannost_<date>.xlsx` в "
        "`data/raw/realty/nashdom/` или в `nashdom/` в корне репо."
    )
    st.stop()

page_header("Распроданность и стройготовность жилья")

# === Шапка: фильтры ===
region_map = {"rf": "Российская Федерация", "msk": "Город Москва"}
available_regions = [r for r in data["regions_available"] if r in region_map]
periods = data["periods"]
period_labels = [period_label(y, m) for y, m in periods]

cols_top = st.columns([2, 5, 2])
with cols_top[0]:
    sel_reg = st.radio(
        "Регион",
        available_regions,
        format_func=lambda x: region_map[x],
        horizontal=True,
        key="rasprod_region",
    )
with cols_top[1]:
    if periods:
        # По умолчанию — последний доступный период
        default_idx = len(periods) - 1
        sel_period_idx = st.select_slider(
            "Отчётный период",
            options=list(range(len(periods))),
            value=default_idx,
            format_func=lambda i: period_label(*periods[i]),
            key="rasprod_period",
        )
        sel_year, sel_month = periods[sel_period_idx]
    else:
        sel_year, sel_month = None, None
with cols_top[2]:
    st.markdown(
        f"<div style='padding-top:30px;color:{COLORS['muted']};'>"
        f"Всего периодов: <b>{len(periods)}</b></div>",
        unsafe_allow_html=True,
    )


# === 4 KPI карточки ===
def kpi_for_period(name_substr: str):
    kdf = data["kpi"]
    sel = kdf[
        (kdf["region_key"] == sel_reg)
        & (kdf["year"] == sel_year)
        & (kdf["month"] == sel_month)
        & (kdf["название"].str.contains(name_substr, na=False))
    ]
    return sel.iloc[0] if not sel.empty else None


st.markdown(f"### KPI за {period_label(sel_year, sel_month) if sel_year else '—'}")
kpi_cols = st.columns(4)
kpi_titles = [
    ("Объем жилищного строительства", "Объём жил. строительства", "тыс. м²", 0),
    ("Распроданность", "Распроданность", "%", 0),
    ("Стройготовность", "Стройготовность", "%", 0),
    ("Отношение", "Отношение распроданности к стройготовности", "%", 0),
]
for i, (substr, short_title, unit_label, digits) in enumerate(kpi_titles):
    with kpi_cols[i]:
        row = kpi_for_period(substr)
        val = row["значение_num"] if row is not None else None
        st.metric(short_title, f"{ru_num(val, digits)} {unit_label}" if val is not None else "—")


# === Прогноз по годам (KPI разбивка) ===
st.markdown(f"### Прогноз ввода по годам — {region_map[sel_reg]}, {period_label(sel_year, sel_month)}")

kpi_df = data["kpi"]
forecast_rows = kpi_df[
    (kpi_df["region_key"] == sel_reg)
    & (kpi_df["year"] == sel_year)
    & (kpi_df["month"] == sel_month)
]
if not forecast_rows.empty:
    # Собираем прогнозы по годам для каждого KPI
    forecast_data = []
    forecast_cols = [c for c in kpi_df.columns if c.startswith("прогноз_") and not c.endswith("_num")]
    forecast_years = [c.replace("прогноз_", "") for c in forecast_cols]

    fig = go.Figure()
    for _, row in forecast_rows.iterrows():
        name = row["название"]
        ys = []
        for c in forecast_cols:
            ys.append(_parse_val := row.get(f"{c}_num"))
        forecast_data.extend(
            {"Показатель": name, "Год": year, "Значение": value}
            for year, value in zip(forecast_years, ys)
        )
        color = KPI_COLORS.get(name.strip(), "#666")
        fig.add_trace(go.Bar(
            x=forecast_years, y=ys, name=name,
            marker_color=color,
            text=[ru_num(v) for v in ys],
            textposition="outside",
        ))
    fig.update_layout(
        barmode="group",
        height=350,
        margin=dict(l=0, r=0, t=20, b=0),
        yaxis_title="",
        xaxis_title="Год",
        legend=dict(orientation="h", y=-0.15),
    )
    style_plotly(fig, height=310)
    st.plotly_chart(fig, use_container_width=True)
    chart_data_expander(
        fig, pd.DataFrame(forecast_data), name="rasprod_forecast", key="rasprod_forecast"
    )


# === Динамика KPI по месяцам с 2020 года ===
st.markdown(f"### Динамика по месяцам — {region_map[sel_reg]}")

time_series = kpi_df[kpi_df["region_key"] == sel_reg].copy()
if not time_series.empty:
    time_series["period"] = pd.to_datetime(
        time_series["year"].astype(str) + "-" + time_series["month"].astype(str).str.zfill(2) + "-01"
    )
    time_series = time_series.sort_values("period")

    # График 1: только Объём жилищного строительства (большие значения, тыс. м²)
    vol_ts = time_series[time_series["название"].str.contains("Объем жилищного", na=False)]
    if not vol_ts.empty:
        st.markdown("**Объём жилищного строительства, тыс. м²**")
        fig_vol = go.Figure()
        fig_vol.add_trace(go.Scatter(
            x=vol_ts["period"], y=vol_ts["значение_num"],
            mode="lines+markers",
            line=dict(color=COLOR_VOLUME, width=2),
            marker=dict(size=4),
            name="Объём строительства",
            hovertemplate="%{x|%b %Y}<br>%{y:,.0f} тыс. м²<extra></extra>",
        ))
        fig_vol.update_layout(
            height=320,
            margin=dict(l=0, r=0, t=10, b=0),
            xaxis_title="",
            yaxis_title="",
            hovermode="x unified",
            showlegend=False,
        )
        style_plotly(fig_vol, height=280)
        st.plotly_chart(fig_vol, use_container_width=True)
        chart_data_expander(
            fig_vol,
            vol_ts[["period", "значение_num"]].rename(
                columns={"period": "Период", "значение_num": "Объём, тыс. м²"}
            ),
            name="rasprod_volume", key="rasprod_volume",
        )

    # График 2: 3 процентных KPI на одной оси
    st.markdown("**Распроданность · Стройготовность · Отношение, %**")
    fig_pct = go.Figure()
    for substr, short_title, unit_label, _ in kpi_titles:
        if "Объем" in substr:
            continue
        ts = time_series[time_series["название"].str.contains(substr, na=False)]
        if ts.empty:
            continue
        name = ts["название"].iloc[0].strip()
        color = KPI_COLORS.get(name, "#666")
        fig_pct.add_trace(go.Scatter(
            x=ts["period"], y=ts["значение_num"],
            mode="lines+markers",
            name=short_title,
            line=dict(color=color, width=2),
            marker=dict(size=4),
            hovertemplate="<b>" + short_title + "</b><br>%{x|%b %Y}<br>%{y}%<extra></extra>",
        ))
    fig_pct.update_layout(
        height=380,
        margin=dict(l=0, r=0, t=10, b=0),
        xaxis_title="",
        yaxis_title="",
        hovermode="x unified",
        legend=dict(orientation="h", y=-0.15),
    )
    style_plotly(fig_pct, height=320)
    st.plotly_chart(fig_pct, use_container_width=True)
    chart_data_expander(
        fig_pct,
        time_series[["period", "название", "значение_num"]].rename(
            columns={"period": "Период", "название": "Показатель", "значение_num": "Значение, %"}
        ),
        name="rasprod_percent", key="rasprod_percent",
    )


# === 6 таблиц ===
st.markdown(f"### Срезы по сегментам — {period_label(sel_year, sel_month)}, {region_map[sel_reg]}")

SECTION_TABS = [
    ("fed_okruga", "Федеральные округа"),
    ("regions", "Регионы"),
    ("developers", "Девелоперы"),
    ("by_dev_volume", "По объёму строительства"),
    ("by_population", "По численности населения"),
    ("by_class", "По классу недвижимости"),
]

tabs = st.tabs([title for _, title in SECTION_TABS])
for tab, (sheet, title) in zip(tabs, SECTION_TABS):
    with tab:
        df = data.get(sheet, pd.DataFrame())
        if df.empty:
            st.info(f"Нет данных для секции «{title}»")
            continue
        # Фильтр по region/period
        sel_df = df[
            (df["region_key"] == sel_reg)
            & (df["year"] == sel_year)
            & (df["month"] == sel_month)
        ].copy()
        if sel_df.empty:
            st.info("Нет данных для выбранного периода")
            continue

        # Найдём числовые колонки метрик
        metric_orig = [c for c in sel_df.columns
                       if c not in ("region_key", "year", "month", "month_name",
                                    "report_period", "section", "наименование")
                       and not c.endswith("_num")]

        col_map = {"наименование": title.rstrip("ы").rstrip("а").rstrip("ы") or "Сегмент"}
        for col in metric_orig:
            # короткие имена
            if "Объем" in col or "Объём" in col:
                col_map[col] = "Объём, м²"
            elif "Распроданность" in col:
                col_map[col] = "Распроданность"
            elif "Стройготовность" in col:
                col_map[col] = "Стройготовность"
            elif "Отношение" in col:
                col_map[col] = "Отношение Р / С"
            else:
                col_map[col] = col

        show_cols = ["наименование"] + metric_orig
        df_show = sel_df[show_cols].rename(columns=col_map)

        # Колонка configs для красивых прогресс-баров на % колонках
        column_config = {}
        for col in df_show.columns:
            if col in ("Распроданность", "Стройготовность", "Отношение Р / С"):
                column_config[col] = st.column_config.TextColumn(col, width="small")

        st.dataframe(
            df_show, hide_index=True, use_container_width=True,
            column_config=column_config,
            height=min(38 * (len(df_show) + 1), 500),
        )
