"""Кнопки экспорта таблиц (xlsx/csv) и графиков (png)."""
from __future__ import annotations

import io

import pandas as pd
import plotly.graph_objects as go
import streamlit as st


def table_download_buttons(df: pd.DataFrame, *, name: str, key_prefix: str) -> None:
    """Две кнопки рядом: скачать xlsx и csv."""
    col1, col2 = st.columns(2)
    with col1:
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="data")
        st.download_button(
            "Скачать Excel",
            data=buf.getvalue(),
            file_name=f"{name}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key=f"{key_prefix}_xlsx",
            width="stretch",
        )
    with col2:
        st.download_button(
            "Скачать CSV",
            data=df.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"{name}.csv",
            mime="text/csv",
            key=f"{key_prefix}_csv",
            width="stretch",
        )


def chart_download_button(fig: go.Figure, *, name: str, key: str) -> None:
    """PNG-экспорт графика через kaleido. Если пакета нет — тихо пропускаем."""
    try:
        import kaleido  # noqa: F401
    except ImportError:
        st.caption("PNG-экспорт недоступен — установите `kaleido==0.2.1`.")
        return
    try:
        png_bytes = fig.to_image(format="png", width=1200, height=600, scale=2)
    except Exception as exc:  # noqa: BLE001
        st.caption(f"PNG-экспорт недоступен: {exc}")
        return
    st.download_button(
        "Скачать график (PNG)",
        data=png_bytes,
        file_name=f"{name}.png",
        mime="image/png",
        key=key,
    )
