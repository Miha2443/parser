"""Streamlit facade: stable page API, data processing lives in pipeline.data_access."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import pandas as pd
import streamlit as st

from pipeline.data_access import DataAccess, DataContext
from pipeline.download_provenance import source_provenance, download_summary
from pipeline.data_access import (
    MONTH_NAMES_RU,
    MONTH_SHORT_RU,
    QUARTER_NAMES_RU,
    QUARTER_ROMAN,
    NA_INDICATORS,
    ERZ_NAKOPL_SCHEMA_VERSION,
    month_label,
    quarter_label,
    format_thousands,
    _add_january_ytd,
    latest_loaded_at,
    latest_period,
    _parse_kvart_number,
    _kvart_empty,
    _kvart_df,
    _kvart_normalize_frames,
    _kvart_name_looks_like_region,
    _kvart_developers_look_like_regions,
    _kvart_payload_is_valid,
    _load_kvartirografia_json,
    _load_kvartirografia_xlsx,
    _load_kvartirografia_file,
    _normalize_developer_name,
    _erz_name_col,
    _erz_has_commissioned_col,
    _erz_commissioned_area_col,
    _erz_nakopl_valid,
    _erz_nakopl_source_matches,
    _refresh_erz_all_developers,
    _parse_rasprod_number,
    _to_float,
    _extract_year,
    _year_rows,
    _emiss_period_from_label,
    _emiss_region_name,
    _parse_emiss_34118_periods_file,
    _parse_emiss_34118_file,
    monitoring_by_year,
)

@lru_cache(maxsize=16)
def _access_for_context(context: DataContext) -> DataAccess:
    return DataAccess(context)


def get_data_access() -> DataAccess:
    return _access_for_context(DataContext.from_environment())


@st.cache_data(show_spinner=False, ttl=300)
def _cached_load(loader_name: str, context_key: tuple, version: tuple, arguments: tuple):
    # version intentionally participates in Streamlit's key before calling core.
    access = DataAccess(DataContext(*context_key))
    value = getattr(access, loader_name)(*arguments)
    return value, access.issues


def _load(loader_name: str, *arguments):
    access = get_data_access()
    value, issues = _cached_load(loader_name, access.context.cache_key(),
                                access.data_version(loader_name, *arguments), arguments)
    for level, message in issues:
        getattr(st, level)(message)
    return value


# Compatibility paths for pages importing them; PARSER_ROOT is resolved by core.
PROJECT_ROOT = get_data_access().PROJECT_ROOT
DATA_PROCESSED = get_data_access().DATA_PROCESSED
DATA_DERIVED = get_data_access().DATA_DERIVED
DATA_MARTS_REALTY = get_data_access().DATA_MARTS_REALTY


def latest_realty_mart_source_date(name: str) -> str:
    return get_data_access().latest_realty_mart_source_date(name)


def latest_raw_source_date(*patterns: str, base: Path | None = None) -> str:
    return get_data_access().latest_raw_source_date(*patterns, base=base)


def load_indicator(indicator_id: str) -> pd.DataFrame:
    return _load('load_indicator', indicator_id)


def _load_derived() -> pd.DataFrame:
    return _load('_load_derived')


def load_salary() -> pd.DataFrame:
    return _load('load_salary')


def load_ipc() -> pd.DataFrame:
    return _load('load_ipc')


def dataset_source_provenance(df: pd.DataFrame) -> pd.DataFrame:
    return source_provenance(df, get_data_access().DOWNLOADS_DIR)


def dataset_download_summary(df: pd.DataFrame) -> str:
    return download_summary(dataset_source_provenance(df))


def show_dataset_sources(df: pd.DataFrame) -> None:
    with st.expander("Исходные файлы и даты скачивания", expanded=False):
        st.write("Дата файла — время изменения локального файла, а не подтверждённое скачивание. "
                 "Период данных указан отдельно для каждого исходника.")
        st.dataframe(dataset_source_provenance(df), hide_index=True, width="stretch")


def load_national_accounts() -> pd.DataFrame:
    return _load('load_national_accounts')


def load_kvartirografia() -> dict:
    return _load('load_kvartirografia')


def load_monitoring_2_0() -> dict:
    return _load('load_monitoring_2_0')


def load_erzrf_top() -> dict:
    return _load('load_erzrf_top')


def load_erzrf_cards() -> pd.DataFrame:
    return _load('load_erzrf_cards')


def load_escrow_manual() -> pd.DataFrame:
    return _load('load_escrow_manual')


def load_rasprodannost() -> dict:
    return _load('load_rasprodannost')


def load_construction_operational() -> dict:
    return _load('load_construction_operational')


def load_vvod_static() -> dict:
    return _load('load_vvod_static')


def load_emiss_34118() -> pd.DataFrame:
    return _load('load_emiss_34118')


def load_emiss_34118_periods() -> pd.DataFrame:
    return _load('load_emiss_34118_periods')


def load_monitoring_2011_2026_static() -> dict[str, pd.DataFrame]:
    return _load('load_monitoring_2011_2026_static')


def load_monitoring_operational_history() -> dict[str, object]:
    return _load('load_monitoring_operational_history')
