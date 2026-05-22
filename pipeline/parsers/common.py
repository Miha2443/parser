"""Общие справочники для парсеров fedstat."""
from __future__ import annotations

MONTHS = {
    "январь": 1, "февраль": 2, "март": 3, "апрель": 4,
    "май": 5, "июнь": 6, "июль": 7, "август": 8,
    "сентябрь": 9, "октябрь": 10, "ноябрь": 11, "декабрь": 12,
}
MONTH_NAME_BY_NUM = {v: k for k, v in MONTHS.items()}

QUARTER_BY_MONTH = {m: (m - 1) // 3 + 1 for m in range(1, 13)}

# Точные сопоставления названий регионов, как их пишет fedstat (включая новые варианты).
REGION_CLEAN = {
    "Российская Федерация": "Российская Федерация",
    "Российская Федерация без учета новых субъектов (с 01.01.2023)": "Российская Федерация",
    "Российская Федерация без учета новых субъектов": "Российская Федерация",
    "Город Москва столица Российской Федерации город федерального значения": "Москва",
    "Москва": "Москва",
}

REGIONS_KEEP = {"Российская Федерация", "Москва"}


def clean_region(raw) -> str | None:
    """Нормализует «сырое» название региона в каноническое.

    Возвращает либо одно из REGIONS_KEEP, либо None (если регион нам не нужен).
    Эвристика покрывает любые будущие варианты «Российская Федерация ...» и «... Москва ...».
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    if s in REGION_CLEAN:
        return REGION_CLEAN[s]
    if s.startswith("Российская Федерация"):
        return "Российская Федерация"
    if "Москва" in s and ("столица" in s or s.strip() == "Москва"):
        return "Москва"
    return None


DATA_COLUMNS = [
    "section",
    "indicator_id",
    "indicator_title",
    "view",
    "region",
    "year",
    "month",
    "quarter",
    "period_type",
    "value",
    "unit",
    "source_file",
    "loaded_at",
]
