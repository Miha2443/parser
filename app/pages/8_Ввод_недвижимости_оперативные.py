"""Operational commissioning in Moscow: YTD charts and quarterly structure."""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from app.components.design import COLORS, apply_theme, page_header, style_plotly
from app.components.export import chart_download_button, table_download_buttons
from app.data_access import load_monitoring_2_0, load_monitoring_operational_history, load_vvod_static
from app.vvod_operational import housing_ytd_table, nonres_ytd_table, quarter_tree


st.set_page_config(page_title="Оперативный ввод — Аналитика Москвы", layout="wide")
apply_theme()

MONTH_LABELS = {
    1: "январь", 2: "январь–февраль", 3: "январь–март", 4: "январь–апрель",
    5: "январь–май", 6: "январь–июнь", 7: "январь–июль", 8: "январь–август",
    9: "январь–сентябрь", 10: "январь–октябрь", 11: "январь–ноябрь", 12: "январь–декабрь",
}


def fmt(value: float, digits: int = 1) -> str:
    if value is None or pd.isna(value):
        return "—"
    return f"{value:.{digits}f}".replace(".", ",")


def period_chart(table: pd.DataFrame, title: str, period_label: str, key: str) -> go.Figure:
    data = table[table[["За год, млн м²", "За выбранный период, млн м²"]].notna().any(axis=1)].copy()
    data["Год"] = data["Год"].astype(int).astype(str)
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=data["Год"], y=data["За год, млн м²"], name="В целом за год",
        marker_color="#C9D4DA", width=.72,
        text=[fmt(v, 2) if pd.notna(v) else "" for v in data["За год, млн м²"]],
        textposition="outside",
        hovertemplate="<b>%{x}</b><br>За год: %{y:.3f} млн м²<extra></extra>",
    ))
    fig.add_trace(go.Bar(
        x=data["Год"], y=data["За выбранный период, млн м²"], name=period_label.capitalize(),
        marker_color=COLORS["red"], width=.72,
        text=[fmt(v, 2) if pd.notna(v) else "" for v in data["За выбранный период, млн м²"]],
        textposition="inside", textfont=dict(color="white"),
        hovertemplate="<b>%{x}</b><br>За период: %{y:.3f} млн м²<extra></extra>",
    ))
    growth = data["Изменение полного года, %"]
    fig.add_trace(go.Scatter(
        x=data["Год"], y=growth, name="Темп за полный год", yaxis="y2",
        mode="lines+markers+text", line=dict(color=COLORS["blue"], dash="dot", width=2),
        marker=dict(size=7),
        text=[f"{v:+.0f}%" if pd.notna(v) else "" for v in growth],
        textposition="top center",
        hovertemplate="<b>%{x}</b><br>Темп за год: %{y:+.1f}%<extra></extra>",
    ))
    fig.update_layout(
        title=title, barmode="overlay", height=500, bargap=.22,
        margin=dict(l=55, r=55, t=75, b=70),
        yaxis=dict(title="млн м²", rangemode="tozero"),
        yaxis2=dict(title="Темп, %", overlaying="y", side="right", showgrid=False, range=[-80, 100]),
        legend=dict(orientation="h", y=-.18, x=.5, xanchor="center"),
    )
    for _, row in data.dropna(subset=["За выбранный период, млн м²", "Изменение к аналогичному периоду, %"]).iterrows():
        change = float(row["Изменение к аналогичному периоду, %"])
        fig.add_annotation(
            x=row["Год"], y=row["За выбранный период, млн м²"], yshift=9,
            text=f"{change:+.0f}%", showarrow=False,
            bgcolor=COLORS["green"] if change >= 0 else "#303943",
            borderpad=3, font=dict(color="white", size=10),
        )
    style_plotly(fig, height=500)
    return fig


def render_table(table: pd.DataFrame, name: str, key: str) -> None:
    shown = table.copy()
    shown["За год, млн м²"] = shown["За год, млн м²"].map(lambda v: round(v, 3) if pd.notna(v) else None)
    shown["За выбранный период, млн м²"] = shown["За выбранный период, млн м²"].map(
        lambda v: round(v, 3) if pd.notna(v) else None
    )
    shown["Изменение к аналогичному периоду, %"] = shown["Изменение к аналогичному периоду, %"].map(
        lambda v: round(v, 1) if pd.notna(v) else None
    )
    shown["Изменение полного года, %"] = shown["Изменение полного года, %"].map(
        lambda v: round(v, 1) if pd.notna(v) else None
    )
    st.dataframe(shown, hide_index=True, use_container_width=True, height=300)
    table_download_buttons(shown, name=name, key_prefix=key)


page_header(
    "Ввод недвижимости · оперативные данные",
    "Москва. Сопоставление ввода с начала года и структура введённых объектов по данным «Мониторинга 2.0».",
)

vvod = load_vvod_static()
monitoring = load_monitoring_2_0()
history = load_monitoring_operational_history()
rv = monitoring.get("rv", pd.DataFrame())
if rv.empty:
    st.error("Нет данных реестра ввода из «Мониторинга 2.0».")
    st.stop()

current_year = int(pd.to_numeric(rv["Год ввода по Мосстату"], errors="coerce").max())
available_months = sorted({
    {"январь": 1, "февраль": 2, "март": 3, "апрель": 4, "май": 5, "июнь": 6,
     "июль": 7, "август": 8, "сентябрь": 9, "октябрь": 10, "ноябрь": 11, "декабрь": 12}.get(str(v).strip().casefold())
    for v in rv.loc[pd.to_numeric(rv["Год ввода по Мосстату"], errors="coerce").eq(current_year), "Месяц ввода по Мосстату"]
} - {None})
source_date = pd.to_datetime(history.get("source_date", ""), dayfirst=True, errors="coerce")
last_closed_month = 12 if pd.notna(source_date) and source_date.month == 1 else (
    int(source_date.month) - 1 if pd.notna(source_date) else 8
)
default_month = max([m for m in available_months if m <= last_closed_month], default=min(max(available_months), 8)) if available_months else 8
month = st.selectbox(
    "Период с начала года",
    options=available_months or list(range(1, 13)),
    index=(available_months or list(range(1, 13))).index(default_month),
    format_func=lambda value: MONTH_LABELS[value].capitalize(),
)
period_label = MONTH_LABELS[month]

st.subheader("1. Ввод жилья")
housing = housing_ytd_table(vvod["msk_total"], rv, history["housing_monthly"], month)
housing_fig = period_chart(housing, "Ввод жилой площади", period_label, "housing_operational")
st.plotly_chart(housing_fig, use_container_width=True, key="housing_operational")
with st.expander("Скачать график", expanded=False):
    chart_download_button(housing_fig, name=f"vvod_zhilya_{current_year}_{month:02d}", key="housing_operational_png")
st.caption("Таблица 1. Жильё: полный год и накопленный ввод за сопоставимый период.")
render_table(housing, f"vvod_zhilya_{period_label}", "housing_table")

st.subheader("2. Ввод нежилой недвижимости")
nonres_mode = st.radio(
    "Состав показателя",
    ["Всего нежилья", "Без нежилых помещений на первых этажах МКД"],
    horizontal=True,
)
exclude_mkd = nonres_mode.startswith("Без")
st.caption(
    "Помесячный фактический ввод нежилья доступен в реестре РВ с 2017 года. "
    "Для более ранних лет показан годовой итог; накопленные значения не подменяются данными о выдаче разрешений."
)
nonres = nonres_ytd_table(
    vvod["msk_nonres"], rv, month, exclude_mkd=exclude_mkd
)
nonres_fig = period_chart(nonres, f"Ввод нежилой недвижимости · {nonres_mode.lower()}", period_label, "nonres_operational")
st.plotly_chart(nonres_fig, use_container_width=True, key="nonres_operational")
with st.expander("Скачать график", expanded=False):
    chart_download_button(nonres_fig, name=f"vvod_nezhilya_{current_year}_{month:02d}", key="nonres_operational_png")
st.caption("Таблица 2. Нежильё: полный год и накопленный ввод за сопоставимый период.")
render_table(nonres, f"vvod_nezhilya_{period_label}", "nonres_table")

st.subheader("3. Структура ввода за квартал")
qcol1, qcol2 = st.columns(2)
years = sorted(pd.to_numeric(rv["Год ввода по Мосстату"], errors="coerce").dropna().astype(int).unique(), reverse=True)
with qcol1:
    tree_year = st.selectbox("Год", years, index=years.index(current_year), key="tree_year")
with qcol2:
    quarter = st.selectbox("Квартал", [1, 2, 3, 4], index=0, format_func=lambda q: f"{q} квартал", key="tree_quarter")
tree = quarter_tree(rv, tree_year, quarter)

st.metric("Всего введено недвижимости", f"{fmt(tree['total'])} млн м²")
left, right = st.columns(2)
with left:
    st.markdown("#### Жилые объекты")
    st.metric("Общая площадь", f"{fmt(tree['housing_objects'])} млн м²")
    st.write(f"**МКД, общая площадь:** {fmt(tree['mkd_total'])} млн м²")
    st.write(f"**Квартиры (жилая площадь МКД):** {fmt(tree['mkd_residential'])} млн м²")
    st.write(f"**ИЖС:** {fmt(tree['izhs'])} млн м²")
    st.write(f"**МОП:** {fmt(tree['mop'])} млн м²")
    st.write(f"**Нежилые помещения в жилых объектах:** {fmt(tree['nonres_in_housing'])} млн м²")
    st.metric("Жилая площадь", f"{fmt(tree['residential_area'])} млн м²")
with right:
    st.markdown("#### Нежилые объекты")
    st.metric("Отдельно стоящие объекты", f"{fmt(tree['nonres_objects'])} млн м²")
    st.write(f"**Офисы:** {fmt(tree['offices'])} млн м²")
    st.write(f"**Гостиницы и апарт-отели:** {fmt(tree['hotels'])} млн м²")
    st.write(f"**Промышленные объекты:** {fmt(tree['industrial'])} млн м²")
    st.write(f"**Социальные объекты:** {fmt(tree['social'])} млн м²")
    st.write(f"**Прочее:** {fmt(tree['other'])} млн м²")
    st.metric("Всё нежильё, включая помещения в жилых объектах", f"{fmt(tree['nonres_total'])} млн м²")

with st.expander("Данные дерева и методика", expanded=False):
    tree_table = pd.DataFrame([
        ("Всего", tree["total"]), ("Жилые объекты, общая площадь", tree["housing_objects"]),
        ("Жилая площадь", tree["residential_area"]), ("МКД, общая площадь", tree["mkd_total"]),
        ("Квартиры (жилая площадь МКД)", tree["mkd_residential"]),
        ("ИЖС", tree["izhs"]), ("МОП", tree["mop"]),
        ("Нежилые помещения в жилых объектах", tree["nonres_in_housing"]),
        ("Нежилые отдельно стоящие объекты", tree["nonres_objects"]),
        ("Офисы", tree["offices"]), ("Гостиницы и апарт-отели", tree["hotels"]),
        ("Промышленные", tree["industrial"]), ("Социальные", tree["social"]),
        ("Прочее", tree["other"]), ("Всё нежильё", tree["nonres_total"]),
    ], columns=["Показатель", "млн м²"])
    st.dataframe(tree_table, hide_index=True, use_container_width=True)
    table_download_buttons(tree_table, name=f"struktura_vvoda_{tree_year}_q{quarter}", key_prefix="tree_data")
    st.caption(
        "Общая площадь делится по отрасли объекта. Жилая площадь, МОП и нежилые помещения внутри жилых "
        "объектов берутся из нормализованных полей реестра РВ «Мониторинга 2.0»."
    )

with st.expander("Исходные файлы и даты скачивания", expanded=False):
    st.write("Мониторинг 2.0:", history.get("source_file") or "источник указан в витрине")
    if history.get("source_date"):
        st.write("Дата файла:", history["source_date"])
    st.write("Исторические годовые значения: книга ввода недвижимости.")
    st.write("Исторические помесячные значения: лист «Данные с 2011 года» файла «Мониторинг 2.0».")
