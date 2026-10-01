"""Current construction: live areas, sales/readiness and construction permits."""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from app.components.design import COLORS, apply_theme, page_header, style_plotly
from app.components.export import chart_data_expander
from app.data_access import load_construction_operational, load_rasprodannost

st.set_page_config(page_title="Текущее строительство — Аналитика Москвы", layout="wide")
apply_theme()

MONTHS = {1: "январь", 2: "февраль", 3: "март", 4: "апрель", 5: "май", 6: "июнь",
          7: "июль", 8: "август", 9: "сентябрь", 10: "октябрь", 11: "ноябрь", 12: "декабрь"}
MONTH_YTD = {month: "январь" if month == 1 else f"январь–{name}" for month, name in MONTHS.items()}


def fmt(value, digits=2):
    if value is None or pd.isna(value):
        return "—"
    return f"{float(value):,.{digits}f}".replace(",", " ").replace(".", ",")


def in_millions(value):
    """Convert source thousand m² to million m² without hiding missing data."""
    return None if value is None or pd.isna(value) else float(value) / 1000


def latest_rasprod_row(data: dict, region: str, metric_name: str):
    kpi = data.get("kpi", pd.DataFrame())
    periods = data.get("periods_by_region", {}).get(region, [])
    if kpi.empty or not periods:
        return None
    year, month = periods[-1]
    normalized = kpi["название"].astype(str).str.strip().str.casefold()
    rows = kpi[kpi["region_key"].eq(region) & kpi["year"].eq(year) & kpi["month"].eq(month)
               & normalized.eq(metric_name.strip().casefold())]
    return rows.iloc[0] if not rows.empty else None


def metric_card(label: str, value: str):
    with st.container(border=True, height=118):
        st.markdown(f"<div style='text-align:center;font-size:2rem;font-weight:700'>{value}</div>"
                    f"<div style='text-align:center;font-size:1.05rem'>{label}</div>",
                    unsafe_allow_html=True)


page_header("Текущее строительство · оперативные данные")
operational = load_construction_operational()
rasprod = load_rasprodannost()
region = st.radio("Регион", ["msk", "rf"], horizontal=True, key="construction_region",
                  format_func=lambda key: "Город Москва" if key == "msk" else "Российская Федерация")

construction = operational.get("construction", pd.DataFrame())
current = construction[construction["region_key"].eq(region)] if not construction.empty else pd.DataFrame()
construction_period = current["report_period"].dropna().iloc[0] if not current.empty else ""
living = current.loc[current["area_kind"].eq("living"), "value_thousand_m2"] if not current.empty else pd.Series(dtype=float)
total = current.loc[current["area_kind"].eq("total"), "value_thousand_m2"] if not current.empty else pd.Series(dtype=float)
if living.empty:
    fallback = latest_rasprod_row(rasprod, region, "Объем жилищного строительства")
    living_value = fallback.get("значение_num") if fallback is not None else None
else:
    living_value = living.iloc[0]
total_value = total.iloc[0] if not total.empty else None

st.subheader(f"1. Объём текущего строительства{f' · {construction_period}' if construction_period else ''}")
area_cols = st.columns(2)
with area_cols[0]:
    metric_card("млн м² жилой площади", f"{fmt(in_millions(living_value))} млн")
with area_cols[1]:
    metric_card("млн м² общей площади", f"{fmt(in_millions(total_value))} млн")

periods = rasprod.get("periods_by_region", {}).get(region, [])
rasprod_period = f"{MONTHS[periods[-1][1]]} {periods[-1][0]}" if periods else ""
st.subheader(f"2. Распроданность и стройготовность{f' · {rasprod_period}' if rasprod_period else ''}")
st.caption("Источник: раздел «Распроданность и стройготовность» на наш.дом.рф; "
           "показан последний подтверждённый период выбранного региона.")
kpi_cols = st.columns(3)
for column, (needle, label) in zip(kpi_cols, [
    ("Распроданность", "Распроданность"), ("Стройготовность", "Стройготовность"),
    ("Отношение распроданности к стройготовности",
     "Отношение распроданности к стройготовности"),
]):
    row = latest_rasprod_row(rasprod, region, needle)
    value = row.get("значение_num") if row is not None else None
    with column:
        metric_card(label, f"{fmt(value, 0)}%" if value is not None else "—")

st.subheader("3. Реализация квартир")
sales = operational.get("sales", pd.DataFrame())
selected_sales = sales[sales["region_key"].eq(region)] if not sales.empty else pd.DataFrame()
if selected_sales.empty:
    st.info("Детальная реализация появится после запуска источника `construction`.")
else:
    sale = selected_sales.iloc[0]
    st.caption(f"Данные за {sale.get('report_period', '—')}; площади переведены в млн м².")
    top_cols = st.columns(2)
    with top_cols[0]:
        metric_card("Всего жилой площади", f"{fmt(in_millions(sale.get('total_living_thousand_m2')))} млн м²")
    with top_cols[1]:
        metric_card("Продажи открыты", f"{fmt(in_millions(sale.get('sales_open_thousand_m2')))} млн м²")
    circle_cols = st.columns(3)
    for column, (label, key, color) in zip(circle_cols, [
        ("Проданная площадь", "sold", COLORS["green"]),
        ("Непроданная площадь", "unsold", COLORS["blue"]),
        ("Продажи не открыты", "sales_not_open", "#AEB9BE"),
    ]):
        pct, area = sale.get(f"{key}_pct"), sale.get(f"{key}_thousand_m2")
        fig = go.Figure(go.Pie(values=[pct or 0, max(0, 100 - (pct or 0))], labels=[label, "Остальное"],
                               hole=.72, marker_colors=[color, "#E8EDF0"], textinfo="none", sort=False,
                               name=label, domain=dict(x=[.12, .88], y=[.08, .92]),
                               hovertemplate="%{label}: %{value:.0f}%<extra></extra>"))
        fig.add_annotation(text=f"<b>{fmt(pct, 0)}%</b>", showarrow=False, font_size=22)
        fig.update_layout(showlegend=False, margin=dict(l=4, r=4, t=4, b=4), height=180)
        style_plotly(fig, height=180)
        with column:
            st.markdown(f"**{label}**")
            st.plotly_chart(fig, width="stretch", key=f"sales_{region}_{key}")
            st.markdown(f"<div style='text-align:center'>{fmt(in_millions(area))} млн м²</div>", unsafe_allow_html=True)
    extra_cols = st.columns(2)
    with extra_cols[0]:
        st.metric("Стоимость 1 м²", f"{fmt(sale.get('price_per_m2_rub'), 0)} руб.")
    with extra_cols[1]:
        st.metric("Объём привлечённых средств", f"{fmt(sale.get('funds_million_rub'), 0)} млн руб.")

st.subheader("4. Разрешения на строительство · Москва")
permits = operational.get("permits", pd.DataFrame())
if permits.empty:
    st.info("Нет данных о выдаче разрешений из «Мониторинга 2.0».")
else:
    permit_kind = st.radio("Назначение", ["housing", "nonresidential"], horizontal=True,
                           format_func=lambda value: "Жильё" if value == "housing" else "Нежильё",
                           key="permit_kind")
    filtered = permits[permits["kind"].eq(permit_kind)].copy()
    if filtered.empty:
        st.info("Для выбранного назначения в «Мониторинге 2.0» нет данных.")
        st.stop()
    current_year = int(filtered["year"].max())
    current_months = sorted(filtered.loc[filtered["year"].eq(current_year), "month"].dropna().astype(int).unique())
    options = current_months or list(range(1, 13))
    month = st.selectbox("Период с начала года", options, index=len(options) - 1,
                         format_func=lambda value: MONTH_YTD[value].capitalize(), key="permit_month")
    monthly_full = filtered.groupby("year")["value_thousand_m2"].sum()
    annual = operational.get("permit_annual", pd.DataFrame())
    annual = annual[annual["kind"].eq(permit_kind)] if not annual.empty else pd.DataFrame()
    full = (annual.set_index("year")["value_thousand_m2"]
            if not annual.empty else pd.Series(dtype=float))
    # The current incomplete year is absent from the annual summary.
    full = full.combine_first(monthly_full)
    ytd = filtered[filtered["month"].le(month)].groupby("year")["value_thousand_m2"].sum()
    table = pd.DataFrame({"Год": sorted(set(full.index) | set(ytd.index))})
    table["За год, тыс. м²"] = table["Год"].map(full)
    table["С начала года, тыс. м²"] = table["Год"].map(ytd)
    table = table[table["Год"].ge(2011)].reset_index(drop=True)
    table["Изменение, %"] = table["С начала года, тыс. м²"].pct_change() * 100
    remainder = (table["За год, тыс. м²"] - table["С начала года, тыс. м²"]).clip(lower=0)
    fig = go.Figure()
    fig.add_bar(x=table["Год"].astype(str), y=table["С начала года, тыс. м²"],
                name=MONTH_YTD[month].capitalize(), marker_color=COLORS["red"],
                text=[fmt(value, 0) for value in table["С начала года, тыс. м²"]], textposition="inside")
    fig.add_bar(x=table["Год"].astype(str), y=remainder, name="Остаток до итога года", marker_color="#C9D4DA")
    fig.add_scatter(x=table["Год"].astype(str), y=table["За год, тыс. м²"], mode="text", showlegend=False,
                    name="Итог за год", hoverinfo="skip",
                    text=[fmt(value, 0) for value in table["За год, тыс. м²"]], textposition="top center")
    for _, row in table.dropna(subset=["Изменение, %"]).iterrows():
        fig.add_annotation(x=str(int(row["Год"])), y=row["С начала года, тыс. м²"], yshift=9,
                           text=f"{row['Изменение, %']:+.0f}%", showarrow=False,
                           bgcolor=COLORS["green"] if row["Изменение, %"] >= 0 else "#303943",
                           font=dict(color="white", size=10), borderpad=3)
    fig.update_layout(barmode="stack", height=510, yaxis_title="тыс. м²", xaxis_title="",
                      legend=dict(orientation="h", y=-.15), margin=dict(l=45, r=30, t=35, b=70))
    style_plotly(fig, height=510)
    st.plotly_chart(fig, use_container_width=True, key=f"permits_{permit_kind}_{month}")
    chart_data_expander(fig, table, name=f"permits_{permit_kind}_{month}", key=f"permits_data_{permit_kind}_{month}")

with st.expander("Исходные файлы и даты скачивания", expanded=False):
    st.write("Оперативные показатели:", operational.get("source_file") or "файл ещё не загружен")
    st.write("Разрешения на строительство: лист «Реестр ОКС» файла «Мониторинг 2.0».")
    st.write("Распроданность и стройготовность: ежемесячная выгрузка наш.дом.рф.")
