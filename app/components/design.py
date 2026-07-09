"""Shared visual system for the Streamlit dashboard."""
from __future__ import annotations

import plotly.io as pio
import streamlit as st


COLORS = {
    "navy": "#12324A",
    "blue": "#1F5E8C",
    "blue_2": "#376F95",
    "teal": "#2F8F9D",
    "cyan": "#58A6C6",
    "green": "#6C9F2E",
    "green_2": "#9DBB63",
    "red": "#B4233C",
    "amber": "#C57A2A",
    "ink": "#1F2933",
    "muted": "#667085",
    "line": "#D9E2EA",
    "surface": "#FFFFFF",
    "surface_2": "#F4F7FA",
    "sidebar": "#0B1F31",
}

SERIES = [
    COLORS["blue"],
    COLORS["red"],
    COLORS["teal"],
    COLORS["green"],
    COLORS["amber"],
    "#7B8794",
    "#4B5563",
    "#A8B5C2",
]


def _register_plotly_template() -> None:
    pio.templates["moscow_business"] = {
        "layout": {
            "font": {"family": "Inter, Segoe UI, Arial, sans-serif", "color": COLORS["ink"]},
            "paper_bgcolor": "rgba(0,0,0,0)",
            "plot_bgcolor": "rgba(0,0,0,0)",
            "colorway": SERIES,
            "margin": {"l": 18, "r": 18, "t": 44, "b": 34},
            "xaxis": {
                "gridcolor": COLORS["line"],
                "zerolinecolor": COLORS["line"],
                "linecolor": COLORS["line"],
                "tickcolor": COLORS["line"],
                "title": {"font": {"color": COLORS["muted"], "size": 12}},
            },
            "yaxis": {
                "gridcolor": COLORS["line"],
                "zerolinecolor": COLORS["line"],
                "linecolor": COLORS["line"],
                "tickcolor": COLORS["line"],
                "title": {"font": {"color": COLORS["muted"], "size": 12}},
            },
            "legend": {
                "orientation": "h",
                "yanchor": "bottom",
                "y": 1.02,
                "xanchor": "right",
                "x": 1,
                "font": {"size": 12},
            },
            "hoverlabel": {
                "bgcolor": "#FFFFFF",
                "bordercolor": COLORS["line"],
                "font": {"color": COLORS["ink"], "size": 12},
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
        :root {{
          --ma-navy: {COLORS["navy"]};
          --ma-blue: {COLORS["blue"]};
          --ma-red: {COLORS["red"]};
          --ma-ink: {COLORS["ink"]};
          --ma-muted: {COLORS["muted"]};
          --ma-line: {COLORS["line"]};
          --ma-bg: #EEF3F7;
          --ma-panel: #FFFFFF;
          --ma-sidebar: {COLORS["sidebar"]};
          --ma-sidebar-soft: #12324A;
          --ma-sidebar-line: rgba(255,255,255,.11);
        }}

        .stApp {{
          background: var(--ma-bg);
          color: var(--ma-ink);
        }}

        [data-testid="stAppViewContainer"] > .main .block-container {{
          max-width: 1540px;
          padding: .9rem 1.25rem 1.25rem;
        }}

        [data-testid="stSidebar"] {{
          background: var(--ma-sidebar);
          border-right: 1px solid rgba(255,255,255,.08);
        }}

        [data-testid="stSidebar"] [data-testid="stSidebarContent"] {{
          padding-top: 1rem;
        }}

        [data-testid="stSidebar"] a,
        [data-testid="stSidebar"] p,
        [data-testid="stSidebar"] span,
        [data-testid="stSidebar"] label {{
          font-size: 0.86rem;
        }}

        [data-testid="stSidebar"] a,
        [data-testid="stSidebar"] p,
        [data-testid="stSidebar"] span,
        [data-testid="stSidebar"] label,
        [data-testid="stSidebar"] li {{
          color: #D7E3EE !important;
        }}

        [data-testid="stSidebar"] h1,
        [data-testid="stSidebar"] h2,
        [data-testid="stSidebar"] h3,
        [data-testid="stSidebar"] strong {{
          color: #FFFFFF !important;
        }}

        [data-testid="stSidebar"] hr {{
          border-color: var(--ma-sidebar-line);
        }}

        [data-testid="stSidebar"] a {{
          border-radius: 7px;
          min-height: 2rem;
        }}

        [data-testid="stSidebar"] a:hover {{
          background: rgba(255,255,255,.08);
        }}

        [data-testid="stSidebar"] [aria-current="page"],
        [data-testid="stSidebar"] a[aria-current="page"] {{
          background: rgba(88,166,198,.22) !important;
          color: #FFFFFF !important;
          border-left: 3px solid {COLORS["cyan"]};
        }}

        [data-testid="stSidebar"] .stButton > button {{
          background: rgba(255,255,255,.08) !important;
          color: #FFFFFF !important;
          border-color: rgba(255,255,255,.18) !important;
        }}

        h1, h2, h3, h4 {{
          color: var(--ma-ink);
          letter-spacing: 0;
        }}

        h1 {{
          font-size: 1.62rem !important;
          line-height: 1.15 !important;
          font-weight: 760 !important;
          margin-bottom: 0.25rem !important;
        }}

        h2 {{
          font-size: 1.16rem !important;
          font-weight: 720 !important;
          margin-top: 1.1rem !important;
        }}

        h3 {{
          font-size: 1rem !important;
          font-weight: 700 !important;
          margin-top: .95rem !important;
        }}

        .ma-page-header {{
          margin-bottom: .85rem;
          padding: .8rem 1.05rem .85rem;
          border: 1px solid rgba(255,255,255,.08);
          border-radius: 8px;
          background: #12324A;
          box-shadow: 0 8px 20px rgba(18, 50, 74, .14);
        }}

        .ma-eyebrow {{
          color: #9ED3EA;
          font-size: 0.76rem;
          font-weight: 760;
          letter-spacing: .08em;
          text-transform: uppercase;
          margin-bottom: 0.2rem;
        }}

        .ma-page-header h1 {{
          color: #FFFFFF !important;
          margin-bottom: .45rem !important;
        }}

        .ma-subtitle {{
          color: #D7E3EE;
          font-size: 0.86rem;
          line-height: 1.35;
          max-width: 1040px;
        }}

        div[data-testid="stVerticalBlock"] > div[data-testid="stHorizontalBlock"] {{
          gap: 1rem;
        }}

        div[data-testid="stMetric"],
        div[data-testid="stDataFrame"],
        div[data-testid="stPlotlyChart"],
        div[data-testid="stForm"],
        div[data-testid="stVerticalBlockBorderWrapper"],
        div[data-testid="stExpander"] > details {{
          border-radius: 8px;
        }}

        div[data-testid="stMetric"] {{
          background: var(--ma-panel);
          border: 1px solid var(--ma-line);
          padding: .72rem .85rem;
          box-shadow: 0 5px 14px rgba(18, 50, 74, 0.05);
        }}

        div[data-testid="stMetricLabel"] p {{
          color: var(--ma-muted);
          font-size: .78rem;
          font-weight: 700;
          letter-spacing: .04em;
          text-transform: uppercase;
        }}

        div[data-testid="stMetricValue"] {{
          color: var(--ma-navy);
          font-weight: 760;
        }}

        div[data-testid="stPlotlyChart"] {{
          background: var(--ma-panel);
          border: 1px solid var(--ma-line);
          padding: .35rem .45rem;
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
          border: 1px solid var(--ma-line);
          background: var(--ma-panel);
          box-shadow: none;
        }}

        div[data-testid="stVerticalBlockBorderWrapper"] {{
          border-color: #C8D5E0 !important;
          background: #FFFFFF !important;
          box-shadow: none;
        }}

        div[data-testid="stExpander"] > details {{
          border-color: var(--ma-line);
          background: var(--ma-panel);
        }}

        div[data-testid="stTabs"] button {{
          font-weight: 650;
          color: var(--ma-muted);
        }}

        div[data-testid="stTabs"] button[aria-selected="true"] {{
          color: var(--ma-navy);
        }}

        .stButton > button,
        .stDownloadButton > button,
        [data-testid="stBaseButton-secondary"],
        [data-testid="stBaseButton-primary"] {{
          border-radius: 6px !important;
          border-color: var(--ma-line) !important;
          font-weight: 650 !important;
        }}

        [data-testid="stBaseButton-primary"] {{
          background: var(--ma-blue) !important;
          border-color: var(--ma-blue) !important;
        }}

        div[data-baseweb="select"] > div,
        div[data-baseweb="input"] > div,
        textarea,
        [data-testid="stFileUploader"] section {{
          border-radius: 6px !important;
          border-color: var(--ma-line) !important;
          background-color: #FFFFFF !important;
        }}

        label p {{
          color: var(--ma-ink);
          font-weight: 650;
        }}

        hr {{
          border-color: var(--ma-line);
        }}

        .ma-card {{
          border: 1px solid var(--ma-line);
          background: var(--ma-panel);
          border-radius: 12px;
          padding: 1.05rem 1.15rem;
          min-height: 100%;
          box-shadow: 0 10px 26px rgba(18, 50, 74, 0.07);
        }}

        .ma-feature-card {{
          min-height: 194px;
          border: 1px solid #C8D5E0;
          background: #FFFFFF;
          border-radius: 9px;
          padding: .95rem 1rem;
          box-shadow: 0 7px 18px rgba(18, 50, 74, .06);
          position: relative;
          overflow: hidden;
        }}

        .ma-feature-card::before {{
          content: "";
          position: absolute;
          inset: 0 0 auto 0;
          height: 4px;
          background: {COLORS["blue"]};
        }}

        .ma-feature-index {{
          color: {COLORS["blue"]};
          font-size: .76rem;
          font-weight: 800;
          letter-spacing: .08em;
          text-transform: uppercase;
          margin-bottom: .55rem;
        }}

        .ma-feature-card h3 {{
          margin: 0 0 .55rem 0 !important;
          font-size: 1.08rem !important;
          color: var(--ma-navy);
        }}

        .ma-feature-card p {{
          color: #455466;
          font-size: .92rem;
          line-height: 1.5;
          margin-bottom: .8rem;
        }}

        .ma-feature-meta {{
          color: #344054;
          font-size: .86rem;
          line-height: 1.55;
        }}

        .ma-card h3 {{
          margin: 0 0 .45rem 0 !important;
          font-size: 1.05rem !important;
          color: var(--ma-navy);
        }}

        .ma-card p {{
          color: var(--ma-muted);
          font-size: .9rem;
          line-height: 1.45;
        }}

        .ma-meta {{
          color: var(--ma-muted);
          font-size: .86rem;
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
        </style>
        """,
        unsafe_allow_html=True,
    )


def page_header(title: str, subtitle: str | None = None, eyebrow: str = "Аналитика Москвы") -> None:
    subtitle_html = f"<div class='ma-subtitle'>{subtitle}</div>" if subtitle else ""
    st.markdown(
        f"""
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
    fig.update_layout(
        template="moscow_business",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Inter, Segoe UI, Arial, sans-serif", color=COLORS["ink"]),
        hovermode="closest",
    )
    if height is not None:
        fig.update_layout(height=height)
    fig.update_xaxes(gridcolor=COLORS["line"], zerolinecolor=COLORS["line"])
    fig.update_yaxes(gridcolor=COLORS["line"], zerolinecolor=COLORS["line"])
    return fig
