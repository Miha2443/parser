"""Профиль застройщика — полный дашборд из 5 источников.

Дизайн соответствует эскизу пользователя:
- Шапка: рейтинг ЕРЗ + KPI (строится / введено всего)
- 3 donut-диаграммы: ввод с 2016 / ввод за послед. год / в строительстве —
  каждая разбита на 4 категории площади (Жилое / МОП / Нежилое в жилом /
  Нежилое отдельное)
- Stacked bar chart: динамика ввода по годам (4 категории)
- Правый блок: распроданность / стройготовность / отношение
  (latest period из rasprodannost)
- Таблица: типы квартир (1/2/3/4+/Все) из kvartirografia
- Stacked bar: сроки сдачи (Сдано / Перенос / Уточнение) по годам из ERZRF cards
"""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from app.data_access import (
    load_monitoring_2_0,
    load_erzrf_top,
    load_erzrf_cards,
    load_rasprodannost,
    load_kvartirografia,
    _normalize_developer_name as norm,
)

st.set_page_config(page_title="По застройщикам — Аналитика Москвы", layout="wide")

# Цвета 4 категорий
CAT_KEYS = ["жилое", "моп", "нежилое_в_жилом", "нежилое_отдельное"]
CAT_LABELS = ["Жилое", "МОП", "Нежилое в жилом", "Нежилое отдельное"]
CAT_COLORS = ["#8BC540", "#A8DC74", "#4EC3E0", "#7A8386"]
CAT_COL_PREFIX = "category_"

ERZRF_COLOR = "#1f4e79"
DELAY_COLOR = "#c8102e"


def ru_num(value, digits=0):
    if value is None or pd.isna(value):
        return "—"
    try:
        s = f"{float(value):,.{digits}f}"
    except (TypeError, ValueError):
        return str(value)
    return s.replace(",", " ").replace(".", ",")


def categorize_sum(df: pd.DataFrame) -> dict:
    """Возвращает {label: sum_m²} для 4 категорий."""
    if df.empty:
        return {lbl: 0.0 for lbl in CAT_LABELS}
    return {
        lbl: float(df[f"{CAT_COL_PREFIX}{key}"].sum())
        for lbl, key in zip(CAT_LABELS, CAT_KEYS)
    }


def render_donut(values: dict, title: str = "", subtitle: str = ""):
    """Donut с 4 цветными сегментами + значение в центре."""
    total = sum(values.values())
    if total <= 0:
        st.info(f"Нет данных: {title}")
        return
    fig = go.Figure(go.Pie(
        labels=list(values.keys()),
        values=list(values.values()),
        marker=dict(colors=CAT_COLORS),
        textinfo="percent",
        textfont=dict(size=10),
        hovertemplate="<b>%{label}</b><br>%{value:,.0f} м² (%{percent})<extra></extra>",
        sort=False,
        hole=0.55,
    ))
    fig.update_layout(
        title=dict(text=title, x=0.5, font=dict(size=13)),
        height=290,
        margin=dict(l=10, r=10, t=40, b=10),
        annotations=[
            dict(text=f"<b>{ru_num(total/1000, 0)}</b><br>тыс. м²",
                 x=0.5, y=0.5, showarrow=False, font=dict(size=12)),
        ],
        showlegend=False,
    )
    st.plotly_chart(fig, use_container_width=True, key=f"donut_{title}")
    if subtitle:
        st.caption(subtitle)


# === Загружаем все источники ===
mon = load_monitoring_2_0()
erzrf_top = load_erzrf_top()
erzrf_cards = load_erzrf_cards()
rasprod = load_rasprodannost()
kvart = load_kvartirografia()

# === Собираем все имена застройщиков ===
all_devs: dict[str, str] = {}  # norm_key → canonical name (предпочитаем monitoring)
for name in mon.get("developers", []):
    all_devs[norm(name)] = name
for name in erzrf_top.get("all_developers", []):
    k = norm(name)
    if k and k not in all_devs:
        all_devs[k] = name

if not all_devs:
    st.warning(
        "Нет данных ни в одном источнике (monitoring 2.0 / erzrf). "
        "Положи xlsx-файлы в data/raw/realty/nashdom/ и data/raw/realty/erzrf/."
    )
    st.stop()

st.title("Профиль застройщика")

cols_top = st.columns([3, 2])
with cols_top[0]:
    sel_canon = st.selectbox(
        "Группа компаний",
        sorted(all_devs.values()),
        key="dev_select_full",
        help=f"Всего {len(all_devs)} уникальных застройщиков (объединение источников)",
    )
sel_key = norm(sel_canon)

# Хелпер: найти строки в DataFrame по нормализованному имени застройщика
def find_dev_rows(df: pd.DataFrame, name_col: str, key: str) -> pd.DataFrame:
    if df is None or df.empty or name_col not in df.columns:
        return pd.DataFrame()
    mask = df[name_col].dropna().apply(lambda x: norm(str(x)) == key)
    return df[df[name_col].notna() & mask]


# === Рейтинг (из ERZRF top по объёму строительства) ===
def get_rating(region: str) -> dict:
    df = erzrf_top.get("obyem_stroitelstva", {}).get(region)
    if df is None or df.empty:
        return {}
    name_col = next((c for c in df.columns if "Наименование" in str(c)), None)
    if not name_col:
        return {}
    rows = find_dev_rows(df, name_col, sel_key)
    if rows.empty:
        return {}
    r = rows.iloc[0]
    out = {}
    for key in ["Место", "Место ", "+/-", "Строится, м²", "Введено, м²",
                "Рейтинг ЕРЗ", "ЖК", "Регионов"]:
        if key in r.index:
            out[key.strip()] = r[key]
    return out


with cols_top[1]:
    rating_msk = get_rating("msk")
    rating_rf = get_rating("rf")
    if rating_rf or rating_msk:
        info = []
        if rating_rf.get("Место"):
            info.append(f"место по РФ: <b>{ru_num(rating_rf['Место'])}</b>")
        if rating_msk.get("Место"):
            info.append(f"по Москве: <b>{ru_num(rating_msk['Место'])}</b>")
        if rating_rf.get("Рейтинг ЕРЗ"):
            info.append(f"Рейтинг ЕРЗ: <b>{rating_rf['Рейтинг ЕРЗ']}</b>")
        st.markdown(
            "<div style='padding-top:30px;color:#444;'>" + " · ".join(info) + "</div>",
            unsafe_allow_html=True,
        )


# === Топ KPI карточки ===
rv_dev = find_dev_rows(mon.get("rv", pd.DataFrame()), "Группа компаний", sel_key)
oks_dev = find_dev_rows(mon.get("oks", pd.DataFrame()), "Группа компаний", sel_key)
cat_cols = [f"{CAT_COL_PREFIX}{k}" for k in CAT_KEYS]
total_rv = float(rv_dev[cat_cols].sum().sum()) if not rv_dev.empty else 0
total_oks = float(oks_dev[cat_cols].sum().sum()) if not oks_dev.empty else 0

last_year_int = mon.get("max_year")
last_year_rv = rv_dev[rv_dev.get("Год ввода по Мосстату") == last_year_int] if last_year_int and not rv_dev.empty else pd.DataFrame()

# Ввод с 2016 — из ERZRF top по сортировке "накопленный ввод с 2016"
def erzrf_nakopl(region: str) -> float | None:
    df = erzrf_top.get("nakopl_vvod", {}).get(region)
    if df is None or df.empty:
        return None
    name_col = next((c for c in df.columns if "Наименование" in str(c)), None)
    if not name_col:
        return None
    rows = find_dev_rows(df, name_col, sel_key)
    if rows.empty:
        return None
    # Колонка "Введено, м²"
    val_col = next((c for c in df.columns if "Введено" in str(c) and "м²" in str(c)), None)
    if not val_col:
        return None
    return float(pd.to_numeric(rows[val_col].iloc[0], errors="coerce") or 0)


vvod_rf = erzrf_nakopl("rf")
vvod_msk = erzrf_nakopl("msk")
vvod_other = (vvod_rf - vvod_msk) if (vvod_rf is not None and vvod_msk is not None) else None

cols_kpi = st.columns(4)
cols_kpi[0].metric(
    "Введено с 2016 (РФ)",
    f"{ru_num((vvod_rf or 0)/1000)} тыс. м²" if vvod_rf else "—",
    help="Накопленный ввод по ERZRF",
)
cols_kpi[1].metric(
    "В Москве с 2016",
    f"{ru_num((vvod_msk or 0)/1000)} тыс. м²" if vvod_msk else "—",
)
cols_kpi[2].metric(
    f"В строительстве (Москва)",
    f"{ru_num(total_oks/1000)} тыс. м²",
    help="Monitoring 2.0 / Реестр ОКС",
)
cols_kpi[3].metric(
    "В других регионах с 2016",
    f"{ru_num((vvod_other or 0)/1000)} тыс. м²" if vvod_other is not None else "—",
    help="РФ − Москва (ERZRF)",
)


# === 3 donut диаграммы ===
st.markdown("### Структура ввода и строительства по типу площади (Москва)")
pie_cols = st.columns(3)

with pie_cols[0]:
    # Ввод с 2016 — берём данные из monitoring (РВ) — это Москва
    cats = categorize_sum(rv_dev)
    subtitle = ""
    if vvod_other is not None and vvod_msk is not None and vvod_msk > 0:
        share_msk = vvod_msk / (vvod_msk + vvod_other) * 100
        subtitle = f"В других регионах: {ru_num((vvod_other)/1000)} тыс. м² ({100-share_msk:.0f}%)"
    render_donut(cats, "Ввод с 2016 г.", subtitle)

with pie_cols[1]:
    render_donut(
        categorize_sum(last_year_rv) if not last_year_rv.empty else {lbl: 0 for lbl in CAT_LABELS},
        f"Ввод за {last_year_int} г." if last_year_int else "Ввод за последний год",
    )

with pie_cols[2]:
    render_donut(
        categorize_sum(oks_dev),
        "В строительстве",
    )


# === Динамика ввода по годам + правый блок ===
st.markdown(f"### Динамика ввода в Москве — {sel_canon}")
left, right = st.columns([3, 1])

with left:
    if rv_dev.empty:
        st.info("Нет данных по введённым объектам")
    else:
        by_year = rv_dev.groupby("Год ввода по Мосстату")[cat_cols].sum().reset_index()
        by_year = by_year.sort_values("Год ввода по Мосстату")
        by_year["Год ввода по Мосстату"] = by_year["Год ввода по Мосстату"].astype(int).astype(str)

        fig = go.Figure()
        for lbl, key, color in zip(CAT_LABELS, CAT_KEYS, CAT_COLORS):
            vals = by_year[f"{CAT_COL_PREFIX}{key}"] / 1000.0
            fig.add_trace(go.Bar(
                x=by_year["Год ввода по Мосстату"], y=vals,
                name=lbl, marker_color=color,
                text=[ru_num(v) if v > 0 else "" for v in vals],
                textposition="inside",
                hovertemplate="<b>" + lbl + "</b><br>%{x}: %{y:,.0f} тыс. м²<extra></extra>",
            ))
        fig.update_layout(
            barmode="stack", height=380,
            margin=dict(l=0, r=0, t=10, b=0),
            xaxis_title="Год ввода", yaxis_title="тыс. м²",
            legend=dict(orientation="h", y=-0.15),
        )
        st.plotly_chart(fig, use_container_width=True, key="dynamics_bar")

with right:
    st.markdown("#### Распроданность / готовность")
    # Найти строку в rasprodannost developers за последний период
    rasprod_dev_df = rasprod.get("developers")
    if rasprod_dev_df is not None and not rasprod_dev_df.empty:
        rows = find_dev_rows(rasprod_dev_df, "наименование", sel_key)
        if not rows.empty:
            latest_period = rasprod.get("latest_period")
            if latest_period:
                ly, lm = latest_period
                rows = rows[(rows["year"] == ly) & (rows["month"] == lm)]
            if not rows.empty:
                r = rows.iloc[0]
                # Колонки могут иметь &nbsp;
                cols_metrics = {c: c for c in r.index
                                if any(k in str(c) for k in
                                       ["Распроданность", "Стройготовность", "Отношение"])}
                for raw_col, _ in cols_metrics.items():
                    val = r.get(raw_col)
                    name = "Распроданность" if "Распроданность" in raw_col \
                        else "Стройготовность" if "Стройготовность" in raw_col \
                        else "Отношение Р/С"
                    if val is not None and not pd.isna(val):
                        st.metric(name, str(val))
            else:
                st.info("Нет данных за последний период")
        else:
            st.info("Застройщик не найден в распроданности")
    else:
        st.info("Распроданность не загружена")


# === Квартирография: таблица типов ===
st.markdown(f"### Структура портфеля по типам квартир")
kvart_devs = kvart.get("developers")
if kvart_devs is not None and not kvart_devs.empty:
    rows = find_dev_rows(kvart_devs, "наименование", sel_key)
    if not rows.empty:
        r = rows.iloc[0]
        # Берём шапку из apartments — те же 5 строк
        kvart_table = pd.DataFrame([
            {"Тип квартир": "Все",
             "Квартиры, тыс. шт": r.get("квартиры_тыс_шт_num"),
             "Площадь, тыс. м²": r.get("площадь_тыс_м²_num")},
            {"Тип квартир": "1 комн", "Квартиры, тыс. шт": None, "Площадь, тыс. м²": None,
             "Доля, %": r.get("доля_1комн_%_num")},
            {"Тип квартир": "2 комн", "Квартиры, тыс. шт": None, "Площадь, тыс. м²": None,
             "Доля, %": r.get("доля_2комн_%_num")},
            {"Тип квартир": "3 комн", "Квартиры, тыс. шт": None, "Площадь, тыс. м²": None,
             "Доля, %": r.get("доля_3комн_%_num")},
            {"Тип квартир": "4+ комн", "Квартиры, тыс. шт": None, "Площадь, тыс. м²": None,
             "Доля, %": r.get("доля_4+комн_%_num")},
        ])
        st.dataframe(
            kvart_table, hide_index=True, use_container_width=True,
            column_config={
                "Доля, %": st.column_config.ProgressColumn(
                    format="%d%%", min_value=0, max_value=100),
                "Квартиры, тыс. шт": st.column_config.NumberColumn(format="%.1f"),
                "Площадь, тыс. м²": st.column_config.NumberColumn(format="%d"),
            },
        )
    else:
        st.info("Застройщик не найден в данных квартирографии")
else:
    st.info("Квартирография не загружена")


# === Сроки сдачи из ERZRF cards ===
st.markdown("### Сроки сдачи (ERZRF карточка)")
if not erzrf_cards.empty:
    rows = find_dev_rows(erzrf_cards, "name_card", sel_key)
    if rows.empty:
        # Попробовать name_table
        rows = find_dev_rows(erzrf_cards, "name_table", sel_key)
    if not rows.empty:
        r = rows.iloc[0]
        # Колонки Сдано_YYYY_м², Перенос_YYYY_м², Перенос_YYYY_%, Уточн_YYYY_мес
        years = sorted({c.split("_")[1] for c in r.index
                        if c.startswith("Сдано_") and c.endswith("_м²")
                        and c.split("_")[1].isdigit()})
        if years:
            df_sdano = pd.DataFrame({
                "Год": years,
                "Сдано, м²": [pd.to_numeric(r.get(f"Сдано_{y}_м²"), errors="coerce") for y in years],
                "Перенос, м²": [pd.to_numeric(r.get(f"Перенос_{y}_м²"), errors="coerce") for y in years],
                "Перенос, %": [pd.to_numeric(r.get(f"Перенос_{y}_%"), errors="coerce") for y in years],
                "Уточн, мес": [pd.to_numeric(r.get(f"Уточн_{y}_мес"), errors="coerce") for y in years],
            })

            fig = go.Figure()
            sdano = df_sdano["Сдано, м²"].fillna(0)
            perenos = df_sdano["Перенос, м²"].fillna(0)
            sdano_clean = (sdano - perenos).clip(lower=0)
            fig.add_trace(go.Bar(
                x=df_sdano["Год"], y=sdano_clean,
                name="Сдано в срок", marker_color="#8BC540",
                hovertemplate="<b>В срок</b><br>%{x}: %{y:,.0f} м²<extra></extra>",
            ))
            fig.add_trace(go.Bar(
                x=df_sdano["Год"], y=perenos,
                name="С переносом срока", marker_color=DELAY_COLOR,
                hovertemplate="<b>С переносом</b><br>%{x}: %{y:,.0f} м²<extra></extra>",
            ))
            fig.update_layout(
                barmode="stack", height=320,
                margin=dict(l=0, r=0, t=10, b=0),
                xaxis_title="Год", yaxis_title="м²",
                legend=dict(orientation="h", y=-0.15),
            )
            st.plotly_chart(fig, use_container_width=True, key="srok_bar")

            with st.expander("Детали: перенос (%), уточнение (мес)"):
                st.dataframe(df_sdano, hide_index=True, use_container_width=True)
        else:
            st.info("Нет годовых данных в карточке")
    else:
        st.info("Застройщик не найден в карточках ERZRF")
else:
    st.info("ERZRF карточки не загружены")


# === Список объектов ===
with st.expander("📋 Список введённых объектов (Реестр РВ)"):
    if rv_dev.empty:
        st.info("Нет данных")
    else:
        cols_show = [c for c in [
            "Коммерческое наименование", "Наименование объекта", "Округ", "Район",
            "Отрасли", "Группировка", "Подтип объекта",
            "Общая площадь", "Жилая площадь", "Количество квартир",
            "Год ввода по Мосстату", "Месяц ввода по Мосстату",
        ] if c in rv_dev.columns]
        st.dataframe(
            rv_dev[cols_show].sort_values(
                ["Год ввода по Мосстату", "Общая площадь"], ascending=[False, False]
            ),
            hide_index=True, use_container_width=True, height=300,
        )

with st.expander("📋 Список объектов в строительстве (Реестр ОКС)"):
    if oks_dev.empty:
        st.info("Нет данных")
    else:
        cols_show = [c for c in [
            "Коммерческое название", "Наименование объекта", "Округ", "Район",
            "Назначение", "Подтип объекта",
            "Общая площадь", "Жилая площадь", "Количество квартир",
            "Процент готовности", "Год ввода по графику", "Статус объекта",
        ] if c in oks_dev.columns]
        st.dataframe(
            oks_dev[cols_show].sort_values("Общая площадь", ascending=False),
            hide_index=True, use_container_width=True, height=300,
        )
