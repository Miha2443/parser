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
    load_escrow_manual,
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
    # На сегментах — ЗНАЧЕНИЕ (тыс. м²), в hover — процент.
    seg_texts = [ru_num(v / 1000, 0) for v in values.values()]
    fig = go.Figure(go.Pie(
        labels=list(values.keys()),
        values=list(values.values()),
        marker=dict(colors=CAT_COLORS),
        text=seg_texts,
        textinfo="text",
        textfont=dict(size=11),
        hovertemplate="<b>%{label}</b><br>%{percent}<br>%{value:,.0f} м²<extra></extra>",
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
escrow = load_escrow_manual()


# Даты файлов источников (mtime) для подписей
def _mtime_for(*patterns: str) -> str:
    """Возвращает дату последней модификации первого найденного файла."""
    from datetime import datetime as _dt
    from pathlib import Path as _P
    base = _P("/home/user/parser/data/raw/realty")
    for pat in patterns:
        files = list(base.rglob(pat))
        if files:
            latest = max(files, key=lambda p: p.stat().st_mtime)
            return _dt.fromtimestamp(latest.stat().st_mtime).strftime("%d.%m.%Y")
    return ""


date_monitoring = _mtime_for("monitoring_2_0_*.xlsx")
date_kvart = _mtime_for("kvartirografia_*.xlsx", "kvartirografia_*.json")
date_erzrf_top = _mtime_for("top_obyem_stroitelstva_rf_*.xlsx")
date_erzrf_cards = _mtime_for("cards_*.xlsx")
date_rasprod = _mtime_for("rasprodannost_*.xlsx")
date_escrow = _mtime_for("Наполняемость*.xlsx", "наполняемость*.xlsx", "*эскроу*.xlsx")

# === Собираем все имена застройщиков ===
# Селектор показывает ТОЛЬКО имена из monitoring (главный источник).
# Остальные источники (ERZRF top/cards, rasprodannost, kvartirografia)
# матчатся по нормализованному ключу: "ПИК, г.Москва" → "пик" → данные ПИК.
mon_devs = list(mon.get("developers", []))

if not mon_devs:
    st.warning(
        "Нет данных в monitoring 2.0. "
        "Положи monitoring_2_0_*.xlsx в data/raw/realty/nashdom/."
    )
    st.stop()

# Порядок в селекторе: сначала топ ERZRF по объёму строительства РФ
# (так Самолет/ПИК/ДОГМА идут первыми вместо «ФОНД СВЯТОСЛАВА ФЕДОРОВА»
# который алфавитно был наверху из-за кавычек). Если девелопера нет в топе —
# идёт ниже алфавитно.
def _build_ordered_devs(mon_names: list[str]) -> list[str]:
    mon_by_key = {norm(n): n for n in mon_names}
    ordered: list[str] = []
    used_keys: set[str] = set()
    # 1) По топу ERZRF
    top_df = erzrf_top.get("obyem_stroitelstva", {}).get("rf")
    if top_df is not None and not top_df.empty:
        name_col = next((c for c in top_df.columns if "Наименование" in str(c)), None)
        place_col = next((c for c in top_df.columns
                          if str(c).strip().lower() in ("место", "место ")), None)
        if name_col:
            df = top_df.sort_values(place_col) if place_col else top_df
            for raw in df[name_col].dropna():
                k = norm(str(raw))
                if k in used_keys:
                    continue
                if k in mon_by_key:
                    ordered.append(mon_by_key[k])
                    used_keys.add(k)
    # 2) Остальные monitoring (которых нет в топе) — алфавитно
    rest = [n for k, n in mon_by_key.items() if k not in used_keys]
    ordered.extend(sorted(rest))
    return ordered


ordered_devs = _build_ordered_devs(mon_devs)

st.title("Профиль застройщика")
# Подпись с датами всех источников
src_dates = []
if date_monitoring: src_dates.append(f"Мониторинг 2.0 — **{date_monitoring}**")
if date_erzrf_top: src_dates.append(f"ERZRF топ — **{date_erzrf_top}**")
if date_erzrf_cards: src_dates.append(f"ERZRF карточки — **{date_erzrf_cards}**")
if date_kvart: src_dates.append(f"Квартирография — **{date_kvart}**")
if date_rasprod: src_dates.append(f"Распроданность — **{date_rasprod}**")
if date_escrow: src_dates.append(f"Эскроу — **{date_escrow}**")
if src_dates:
    st.caption("Даты выгрузки источников: " + " · ".join(src_dates))

cols_top = st.columns([3, 2])
with cols_top[0]:
    sel_canon = st.selectbox(
        "Группа компаний",
        ordered_devs,
        key="dev_select_full",
        help=f"{len(ordered_devs)} групп компаний. Порядок: топ ERZRF по объёму "
             f"строительства РФ → дальше остальные алфавитно. "
             f"Данные подтягиваются из 5 источников по нормализованному имени.",
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


# === Данные ===
rv_dev = find_dev_rows(mon.get("rv", pd.DataFrame()), "Группа компаний", sel_key)
oks_dev = find_dev_rows(mon.get("oks", pd.DataFrame()), "Группа компаний", sel_key)
# ВАЖНО: Реестр ОКС содержит ВСЕ объекты с разрешением на строительство,
# включая уже ВВЕДЁННЫЕ (status=«Введенный») и планируемые. Для blocка
# «В строительстве» оставляем только реально строящиеся — это совпадает
# с тем что показывает наш.дом.рф/квартирография (например ПИК Москва:
# 1 910 тыс. м² жилой площади vs сайт 1 914 тыс. м²).
oks_dev_all = oks_dev  # сохраняем для expander/списка
if not oks_dev.empty and "Статус объекта" in oks_dev.columns:
    oks_dev = oks_dev[oks_dev["Статус объекта"] == "Строящийся"]
cat_cols = [f"{CAT_COL_PREFIX}{k}" for k in CAT_KEYS]

last_year_int = mon.get("max_year")  # 2026
prev_year_int = last_year_int - 1 if last_year_int else None  # 2025
last_rv = rv_dev[rv_dev.get("Год ввода по Мосстату") == last_year_int] if last_year_int and not rv_dev.empty else pd.DataFrame()
prev_rv = rv_dev[rv_dev.get("Год ввода по Мосстату") == prev_year_int] if prev_year_int and not rv_dev.empty else pd.DataFrame()


def erzrf_value(sorting: str, region: str, value_substr: str = "Введено") -> float | None:
    """Достать число из ERZRF top для текущего застройщика."""
    df = erzrf_top.get(sorting, {}).get(region)
    if df is None or df.empty:
        return None
    name_col = next((c for c in df.columns if "Наименование" in str(c)), None)
    if not name_col:
        return None
    rows = find_dev_rows(df, name_col, sel_key)
    if rows.empty:
        return None
    val_col = next((c for c in df.columns if value_substr in str(c) and "м²" in str(c)), None)
    if not val_col:
        return None
    return float(pd.to_numeric(rows[val_col].iloc[0], errors="coerce") or 0)


# Достаём cards-строку для выбранного девелопера (один раз, переиспользуем)
def get_cards_row() -> pd.Series | None:
    if erzrf_cards.empty:
        return None
    rows = find_dev_rows(erzrf_cards, "name_card", sel_key)
    if rows.empty:
        rows = find_dev_rows(erzrf_cards, "name_table", sel_key)
    return rows.iloc[0] if not rows.empty else None


cards_row = get_cards_row()


def other_regions_for_year(year: int) -> str:
    """% «в других регионах за год» — та же формула что для «с 2016»:
    (cards.Сдано_РФ − Monitoring.МСК) / cards.Сдано_РФ.

    Для ТЕКУЩЕГО года cards.Сдано показывает только то что УЖЕ сдано
    (за прошедшие месяцы), а monitoring РВ — полный годовой план МСК.
    Если cards < monitoring → берём пропорцию строящегося из
    top_obyem_stroitelstva (РФ-МСК)/РФ — это лучшая аппроксимация
    структуры предстоящего ввода.
    """
    if cards_row is None:
        return "—"
    rf_val = float(cards_row.get(f"Сдано_{year}_м²_num") or 0)
    msk_rv = rv_dev[rv_dev.get("Год ввода по Мосстату") == year] if not rv_dev.empty else pd.DataFrame()
    msk_val = float(msk_rv["Общая площадь"].sum()) if not msk_rv.empty else 0.0

    if rf_val > 0 and rf_val >= msk_val:
        other = rf_val - msk_val
        pct = other / rf_val * 100
        return f"{pct:.0f}% ({ru_num(other/1000)} тыс. м²)"

    # Fallback для текущего/будущего года: пропорция строящегося из ERZRF.
    stroy_rf = erzrf_value("obyem_stroitelstva", "rf", "Строится")
    stroy_msk = erzrf_value("obyem_stroitelstva", "msk", "Строится")
    if stroy_rf and stroy_rf > 0:
        share = max(stroy_rf - (stroy_msk or 0), 0) / stroy_rf
        # Применяем эту долю к monitoring МСК (как точке отсчёта)
        if msk_val > 0:
            # МСК = (1−share) × total → total = МСК / (1−share)
            if share < 0.99:
                total_est = msk_val / (1 - share)
                other = total_est - msk_val
                return f"≈ {share*100:.0f}% (≈ {ru_num(other/1000)} тыс. м²)"
        return f"≈ {share*100:.0f}% (по доле строящегося)"
    return "—"


def other_regions_total() -> str:
    """% накопленного ввода в других регионах = (РФ − Москва) / РФ.

    Источник РФ:
      1) cards.Сдано_YYYY_м² (sum 2016..max_year) — точно по жилью топ-100
      2) fallback на ERZRF top obyem_vvoda.Введено, м²
    Источник Москвы:
      monitoring 2.0 РВ → Общая площадь всех введённых объектов
      (как у ERZRF: учитывается полная площадь жилых проектов —
      жильё + МОП + 1-е этажи + соцобъекты ЖК + отдельные нежилые).
    """
    rf_val: float | None = None
    if cards_row is not None:
        s = 0.0
        for y in range(2016, (last_year_int or 2026) + 1):
            s += float(cards_row.get(f"Сдано_{y}_м²_num") or 0)
        if s > 0:
            rf_val = s
    if rf_val is None:
        rf_val = erzrf_value("obyem_vvoda", "rf")
    if rf_val is None or rf_val <= 0:
        return "—"
    msk_val = float(rv_dev["Общая площадь"].sum()) if not rv_dev.empty else 0.0
    other = max(rf_val - msk_val, 0)
    pct = other / rf_val * 100
    return f"{pct:.0f}% ({ru_num(other/1000)} тыс. м²)"


# === 3 donut диаграммы ===
st.markdown("### Структура ввода по типу площади (Москва)")
pie_cols = st.columns(3)

with pie_cols[0]:
    render_donut(
        categorize_sum(rv_dev),
        "Ввод с 2016 г.",
        f"В других регионах: {other_regions_total()}",
    )

with pie_cols[1]:
    render_donut(
        categorize_sum(prev_rv) if not prev_rv.empty else {lbl: 0 for lbl in CAT_LABELS},
        f"Ввод за {prev_year_int} г." if prev_year_int else "Ввод за пред. год",
        f"В других регионах: {other_regions_for_year(prev_year_int) if prev_year_int else '—'}",
    )

with pie_cols[2]:
    render_donut(
        categorize_sum(last_rv) if not last_rv.empty else {lbl: 0 for lbl in CAT_LABELS},
        f"Ввод за {last_year_int} г." if last_year_int else "Ввод за последний год",
        f"В других регионах: {other_regions_for_year(last_year_int) if last_year_int else '—'}",
    )


# === Динамика ввода по годам ===
st.markdown(f"### Динамика ввода в Москве — {sel_canon}")
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
    # Сумма над каждым столбом
    totals = by_year[[f"{CAT_COL_PREFIX}{k}" for k in CAT_KEYS]].sum(axis=1) / 1000.0
    fig.add_trace(go.Scatter(
        x=by_year["Год ввода по Мосстату"], y=totals,
        mode="text", text=[ru_num(v) for v in totals],
        textposition="top center",
        textfont=dict(size=12, color="#333"),
        showlegend=False, hoverinfo="skip",
    ))
    fig.update_layout(
        barmode="stack", height=400,
        margin=dict(l=0, r=0, t=20, b=0),
        xaxis_title="Год ввода", yaxis_title="тыс. м²",
        yaxis=dict(range=[0, totals.max() * 1.15]),  # запас сверху для надписи
        legend=dict(orientation="h", y=-0.15),
    )
    st.plotly_chart(fig, use_container_width=True, key="dynamics_bar")


# === В строительстве (donut слева) + Распроданность/готовность (справа) ===
st.markdown("### В строительстве и распроданность")
left, right = st.columns([2, 1])

with left:
    # Подпись: количество квартир + жилая площадь (=что показывает наш.дом.рф)
    n_objects = len(oks_dev)
    n_apartments = int(pd.to_numeric(
        oks_dev.get("Количество квартир", pd.Series(dtype=float)),
        errors="coerce").fillna(0).sum())
    zhilaya = float(oks_dev["category_жилое"].sum()) if not oks_dev.empty else 0
    subtitle = ""
    if n_objects:
        parts = []
        if n_apartments:
            parts.append(f"{ru_num(n_apartments)} квартир")
        if zhilaya > 0:
            parts.append(f"жилая {ru_num(zhilaya/1000)} тыс. м²")
        parts.append(f"{n_objects} объектов")
        subtitle = " · ".join(parts)
    render_donut(
        categorize_sum(oks_dev),
        "В строительстве (Москва)",
        subtitle,
    )
    st.caption(
        "Только объекты в статусе «Строящийся». Число в центре — "
        "**общая площадь** (жильё + МОП + паркинги + соцобъекты). "
        "На наш.дом.рф/квартирография показывают только зелёный сегмент "
        "«Жилое». Источник: Мониторинг 2.0 / Реестр ОКС."
    )

with right:
    st.markdown("#### Распроданность / стройготовность")
    rasprod_dev_df = rasprod.get("developers")
    if rasprod_dev_df is not None and not rasprod_dev_df.empty and "наименование" in rasprod_dev_df.columns:
        rows = find_dev_rows(rasprod_dev_df, "наименование", sel_key)
        latest_period = rasprod.get("latest_period")
        if latest_period and not rows.empty:
            ly, lm = latest_period
            rows = rows[(rows["year"] == ly) & (rows["month"] == lm)]
        if not rows.empty:
            r = rows.iloc[0]
            # Берём только _num колонки чтобы не дублировать.
            # ВНИМАНИЕ: в rasprodannost у колонки «Отношение» ДВА пробела
            # подряд: «Отношение распроданности  к стройготовности».
            # Поэтому матчим по началу «Отношение распроданности».
            metric_map = [
                (lambda c: "Распроданность" in c and "Отношение" not in c,
                 "Распроданность"),
                (lambda c: "Стройготовность" in c and "Отношение" not in c,
                 "Стройготовность"),
                (lambda c: c.startswith("Отношение распроданности"),
                 "Отношение распроданности к стройготовности"),
            ]
            shown = 0
            for match_fn, label in metric_map:
                num_col = next((c for c in r.index
                                if c.endswith("_num") and match_fn(c)), None)
                if not num_col:
                    continue
                val = r.get(num_col)
                if val is None or pd.isna(val):
                    continue
                st.metric(label, f"{float(val):.0f}%")
                shown += 1
            if shown == 0:
                st.info("Нет метрик за последний период")
        else:
            st.info("Застройщик не найден в распроданности")
    else:
        st.info("Распроданность не загружена (положи rasprodannost_*.xlsx в data/raw/realty/nashdom/)")


# === Квартирография: таблица типов ===
st.markdown(f"### Структура портфеля по типам квартир")

# Сначала пробуем точные данные из apartments_per_dev (новый парсер
# проходится по каждому девелоперу и берёт exact count). Если их нет —
# fallback на расчёт через % и общий total.
per_dev = kvart.get("apartments_per_dev", pd.DataFrame())
exact_rows = pd.DataFrame()
if isinstance(per_dev, pd.DataFrame) and not per_dev.empty:
    exact_rows = find_dev_rows(per_dev, "наименование", sel_key)
    if exact_rows.empty:
        exact_rows = find_dev_rows(per_dev, "monitoring_name", sel_key)

def _render_kvart_section(total_shtuk: float, area_th: float,
                          rooms_data: dict[str, tuple[float, float, float | None]],
                          region_label: str, market_total_area: float,
                          source_note: str):
    """Унифицированный рендер: KPI-строка + таблица комнат (без 'Все').

    rooms_data: {label: (count_shtuk, pct, area_th_optional)}
    market_total_area: суммарная площадь всех девелоперов в регионе (для доли рынка)
    """
    # KPI верхняя строка
    kpi = st.columns(4)
    kpi[0].metric("Квартиры", f"{ru_num(total_shtuk)} шт")
    kpi[1].metric("Площадь", f"{ru_num(area_th)} тыс. м²")
    if total_shtuk > 0 and area_th > 0:
        avg = area_th * 1000 / total_shtuk
        kpi[2].metric("Ср. площадь квартиры", f"{ru_num(avg, 1)} м²")
    else:
        kpi[2].metric("Ср. площадь квартиры", "—")
    if market_total_area > 0:
        share = area_th / market_total_area * 100
        kpi[3].metric("Доля рынка региона", f"{share:.2f}%")
    else:
        kpi[3].metric("Доля рынка региона", "—")

    # Таблица комнат
    rows = []
    for label, (cnt, pct, ar) in rooms_data.items():
        rows.append({
            "Тип": label,
            "Количество, шт": cnt if cnt else None,
            "Доля, %": pct if pct else None,
            "Площадь, тыс. м²": ar if ar else None,
        })
    df_table = pd.DataFrame(rows)
    st.dataframe(
        df_table, hide_index=True, use_container_width=True,
        column_config={
            "Доля, %": st.column_config.ProgressColumn(
                format="%.1f%%", min_value=0, max_value=100),
            "Количество, шт": st.column_config.NumberColumn(format="%d"),
            "Площадь, тыс. м²": st.column_config.NumberColumn(format="%.1f"),
        },
    )
    date_suffix = f" · дата: **{date_kvart}**" if date_kvart else ""
    st.caption(f"Регион: **{region_label}**. {source_note}{date_suffix}")


if not exact_rows.empty:
    # === Точные числа из apartments_per_dev ===
    # Если есть оба региона — переключатель
    available_regions = list(exact_rows["region_key"].unique()) if "region_key" in exact_rows.columns else []
    region_choice = "msk"
    if len(available_regions) > 1:
        region_choice = st.radio(
            "Регион", ["msk", "rf"], horizontal=True,
            format_func=lambda x: "г.Москва" if x == "msk" else "Российская Федерация",
            key="kvart_region",
        )
    elif available_regions:
        region_choice = available_regions[0]
    region_label = "г.Москва" if region_choice == "msk" else "Российская Федерация"

    if region_choice in available_regions:
        r = exact_rows[exact_rows["region_key"] == region_choice].iloc[0]
    else:
        r = exact_rows.iloc[0]
    total = float(r.get("Все_количество_шт_num") or 0)
    total_area = float(r.get("Все_площадь_тыс_м²_num") or 0)
    rooms_data = {}
    for room, label in [("1комн", "1 комн"), ("2комн", "2 комн"),
                        ("3комн", "3 комн"), ("4+комн", "4+ комн")]:
        cnt = float(r.get(f"{room}_количество_шт_num") or 0)
        ar = float(r.get(f"{room}_площадь_тыс_м²_num") or 0)
        pct = (cnt / total * 100) if total > 0 else 0
        rooms_data[label] = (cnt, pct, ar if ar else None)

    # Доля рынка региона
    market_total = 0.0
    per_dev_reg = per_dev[per_dev["region_key"] == region_choice] if "region_key" in per_dev.columns else per_dev
    if "Все_площадь_тыс_м²_num" in per_dev_reg.columns:
        market_total = float(per_dev_reg["Все_площадь_тыс_м²_num"].fillna(0).sum())

    _render_kvart_section(
        total, total_area, rooms_data, region_label, market_total,
        "Точные числа из шапки наш.дом.рф/квартирография"
    )
else:
    # === Fallback: считаем количество через total × % ===
    kvart_devs = kvart.get("developers")
    if kvart_devs is not None and not kvart_devs.empty:
        rows = find_dev_rows(kvart_devs, "наименование", sel_key)
        if not rows.empty:
            available_regions = list(rows["region_key"].unique()) if "region_key" in rows.columns else []
            region_choice = "msk"
            if len(available_regions) > 1:
                region_choice = st.radio(
                    "Регион", ["msk", "rf"], horizontal=True,
                    format_func=lambda x: "г.Москва" if x == "msk" else "Российская Федерация",
                    key="kvart_region_fb",
                )
            elif available_regions:
                region_choice = available_regions[0]
            region_label = "г.Москва" if region_choice == "msk" else "Российская Федерация"

            if region_choice in available_regions:
                r = rows[rows["region_key"] == region_choice].iloc[0]
            else:
                r = rows.iloc[0]
            total_th = float(r.get("квартиры_тыс_шт_num") or 0)
            total = total_th * 1000.0
            area_th = float(r.get("площадь_тыс_м²_num") or 0)
            rooms_data = {}
            for k, label in [("1комн", "1 комн"), ("2комн", "2 комн"),
                             ("3комн", "3 комн"), ("4+комн", "4+ комн")]:
                pct = r.get(f"доля_{k}_%_num")
                if pct is not None and not pd.isna(pct):
                    rooms_data[label] = (total * float(pct) / 100.0, float(pct), None)
                else:
                    rooms_data[label] = (0, 0, None)
            market_total = 0.0
            if "region_key" in kvart_devs.columns:
                reg_df = kvart_devs[kvart_devs["region_key"] == region_choice]
            else:
                reg_df = kvart_devs
            if "площадь_тыс_м²_num" in reg_df.columns:
                market_total = float(reg_df["площадь_тыс_м²_num"].fillna(0).sum())
            _render_kvart_section(
                total, area_th, rooms_data, region_label, market_total,
                "Количество в комнатах = всего × долю % (точные числа: "
                "запусти `KVART_PER_DEV=1 py nashdom_checker.py kvartirografia`)"
            )
        else:
            st.info(f"«{sel_canon}» не найден в данных квартирографии")
    else:
        st.info("Квартирография не загружена")


# === Переносы сроков ввода (2 KPI карточки) ===
st.markdown("### Переносы сроков ввода")


def fmt_thousand_m2(v: float | None) -> str:
    if v is None or v <= 0:
        return "—"
    return f"{ru_num(v/1000, 1)} тыс. м²"


def render_delay_card(title: str, value: float | None, pct_total: float | None,
                      other_value: float | None, other_pct: float | None,
                      pct_msk: float | None = None,
                      base_label: str = "от общего объёма",
                      msk_label: str = "от стройки в Москве"):
    """Карточка: большое число (перенос) + 3 подписи (% от РФ, % от МСК, в др.рег)."""
    val_str = fmt_thousand_m2(value)
    pct_str = f"{pct_total:.1f}%" if pct_total is not None else "—"
    pct_msk_str = f"{pct_msk:.1f}%" if pct_msk is not None else "—"
    other_val_str = fmt_thousand_m2(other_value) if other_value else "—"
    other_pct_str = f"{other_pct:.0f}%" if other_pct is not None else "—"
    st.markdown(
        f"""
        <div style='padding:18px;border:1px solid #e5e5e5;border-radius:8px;
                    background:#fafafa;height:100%;'>
          <div style='color:#888;font-size:11px;text-transform:uppercase;
                      letter-spacing:0.5px;margin-bottom:6px;'>{title}</div>
          <div style='display:flex;align-items:baseline;gap:14px;margin-bottom:8px;'>
            <div style='font-size:34px;font-weight:700;color:{DELAY_COLOR};line-height:1;'>{val_str}</div>
          </div>
          <div style='color:#555;font-size:13px;margin-bottom:6px;'>
            <span style='font-weight:600;color:{DELAY_COLOR};'>{pct_str}</span> {base_label}
          </div>
          <div style='color:#555;font-size:13px;margin-bottom:10px;'>
            <span style='font-weight:600;color:{DELAY_COLOR};'>{pct_msk_str}</span> {msk_label}
          </div>
          <div style='border-top:1px dashed #ccc;padding-top:10px;color:#555;font-size:13px;'>
            В других регионах:
            <span style='font-weight:600;color:#333;'>{other_val_str}</span> /
            <span style='font-weight:600;color:#333;'>{other_pct_str}</span>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# Карточка 1: переносы в текущем строительстве (top_obyem_stroitelstva)
stroitelstvo_rf = erzrf_value("obyem_stroitelstva", "rf", "Строится")
stroitelstvo_msk = erzrf_value("obyem_stroitelstva", "msk", "Строится")
perenos_stroitelstvo_rf = erzrf_value("obyem_stroitelstva", "rf", "С переносом срока")
perenos_stroitelstvo_msk = erzrf_value("obyem_stroitelstva", "msk", "С переносом срока")

pct1 = (perenos_stroitelstvo_rf / stroitelstvo_rf * 100) \
    if perenos_stroitelstvo_rf and stroitelstvo_rf else None
# % от стройки в МОСКВЕ (новая подпись по просьбе пользователя)
pct1_msk = (perenos_stroitelstvo_msk / stroitelstvo_msk * 100) \
    if perenos_stroitelstvo_msk and stroitelstvo_msk else None
other_val1 = max((perenos_stroitelstvo_rf or 0) - (perenos_stroitelstvo_msk or 0), 0) \
    if perenos_stroitelstvo_rf is not None else None
other_pct1 = (other_val1 / perenos_stroitelstvo_rf * 100) \
    if other_val1 is not None and perenos_stroitelstvo_rf else None

# Карточка 2: переносы в объектах ввода за 2022-2025 (cards)
sdano_2225_rf = 0.0
perenos_2225_rf = 0.0
if cards_row is not None:
    for y in range(2022, 2026):
        sdano_2225_rf += float(cards_row.get(f"Сдано_{y}_м²_num") or 0)
        perenos_2225_rf += float(cards_row.get(f"Перенос_{y}_м²_num") or 0)

pct2 = (perenos_2225_rf / sdano_2225_rf * 100) if sdano_2225_rf > 0 else None

# Москва: введённое 2022-2025 (Общая площадь, как у ERZRF)
msk_2225_total = 0.0
if not rv_dev.empty:
    msk_2225 = rv_dev[rv_dev.get("Год ввода по Мосстату").isin([2022, 2023, 2024, 2025])]
    msk_2225_total = float(msk_2225["Общая площадь"].sum())

# % от стройки в Москве за период 2022-2025: пропорциональная оценка
# (cards не делит на регионы; берём долю Москвы во вводе и применяем к переносу)
pct2_msk = None
if msk_2225_total > 0 and sdano_2225_rf > 0:
    msk_share = min(msk_2225_total / sdano_2225_rf, 1.0)
    perenos_msk_est = perenos_2225_rf * msk_share
    pct2_msk = (perenos_msk_est / msk_2225_total * 100) if msk_2225_total > 0 else None

if sdano_2225_rf > 0 and perenos_2225_rf > 0:
    msk_share = min(msk_2225_total / sdano_2225_rf, 1.0)
    other_share2 = 1.0 - msk_share
    other_val2 = perenos_2225_rf * other_share2
    other_pct2 = other_share2 * 100
else:
    other_val2 = None
    other_pct2 = None

dc1, dc2 = st.columns(2)
with dc1:
    render_delay_card(
        "Переносы сроков ввода (текущее строительство)",
        perenos_stroitelstvo_rf, pct1, other_val1, other_pct1,
        pct_msk=pct1_msk,
        base_label=f"от {ru_num((stroitelstvo_rf or 0)/1000)} тыс. м² в стройке по РФ",
        msk_label=f"от {ru_num((stroitelstvo_msk or 0)/1000)} тыс. м² в стройке в Москве",
    )
with dc2:
    render_delay_card(
        "Переносы сроков в объектах ввода за 2022–2025 гг.",
        perenos_2225_rf if perenos_2225_rf > 0 else None, pct2, other_val2, other_pct2,
        pct_msk=pct2_msk,
        base_label=f"от {ru_num(sdano_2225_rf/1000)} тыс. м² введённых в РФ за период",
        msk_label=f"от {ru_num(msk_2225_total/1000)} тыс. м² введённых в Москве",
    )
st.caption(
    "Источник: ERZRF. **Текущее строительство** — площадь объектов с задержкой ввода "
    "относительно того, что застройщик строит сейчас по всей РФ. "
    "**Объекты ввода 2022-2025** — сумма «Перенос» по годам из карточки ERZRF, "
    "доля от введённого жилья за тот же период."
)


# === Кредитные лимиты и наполнение Эскроу ===
st.markdown("### Кредитные лимиты и наполнение Эскроу")
if escrow.empty:
    st.info("Положи «Наполняемость счетов.xlsx» в data/raw/realty/escrow_manual/")
else:
    # Сопоставляем по «ГК застройщика»
    gk_col = next((c for c in escrow.columns if "ГК застройщика" in str(c)), None)
    if not gk_col:
        st.info("В файле эскроу нет колонки «ГК застройщика»")
    else:
        rows = escrow[escrow[gk_col].apply(lambda x: norm(str(x)) == sel_key)]
        if rows.empty:
            st.info(f"«{sel_canon}» не найден в эскроу-реестре (он содержит только Москву)")
        else:
            def find_col(*subs: str) -> str | None:
                for c in escrow.columns:
                    cs = str(c)
                    if all(s in cs for s in subs):
                        return c
                return None

            credit_col = find_col("Сумма кредита")
            debt_col = find_col("Сумма задолженности")
            revenue_col = find_col("Выручка от реализации всех площадей") \
                or find_col("Выручка от реализации")
            # уточнение — может быть несколько «Выручка от реализации …»
            # нужно «всех площадей» (не «жилой», не «по эскроу»)
            for c in escrow.columns:
                cs = str(c)
                if "Выручка от реализации всех площадей" in cs and "эскроу" not in cs:
                    revenue_col = c
                    break

            objem = pd.to_numeric(rows[credit_col], errors="coerce").sum() if credit_col else 0
            ostatok = pd.to_numeric(rows[debt_col], errors="coerce").sum() if debt_col else 0
            vyruchka = pd.to_numeric(rows[revenue_col], errors="coerce").sum() if revenue_col else 0
            dolya_ostatka = (ostatok / objem * 100) if objem > 0 else None
            # Покрытие = выручка / остаток задолженности (а не /объём займов).
            # На образце ДОМ.РФ: 388.2/270.4 = 144% — именно это и показывают.
            pokrytie = (vyruchka / ostatok * 100) if ostatok > 0 else None

            def fmt_mlrd(v):
                if v is None or v == 0:
                    return "—"
                return f"{v/1e9:.1f}".replace(".", ",") + " млрд ₽"

            ec = st.columns(5)
            ec[0].metric("Объём займов", fmt_mlrd(objem))
            ec[1].metric("Остаток выплат", fmt_mlrd(ostatok))
            ec[2].metric("% Доля остатка", f"{dolya_ostatka:.0f}%" if dolya_ostatka else "—")
            ec[3].metric("Выручка от продаж", fmt_mlrd(vyruchka))
            ec[4].metric("Покрытие займов выручкой",
                         f"{pokrytie:.0f}%" if pokrytie else "—")
            date_suffix = f" · дата выгрузки: **{date_escrow}**" if date_escrow else ""
            st.caption(
                f"Источник: ДОМ.РФ ЕИСЖС, {len(rows)} объектов в Москве{date_suffix}")


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

with st.expander(f"📋 Все объекты с разрешением на строительство — Реестр ОКС "
                 f"({len(oks_dev_all)} всего, {len(oks_dev)} строящихся)"):
    if oks_dev_all.empty:
        st.info("Нет данных")
    else:
        cols_show = [c for c in [
            "Коммерческое название", "Наименование объекта", "Округ", "Район",
            "Назначение", "Подтип объекта",
            "Общая площадь", "Жилая площадь", "Количество квартир",
            "Процент готовности", "Год ввода по графику", "Статус объекта",
        ] if c in oks_dev_all.columns]
        if "Статус объекта" in oks_dev_all.columns:
            st.caption("В блоке donut «В строительстве» показаны только Строящиеся. "
                       "Здесь — все, включая Введённые и Планируемые.")
        st.dataframe(
            oks_dev_all[cols_show].sort_values(
                ["Статус объекта", "Общая площадь"], ascending=[True, False]),
            hide_index=True, use_container_width=True, height=300,
        )
