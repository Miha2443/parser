"""Shared visual system for the Streamlit dashboard."""
from __future__ import annotations

import plotly.io as pio
import streamlit as st


COLORS = {
    "bg": "#0C141D",
    "bg_2": "#101923",
    "panel": "#1A2634",
    "panel_2": "#212F40",
    "stroke": "#2B3D50",
    "text": "#E8EEF4",
    "muted": "#8BA0B5",
    "red": "#E84C4C",
    "red_soft": "rgba(232,76,76,.9)",
    "gray_bar": "#94A9BC",
    "navy_bar": "#3B566F",
    "blue": "#3B566F",
    "blue_2": "#4B6D8A",
    "navy": "#17222E",
    "teal": "#6FB3D2",
    "cyan": "#53A6C9",
    "green": "#2FBF71",
    "green_2": "#A4C866",
    "amber": "#D39B46",
    "ink": "#E8EEF4",
    "line": "#2B3D50",
    "surface": "#1A2634",
    "surface_2": "#212F40",
    "sidebar": "#1A2634",
}

SERIES = [
    COLORS["red"],
    COLORS["blue"],
    COLORS["teal"],
    COLORS["green"],
    COLORS["amber"],
    COLORS["gray_bar"],
    "#C6D4E0",
    "#7D91A5",
]

FONT_STACK = "Montserrat, Segoe UI, Roboto, Arial, sans-serif"


def _register_plotly_template() -> None:
    pio.templates["moscow_business"] = {
        "layout": {
            "font": {"family": FONT_STACK, "color": COLORS["text"]},
            "paper_bgcolor": "rgba(0,0,0,0)",
            "plot_bgcolor": "rgba(0,0,0,0)",
            "colorway": SERIES,
            "margin": {"l": 18, "r": 18, "t": 44, "b": 34},
            "xaxis": {
                "gridcolor": "rgba(43,61,80,.72)",
                "zerolinecolor": "rgba(43,61,80,.9)",
                "linecolor": COLORS["stroke"],
                "tickcolor": COLORS["stroke"],
                "tickfont": {"color": COLORS["muted"], "size": 11},
                "title": {"font": {"color": COLORS["muted"], "size": 12}},
            },
            "yaxis": {
                "gridcolor": "rgba(43,61,80,.72)",
                "zerolinecolor": "rgba(43,61,80,.9)",
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
                "font": {"size": 12, "color": "#C6D4E0"},
            },
            "hoverlabel": {
                "bgcolor": "#0E1720",
                "bordercolor": COLORS["stroke"],
                "font": {"color": COLORS["text"], "size": 12},
            },
        }
    }
    pio.templates.default = "moscow_business"


def apply_theme() -> None:
    """Apply global CSS and Plotly defaults for all dashboard pages."""
    _register_plotly_template()
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

        [data-testid="stHeader"],
        [data-testid="stToolbar"],
        [data-testid="stDecoration"],
        #MainMenu,
        footer {{
          display: none !important;
          height: 0 !important;
          visibility: hidden !important;
          pointer-events: none !important;
        }}

        [data-testid="stAppViewContainer"] > .main .block-container,
        [data-testid="stMainBlockContainer"],
        .stMainBlockContainer.block-container {{
          max-width: 1540px;
          padding: .65rem 1.35rem 1.35rem;
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
          color: #D8E3EE !important;
          font-size: .82rem;
        }}

        [data-testid="stSidebar"] h1,
        [data-testid="stSidebar"] h2,
        [data-testid="stSidebar"] h3,
        [data-testid="stSidebar"] strong {{
          color: #FFFFFF !important;
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
          color: #FFFFFF !important;
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

        .ma-page-header {{
          position: relative;
          overflow: hidden;
          margin-bottom: 1rem;
          padding: 1rem 1.2rem 1.1rem;
          border: 1px solid var(--ma-stroke);
          border-radius: 14px;
          background:
            linear-gradient(120deg, #16222f 0%, #1c2c3d 56%, #233b52 100%);
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
          background:
            linear-gradient(120deg, rgba(35,54,72,.96), rgba(24,38,52,.96));
          box-shadow: 0 14px 30px rgba(0,0,0,.18);
        }}

        .ma-brand-mark {{
          display: inline-flex;
          align-items: center;
          justify-content: center;
          width: 2.55rem;
          height: 2.55rem;
          flex: 0 0 auto;
          border: 2px solid rgba(255,255,255,.82);
          color: #FFFFFF;
          font-size: 1.05rem;
          font-weight: 800;
          line-height: 1;
        }}

        .ma-brand-mark.shield {{
          border-radius: .45rem .45rem .9rem .9rem;
        }}

        .ma-brand-mark.circle {{
          border-radius: 50%;
        }}

        .ma-brand-text {{
          color: #FFFFFF;
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
          background: rgba(255,255,255,.28);
          flex: 0 0 auto;
        }}

        @media (max-width: 760px) {{
          .ma-brand-header {{
            width: 100%;
            gap: .7rem;
            padding-right: .85rem;
          }}

          .ma-brand-text {{
            font-size: .62rem;
            white-space: normal;
          }}

          .ma-brand-mark {{
            width: 2.1rem;
            height: 2.1rem;
            font-size: .88rem;
          }}
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
          color: var(--ma-muted);
          font-size: .68rem;
          font-weight: 760;
          letter-spacing: .14em;
          text-transform: uppercase;
          margin-bottom: .42rem;
        }}

        .ma-page-header h1 {{
          color: #FFFFFF !important;
          margin-bottom: .45rem !important;
        }}

        .ma-subtitle {{
          color: #AFC0CF;
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
          border-color: var(--ma-stroke) !important;
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
          background: rgba(232,76,76,.16) !important;
          border: 1px solid rgba(232,76,76,.42) !important;
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
          color: #AFC0CF;
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
          <div class="ma-brand-mark circle">ГК</div>
          <div class="ma-brand-text">Градостроительный<br>комплекс Москвы</div>
          <div class="ma-brand-mark circle">ДГП</div>
          <div class="ma-brand-text">Департамент<br>градостроительной политики<br>города Москвы</div>
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
    existing_margin = fig.layout.margin.to_plotly_json() if fig.layout.margin else {}
    margin = {"l": 54, "r": 28, "t": 56, "b": 70}
    margin.update({k: v for k, v in existing_margin.items() if v is not None})
    fig.update_layout(
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
    if height is not None:
        fig.update_layout(height=height)
    fig.update_traces(cliponaxis=False, selector=dict(type="bar"))
    fig.update_xaxes(
        gridcolor="rgba(43,61,80,.72)",
        zerolinecolor="rgba(43,61,80,.9)",
        linecolor=COLORS["stroke"],
        tickfont=dict(color=COLORS["muted"]),
        title_font=dict(color=COLORS["muted"]),
        automargin=True,
    )
    fig.update_yaxes(
        gridcolor="rgba(43,61,80,.72)",
        zerolinecolor="rgba(43,61,80,.9)",
        linecolor=COLORS["stroke"],
        tickfont=dict(color=COLORS["muted"]),
        title_font=dict(color=COLORS["muted"]),
        automargin=True,
    )
    return fig
