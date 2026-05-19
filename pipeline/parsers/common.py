"""Общие справочники для парсеров fedstat."""
from __future__ import annotations

MONTHS = {
    "январь": 1, "февраль": 2, "март": 3, "апрель": 4,
    "май": 5, "июнь": 6, "июль": 7, "август": 8,
    "сентябрь": 9, "октябрь": 10, "ноябрь": 11, "декабрь": 12,
}
MONTH_NAME_BY_NUM = {v: k for k, v in MONTHS.items()}

QUARTER_BY_MONTH = {m: (m - 1) // 3 + 1 for m in range(1, 13)}

REGION_CLEAN = {
    "Российская Федерация": "Российская Федерация",
    "Российская Федерация без учета новых субъектов (с 01.01.2023)":
        "Российская Федерация без учета новых субъектов",
    "Город Москва столица Российской Федерации город федерального значения": "Москва",
}

REGIONS_KEEP = {"Российская Федерация", "Москва"}

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
