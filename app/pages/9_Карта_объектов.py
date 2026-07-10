"""Карта объектов мониторинга 2.0."""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from app.components.design import COLORS, apply_theme, page_header
from app.realty_map import GEOCODE_CACHE, MAP_ADDRESSES, load_monitoring_map_objects

MISSING_REPORT = GEOCODE_CACHE.parent / "monitoring_geocodes_missing.csv"
SUSPICIOUS_REPORT = GEOCODE_CACHE.parent / "monitoring_geocodes_suspicious.csv"
MISSING_ADDRESSES = GEOCODE_CACHE.parent / "missing_geocode_addresses.csv"
UNIQUE_ADDRESSES = GEOCODE_CACHE.parent / "monitoring_map_unique_addresses.csv"

st.set_page_config(page_title="Карта объектов — Аналитика Москвы", layout="wide")
apply_theme()


def ru_num(value, digits=0) -> str:
    if value is None or pd.isna(value):
        return "—"
    try:
        text = f"{float(value):,.{digits}f}"
    except (TypeError, ValueError):
        return str(value)
    return text.replace(",", " ").replace(".", ",")


def _marker_size(area: pd.Series) -> pd.Series:
    values = pd.to_numeric(area, errors="coerce").fillna(0).clip(lower=0)
    if values.max() <= 0:
        return pd.Series([9] * len(values), index=values.index)
    return 7 + (values.pow(0.5) / values.max() ** 0.5) * 18


def _coord_quality(source: object, has_coords: object) -> str:
    if not bool(has_coords):
        return "Нет координат"
    text = "" if pd.isna(source) else str(source)
    return "Приблизительные" if "centroid" in text else "Точные"


objects = load_monitoring_map_objects()
if objects.empty:
    st.warning("Нет данных monitoring 2.0.")
    st.stop()

objects = objects.copy()
objects["coord_quality"] = [
    _coord_quality(source, has_coords)
    for source, has_coords in zip(objects["coord_source"], objects["has_coords"])
]
with_coords = objects[objects["has_coords"]].copy()
missing_count = int((~objects["has_coords"]).sum())
exact_count = int(objects["coord_quality"].eq("Точные").sum())
approx_count = int(objects["coord_quality"].eq("Приблизительные").sum())
with_coords_label = f"{len(with_coords):,}".replace(",", " ")
objects_label = f"{len(objects):,}".replace(",", " ")
missing_label = f"{missing_count:,}".replace(",", " ")
exact_label = f"{exact_count:,}".replace(",", " ")
approx_label = f"{approx_count:,}".replace(",", " ")

st.markdown(
    """
    <style>
    [data-testid="stAppViewContainer"] > .main .block-container {
      max-width: 1500px;
      padding-top: 1rem;
    }
    [data-testid="stMainBlockContainer"],
    .stMainBlockContainer.block-container {
      max-width: 1500px !important;
      padding-top: 1rem !important;
    }
    div[data-testid="stVerticalBlock"] {
      gap: .65rem;
    }
    div[data-testid="stHorizontalBlock"] {
      gap: .75rem;
    }
    div[data-testid="stSelectbox"],
    div[data-testid="stMultiSelect"],
    div[data-testid="stSlider"],
    div[data-testid="stCheckbox"] {
      margin-bottom: 0 !important;
    }
    label p {
      font-size: .76rem !important;
      line-height: 1.1 !important;
      margin-bottom: .15rem !important;
    }
    div[data-testid="stPlotlyChart"] {
      padding: 0 !important;
      overflow: hidden;
    }
    div[data-testid="stPlotlyChart"] > div {
      border-radius: 8px;
    }
    div[data-testid="stPlotlyChart"] .js-plotly-plot,
    div[data-testid="stPlotlyChart"] .plot-container,
    div[data-testid="stPlotlyChart"] .svg-container,
    div[data-testid="stPlotlyChart"] .mapboxgl-map {
      width: 100% !important;
      min-width: 100% !important;
    }
    div[data-testid="stPlotlyChart"] .mapboxgl-map {
      left: 0 !important;
      right: 0 !important;
    }
    div[data-testid="stPlotlyChart"] .mapboxgl-canvas-container,
    div[data-testid="stPlotlyChart"] .mapboxgl-canvas {
      width: 100% !important;
      min-width: 100% !important;
    }
    div[data-testid="stPlotlyChart"] .modebar-container,
    div[data-testid="stPlotlyChart"] .modebar {
      display: block !important;
      opacity: 1 !important;
      visibility: visible !important;
      pointer-events: auto !important;
    }
    div[data-testid="stPlotlyChart"] .modebar {
      display: flex !important;
      right: 10px !important;
      top: 10px !important;
      background: rgba(7, 23, 35, .72);
      border: 1px solid rgba(255,255,255,.18);
      border-radius: 7px;
      padding: 3px;
    }
    div[data-testid="stPlotlyChart"] .modebar-group {
      display: flex !important;
      background: transparent !important;
      padding-left: 0 !important;
    }
    div[data-testid="stPlotlyChart"] .modebar-btn {
      display: inline-flex !important;
      align-items: center;
      justify-content: center;
      opacity: .86 !important;
      visibility: visible !important;
      pointer-events: auto !important;
      border-radius: 5px;
    }
    div[data-testid="stPlotlyChart"] .modebar-btn:hover {
      background: rgba(255,255,255,.14) !important;
      opacity: 1 !important;
    }
    div[data-testid="stPlotlyChart"] .modebar-btn svg,
    div[data-testid="stPlotlyChart"] .modebar-btn path {
      display: block !important;
      opacity: 1 !important;
      visibility: visible !important;
      pointer-events: auto !important;
      fill: #FFFFFF !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

page_header("Карта объектов", "Объекты мониторинга 2.0 с координатами, статусами и фильтрами.")
metric_cols = st.columns(5)
metric_cols[0].metric("Всего", objects_label)
metric_cols[1].metric("На карте", with_coords_label)
metric_cols[2].metric("Точные", exact_label)
metric_cols[3].metric("Приблизительные", approx_label)
metric_cols[4].metric("Без координат", missing_label)

filter_cols = st.columns([2.15, 1.15, 1.1, 1.25, 1.2, .95])
developers = sorted([x for x in objects["developer"].dropna().unique() if str(x).strip()])
with filter_cols[0]:
    developer = st.selectbox("Застройщик", ["Все"] + developers, index=0)
with filter_cols[1]:
    statuses = st.multiselect("Статус", sorted(objects["status"].dropna().unique()), default=sorted(objects["status"].dropna().unique()))
with filter_cols[2]:
    okrugs = st.multiselect("Округ", sorted([x for x in objects["okrug"].dropna().unique() if str(x).strip()]))
with filter_cols[3]:
    years = sorted(int(y) for y in objects["year"].dropna().unique() if 2000 <= int(y) <= 2035)
    if years:
        year_range = st.slider("Годы", min_value=min(years), max_value=max(years), value=(min(years), max(years)))
    else:
        year_range = None
with filter_cols[4]:
    coord_quality = st.selectbox("Координаты", ["Все", "Точные", "Приблизительные"], index=0)
with filter_cols[5]:
    only_with_coords = st.checkbox("Только на карте", value=True)

data = objects.copy()
if developer != "Все":
    data = data[data["developer"] == developer]
if statuses:
    data = data[data["status"].isin(statuses)]
if okrugs:
    data = data[data["okrug"].isin(okrugs)]
if year_range is not None:
    data = data[(data["year"].isna()) | ((data["year"] >= year_range[0]) & (data["year"] <= year_range[1]))]
if coord_quality != "Все":
    data = data[data["coord_quality"] == coord_quality]
if only_with_coords:
    data = data[data["has_coords"]]

map_data = data[data["has_coords"]].copy()

if map_data.empty:
    st.info("Для выбранных фильтров нет объектов с координатами.")
else:
    color_map = {
        "Введено": COLORS["green"],
        "Строится": COLORS["blue"],
    }
    fig = go.Figure()
    for (status, quality), group in map_data.groupby(["status", "coord_quality"]):
        custom = pd.DataFrame({
            "object_name": group["object_name"].fillna(""),
            "address": group["address"].fillna(""),
            "developer": group["developer"].fillna(""),
            "district": group["district"].fillna(""),
            "year": group["year"].fillna("").astype(str).str.replace(".0", "", regex=False),
            "area": group["area_total"].map(lambda v: ru_num(v, 0)),
            "apartments": group["apartments"].map(lambda v: ru_num(v, 0)),
            "coord_source": group["coord_source"].fillna(""),
        })
        fig.add_trace(go.Scattermapbox(
            lat=group["lat"],
            lon=group["lon"],
            mode="markers",
            name=status if quality == "Точные" else f"{status} · приблизительно",
            marker=dict(
                size=_marker_size(group["area_total"]),
                color=color_map.get(status, COLORS["amber"]),
                opacity=0.44 if quality == "Приблизительные" else 0.82,
            ),
            customdata=custom,
            hovertemplate=(
                "<b>%{customdata[0]}</b><br>"
                "%{customdata[1]}<br>"
                "Застройщик: %{customdata[2]}<br>"
                "Район: %{customdata[3]}<br>"
                "Год: %{customdata[4]}<br>"
                "Площадь: %{customdata[5]} м²<br>"
                "Квартиры: %{customdata[6]}<br>"
                "Координаты: %{customdata[7]}<extra></extra>"
            ),
        ))

    center_lat = float(map_data["lat"].mean()) if not map_data.empty else 55.7558
    center_lon = float(map_data["lon"].mean()) if not map_data.empty else 37.6176
    fig.update_layout(
        autosize=True,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Inter, Segoe UI, Arial, sans-serif", color=COLORS["ink"]),
        hovermode="closest",
        mapbox=dict(
            style="open-street-map",
            center=dict(lat=center_lat, lon=center_lon),
            zoom=10.2 if developer == "Все" else 11.2,
            domain=dict(x=[0, 1], y=[0, 1]),
        ),
        height=660,
        margin=dict(l=0, r=0, t=0, b=0),
        legend=dict(orientation="h", y=0.01, x=0.02, bgcolor="rgba(255,255,255,.86)"),
    )
    st.plotly_chart(
        fig,
        use_container_width=True,
        config={
            "displayModeBar": True,
            "displaylogo": False,
            "scrollZoom": True,
            "responsive": True,
        },
    )

with st.expander("Статус координат", expanded=False):
    unique_missing_addresses = int(
        objects[
            (~objects["has_coords"])
            & objects["address"].fillna("").astype(str).str.strip().ne("")
        ]["address_key"].nunique()
    )
    coord_cols = st.columns(6)
    coord_cols[0].metric("Всего объектов", f"{len(objects):,}".replace(",", " "))
    coord_cols[1].metric("С координатами", f"{int(objects['has_coords'].sum()):,}".replace(",", " "))
    coord_cols[2].metric("Точные", f"{exact_count:,}".replace(",", " "))
    coord_cols[3].metric("Приблизительные", f"{approx_count:,}".replace(",", " "))
    coord_cols[4].metric("Без координат", f"{missing_count:,}".replace(",", " "))
    coord_cols[5].metric("Уникальных адресов", f"{unique_missing_addresses:,}".replace(",", " "))

    report_cols = st.columns(5)
    report_files = [
        (MAP_ADDRESSES, "Скачать все объекты", "monitoring_map_addresses.csv"),
        (UNIQUE_ADDRESSES, "Скачать все адреса", "monitoring_map_unique_addresses.csv"),
        (MISSING_ADDRESSES, "Скачать адреса для геокодинга", "missing_geocode_addresses.csv"),
        (MISSING_REPORT, "Скачать объекты без координат", "monitoring_geocodes_missing.csv"),
        (SUSPICIOUS_REPORT, "Скачать подозрительные координаты", "monitoring_geocodes_suspicious.csv"),
    ]
    for col, (path, label, file_name) in zip(report_cols, report_files):
        if path.exists():
            col.download_button(
                label,
                data=path.read_bytes(),
                file_name=file_name,
                mime="text/csv",
                use_container_width=True,
            )
        else:
            col.button(label, disabled=True, use_container_width=True)

    st.code(
        "python scripts\\build_monitoring_geocodes.py --limit 0 --missing-out data\\derived\\missing_geocode_addresses.csv\n"
        "python scripts\\build_monitoring_geocodes.py --provider nominatim --limit 50 --missing-out data\\derived\\missing_geocode_addresses.csv",
        language="powershell",
    )

table_cols = [
    "status", "developer", "object_name", "address", "okrug", "district",
    "year", "area_total", "apartments", "coord_source",
]
table = data[table_cols].copy()
table = table.rename(columns={
    "status": "Статус",
    "developer": "Застройщик",
    "object_name": "Объект",
    "address": "Адрес",
    "okrug": "Округ",
    "district": "Район",
    "year": "Год",
    "area_total": "Площадь, м²",
    "apartments": "Квартиры",
    "coord_source": "Источник координат",
})
with st.expander("Таблица объектов", expanded=False):
    st.dataframe(
        table,
        hide_index=True,
        use_container_width=True,
        height=min(420, 36 * (len(table) + 1)),
        column_config={
            "Площадь, м²": st.column_config.NumberColumn(format="%.0f"),
            "Квартиры": st.column_config.NumberColumn(format="%.0f"),
            "Год": st.column_config.NumberColumn(format="%d"),
        },
    )
