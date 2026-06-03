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
    source: str                 # "fedstat" / "rosstat" / "nashdom" / "erzrf" / "manual"
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
        source_ids=["43246", "57824"],
        parser="fedstat_salary",
        file_patterns=["*Среднемесячная номинальная начисленная заработная плата*.xls*"],
        page="1_Заработная_плата.py",
        ytd_mode="from_source",
        description="fedstat 43246 (по 2016 г., 2011-2016) + 57824 (с 2017 г.). Москва + РФ, отрасли «Всего» и «Строительство».",
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
        description=(
            "fedstat 31074 (часть1: 2011-2018, часть2: 2019-2026 — деление вынужденное, .xls "
            "ограничен 256 колонками). Москва + РФ (включая код 57831_1849012, нормализуется "
            "к «Российская Федерация»), индекс к пред. месяцу и YTD к АППГ."
        ),
    ),
    Indicator(
        id="gdp_rf",
        section="national_accounts",
        title="ВВП Российской Федерации (годовой)",
        unit="млрд руб",
        source="rosstat",
        source_ids=["vvp_god"],
        parser="rosstat_gdp_year",
        file_patterns=["*VVP_god*.xls*", "*ВВП годы*1995*.xls*"],
        page="",
        description="Росстат, страница statistics/accounts. Файл VVP_god_s1995-*.xlsx (транслит).",
    ),
    Indicator(
        id="gdp_per_capita_rf",
        section="national_accounts",
        title="ВВП на душу населения, РФ",
        unit="руб",
        source="rosstat",
        source_ids=["vvp_na_dushu"],
        parser="rosstat_gdp_per_capita",
        file_patterns=["*VVP_na_dushu*.xls*", "*ВВП на душу*.xls*"],
        page="",
        description="Росстат, страница statistics/accounts. Файл VVP_na_dushu_s1995-*.xlsx (транслит).",
    ),
    Indicator(
        id="vrp_msk",
        section="national_accounts",
        title="ВРП города Москвы",
        unit="млн руб",
        source="rosstat",
        source_ids=["vrp_msk"],
        parser="rosstat_vrp_msk",
        file_patterns=["*ВРП с 1998*.xls*"],
        page="",
        description="Мосстат, страница folder/134924. Файл «ВРП с 1998 года.xlsx» — 5 строк показателей на каждом из двух листов.",
    ),
    Indicator(
        id="vds_msk",
        section="national_accounts",
        title="ВДС Москвы по отраслям (с 2016 г.)",
        unit="млрд руб",
        source="rosstat",
        source_ids=["vds_msk_s2016"],
        parser="rosstat_vds_msk",
        file_patterns=["*ВДС годы ОКВЭД2*2016*.xls*"],
        page="",
        description="Мосстат, страница folder/134924. Файл «ВДС годы ОКВЭД2 (с 2016 г.).xlsx».",
    ),
    Indicator(
        id="vds_rf",
        section="national_accounts",
        title="ВДС РФ по отраслям (с 2011 г.)",
        unit="млрд руб",
        source="rosstat",
        source_ids=["vds_rf_s2011"],
        parser="rosstat_vds_rf",
        file_patterns=["*VDS_god_OKVED2_s2011*.xls*", "*ВДС годы ОКВЭД2*2011*.xls*"],
        page="",
        description="Росстат, страница statistics/accounts. Файл VDS_god_OKVED2_s2011-*.xlsx (транслит).",
    ),
    Indicator(
        id="vds_msk_legacy",
        section="national_accounts",
        title="Отраслевая структура ВДС Москвы, ОКВЭД-2007 (2011-2015)",
        unit="%",
        source="rosstat",
        source_ids=["vrp_okved2007"],
        parser="rosstat_vds_msk_legacy",
        file_patterns=["*VRP_OKVED2007*.xls*", "*ВРП ОКВЭД 2007*.xls*"],
        page="",
        description=(
            "Росстат statistics/accounts, файл «ВРП ОКВЭД 2007 (с 2004 г.)». "
            "Листы «2. 2011»…«2. 2015» — доли ВДС Москвы (% к итогу) в старом "
            "ОКВЭД-2007. Дополняет vds_structure Москвы за 2011-2015 (vds_msk даёт 2016+)."
        ),
    ),
    # ─── Волна 4: недвижимость ───────────────────────────────────────────
    Indicator(
        id="realty_monitoring_2_0",
        section="realty",
        title="Мониторинг новостроек 2.0",
        unit="шт",
        source="nashdom",
        source_ids=["monitoring_2_0"],
        parser="nashdom_monitoring_2_0",
        file_patterns=["realty/nashdom/monitoring_2_0_*.xlsx"],
        page="",
        description="наш.дом.рф/аналитика/мониторинг-новостроек-2-0. Скачивается целиком ежедневно.",
    ),
    Indicator(
        id="realty_rasprodannost",
        section="realty",
        title="Распроданность новостроек",
        unit="%",
        source="nashdom",
        source_ids=["rasprodannost"],
        parser="nashdom_rasprodannost",
        file_patterns=["realty/nashdom/rasprodannost_*.json"],
        page="",
        description="наш.дом.рф/аналитика/распроданность-новостроек. DOM-скрейп, 5 классов (Все+Типовой+Комфорт+Бизнес+Элитный), ТОП-100+Все.",
    ),
    Indicator(
        id="realty_kvartirografia",
        section="realty",
        title="Квартирография новостроек",
        unit="%",
        source="nashdom",
        source_ids=["kvartirografia"],
        parser="nashdom_kvartirografia",
        file_patterns=["realty/nashdom/kvartirografia_*.json"],
        page="",
        description="наш.дом.рф/аналитика/квартирография-новостроек. DOM-скрейп, 5 классов, ТОП-100+Все.",
    ),
    Indicator(
        id="realty_erzrf_top_rf",
        section="realty",
        title="ТОП застройщиков ЕРЗ (Россия)",
        unit="м²",
        source="erzrf",
        source_ids=["top_rf"],
        parser="erzrf_top",
        file_patterns=["realty/erzrf/top_*_rf_*.xlsx"],
        page="",
        description="erzrf.ru/top-zastroyshchikov/. Сортировка «по объёму строительства» (default), регион Россия. Список SORTINGS в erzrf_checker.py расширяется по результату Шага 0.",
    ),
    Indicator(
        id="realty_erzrf_top_msk",
        section="realty",
        title="ТОП застройщиков ЕРЗ (Москва)",
        unit="м²",
        source="erzrf",
        source_ids=["top_msk"],
        parser="erzrf_top",
        file_patterns=["realty/erzrf/top_*_msk_*.xlsx"],
        page="",
        description="erzrf.ru/top-zastroyshchikov/. Сортировка «по объёму строительства», фильтр Москва.",
    ),
    Indicator(
        id="realty_erzrf_cards",
        section="realty",
        title="Карточки ТОП-100 застройщиков ЕРЗ",
        unit="—",
        source="erzrf",
        source_ids=["cards"],
        parser="erzrf_cards",
        file_patterns=["realty/erzrf/cards/cards_*.xlsx"],
        page="",
        description="erzrf.ru/zastroyschiki/<slug>. DOM-скрейп карточек: имя/регионы/Сдано-Перенос-Уточнение по годам/рейтинги.",
    ),
    Indicator(
        id="realty_escrow_manual",
        section="realty",
        title="Наполненность счетов эскроу",
        unit="млн руб",
        source="manual",
        source_ids=["escrow"],
        parser="escrow_manual",
        file_patterns=["realty/escrow_manual/*.xlsx"],
        page="",
        description="Файл кладётся вручную пользователем в data/raw/realty/escrow_manual/. Скачивателя нет.",
    ),
]


REGIONS_KEEP = {"Российская Федерация", "Москва"}


def by_id(indicator_id: str) -> Indicator:
    for ind in INDICATORS:
        if ind.id == indicator_id:
            return ind
    raise KeyError(indicator_id)
