"""Shared visual system for the Streamlit dashboard."""
from __future__ import annotations

from html import escape

import pandas as pd
import plotly.io as pio
import streamlit as st


LIGHT_COLORS = {
    "bg": "#F4F7FA",
    "bg_2": "#FFFFFF",
    "panel": "#FFFFFF",
    "panel_2": "#EAF0F5",
    "stroke": "#C8D2DC",
    "text": "#17212B",
    "muted": "#536579",
    "red": "#C62828",
    "red_soft": "rgba(198,40,40,.88)",
    "gray_bar": "#64748B",
    "navy_bar": "#274C77",
    "blue": "#1D5D96",
    "blue_2": "#327AB7",
    "navy": "#17324D",
    "teal": "#087E8B",
    "cyan": "#0077A6",
    "green": "#18794E",
    "green_2": "#5B7F19",
    "amber": "#9A6700",
    "purple": "#6743A7",
    "orange": "#B85A1B",
    "ink": "#17212B",
    "line": "#C8D2DC",
    "surface": "#FFFFFF",
    "surface_2": "#EAF0F5",
    "sidebar": "#FFFFFF",
    "neutral": "#536579",
    "red_tint": "rgba(198,40,40,.12)",
}

COLORS = LIGHT_COLORS.copy()

SERIES = [
    COLORS["red"],
    COLORS["blue"],
    COLORS["green"],
    COLORS["amber"],
    COLORS["purple"],
    COLORS["teal"],
    COLORS["orange"],
    COLORS["gray_bar"],
]

FONT_STACK = "Montserrat, Segoe UI, Roboto, Arial, sans-serif"
if not hasattr(st, "_ma_native_dataframe"):
    st._ma_native_dataframe = st.dataframe
_NATIVE_DATAFRAME = st._ma_native_dataframe


def _activate_palette() -> None:
    COLORS.clear()
    COLORS.update(LIGHT_COLORS)
    SERIES[:] = [
        COLORS["red"], COLORS["blue"], COLORS["green"], COLORS["amber"],
        COLORS["purple"], COLORS["teal"], COLORS["orange"], COLORS["gray_bar"],
    ]


def moscow_first(values) -> list:
    """Return region choices with Moscow first and stable source order after it."""
    return sorted(
        list(values),
        key=lambda value: 0 if "моск" in str(value).casefold() or str(value) == "msk" else 1,
    )


def _display_value(value) -> str:
    if value is None:
        return "—"
    try:
        if bool(pd.isna(value)):
            return "—"
    except (TypeError, ValueError):
        pass
    if isinstance(value, float):
        if value.is_integer():
            return f"{int(value):,}".replace(",", " ")
        return f"{value:,.1f}".replace(",", " ").replace(".", ",")
    return str(value)


def _light_dataframe(data, *, hide_index: bool | None = None, height=None, **_kwargs):
    """Render dataframes as real themed tables because Streamlit's canvas grid
    keeps the server theme colors and cannot follow our runtime theme selector.
    """
    if hasattr(data, "data") and data.__class__.__name__ == "Styler":
        table_html = data.to_html(
            border=0,
            table_attributes='class="ma-light-dataframe-table" role="table"',
        )
    else:
        frame = data if isinstance(data, pd.DataFrame) else pd.DataFrame(data)
        show_index = not bool(hide_index)
        headers = ([frame.index.name or ""] if show_index else []) + [str(c) for c in frame.columns]
        head = "".join(f"<th scope='col'>{escape(label)}</th>" for label in headers)
        rows = []
        for index, row in frame.iterrows():
            cells = []
            if show_index:
                cells.append(f"<th scope='row'>{escape(_display_value(index))}</th>")
            for column, value in row.items():
                rendered = escape(_display_value(value))
                is_percent = "%" in str(column)
                if is_percent and pd.notna(value):
                    try:
                        percent = max(0.0, min(100.0, float(value)))
                    except (TypeError, ValueError):
                        percent = None
                    if percent is not None:
                        rendered = (
                            "<div class='ma-progress-cell'>"
                            f"<span class='ma-progress-track'><span style='width:{percent:.2f}%'></span></span>"
                            f"<span>{rendered}%</span></div>"
                        )
                cells.append(f"<td>{rendered}</td>")
            rows.append("<tr>" + "".join(cells) + "</tr>")
        table_html = (
            "<table class='ma-light-dataframe-table' role='table'>"
            f"<thead><tr>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table>"
        )
    max_height = "none"
    if isinstance(height, int) and height > 0:
        max_height = f"{height}px"
    st.markdown(
        f"<div class='ma-light-dataframe' style='max-height:{max_height}'>{table_html}</div>",
        unsafe_allow_html=True,
    )


def dataframe(data=None, *args, **kwargs):
    """Light-theme replacement for st.dataframe used by dashboard pages."""
    return _light_dataframe(data, **kwargs)


def _register_plotly_template() -> None:
    grid = "rgba(100,116,139,.26)"
    zero = "rgba(100,116,139,.48)"
    pio.templates["moscow_business"] = {
        "layout": {
            "font": {"family": FONT_STACK, "color": COLORS["text"]},
            "paper_bgcolor": "rgba(0,0,0,0)",
            "plot_bgcolor": "rgba(0,0,0,0)",
            "colorway": SERIES,
            "margin": {"l": 18, "r": 18, "t": 44, "b": 34},
            "xaxis": {
                "gridcolor": grid,
                "zerolinecolor": zero,
                "linecolor": COLORS["stroke"],
                "tickcolor": COLORS["stroke"],
                "tickfont": {"color": COLORS["muted"], "size": 11},
                "title": {"font": {"color": COLORS["muted"], "size": 12}},
            },
            "yaxis": {
                "gridcolor": grid,
                "zerolinecolor": zero,
                "linecolor": COLORS["stroke"],
                "tickcolor": COLORS["stroke"],
                "tickfont": {"color": COLORS["muted"], "size": 11},
                "title": {"font": {"color": COLORS["muted"], "size": 12}},
            },
            "legend": {
                "orientation": "h",
                "yanchor": "bottom",
                "y": 1.02,
                "xanchor": "right",
                "x": 1,
                "font": {"size": 12, "color": COLORS["text"]},
            },
            "hoverlabel": {
                "bgcolor": COLORS["panel"],
                "bordercolor": COLORS["stroke"],
                "font": {"color": COLORS["text"], "size": 12},
            },
        }
    }
    pio.templates.default = "moscow_business"


def apply_theme() -> None:
    """Apply global CSS and Plotly defaults for all dashboard pages."""
    _activate_palette()
    _register_plotly_template()
    st.dataframe = dataframe
    page_header_bg = "#FFFFFF"
    brand_header_bg = "#FFFFFF"
    header_text = COLORS["text"]
    header_muted = COLORS["muted"]
    brand_divider = COLORS["stroke"]
    st.markdown(
        f"""
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Montserrat:wght@400;500;600;700;800&display=swap');

        :root {{
          --ma-bg: {COLORS["bg"]};
          --ma-bg2: {COLORS["bg_2"]};
          --ma-panel: {COLORS["panel"]};
          --ma-panel2: {COLORS["panel_2"]};
          --ma-stroke: {COLORS["stroke"]};
          --ma-text: {COLORS["text"]};
          --ma-muted: {COLORS["muted"]};
          --ma-red: {COLORS["red"]};
          --ma-teal: {COLORS["teal"]};
          --ma-green: {COLORS["green"]};
          --ma-blue: {COLORS["blue"]};
          --ma-amber: {COLORS["amber"]};
          --ma-neutral: {COLORS["neutral"]};
          --ma-red-tint: {COLORS["red_tint"]};
        }}

        html,
        body,
        .stApp {{
          background: var(--ma-bg);
          color: var(--ma-text);
          font-family: {FONT_STACK};
        }}

        .stApp {{
          background:
            radial-gradient(circle at 16% -8%, rgba(111,179,210,.12), transparent 34rem),
            linear-gradient(180deg, var(--ma-bg2) 0%, var(--ma-bg) 100%);
        }}

        [data-testid="stDecoration"],
        footer {{
          display: none !important;
          height: 0 !important;
          visibility: hidden !important;
          pointer-events: none !important;
        }}

        /* Keep Streamlit's native navigation and sidebar toggle available. */
        [data-testid="stHeader"] {{
          background: var(--ma-panel);
          border-bottom: 1px solid var(--ma-stroke);
        }}

        [data-testid="stAppViewContainer"] > .main .block-container,
        [data-testid="stMainBlockContainer"],
        .stMainBlockContainer.block-container {{
          max-width: 1540px;
          padding: 4.5rem 1.35rem 1.35rem;
        }}

        [data-testid="stSidebar"] {{
          background: var(--ma-panel);
          border-right: 1px solid var(--ma-stroke);
        }}

        [data-testid="stSidebar"] [data-testid="stSidebarContent"] {{
          padding: 1rem .65rem 1.75rem;
        }}

        [data-testid="stSidebar"] a,
        [data-testid="stSidebar"] p,
        [data-testid="stSidebar"] span,
        [data-testid="stSidebar"] label,
        [data-testid="stSidebar"] li {{
          color: var(--ma-text) !important;
          font-size: .82rem;
        }}

        [data-testid="stSidebar"] h1,
        [data-testid="stSidebar"] h2,
        [data-testid="stSidebar"] h3,
        [data-testid="stSidebar"] strong {{
          color: var(--ma-text) !important;
        }}

        [data-testid="stSidebar"] hr {{
          border-color: var(--ma-stroke);
        }}

        [data-testid="stSidebar"] a {{
          border-radius: 8px;
          min-height: 2.05rem;
        }}

        [data-testid="stSidebar"] a:hover {{
          background: var(--ma-panel2);
        }}

        [data-testid="stSidebar"] [aria-current="page"],
        [data-testid="stSidebar"] a[aria-current="page"] {{
          background: var(--ma-panel2) !important;
          box-shadow: inset 3px 0 0 var(--ma-red);
          color: var(--ma-text) !important;
        }}

        [data-testid="stSidebar"] .stButton > button {{
          background: var(--ma-panel2) !important;
          color: var(--ma-text) !important;
          border-color: var(--ma-stroke) !important;
        }}

        h1, h2, h3, h4, h5, h6,
        p, li, label,
        [data-testid="stMarkdownContainer"] {{
          color: var(--ma-text);
          letter-spacing: 0;
        }}

        h1 {{
          font-size: 1.7rem !important;
          line-height: 1.15 !important;
          font-weight: 800 !important;
          margin-bottom: .25rem !important;
        }}

        h2 {{
          font-size: 1.16rem !important;
          font-weight: 760 !important;
          margin-top: 1.05rem !important;
        }}

        h3 {{
          font-size: 1rem !important;
          font-weight: 730 !important;
          margin-top: .9rem !important;
        }}

        .ma-home-section-title {{
          margin: 2rem 0 1rem !important;
          padding-bottom: .55rem;
          border-bottom: 3px solid var(--ma-red);
          font-size: 1.65rem !important;
          line-height: 1.2 !important;
          font-weight: 820 !important;
        }}

        .ma-page-header {{
          position: relative;
          overflow: hidden;
          margin-bottom: 1rem;
          padding: 1rem 1.2rem 1.1rem;
          border: 1px solid var(--ma-stroke);
          border-radius: 14px;
          background: {page_header_bg};
          box-shadow: 0 18px 40px rgba(0,0,0,.18);
        }}

        .ma-brand-header {{
          display: flex;
          align-items: center;
          gap: 1.05rem;
          width: 100%;
          max-width: 100%;
          min-height: 4.2rem;
          margin: 0 0 .85rem 0;
          padding: .78rem 1.2rem .78rem 1rem;
          border: 1px solid var(--ma-stroke);
          border-radius: 0 0 18px 0;
          background: {brand_header_bg};
          box-shadow: 0 14px 30px rgba(0,0,0,.18);
        }}

        .ma-brand-mark {{
          display: inline-flex;
          align-items: center;
          justify-content: center;
          width: 2.55rem;
          height: 2.55rem;
          flex: 0 0 auto;
          border: 2px solid {header_text};
          color: {header_text};
          font-size: 1.05rem;
          font-weight: 800;
          line-height: 1;
        }}

        .ma-brand-unit {{
          display: flex;
          align-items: center;
          gap: .7rem;
          min-width: 0;
        }}

        .ma-brand-mark.shield {{
          border-radius: .45rem .45rem .9rem .9rem;
        }}

        .ma-brand-mark.circle {{
          border-radius: 50%;
        }}

        .ma-brand-text {{
          color: {header_text};
          font-size: .78rem;
          line-height: 1.12;
          font-weight: 800;
          letter-spacing: .02em;
          text-transform: uppercase;
          white-space: nowrap;
        }}

        .ma-brand-divider {{
          width: 1px;
          height: 2.65rem;
          background: {brand_divider};
          flex: 0 0 auto;
        }}

        .ma-page-header::before {{
          content: "";
          position: absolute;
          left: 0;
          top: 0;
          bottom: 0;
          width: 4px;
          background: var(--ma-red);
        }}

        .ma-page-header::after {{
          content: "";
          position: absolute;
          inset: 0;
          background:
            repeating-linear-gradient(90deg, transparent 0 118px, rgba(148,169,188,.045) 118px 119px);
          pointer-events: none;
        }}

        .ma-page-header > * {{
          position: relative;
          z-index: 1;
        }}

        .ma-eyebrow {{
          color: {header_muted};
          font-size: .68rem;
          font-weight: 760;
          letter-spacing: .14em;
          text-transform: uppercase;
          margin-bottom: .42rem;
        }}

        .ma-page-header h1 {{
          color: {header_text} !important;
          margin-bottom: .45rem !important;
        }}

        .ma-subtitle {{
          color: {header_muted};
          font-size: .86rem;
          line-height: 1.45;
          max-width: 1080px;
        }}

        div[data-testid="stVerticalBlock"] > div[data-testid="stHorizontalBlock"] {{
          gap: 1rem;
        }}

        div[data-testid="stVerticalBlock"] {{
          gap: .78rem;
        }}

        div[data-testid="stMetric"],
        div[data-testid="stDataFrame"],
        div[data-testid="stPlotlyChart"],
        div[data-testid="stForm"],
        div[data-testid="stVerticalBlockBorderWrapper"],
        div[data-testid="stExpander"] > details {{
          border-radius: 14px;
        }}

        div[data-testid="stMetric"] {{
          background: var(--ma-panel);
          border: 1px solid var(--ma-stroke);
          padding: .78rem .95rem;
          box-shadow: none;
        }}

        div[data-testid="stMetricLabel"] p {{
          color: var(--ma-muted) !important;
          font-size: .72rem;
          font-weight: 700;
          letter-spacing: .08em;
          text-transform: uppercase;
          white-space: normal;
          overflow-wrap: anywhere;
        }}

        div[data-testid="stMetricValue"] {{
          color: var(--ma-text);
          font-weight: 800;
        }}

        div[data-testid="stMetricDelta"] {{
          color: var(--ma-teal);
        }}

        div[data-testid="stPlotlyChart"] {{
          background: var(--ma-panel);
          border: 1px solid var(--ma-stroke);
          padding: .5rem .55rem;
          box-shadow: none;
          overflow: hidden !important;
        }}

        div[data-testid="stPlotlyChart"] > div,
        div[data-testid="stPlotlyChart"] .js-plotly-plot,
        div[data-testid="stPlotlyChart"] .plot-container,
        div[data-testid="stPlotlyChart"] .svg-container {{
          overflow: hidden !important;
        }}

        div[data-testid="stDataFrame"] {{
          border: 1px solid var(--ma-stroke);
          background: var(--ma-panel);
          box-shadow: none;
          overflow: hidden;
        }}

        div[data-testid="stDataFrame"] [role="grid"],
        div[data-testid="stDataFrame"] [data-testid="stDataFrameResizable"],
        div[data-testid="stDataFrame"] div {{
          border-color: var(--ma-stroke) !important;
        }}

        div[data-testid="stVerticalBlockBorderWrapper"] {{
          border: 1px solid var(--ma-stroke) !important;
          background: var(--ma-panel) !important;
          box-shadow: none;
        }}

        div[data-testid="stExpander"] > details {{
          border-color: var(--ma-stroke);
          background: var(--ma-panel);
        }}

        div[data-testid="stExpander"] summary,
        div[data-testid="stExpander"] summary p {{
          color: var(--ma-text) !important;
          font-weight: 700;
        }}

        div[data-testid="stTabs"] button {{
          color: var(--ma-muted);
          font-weight: 650;
        }}

        div[data-testid="stTabs"] button[aria-selected="true"] {{
          color: var(--ma-text);
        }}

        div[data-testid="stTabs"] [data-baseweb="tab-highlight"] {{
          background-color: var(--ma-red);
        }}

        .stButton > button,
        .stDownloadButton > button,
        [data-testid="stBaseButton-secondary"],
        [data-testid="stBaseButton-primary"] {{
          border-radius: 8px !important;
          border-color: var(--ma-stroke) !important;
          background: var(--ma-panel2) !important;
          color: var(--ma-text) !important;
          font-weight: 700 !important;
        }}

        [data-testid="stBaseButton-primary"] {{
          background: var(--ma-red) !important;
          border-color: var(--ma-red) !important;
          color: #FFFFFF !important;
        }}

        .stButton > button:hover,
        .stDownloadButton > button:hover {{
          background: var(--ma-teal) !important;
          border-color: var(--ma-teal) !important;
          color: #FFFFFF !important;
        }}

        div[data-testid="stSelectbox"],
        div[data-testid="stMultiSelect"],
        div[data-testid="stSlider"],
        div[data-testid="stCheckbox"],
        div[data-testid="stRadio"] {{
          margin-bottom: 0 !important;
        }}

        div[data-baseweb="select"] > div,
        div[data-baseweb="input"] > div,
        textarea,
        input,
        [data-testid="stFileUploader"] section {{
          border-radius: 8px !important;
          border-color: var(--ma-stroke) !important;
          background-color: var(--ma-panel2) !important;
          color: var(--ma-text) !important;
        }}

        div[data-baseweb="select"] span,
        div[data-baseweb="select"] svg,
        input,
        textarea {{
          color: var(--ma-text) !important;
          fill: var(--ma-text) !important;
        }}

        div[data-baseweb="select"] [role="option"] {{
          background: var(--ma-panel2);
          color: var(--ma-text);
        }}

        div[data-testid="stMultiSelect"] div[data-baseweb="select"] > div {{
          min-height: 2.7rem !important;
          height: auto !important;
          align-items: flex-start !important;
          box-sizing: border-box !important;
          padding-left: .72rem !important;
          overflow: visible !important;
        }}

        div[data-testid="stMultiSelect"] div[data-baseweb="select"] > div > div {{
          display: flex !important;
          flex-wrap: wrap !important;
          align-items: center !important;
          gap: .22rem !important;
          padding-left: .3rem !important;
          overflow: visible !important;
        }}

        div[data-testid="stMultiSelect"] div[data-baseweb="select"],
        div[data-testid="stMultiSelect"] div[data-baseweb="tag"] {{
          overflow: visible !important;
        }}

        div[data-baseweb="tag"] {{
          background: var(--ma-red-tint) !important;
          border: 1px solid var(--ma-red) !important;
          color: var(--ma-text) !important;
          height: auto !important;
          min-height: 1.55rem !important;
          max-width: 100% !important;
          margin-top: .18rem !important;
          margin-bottom: .18rem !important;
          margin-left: .18rem !important;
          white-space: normal !important;
          position: relative !important;
          z-index: 2 !important;
          flex: 0 1 auto !important;
        }}

        div[data-testid="stMultiSelect"] div[data-baseweb="tag"]:first-of-type {{
          margin-left: .42rem !important;
        }}

        div[data-baseweb="tag"] span {{
          white-space: normal !important;
          overflow: visible !important;
          text-overflow: clip !important;
          line-height: 1.18 !important;
        }}

        label p {{
          color: var(--ma-text) !important;
          font-weight: 650;
        }}

        hr {{
          border-color: var(--ma-stroke);
        }}

        .ma-card {{
          border: 1px solid var(--ma-stroke);
          background: var(--ma-panel);
          border-radius: 14px;
          padding: 1.05rem 1.15rem;
          min-height: 100%;
          box-shadow: none;
        }}

        .ma-feature-card {{
          min-height: 194px;
          border: 1px solid var(--ma-stroke);
          background: var(--ma-panel);
          border-radius: 14px;
          padding: 1rem 1.05rem;
          box-shadow: none;
          position: relative;
          overflow: hidden;
        }}

        .ma-feature-card::before {{
          content: "";
          position: absolute;
          inset: 0 0 auto 0;
          height: 4px;
          background: var(--ma-red);
        }}

        .ma-feature-index {{
          color: var(--ma-red);
          font-size: .68rem;
          font-weight: 800;
          letter-spacing: .12em;
          text-transform: uppercase;
          margin-bottom: .6rem;
        }}

        .ma-feature-card h3,
        .ma-card h3 {{
          margin: 0 0 .55rem 0 !important;
          color: var(--ma-text) !important;
        }}

        .ma-feature-card p,
        .ma-card p {{
          color: var(--ma-muted);
          font-size: .9rem;
          line-height: 1.52;
          margin-bottom: .75rem;
        }}

        .ma-feature-meta,
        .ma-meta {{
          color: var(--ma-muted);
          font-size: .84rem;
          line-height: 1.55;
        }}

        .ma-table-scroll {{
          width: 100%;
          max-width: 100%;
          overflow-x: auto;
          overscroll-behavior-x: contain;
          border-radius: 8px;
        }}

        .ma-table-scroll table {{
          min-width: 22rem;
        }}

        .ma-light-dataframe {{
          width: 100%;
          max-width: 100%;
          overflow: auto;
          border: 1px solid var(--ma-stroke);
          border-radius: 10px;
          background: var(--ma-panel);
        }}

        .ma-light-dataframe-table {{
          width: 100%;
          min-width: max-content;
          border-collapse: separate;
          border-spacing: 0;
          color: var(--ma-text);
          background: var(--ma-panel);
          font-family: {FONT_STACK};
          font-size: .82rem;
        }}

        .ma-light-dataframe-table th,
        .ma-light-dataframe-table td {{
          padding: .58rem .68rem;
          border-right: 1px solid var(--ma-stroke);
          border-bottom: 1px solid var(--ma-stroke);
          text-align: left;
          white-space: nowrap;
          vertical-align: middle;
        }}

        .ma-light-dataframe-table thead th {{
          position: sticky;
          top: 0;
          z-index: 1;
          color: var(--ma-muted);
          background: var(--ma-panel2);
          font-weight: 700;
        }}

        .ma-light-dataframe-table tbody th {{
          color: var(--ma-text);
          background: var(--ma-bg2);
          font-weight: 700;
        }}

        .ma-light-dataframe-table tr:last-child > * {{ border-bottom: 0; }}
        .ma-light-dataframe-table tr > *:last-child {{ border-right: 0; }}
        .ma-light-dataframe-table tbody tr:hover > * {{ background: var(--ma-panel2); }}

        .ma-progress-cell {{
          display: grid;
          grid-template-columns: minmax(3.5rem, 1fr) auto;
          align-items: center;
          gap: .5rem;
          min-width: 7rem;
        }}

        .ma-progress-track {{
          display: block;
          height: .42rem;
          overflow: hidden;
          border-radius: 99px;
          background: var(--ma-panel2);
        }}

        .ma-progress-track > span {{
          display: block;
          height: 100%;
          border-radius: inherit;
          background: var(--ma-red);
        }}

        .ma-chart-legend {{
          display: flex;
          flex-wrap: wrap;
          justify-content: center;
          gap: .5rem 1rem;
          margin-bottom: .5rem;
          font-size: .82rem;
        }}

        .ma-chart-legend-item {{
          display: inline-flex;
          align-items: center;
          gap: .35rem;
          max-width: 100%;
        }}

        .ma-chart-legend-swatch {{
          width: .7rem;
          height: .7rem;
          border-radius: 2px;
          flex: 0 0 auto;
        }}

        a:focus-visible,
        button:focus-visible,
        [tabindex="0"]:focus-visible {{
          outline: 2px solid var(--ma-teal) !important;
          outline-offset: 3px;
        }}

        /* CSS-only reflow: keep one widget tree and the native session state. */
        @media (max-width: 900px) {{
          [data-testid="stHorizontalBlock"] {{
            flex-direction: column;
            align-items: stretch;
            gap: .8rem !important;
          }}

          [data-testid="stHorizontalBlock"] > [data-testid="stColumn"],
          [data-testid="stHorizontalBlock"] > [data-testid="column"] {{
            flex: 1 1 auto !important;
            width: 100% !important;
            min-width: 0 !important;
            max-width: 100% !important;
          }}

          .ma-feature-card {{
            min-height: 0;
          }}

          .ma-brand-header {{
            display: grid;
            grid-template-columns: auto minmax(0, 1fr);
            gap: .65rem .85rem;
            padding: .8rem;
          }}

          .ma-brand-header > .shield {{
            grid-row: 1 / span 2;
          }}

          .ma-brand-divider {{
            display: none;
          }}

          .ma-brand-unit {{
            grid-column: 2;
          }}

          .ma-brand-text {{
            font-size: .7rem;
            white-space: normal;
          }}

          .ma-brand-mark {{
            width: 2.1rem;
            height: 2.1rem;
            font-size: .88rem;
          }}
        }}

        @media (max-width: 760px) {{
          [data-testid="stAppViewContainer"] > .main .block-container,
          [data-testid="stMainBlockContainer"],
          .stMainBlockContainer.block-container {{
            padding: 4.25rem .75rem 1.5rem;
          }}

          .ma-page-header {{
            padding: .85rem 1rem;
          }}

          h1 {{
            font-size: 1.45rem !important;
            overflow-wrap: anywhere;
          }}

          [data-testid="stMetricLabel"] p,
          [data-testid="stSidebar"] p,
          [data-testid="stSidebar"] span,
          [data-testid="stSidebar"] label,
          [data-testid="stSidebar"] li {{
            font-size: .9rem;
            line-height: 1.45;
          }}

          [data-testid="stMetricValue"],
          [data-testid="stMetricValue"] > div {{
            font-size: 1.65rem;
            white-space: normal;
            overflow-wrap: anywhere;
          }}

          [data-testid="stMarkdownContainer"],
          [data-testid="stAlert"],
          .ma-feature-meta,
          .ma-subtitle {{
            overflow-wrap: anywhere;
          }}

          button,
          [data-testid="stPageLink"] a,
          [data-testid="stSidebarNav"] a,
          [data-testid="stExpander"] summary,
          [data-testid="stCheckbox"] label,
          [data-testid="stRadio"] label,
          [data-baseweb="select"] > div,
          [data-baseweb="input"] {{
            min-height: 44px !important;
          }}

          [data-testid="stHeader"] button,
          [data-testid="stSidebar"] button {{
            min-width: 44px;
          }}

          input,
          textarea,
          [data-baseweb="select"] {{
            font-size: 16px !important;
          }}

          [data-testid="stTabs"] [role="tablist"] {{
            overflow-x: auto;
            max-width: 100%;
          }}

          [data-testid="stTabs"] [role="tab"] {{
            flex-shrink: 0;
          }}

          [data-testid="stDataFrame"],
          [data-testid="stPlotlyChart"] {{
            min-width: 0;
            max-width: 100%;
          }}
        }}

        .modebar-container,
        .modebar {{
          display: none !important;
          opacity: 0 !important;
          visibility: hidden !important;
          pointer-events: none !important;
        }}

        .modebar-container *,
        .modebar * {{
          display: none !important;
          opacity: 0 !important;
          visibility: hidden !important;
          pointer-events: none !important;
        }}

        ::-webkit-scrollbar {{
          width: 9px;
          height: 9px;
        }}

        ::-webkit-scrollbar-thumb {{
          background: var(--ma-stroke);
          border-radius: 7px;
        }}

        ::-webkit-scrollbar-track {{
          background: transparent;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def page_header(title: str, subtitle: str | None = None, eyebrow: str = "Аналитика Москвы") -> None:
    subtitle_html = f"<div class='ma-subtitle'>{subtitle}</div>" if subtitle else ""
    st.markdown(
        f"""
        <div class="ma-brand-header">
          <div class="ma-brand-mark shield">М</div>
          <div class="ma-brand-divider"></div>
          <div class="ma-brand-unit">
            <div class="ma-brand-mark circle">ГК</div>
            <div class="ma-brand-text">Градостроительный<br>комплекс Москвы</div>
          </div>
          <div class="ma-brand-unit">
            <div class="ma-brand-mark circle">ДГП</div>
            <div class="ma-brand-text">Департамент<br>градостроительной политики<br>города Москвы</div>
          </div>
        </div>
        <div class="ma-page-header">
          <div class="ma-eyebrow">{eyebrow}</div>
          <h1>{title}</h1>
          {subtitle_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def style_plotly(fig, *, height: int | None = None):
    """Apply final chart polish to a Plotly figure."""
    has_title = bool(fig.layout.title and fig.layout.title.text)
    grid = "rgba(100,116,139,.26)"
    zero = "rgba(100,116,139,.48)"
    existing_margin = fig.layout.margin.to_plotly_json() if fig.layout.margin else {}
    margin = {"l": 54, "r": 28, "t": 56, "b": 70}
    margin.update({k: v for k, v in existing_margin.items() if v is not None})
    layout_options = dict(
        autosize=True,
        template="moscow_business",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family=FONT_STACK, color=COLORS["text"]),
        hovermode="closest",
        margin=margin,
        legend=dict(
            orientation="h",
            yanchor="top",
            y=-0.18,
            xanchor="center",
            x=0.5,
            font=dict(size=12, color=COLORS["text"]),
        ),
        uniformtext=dict(minsize=10, mode="hide"),
    )
    # Supplying title_font without title text makes Plotly render the literal
    # word "undefined" in some Streamlit/Plotly combinations.
    if has_title:
        layout_options["title_font"] = dict(family=FONT_STACK, color=COLORS["text"])
    else:
        # An actual (invisible) text value is required here: with None or an
        # empty string Plotly.js may turn Streamlit's missing title into
        # visible "undefined" text.
        layout_options["title"] = dict(text="\u200b")
    fig.update_layout(**layout_options)
    if height is not None:
        fig.update_layout(height=height)
    fig.update_traces(cliponaxis=False, selector=dict(type="bar"))
    fig.update_xaxes(
        gridcolor=grid,
        zerolinecolor=zero,
        linecolor=COLORS["stroke"],
        tickfont=dict(color=COLORS["muted"]),
        title_font=dict(color=COLORS["muted"]),
        automargin=True,
    )
    fig.update_yaxes(
        gridcolor=grid,
        zerolinecolor=zero,
        linecolor=COLORS["stroke"],
        tickfont=dict(color=COLORS["muted"]),
        title_font=dict(color=COLORS["muted"]),
        automargin=True,
    )
    return fig
