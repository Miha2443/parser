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

st.set_page_config(page_title="Профиль застройщика — Аналитика Москвы", layout="wide")

# Цвета 4 категорий
CAT_KEYS = ["жилое", "моп", "нежилое_в_жилом", "нежилое_отдельное"]
CAT_LABELS = ["Жилое", "МОП", "Нежилье в жилье", "Нежилое отдельное"]
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


def render_donut(values: dict, title: str = "", subtitle: str = "",
                 colors: list[str] | None = None):
    """Donut с цветными сегментами + значение в центре."""
    total = sum(values.values())
    if total <= 0:
        st.info(f"Нет данных: {title}")
        return
    # На сегментах — ЗНАЧЕНИЕ (тыс. м²), в hover — процент.
    seg_texts = [ru_num(v / 1000, 0) for v in values.values()]
    seg_colors = colors if colors is not None else CAT_COLORS
    fig = go.Figure(go.Pie(
        labels=list(values.keys()),
        values=list(values.values()),
        marker=dict(colors=seg_colors),
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

# Порядок в селекторе: сначала топ ERZRF по объёму ввода в Москве
# (так Самолет/ПИК/ДОГМА идут первыми вместо «ФОНД СВЯТОСЛАВА ФЕДОРОВА»
# который алфавитно был наверху из-за кавычек). Если девелопера нет в топе —
# идёт ниже алфавитно.
def _build_ordered_devs(mon_names: list[str]) -> list[str]:
    """Сортировка для селектора: сначала топ по накопленному вводу с 2016.

    Накопленный ввод считается из cards (sum Сдано_YYYY за все годы).
    Кто не в cards (не топ-100) — добавляется ниже алфавитно.
    """
    mon_by_key = {norm(n): n for n in mon_names}
    ordered: list[str] = []
    used_keys: set[str] = set()

    # 1) По сумме Сдано из cards (накопленный ввод)
    if not erzrf_cards.empty:
        sdano_cols = [c for c in erzrf_cards.columns
                      if str(c).startswith("Сдано_") and str(c).endswith("_м²_num")]
        if sdano_cols:
            tmp = erzrf_cards.copy()
            tmp["_total"] = tmp[sdano_cols].sum(axis=1)
            tmp = tmp.sort_values("_total", ascending=False)
            name_col = "name_card" if "name_card" in tmp.columns else (
                "name_table" if "name_table" in tmp.columns else None)
            if name_col:
                for raw in tmp[name_col].dropna():
                    k = norm(str(raw))
                    if k in used_keys:
                        continue
                    if k in mon_by_key:
                        ordered.append(mon_by_key[k])
                        used_keys.add(k)

    # 2) Остальные monitoring — алфавитно
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
             f"ввода в Москве → дальше остальные алфавитно. "
             f"Данные подтягиваются из 5 источников по нормализованному имени.",
    )
sel_key = norm(sel_canon)

# Хелпер: найти строки в DataFrame по нормализованному имени застройщика
def find_dev_rows(df: pd.DataFrame, name_col: str, key: str) -> pd.DataFrame:
    if df is None or df.empty or name_col not in df.columns:
        return pd.DataFrame()
    mask = df[name_col].dropna().apply(lambda x: norm(str(x)) == key)
    return df[df[name_col].notna() & mask]


# === Рейтинги ERZRF по 2 сортировкам × 2 регионам ===
def get_rating(sorting: str, region: str) -> dict:
    """Возвращает {Место, Рейтинг ЕРЗ, Строится, Введено, ...} из ERZRF top."""
    df = erzrf_top.get(sorting, {}).get(region)
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
    # 4 рейтинга: по строительству и по вводу × по РФ и по Москве
    str_rf = get_rating("obyem_stroitelstva", "rf")
    str_msk = get_rating("obyem_stroitelstva", "msk")
    vv_rf = get_rating("obyem_vvoda", "rf")
    vv_msk = get_rating("obyem_vvoda", "msk")
    erz_rating = (str_rf.get("Рейтинг ЕРЗ") or vv_rf.get("Рейтинг ЕРЗ")
                  or str_msk.get("Рейтинг ЕРЗ") or vv_msk.get("Рейтинг ЕРЗ"))

    def fmt_place(d: dict) -> str:
        p = d.get("Место")
        if p is None or pd.isna(p):
            return "—"
        try:
            return str(int(p))
        except (ValueError, TypeError):
            return str(p)

    rating_html = f"""
    <div style='padding-top:18px;font-size:13px;color:#444;line-height:1.5;'>
      <div style='font-size:11px;text-transform:uppercase;letter-spacing:0.5px;
                  color:#888;margin-bottom:4px;'>Рейтинги ЕРЗ</div>
      <table style='border-collapse:collapse;font-size:13px;'>
        <tr>
          <th style='text-align:left;padding:2px 12px 2px 0;color:#666;font-weight:500;'></th>
          <th style='text-align:center;padding:2px 10px;color:#666;font-weight:500;'>РФ</th>
          <th style='text-align:center;padding:2px 10px;color:#666;font-weight:500;'>Москва</th>
        </tr>
        <tr>
          <td style='padding:2px 12px 2px 0;'>По вводу</td>
          <td style='text-align:center;padding:2px 10px;font-weight:700;color:#1f4e79;'>{fmt_place(vv_rf)}</td>
          <td style='text-align:center;padding:2px 10px;font-weight:700;color:#1f4e79;'>{fmt_place(vv_msk)}</td>
        </tr>
        <tr>
          <td style='padding:2px 12px 2px 0;'>По строительству</td>
          <td style='text-align:center;padding:2px 10px;font-weight:700;color:#1f4e79;'>{fmt_place(str_rf)}</td>
          <td style='text-align:center;padding:2px 10px;font-weight:700;color:#1f4e79;'>{fmt_place(str_msk)}</td>
        </tr>
      </table>
      {f"<div style='margin-top:6px;color:#666;'>Оценка ЕРЗ: <b style='color:#1f4e79;'>{erz_rating}</b></div>" if erz_rating else ""}
    </div>
    """
    st.markdown(rating_html, unsafe_allow_html=True)


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
    """% «в других регионах за год».

    Логика разная для прошлого vs текущего года:

    Текущий год (year == last_year_int): используем top_obyem_vvoda
      (это «введено за послед.12 мес» в ERZRF — самое свежее число
      для текущего года). РФ−МСК даёт точное «в других регионах».
      Пример ПИК 2026: 492 − 421 = 71 тыс. (14%) — совпадает с ERZRF.

    Прошлый год: cards.Сдано_YYYY (РФ-уровень) минус
      monitoring.category_жилое (только Жилая площадь, без МОП —
      методика ERZRF для конкретного года).
      Пример ПИК 2025: 1 678 − 773 = 905 тыс. (54%).
    """
    # === Текущий год — top_obyem_vvoda ===
    if year == last_year_int:
        rf_val = erzrf_value("obyem_vvoda", "rf", "Введено")
        msk_val = erzrf_value("obyem_vvoda", "msk", "Введено")
        if rf_val and rf_val > 0:
            other = max(rf_val - (msk_val or 0), 0)
            pct = other / rf_val * 100
            return f"{pct:.0f}% ({ru_num(other/1000)} тыс. м²)"
        return "—"

    # === Прошлый год — cards.Сдано (РФ) − monitoring category_жилое (МСК) ===
    if cards_row is None:
        return "—"
    rf_val = float(cards_row.get(f"Сдано_{year}_м²_num") or 0)
    if rf_val <= 0:
        return "—"
    msk_rv = rv_dev[rv_dev.get("Год ввода по Мосстату") == year] if not rv_dev.empty else pd.DataFrame()
    msk_val = float(msk_rv["category_жилое"].sum()) if not msk_rv.empty else 0.0
    other = max(rf_val - msk_val, 0)
    pct = other / rf_val * 100
    return f"{pct:.0f}% ({ru_num(other/1000)} тыс. м²)"


def other_regions_total() -> str:
    """% в других регионах = (cards.Сдано_РФ за все годы − category_жилое МСК) / РФ.

    По требованию пользователя — Москва считается ТОЛЬКО как Жилая
    площадь (без МОП и нежилого), РФ — общий ввод из ERZ карточки.
    Единая методика «только жильё» — сопоставимо с ERZ для каждого года.
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
    # Москва: только жилое (методика ERZ)
    msk_val = float(rv_dev["category_жилое"].sum()) if not rv_dev.empty else 0.0
    other = max(rf_val - msk_val, 0)
    pct = other / rf_val * 100
    return f"{pct:.0f}% ({ru_num(other/1000)} тыс. м²)"


# === 3 donut диаграммы ===
st.markdown("### Структура ввода по типу площади (Москва)")

# Легенда цветов для 4 категорий (общая для всех 3 донатов)
legend_html = "<div style='text-align:center;margin-bottom:8px;font-size:13px;'>" + \
    " &nbsp; ".join(
        f"<span style='display:inline-block;width:11px;height:11px;"
        f"background:{c};vertical-align:middle;margin-right:4px;border-radius:2px;'></span>"
        f"<span style='vertical-align:middle;'>{lbl}</span>"
        for lbl, c in zip(CAT_LABELS, CAT_COLORS)
    ) + "</div>"
st.markdown(legend_html, unsafe_allow_html=True)

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

    # Слева — текстовая сводка (как на эскизе), справа — диаграмма
    legend_col, chart_col = st.columns([1, 3])

    with legend_col:
        # Легенда — только за 2022-2026 (как и график справа).
        rv_dev_22 = rv_dev[rv_dev["Год ввода по Мосстату"] >= 2022]
        years_in_legend = sorted(
            rv_dev_22["Год ввода по Мосстату"].dropna().astype(int).unique())
        y_min = str(years_in_legend[0]) if years_in_legend else "—"
        y_max = str(years_in_legend[-1]) if years_in_legend else "—"
        cat_sums = {
            lbl: float(rv_dev_22[f"{CAT_COL_PREFIX}{key}"].sum()) / 1e6  # млн м²
            for lbl, key in zip(CAT_LABELS, CAT_KEYS)
        }
        itogo = sum(cat_sums.values())

        def mln(v: float) -> str:
            return f"{v:.1f}".replace(".", ",")

        lines = "".join(
            f"<div style='margin-bottom:10px;font-size:15px;'>"
            f"<span style='display:inline-block;width:13px;height:13px;"
            f"background:{color};margin-right:8px;border-radius:2px;'></span>"
            f"<b>{lbl}</b> = <span style='color:{color};font-weight:700;'>"
            f"{mln(val)}</span> млн м²</div>"
            for (lbl, val), color in zip(cat_sums.items(), CAT_COLORS)
        )
        st.markdown(
            f"""
            <div style='padding-top:20px;'>
              <div style='font-size:22px;font-weight:700;line-height:1.2;'>
                Ввод недвижимости</div>
              <div style='color:#666;font-size:14px;margin:6px 0 16px 0;'>
                за {y_min}–{y_max} гг.:</div>
              {lines}
              <div style='font-size:18px;font-weight:800;margin-top:14px;'>
                ИТОГО = {mln(itogo)} млн м²</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with chart_col:
        # Сам график — только с 2022 года (более ранние годы видны
        # в легенде слева и в общем итоге за 2017-2026 гг.).
        by_year_chart = by_year[by_year["Год ввода по Мосстату"].astype(int) >= 2022]
        fig = go.Figure()
        for lbl, key, color in zip(CAT_LABELS, CAT_KEYS, CAT_COLORS):
            vals = by_year_chart[f"{CAT_COL_PREFIX}{key}"] / 1000.0
            fig.add_trace(go.Bar(
                x=by_year_chart["Год ввода по Мосстату"], y=vals,
                name=lbl, marker_color=color,
                text=[ru_num(v) if v > 0 else "" for v in vals],
                textposition="inside",
                hovertemplate="<b>" + lbl + "</b><br>%{x}: %{y:,.0f} тыс. м²<extra></extra>",
            ))
        # Сумма над каждым столбом
        totals = by_year_chart[[f"{CAT_COL_PREFIX}{k}" for k in CAT_KEYS]].sum(axis=1) / 1000.0
        fig.add_trace(go.Scatter(
            x=by_year_chart["Год ввода по Мосстату"], y=totals,
            mode="text", text=[ru_num(v) for v in totals],
            textposition="top center",
            textfont=dict(size=12, color="#333"),
            showlegend=False, hoverinfo="skip",
        ))
        y_top = totals.max() * 1.15 if not totals.empty else 1
        fig.update_layout(
            barmode="stack", height=400,
            margin=dict(l=0, r=0, t=20, b=0),
            xaxis_title="Год ввода", yaxis_title="тыс. м²",
            yaxis=dict(range=[0, y_top]),
            legend=dict(orientation="h", y=-0.15),
        )
        st.plotly_chart(fig, use_container_width=True, key="dynamics_bar")


# === В строительстве (donut слева) + Распроданность/стройготовность (справа) ===
st.markdown("### В строительстве")
left, right = st.columns([2, 1])

with left:
    # Для donut «В строительстве» используем 3 категории (а не 4 как
    # в других местах): МОП объединяется с «Нежилье в жилье» в одну.
    raw_cats = categorize_sum(oks_dev)
    cats_3 = {
        "Жилое": raw_cats.get("Жилое", 0),
        "Нежилье в жилье и МОП":
            raw_cats.get("МОП", 0) + raw_cats.get("Нежилье в жилье", 0),
        "Нежилое отдельное": raw_cats.get("Нежилое отдельное", 0),
    }
    # Цвета: зелёный / тёплый жёлто-оранжевый / серый
    colors_3 = ["#8BC540", "#F4A261", "#7A8386"]
    render_donut(
        cats_3,
        "В строительстве (Москва)",
        "",
        colors=colors_3,
    )
    # Легенда цветов для 3 категорий «В строительстве»
    legend_3 = "<div style='text-align:center;font-size:13px;'>" + \
        " &nbsp; ".join(
            f"<span style='display:inline-block;width:11px;height:11px;"
            f"background:{c};vertical-align:middle;margin-right:4px;border-radius:2px;'></span>"
            f"<span style='vertical-align:middle;'>{lbl}</span>"
            for lbl, c in zip(cats_3.keys(), colors_3)
        ) + "</div>"
    st.markdown(legend_3, unsafe_allow_html=True)

with right:
    st.markdown("#### Распроданность / стройготовность")
    rasprod_dev_df = rasprod.get("developers")
    if rasprod_dev_df is not None and not rasprod_dev_df.empty and "наименование" in rasprod_dev_df.columns:
        all_rows = find_dev_rows(rasprod_dev_df, "наименование", sel_key)
        latest_period = rasprod.get("latest_period")
        if latest_period and not all_rows.empty:
            ly, lm = latest_period
            all_rows = all_rows[(all_rows["year"] == ly) & (all_rows["month"] == lm)]
        if not all_rows.empty:
            row_rf = all_rows[all_rows["region_key"] == "rf"]
            row_msk = all_rows[all_rows["region_key"] == "msk"]
            r_rf = row_rf.iloc[0] if not row_rf.empty else None
            r_msk = row_msk.iloc[0] if not row_msk.empty else None

            def find_num(r, predicate) -> str:
                if r is None:
                    return "—"
                num_col = next((c for c in r.index
                                if c.endswith("_num") and predicate(c)), None)
                if not num_col:
                    return "—"
                v = r.get(num_col)
                if v is None or pd.isna(v):
                    return "—"
                return f"{float(v):.0f}%"

            preds = [
                ("Распроданность",
                 lambda c: "Распроданность" in c and "Отношение" not in c),
                ("Стройготовность",
                 lambda c: "Стройготовность" in c and "Отношение" not in c),
                ("Отношение Р/С",
                 lambda c: c.startswith("Отношение распроданности")),
            ]

            # ВАЖНО: HTML без ведущих пробелов в начале строк —
            # иначе st.markdown трактует строки с 4+ пробелами как
            # code block и показывает HTML как текст.
            rows_html = ""
            for label, pred in preds:
                v_msk = find_num(r_msk, pred)
                v_rf = find_num(r_rf, pred)
                rows_html += (
                    "<tr style='border-top:1px solid #eee;'>"
                    f"<td style='padding:8px 0;color:#444;'>{label}</td>"
                    "<td style='text-align:right;padding:8px 8px;"
                    f"font-weight:700;font-size:18px;color:#222;'>{v_msk}</td>"
                    "<td style='text-align:right;padding:8px 0;"
                    f"font-weight:700;font-size:18px;color:#666;'>{v_rf}</td>"
                    "</tr>"
                )
            html = (
                "<table style='width:100%;border-collapse:collapse;font-size:13px;'>"
                "<tr>"
                "<th style='text-align:left;padding:6px 0;color:#888;font-weight:500;'></th>"
                "<th style='text-align:right;padding:6px 8px;color:#888;font-weight:500;'>Москва</th>"
                "<th style='text-align:right;padding:6px 0;color:#888;font-weight:500;'>РФ</th>"
                "</tr>"
                f"{rows_html}"
                "</table>"
            )
            st.markdown(html, unsafe_allow_html=True)
            period_str = f"{lm:02d}.{ly}" if latest_period else ""
            if period_str:
                st.caption(f"На {period_str}")
        else:
            st.info("Застройщик не найден в распроданности")
    else:
        st.info("Распроданность не загружена (положи rasprodannost_*.xlsx в data/raw/realty/nashdom/)")


# === Квартирография: таблица типов ===
st.markdown(f"### Структура портфеля по типам квартир")

# === Квартирография ===
# Используем `developers` лист (агрегаты — стабильные, точные).
# Если есть `apartments_per_dev` (новый парсер с верификацией) —
# используем его только если данные проходят sanity-check:
# total в per-dev должен быть в диапазоне 0.95–1.05 от total в
# developers. Это спасает от ситуации когда per-dev собрал не тот
# блок (раньше брал данные в 4 раза больше правды).
kvart_devs_full = kvart.get("developers", pd.DataFrame())
kvart_rows = (find_dev_rows(kvart_devs_full, "наименование", sel_key)
              if not kvart_devs_full.empty else pd.DataFrame())

per_dev = kvart.get("apartments_per_dev", pd.DataFrame())
per_dev_rows = pd.DataFrame()
if isinstance(per_dev, pd.DataFrame) and not per_dev.empty:
    per_dev_rows = find_dev_rows(per_dev, "наименование", sel_key)
    if per_dev_rows.empty:
        per_dev_rows = find_dev_rows(per_dev, "monitoring_name", sel_key)


def _render_kvart_section(total_shtuk: float, area_th: float,
                          rooms_data: dict[str, tuple[float, float, float | None]],
                          region_label: str, market_total_area: float,
                          source_note: str):
    """Унифицированный рендер: KPI-строка + таблица комнат (без 'Все')."""
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
    st.caption(f"Регион: **{region_label}**{date_suffix}")


if kvart_rows.empty:
    if kvart_devs_full.empty:
        st.info("Квартирография не загружена")
    else:
        st.info(f"«{sel_canon}» не найден в данных квартирографии")
else:
    available_regions = sorted(
        kvart_rows["region_key"].unique() if "region_key" in kvart_rows.columns else [],
        key=lambda x: 0 if x == "msk" else 1,
    )
    if len(available_regions) > 1:
        region_choice = st.radio(
            "Регион", available_regions, horizontal=True,
            format_func=lambda x: "г.Москва" if x == "msk" else "Российская Федерация",
            key="kvart_region",
        )
    else:
        region_choice = available_regions[0] if available_regions else "msk"
    region_label = "г.Москва" if region_choice == "msk" else "Российская Федерация"

    r = kvart_rows[kvart_rows["region_key"] == region_choice].iloc[0]
    total_th = float(r.get("квартиры_тыс_шт_num") or 0)
    total = total_th * 1000.0
    total_area = float(r.get("площадь_тыс_м²_num") or 0)

    # Пытаемся использовать per-dev для точных комнат если данные
    # выглядят правильно (sanity check: total в пределах 5% от агрегата).
    use_per_dev = False
    if not per_dev_rows.empty and "region_key" in per_dev_rows.columns:
        pd_region = per_dev_rows[per_dev_rows["region_key"] == region_choice]
        if not pd_region.empty:
            pd_row = pd_region.iloc[0]
            pd_total = float(pd_row.get("Все_количество_шт_num") or 0)
            if total > 0 and abs(pd_total / total - 1) < 0.05:
                # Per-dev данные согласованы с агрегатом — берём точные числа
                use_per_dev = True
                rooms_data = {}
                for room, label in [("1комн", "1 комн"), ("2комн", "2 комн"),
                                    ("3комн", "3 комн"), ("4+комн", "4+ комн")]:
                    cnt = float(pd_row.get(f"{room}_количество_шт_num") or 0)
                    ar = float(pd_row.get(f"{room}_площадь_тыс_м²_num") or 0)
                    pct = (cnt / pd_total * 100) if pd_total > 0 else 0
                    rooms_data[label] = (cnt, pct, ar if ar else None)
                total = pd_total
                total_area = float(pd_row.get("Все_площадь_тыс_м²_num") or total_area)

    if not use_per_dev:
        rooms_data = {}
        for k, label in [("1комн", "1 комн"), ("2комн", "2 комн"),
                         ("3комн", "3 комн"), ("4+комн", "4+ комн")]:
            pct_v = r.get(f"доля_{k}_%_num")
            if pct_v is not None and not pd.isna(pct_v):
                rooms_data[label] = (total * float(pct_v) / 100.0, float(pct_v), None)
            else:
                rooms_data[label] = (0, 0, None)

    market_total = 0.0
    if "region_key" in kvart_devs_full.columns:
        reg_df = kvart_devs_full[kvart_devs_full["region_key"] == region_choice]
        if "площадь_тыс_м²_num" in reg_df.columns:
            market_total = float(reg_df["площадь_тыс_м²_num"].fillna(0).sum())

    src_note = ("Точные числа из per-dev обхода (верифицировано "
                "против агрегата)" if use_per_dev else
                "Источник: наш.дом.рф/квартирография (агрегат `developers`)")
    _render_kvart_section(
        total, total_area, rooms_data, region_label, market_total, src_note
    )


# === Переносы сроков ввода (4 KPI карточки в 2 ряда) ===
st.markdown("### Переносы сроков ввода")


def fmt_thousand_m2(v: float | None) -> str:
    if v is None or v <= 0:
        return "—"
    return f"{ru_num(v/1000, 1)} тыс. м²"


def render_delay_card(title: str, value: float | None,
                      sub_lines: list[tuple[str, str]]):
    """Карточка: заголовок + большое число (перенос) + список подписей.

    sub_lines: список (label, value_html). Если value пустое — рендерится
    как обычный текст без подсветки.
    """
    val_str = fmt_thousand_m2(value)
    parts = []
    for label, v in sub_lines:
        if v:
            parts.append(
                f"<div style='color:#555;font-size:13px;margin-bottom:6px;'>"
                f"<span style='font-weight:600;color:{DELAY_COLOR};'>{v}</span> "
                f"{label}</div>"
            )
        else:
            parts.append(
                f"<div style='color:#555;font-size:13px;margin-bottom:6px;'>"
                f"{label}</div>"
            )
    subs_html = "".join(parts)
    st.markdown(
        "<div style='padding:18px;border:1px solid #e5e5e5;border-radius:8px;"
        "background:#fafafa;height:100%;'>"
        "<div style='color:#888;font-size:11px;text-transform:uppercase;"
        f"letter-spacing:0.5px;margin-bottom:8px;'>{title}</div>"
        f"<div style='font-size:34px;font-weight:700;color:{DELAY_COLOR};"
        f"line-height:1;margin-bottom:14px;'>{val_str}</div>"
        f"{subs_html}"
        "</div>",
        unsafe_allow_html=True,
    )


# === Источники ===
# В строительстве (РФ + МСК) — у нас точные числа из top_obyem_stroitelstva
stroitelstvo_rf = erzrf_value("obyem_stroitelstva", "rf", "Строится")
stroitelstvo_msk = erzrf_value("obyem_stroitelstva", "msk", "Строится")
perenos_stroy_rf = erzrf_value("obyem_stroitelstva", "rf", "С переносом срока")
perenos_stroy_msk = erzrf_value("obyem_stroitelstva", "msk", "С переносом срока")

# Ввод 2026 (РФ + МСК) — точные числа из top_obyem_vvoda
vvod_rf_2026 = erzrf_value("obyem_vvoda", "rf", "Введено")
vvod_msk_2026 = erzrf_value("obyem_vvoda", "msk", "Введено")
perenos_vvod_rf_2026 = erzrf_value("obyem_vvoda", "rf", "С переносом срока")
perenos_vvod_msk_2026 = erzrf_value("obyem_vvoda", "msk", "С переносом срока")

# Перенос 2022-2025 (только РФ из cards — МСК по годам в карточке нет)
sdano_2225_rf = 0.0
perenos_2225_rf = 0.0
if cards_row is not None:
    for y in range(2022, 2026):
        sdano_2225_rf += float(cards_row.get(f"Сдано_{y}_м²_num") or 0)
        perenos_2225_rf += float(cards_row.get(f"Перенос_{y}_м²_num") or 0)

# Москва: введённое 2022-2025 (только жилое)
msk_2225_zhilye = 0.0
if not rv_dev.empty:
    msk_2225 = rv_dev[rv_dev.get("Год ввода по Мосстату").isin([2022, 2023, 2024, 2025])]
    msk_2225_zhilye = float(msk_2225["category_жилое"].sum())

# ТОЧНЫЕ числа из per-year ERZ top_obyem_vvoda_msk_YYYY_*.xlsx
# (если парсер их собрал — после обновления erzrf_checker)
perenos_msk_2225_exact = 0.0
sdano_msk_2225_exact = 0.0
exact_years_found = []
by_year_msk = erzrf_top.get("obyem_vvoda_by_year", {}).get("msk", {})
for y in (2022, 2023, 2024, 2025):
    df_y = by_year_msk.get(y)
    if df_y is None or df_y.empty:
        continue
    name_col = next((c for c in df_y.columns if "Наименование" in str(c)), None)
    if not name_col:
        continue
    rows_y = df_y[df_y[name_col].apply(lambda x: norm(str(x)) == sel_key)]
    if rows_y.empty:
        continue
    r_y = rows_y.iloc[0]
    p_col = next((c for c in df_y.columns
                  if "С переносом срока" in c and "м²" in c), None)
    v_col = next((c for c in df_y.columns
                  if "Введено" in c and "м²" in c), None)
    if p_col:
        perenos_msk_2225_exact += float(pd.to_numeric(r_y[p_col], errors="coerce") or 0)
    if v_col:
        sdano_msk_2225_exact += float(pd.to_numeric(r_y[v_col], errors="coerce") or 0)
    exact_years_found.append(y)

perenos_msk_2225_est = None  # для оценки если точных нет
if not exact_years_found and sdano_2225_rf > 0 and perenos_2225_rf > 0:
    msk_share = min(msk_2225_zhilye / sdano_2225_rf, 1.0)
    perenos_msk_2225_est = perenos_2225_rf * msk_share

# Если точные есть — используем их, иначе оценку
perenos_msk_2225 = (perenos_msk_2225_exact if exact_years_found
                    else perenos_msk_2225_est)
base_msk_2225 = (sdano_msk_2225_exact if exact_years_found
                 else msk_2225_zhilye)

# В других регионах за 2026
other_perenos_2026 = None
if perenos_vvod_rf_2026 and perenos_vvod_rf_2026 > 0:
    other_perenos_2026 = max(perenos_vvod_rf_2026 - (perenos_vvod_msk_2026 or 0), 0)


def pct_str(num: float | None, denom: float | None) -> str:
    if num is None or denom is None or denom <= 0:
        return "—"
    return f"{num / denom * 100:.1f}%"


# Знаменатели для МСК берём из monitoring 2.0:
# — текущее строительство МСК = Общая площадь объектов ОКС со Статусом «Строящийся»
# — ввод 2026 МСК = Общая площадь объектов РВ за 2026
# — ввод 2022-2025 МСК = Общая площадь объектов РВ за 2022..2025
mon_stroy_msk = float(oks_dev["Общая площадь"].sum()) if not oks_dev.empty else 0.0
mon_vvod_msk_2026 = 0.0
mon_vvod_msk_2225 = 0.0
if not rv_dev.empty:
    rv_2026 = rv_dev[rv_dev.get("Год ввода по Мосстату") == 2026]
    mon_vvod_msk_2026 = float(rv_2026["Общая площадь"].sum())
    rv_2225 = rv_dev[rv_dev.get("Год ввода по Мосстату").isin([2022, 2023, 2024, 2025])]
    mon_vvod_msk_2225 = float(rv_2225["Общая площадь"].sum())


# === Ряд 1: текущее строительство (РФ + МСК) ===
r1c1, r1c2 = st.columns(2)
with r1c1:
    subs = [
        (f"от {ru_num((stroitelstvo_rf or 0)/1000)} тыс. м² в стройке по РФ",
         pct_str(perenos_stroy_rf, stroitelstvo_rf)),
    ]
    render_delay_card("Перенос в текущем строительстве РФ", perenos_stroy_rf, subs)
with r1c2:
    # Москва: знаменатель из monitoring 2.0 (Реестр ОКС, статус «Строящийся»)
    subs = [
        (f"от {ru_num(mon_stroy_msk/1000)} тыс. м² в стройке Москвы",
         pct_str(perenos_stroy_msk, mon_stroy_msk)),
    ]
    render_delay_card("Перенос в текущем строительстве МСК", perenos_stroy_msk, subs)

# === Ряд 2: переносы ввода ===
r2c1, r2c2 = st.columns(2)
with r2c1:
    # Москва: знаменатель из monitoring 2.0 (РВ за 2022..2025)
    if exact_years_found:
        title = f"Перенос в Москве за {min(exact_years_found)}-{max(exact_years_found)}"
        value = perenos_msk_2225
    else:
        title = "Перенос в Москве за 2022-2025 (оценка)"
        value = perenos_msk_2225_est
    subs = [
        (f"от {ru_num(mon_vvod_msk_2225/1000)} тыс. м² введённых в Москве 22-25",
         pct_str(value, mon_vvod_msk_2225)),
    ]
    render_delay_card(title, value, subs)
with r2c2:
    # РФ-карточка: знаменатель оставляем ERZ (как договорились)
    subs = [
        (f"от {ru_num((stroitelstvo_rf or 0)/1000)} тыс. м² в стройке РФ",
         pct_str(perenos_vvod_rf_2026, stroitelstvo_rf)),
        # Москва-строка: знаменатель из monitoring 2.0 (РВ за 2026)
        (f"в Москве: {fmt_thousand_m2(perenos_vvod_msk_2026)} "
         f"({pct_str(perenos_vvod_msk_2026, mon_vvod_msk_2026)} от ввода МСК)", ""),
    ]
    render_delay_card("Перенос ввода за 2026 (РФ)", perenos_vvod_rf_2026, subs)

# === Ряд 3: в других регионах ===
r3c1, _ = st.columns(2)
with r3c1:
    subs = [
        (f"от {ru_num((vvod_rf_2026 or 0)/1000)} тыс. м² ввода в РФ за 2026",
         pct_str(other_perenos_2026, vvod_rf_2026)),
        (f"от всех переносов РФ ({fmt_thousand_m2(perenos_vvod_rf_2026)})",
         pct_str(other_perenos_2026, perenos_vvod_rf_2026)),
    ]
    render_delay_card("В других регионах за 2026", other_perenos_2026, subs)

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
            if date_escrow:
                st.caption(f"Дата документа: **{date_escrow}**")


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
