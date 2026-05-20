"""Реестр показателей. Источник истины для оркестратора и UI.

Добавление нового показателя:
1. Создать парсер `pipeline/parsers/<name>.py` с функцией `parse(paths) -> DataFrame`.
2. Создать страницу `app/pages/<N>_<имя>.py` (опционально, иначе показатель только в данных).
3. Добавить запись `Indicator(...)` в `INDICATORS` ниже.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence


@dataclass(frozen=True)
class Indicator:
    id: str
    section: str
    title: str
    unit: str
    source: str                 # "fedstat" / "domrf" / "domrf_mortgage"
    source_ids: Sequence[str]   # ID на источнике (для fedstat — ключ из INDICATORS словаря)
    parser: str                 # имя модуля в pipeline/parsers/
    file_patterns: Sequence[str]  # паттерны имени xls в downloads/ для поиска свежего файла
    page: str = ""              # имя страницы в app/pages/ (пустое = только в данных)
    ytd_mode: str = "from_source"  # from_source / ytd_sum / ytd_avg
    description: str = ""


INDICATORS: list[Indicator] = [
    Indicator(
        id="avg_salary",
        section="employment_salary",
        title="Среднемесячная номинальная начисленная заработная плата",
        unit="руб",
        source="fedstat",
        source_ids=["57824"],
        parser="fedstat_salary",
        file_patterns=["*Среднемесячная номинальная начисленная заработная плата*.xls*"],
        page="1_Заработная_плата.py",
        ytd_mode="from_source",
        description="fedstat 57824. Москва + РФ, отрасли «Всего» и «Строительство».",
    ),
    Indicator(
        id="ipc",
        section="prices",
        title="Индексы потребительских цен на товары и услуги",
        unit="%",
        source="fedstat",
        source_ids=["31074_часть1", "31074_часть2"],
        parser="fedstat_ipc",
        file_patterns=[
            "*Индексы потребительских цен*часть1*.xls*",
            "*Индексы потребительских цен*часть2*.xls*",
        ],
        page="2_ИПЦ.py",
        ytd_mode="from_source",
        description="fedstat 31074 (части 1 и 2). Москва + РФ, индекс к пред. месяцу и YTD к АППГ.",
    ),
]


REGIONS_KEEP = {"Российская Федерация", "Москва"}


def by_id(indicator_id: str) -> Indicator:
    for ind in INDICATORS:
        if ind.id == indicator_id:
            return ind
    raise KeyError(indicator_id)
