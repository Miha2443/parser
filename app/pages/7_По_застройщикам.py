"""По застройщикам — карточка одного застройщика.

Основной источник — мониторинг 2.0 наш.дом.рф (реестры ОКС + РВ).
Категории площадей:
- Жилая (Жилая площадь, где Группировка=Жилье)
- Нежилая в жилых (Общая − Жилая, где Группировка=Жилье)
- Нежилые здания (Общая, где Группировка=Нежилье)
"""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from app.data_access import load_monitoring_2_0

st.set_page_config(page_title="По застройщикам — Аналитика Москвы", layout="wide")

# Цвета для 3 категорий площади
CAT_COLORS = {
    "Жилая": "#8BC540",
    "Нежилая в жилых (МОП, 1-е этажи)": "#4EC3E0",
    "Нежилые здания": "#7A8386",
}
CAT_NAMES = list(CAT_COLORS.keys())
CAT_COLS = ["category_жилая", "category_нежилая_в_жилых", "category_нежилые_здания"]


def ru_num(value, digits=0):
    if value is None or pd.isna(value):
        return "—"
    try:
        s = f"{float(value):,.{digits}f}"
    except (TypeError, ValueError):
        return str(value)
    return s.replace(",", " ").replace(".", ",")


def categorize_sum(df: pd.DataFrame) -> dict:
    """Возвращает {category_name: sum_m²} для DataFrame."""
    if df.empty:
        return {n: 0.0 for n in CAT_NAMES}
    return {
        CAT_NAMES[0]: float(df["category_жилая"].sum()),
        CAT_NAMES[1]: float(df["category_нежилая_в_жилых"].sum()),
        CAT_NAMES[2]: float(df["category_нежилые_здания"].sum()),
    }


def render_pie(values: dict, title: str = ""):
    total = sum(values.values())
    if total == 0:
        st.info("Нет данных")
        return
    fig = go.Figure(go.Pie(
        labels=list(values.keys()),
        values=list(values.values()),
        marker=dict(colors=[CAT_COLORS[k] for k in values.keys()]),
        textinfo="percent",
        hovertemplate="<b>%{label}</b><br>%{value:,.0f} м² (%{percent})<extra></extra>",
        sort=False,
        hole=0.4,
    ))
    fig.update_layout(
        title=dict(text=title, x=0.5, font=dict(size=14)),
        height=320,
        margin=dict(l=10, r=10, t=40, b=10),
        annotations=[dict(text=f"{ru_num(total/1000, 1)}<br>тыс. м²",
                          x=0.5, y=0.5, showarrow=False, font=dict(size=14))],
        showlegend=True,
        legend=dict(orientation="h", y=-0.1, font=dict(size=10)),
    )
    st.plotly_chart(fig, use_container_width=True)


data = load_monitoring_2_0()
if not data["developers"]:
    st.warning(
        "Нет данных. Положи `monitoring_2_0_<date>.xlsx` в "
        "`data/raw/realty/nashdom/` или в `nashdom/` в корне репо."
    )
    st.stop()

st.title("По застройщикам — мониторинг 2.0")

# === Выбор застройщика ===
cols_top = st.columns([3, 2, 2])
with cols_top[0]:
    sel_dev = st.selectbox(
        "Застройщик (Группа компаний)",
        data["developers"],
        key="dev_select_mon",
        help=f"Всего {len(data['developers'])} застройщиков",
    )
with cols_top[1]:
    st.markdown(
        f"<div style='padding-top:30px;color:#7A8386;'>"
        f"Реестр РВ: <b>{data['min_year']}–{data['max_year']}</b></div>",
        unsafe_allow_html=True,
    )

# Фильтруем данные по выбранному
rv = data["rv"]
oks = data["oks"]
rv_dev = rv[rv["Группа компаний"] == sel_dev].copy()
oks_dev = oks[oks["Группа компаний"] == sel_dev].copy()

# === KPI карточки ===
total_rv = rv_dev[CAT_COLS].sum().sum()
total_oks = oks_dev[CAT_COLS].sum().sum()
last_year = int(data["max_year"]) if data["max_year"] else None
last_year_rv = rv_dev[rv_dev["Год ввода по Мосстату"] == last_year] if last_year else rv_dev
total_last = last_year_rv[CAT_COLS].sum().sum()

cols_kpi = st.columns(4)
cols_kpi[0].metric("Введено всего",
                   f"{ru_num(total_rv/1000, 0)} тыс. м²",
                   help=f"Сумма всех введённых объектов за {data['min_year']}–{data['max_year']}")
cols_kpi[1].metric(f"Введено за {last_year}",
                   f"{ru_num(total_last/1000, 0)} тыс. м²",
                   help=f"Сумма за последний доступный год")
cols_kpi[2].metric("В строительстве",
                   f"{ru_num(total_oks/1000, 0)} тыс. м²",
                   help="Реестр ОКС, текущие незавершённые объекты")
cols_kpi[3].metric("Объектов всего",
                   f"введено: {len(rv_dev)} · строится: {len(oks_dev)}")

# === 3 круговые диаграммы ===
st.markdown(f"### Структура площадей по типу — {sel_dev}")
pie_cols = st.columns(3)
with pie_cols[0]:
    render_pie(
        categorize_sum(rv_dev),
        f"Введено с {data['min_year']} г.",
    )
with pie_cols[1]:
    render_pie(
        categorize_sum(last_year_rv),
        f"Введено за {last_year}",
    )
with pie_cols[2]:
    render_pie(
        categorize_sum(oks_dev),
        "В строительстве",
    )

# === Столбики по годам ввода (stacked) ===
st.markdown(f"### Ввод недвижимости по годам — {sel_dev}")

if rv_dev.empty:
    st.info("У застройщика нет введённых объектов в реестре")
else:
    by_year = rv_dev.groupby("Год ввода по Мосстату")[CAT_COLS].sum().reset_index()
    by_year = by_year.sort_values("Год ввода по Мосстату")
    by_year["Год ввода по Мосстату"] = by_year["Год ввода по Мосстату"].astype(int).astype(str)

    fig = go.Figure()
    for cat_name, col in zip(CAT_NAMES, CAT_COLS):
        values_thousand = by_year[col] / 1000.0
        fig.add_trace(go.Bar(
            x=by_year["Год ввода по Мосстату"],
            y=values_thousand,
            name=cat_name,
            marker_color=CAT_COLORS[cat_name],
            text=[ru_num(v, 0) if v > 0 else "" for v in values_thousand],
            textposition="inside",
            hovertemplate="<b>" + cat_name + "</b><br>%{x}: %{y:,.0f} тыс. м²<extra></extra>",
        ))
    fig.update_layout(
        barmode="stack",
        height=380,
        margin=dict(l=0, r=0, t=10, b=0),
        xaxis_title="Год ввода",
        yaxis_title="тыс. м²",
        legend=dict(orientation="h", y=-0.15),
    )
    st.plotly_chart(fig, use_container_width=True)


# === Дополнительно: топ объектов ===
with st.expander("Список введённых объектов"):
    if rv_dev.empty:
        st.info("Нет данных")
    else:
        show_cols = [c for c in [
            "Коммерческое наименование", "Наименование объекта", "Округ", "Район",
            "Группировка", "Подтип объекта",
            "Общая площадь", "Жилая площадь", "Количество квартир",
            "Год ввода по Мосстату", "Месяц ввода по Мосстату",
        ] if c in rv_dev.columns]
        st.dataframe(
            rv_dev[show_cols].sort_values(
                ["Год ввода по Мосстату", "Общая площадь"], ascending=[False, False]
            ),
            hide_index=True,
            use_container_width=True,
            height=350,
        )

with st.expander("Список объектов в строительстве"):
    if oks_dev.empty:
        st.info("Нет данных")
    else:
        show_cols = [c for c in [
            "Коммерческое название", "Наименование объекта", "Округ", "Район",
            "Группировка", "Подтип объекта",
            "Общая площадь", "Жилая площадь", "Количество квартир",
            "Процент готовности", "Год ввода по графику", "Месяц ввода по графику",
            "Статус объекта",
        ] if c in oks_dev.columns]
        st.dataframe(
            oks_dev[show_cols].sort_values("Общая площадь", ascending=False),
            hide_index=True,
            use_container_width=True,
            height=350,
        )
