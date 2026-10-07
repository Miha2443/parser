"""Квартирография — дашборд в стиле наш.дом.рф.

Layout повторяет оригинальную страницу:
- Селектор региона (РФ / Москва) вверху
- 4 блока:
  · Типы квартир (5 строк × 2 показателя)
  · Распределение квартир по площади (гистограмма)
  · Объём строительства по девелоперам (таблица)
  · Объём строительства по регионам (таблица)
"""
from __future__ import annotations

from html import escape
import pandas as pd
import plotly.express as px
import streamlit as st

from app.components.design import COLORS, apply_theme, page_header, style_plotly
from app.components.export import chart_data_expander, table_download_buttons
from app.data_access import load_kvartirografia

st.set_page_config(page_title="Квартирография — Аналитика Москвы", layout="wide")
apply_theme()

# Единая деловая палитра для комнатности.
COLOR_1K = COLORS["green"]
COLOR_2K = COLORS["blue"]
COLOR_3K = COLORS["amber"]
COLOR_4K = COLORS["red"]


def ru_num(value, digits=0):
    if value is None or pd.isna(value):
        return "—"
    try:
        s = f"{float(value):,.{digits}f}"
    except (TypeError, ValueError):
        return str(value)
    return s.replace(",", " ").replace(".", ",")


data = load_kvartirografia()
if data["apartments"].empty:
    st.warning(
        "Нет данных. Положи `kvartirografia_<date>.json` в "
        "`data/raw/realty/nashdom/` или в `nashdom/` в корне репо."
    )
    st.stop()

page_header("Квартирография жилищного строительства")

cols_top = st.columns([3, 2])
with cols_top[0]:
    region_map = {"rf": "Российская Федерация", "msk": "Город Москва"}
    available = sorted(
        [r for r in data["regions_available"] if r in region_map],
        key=lambda value: 0 if value == "msk" else 1,
    )
    if not available:
        st.error("В данных нет регионов rf/msk")
        st.stop()
    sel = st.radio(
        "Регион",
        available,
        format_func=lambda x: region_map[x],
        horizontal=True,
        key="kvart_region",
    )
with cols_top[1]:
    st.markdown(f"<div style='padding-top:30px;color:{COLORS['muted']};'>"
                f"Отчёт по данным на <b>{data['report_date']}</b></div>",
                unsafe_allow_html=True)

# === Блок 1: типы квартир + распределение ===
b1_left, b1_right = st.columns(2)

with b1_left:
    st.subheader("Типы квартир")
    apartments = data["apartments"]
    df = apartments[apartments["region_key"] == sel].copy()
    if df.empty:
        st.info("Нет данных для региона")
    else:
        df_show = pd.DataFrame({
            "Тип квартир": df["тип"],
            "Количество квартир, шт.": df["количество_шт_num"].apply(lambda v: ru_num(v)),
            "Площадь, тыс. м²": df["площадь_тыс_м²_num"].apply(lambda v: ru_num(v)),
        })
        st.dataframe(df_show, hide_index=True, use_container_width=True)

with b1_right:
    st.subheader("Распределение квартир по площади, м²")
    distribution = data["distribution"]
    df = distribution[distribution["region_key"] == sel].copy()
    if df.empty:
        st.info("Нет данных")
    else:
        fig = px.bar(
            df, x="диапазон", y="доля_num",
            text=df["доля_num"].apply(lambda v: f"{int(v)}%" if v is not None else ""),
            color_discrete_sequence=[COLOR_1K],
        )
        fig.update_traces(textposition="outside")
        fig.update_layout(
            margin=dict(l=0, r=0, t=10, b=0), height=350,
            yaxis_title="", xaxis_title="", showlegend=False,
        )
        style_plotly(fig, height=350)
        fig.update_yaxes(visible=False)
        st.plotly_chart(fig, use_container_width=True)
        chart_data_expander(
            fig,
            df[["диапазон", "доля_num"]].rename(
                columns={"диапазон": "Площадь, м²", "доля_num": "Доля, %"}
            ),
            name=f"kvart_distribution_{sel}",
            key=f"kvart_distribution_{sel}",
        )


# === Блок 2 + 3: таблицы по девелоперам и регионам ===
def render_top_table(df: pd.DataFrame, name_label: str, top_n: int = 50, *, preserve_order: bool = False):
    """Показывает доли комнатности одной составной полосой, как в источнике."""
    if df.empty:
        st.info("Нет данных")
        return
    df_sorted = (df if preserve_order else df.sort_values("площадь_тыс_м²_num", ascending=False)).head(top_n)
    room_cols = ["доля_1комн_%_num", "доля_2комн_%_num", "доля_3комн_%_num", "доля_4+комн_%_num"]
    room_colors = [COLOR_1K, COLOR_2K, COLOR_3K, COLOR_4K]
    rows = []
    for _, row in df_sorted.iterrows():
        shares = [max(0.0, float(value)) if pd.notna(value) else 0.0
                  for value in (pd.to_numeric(row.get(col), errors="coerce") for col in room_cols)]
        total_share = sum(shares)
        segments = "".join(
            f"<span style='width:{share / total_share * 100:.3f}%;background:{color}' "
            f"title='{i + 1 if i < 3 else '4+'} комнаты: {share:.1f}%'>"
            f"{share:.0f}%</span>"
            for i, (share, color) in enumerate(zip(shares, room_colors)) if share > 0 and total_share > 0
        )
        rows.append(
            f"<tr><td title='{escape(str(row['наименование']))}'>{escape(str(row['наименование']))}</td>"
            f"<td>{ru_num(row['квартиры_тыс_шт_num'], 1)}</td>"
            f"<td>{ru_num(row['площадь_тыс_м²_num'])}</td>"
            f"<td><div class='room-strip'>{segments}</div></td></tr>"
        )
    st.markdown(
        "<style>.room-table-wrap{max-height:600px;overflow:auto;border:1px solid #C8D2DC;border-radius:8px}"
        ".room-table{width:100%;border-collapse:collapse;font-size:12px;color:#17212B}"
        ".room-table th{position:sticky;top:0;background:#F4F7FA;z-index:1;text-align:left}"
        ".room-table td,.room-table th{padding:7px 6px;border-bottom:1px solid #E3E9EF}"
        ".room-table td:first-child{max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}"
        ".room-table td:nth-child(2),.room-table td:nth-child(3){text-align:right;white-space:nowrap}"
        ".room-strip{display:flex;width:100%;min-width:150px;height:22px;overflow:hidden}"
        ".room-strip span{display:flex;align-items:center;justify-content:center;overflow:hidden;white-space:nowrap;"
        "font-size:10px;color:white;font-weight:600}</style>"
        f"<div class='room-table-wrap'><table class='room-table'><thead><tr><th>{escape(name_label)}</th>"
        "<th>Квартиры,<br>тыс. шт.</th><th>Площадь,<br>тыс. м²</th><th>Доля по комнатности</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>", unsafe_allow_html=True,
    )
    with st.expander("Данные таблицы и скачивание", expanded=False):
        export = df_sorted[["наименование", "квартиры_тыс_шт_num", "площадь_тыс_м²_num"] + room_cols].copy()
        st.dataframe(export, hide_index=True, use_container_width=True)
        table_download_buttons(export, name=f"kvart_{name_label}", key_prefix=f"kvart_table_{name_label}")


b2_left, b2_right = st.columns(2)

with b2_left:
    devs = data["developers"]
    if devs.empty or "region_key" not in devs.columns:
        st.subheader("Объём строительства по девелоперам")
        st.info("Нет данных")
    else:
        df = devs[devs["region_key"] == sel].copy()
        st.subheader(f"Объём строительства по девелоперам ({len(df)})")
        render_top_table(df, "Девелопер")

with b2_right:
    regs = data["regions"]
    if regs.empty or "region_key" not in regs.columns:
        st.subheader("Объём строительства по регионам")
        st.info("Нет данных")
    else:
        df = regs[regs["region_key"] == sel].copy()
        moscow_mask = regs["наименование"].astype(str).str.strip().str.casefold().isin(["город москва", "г. москва", "москва"])
        if sel == "msk":
            moscow = regs[regs["region_key"].eq("rf") & moscow_mask]
            df = pd.concat([moscow, df], ignore_index=True).drop_duplicates("наименование", keep="first")
        df = df.sort_values("площадь_тыс_м²_num", ascending=False)
        first = df["наименование"].astype(str).str.strip().str.casefold().isin(["город москва", "г. москва", "москва"])
        df = pd.concat([df[first], df[~first]])
        st.subheader(f"Объём строительства по регионам ({len(df)})")
        render_top_table(df, "Регион", preserve_order=True)
