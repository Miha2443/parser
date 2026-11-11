"""Moscow permit views sharing the same historical and registry facts."""
from __future__ import annotations

import copy
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import re
import threading
import time
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile
from zlib import error as DecompressionError

import pandas as pd
from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from backend.profile_service import FilterUnavailable, SourceUnavailable
from pipeline.construction_calculations import catalog, report
from pipeline.data_access import DataAccess, DataContext, file_signature
from pipeline.permit_page_calculations import (OKS_SHEET, apply_main_presentation, breakdowns, main_presentation, read_breakdown_sources,
                                               read_forecast, read_main_sources,
                                               select_forecast, structure_base, structure_controls)

CALCULATION_REVISION = "permits-history-2011-2021-oks-from-2022-v4"


_SOURCE_ERRORS = (OSError, ValueError, TypeError, LookupError, RuntimeError, OverflowError,
                  BadZipFile, InvalidFileException, ParseError, EOFError, DecompressionError)
try:
    from lxml.etree import XMLSyntaxError
except ImportError:
    pass
else:
    _SOURCE_ERRORS += (XMLSyntaxError,)


def _date(path):
    match = re.search(r"_(\d{8})$", path.stem)
    if match:
        try:
            return datetime.strptime(match[1], "%Y%m%d").date().isoformat()
        except ValueError:
            pass
    return None


def _default_month(periods, source_date, fallback=None):
    snapshot = tuple(map(int, source_date[:7].split("-"))) if source_date else None
    available = [period for period in periods if snapshot is None or period <= snapshot]
    if available:
        return max(available)[1]
    if snapshot:
        return snapshot[1]
    if fallback is not None:
        return fallback
    raise SourceUnavailable("Permit source has no available month observations")


class PermitSelectionInvalid(ValueError):
    """Syntactically invalid structure selection, distinct from unavailable source filters."""


class PermitsPageService:
    def __init__(self, context: DataContext, check_interval: float = 5):
        self.context = context
        self.check_interval = check_interval
        self._lock = threading.RLock()
        self._checked = 0.0
        self._signature = None
        self._generation = None

    def _inputs(self):
        access = DataAccess(replace(self.context, use_marts=False, require_marts=False))
        signatures = [(path, file_signature(path)) for path in access.source_files("monitoring_2_0")]
        if not signatures or any(signature[1] is None for _, signature in signatures):
            raise SourceUnavailable("Monitoring workbook is missing")
        newest = max(signature[2] for _, signature in signatures)
        selected = [(path, signature) for path, signature in signatures if signature[2] == newest]
        if len(selected) != 1:
            raise SourceUnavailable("Latest monitoring workbook is ambiguous")
        path, signature = selected[0]
        return path, (signature,), [path]

    def _relative(self, path):
        try:
            return path.resolve().relative_to(self.context.root).as_posix()
        except ValueError:
            return path.name

    def _refresh(self):
        now = time.monotonic()
        if self._generation is not None and now - self._checked < self.check_interval:
            return
        path, signature, files = self._inputs()
        if signature == self._signature:
            self._checked = now
            return
        with path.open("rb") as stream:
            book = load_workbook(stream, read_only=True, data_only=True)
            try:
                if OKS_SHEET not in book.sheetnames:
                    raise SourceUnavailable("Primary permit registry is missing")
                headers = {cell.value for cell in next(book[OKS_SHEET].iter_rows(max_row=1))}
                if not {"Назначение", "Срок выдачи РС", "Год выдачи", "Общая площадь", "Жилая площадь"} <= headers:
                    raise SourceUnavailable("Primary permit columns are missing")
                forecast = read_forecast(book, self._relative(path))
                detailed = read_breakdown_sources(book)
                main_sources = read_main_sources(book, detailed, self._relative(path))
            finally:
                book.close()
        # The table shell also comes from the canonical source, not the legacy
        # operational loader (which serves other pages and has different controls).
        operational = {"permits": pd.DataFrame(
            [{"year": year, "month": month, "kind": kind, "value_thousand_m2": row["value"]}
             for kind in ("housing", "nonresidential")
             for (year, month), row in main_sources["monthly"][kind].items()],
            columns=["year", "month", "kind", "value_thousand_m2"]),
            "permit_annual": pd.DataFrame(
                [{"year": year, "kind": kind, "value_thousand_m2": row["value"]}
                 for (year, kind), row in main_sources["annual"].items() if kind != "housing_total"],
                columns=["year", "kind", "value_thousand_m2"])}
        if operational["permits"].empty:
            raise SourceUnavailable("Monitoring contains no permit observations")
        data = {"load_construction_operational": operational, "load_rasprodannost": {}}
        discovered = catalog(data)
        controls = {"permitKinds": sorted(discovered["permitKinds"], key=lambda row:
                                         ("housing", "nonresidential", "total").index(row["id"])),
                    "monthsByKind": {kind: list(range(1, 13)) for kind in ("housing", "nonresidential", "total")}}
        issues = []
        if not forecast["rows"]:
            issues.append({"level": "warning", "message": "Прогноз недоступен; см. forecast.notes."})
        payload = {"schemaVersion": 1, "contractRevision": 2, "calculationRevision": CALCULATION_REVISION,
                   "version": hashlib.sha256(repr((CALCULATION_REVISION, signature)).encode()).hexdigest(),
                   "generatedAt": datetime.now(timezone.utc).isoformat(),
                   "source": {"date": _date(path), "files": [self._relative(item) for item in files],
                              "fileEvidence": "selected", "issues": issues},
                   "controls": controls}
        structures = structure_base(detailed, payload["source"]["date"], main_sources)
        source_default = _default_month([(int(row.year), int(row.month))
                                         for row in operational["permits"].itertuples()], payload["source"]["date"])
        default_months, structure_months = {}, {}
        # Availability defaults do not restrict the twelve explicit calendar choices.
        for kind in controls["monthsByKind"]:
            kinds = ("housing", "nonresidential") if kind == "total" else (kind,)
            periods = {period for k in kinds for period, row in main_sources["monthly"][k].items()
                       if row["value"] is not None}
            default_months[kind] = _default_month(periods, payload["source"]["date"],
                                                  source_default)
            block_id = {"housing": "housing_components", "nonresidential": "nonresidential_components",
                        "total": "area_composition"}[kind]
            block = structures[block_id]
            fields = ["nonresidential"] if kind == "nonresidential" else [field for field, _ in block["fields"]]
            periods = [period for period, row in block["monthly"].items()
                       if any(row.get(field) is not None for field in fields)]
            structure_months[kind] = _default_month(periods, payload["source"]["date"], default_months[kind])
        generation = {"payload": payload, "data": data, "forecast": forecast,
                      "structures": structures, "main_sources": main_sources,
                      "default_months": default_months, "structure_months": structure_months}
        json.dumps([payload, forecast], allow_nan=False)
        if self._inputs()[1] != signature:
            raise SourceUnavailable("Permit sources changed during loading")
        self._generation, self._signature, self._checked = generation, signature, now

    def report(self, permit_kind="total", month=None, *, structure_mode="period", structure_month_from=1,
               structure_month_to=None, structure_year_from=None, structure_year_to=None):
        if permit_kind not in ("housing", "nonresidential", "total") or (month is not None and
                (isinstance(month, bool) or not isinstance(month, int) or not 1 <= month <= 12)):
            raise FilterUnavailable("Permit filter is unavailable")
        if structure_mode not in ("period", "annual"):
            raise PermitSelectionInvalid("Structure mode is invalid")
        for value in (structure_month_from, structure_month_to):
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 12):
                raise PermitSelectionInvalid("Structure month is invalid")
        if structure_month_from is None:
            raise PermitSelectionInvalid("Structure start month is required")
        for value in (structure_year_from, structure_year_to):
            if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
                raise PermitSelectionInvalid("Structure year is invalid")
        if structure_month_to is not None and structure_month_from > structure_month_to:
            raise PermitSelectionInvalid("Structure month interval is reversed")
        if structure_year_from is not None and structure_year_to is not None and structure_year_from > structure_year_to:
            raise PermitSelectionInvalid("Structure year interval is reversed")
        with self._lock:
            try:
                self._refresh()
            except _SOURCE_ERRORS as exc:
                self._generation = self._signature = None
                self._checked = 0.0
                raise SourceUnavailable("Permit sources are unavailable") from exc
            generation = self._generation
            payload = generation["payload"]
            controls = payload["controls"]
            months = controls["monthsByKind"][permit_kind]
            if month is None:
                month = generation["default_months"][permit_kind]
            if month not in months:
                raise FilterUnavailable("Permit month is unavailable")
            structures = generation["structures"]
            structure_options = structure_controls(structures, permit_kind)
            years = structure_options["years"]
            if any(value is not None and value not in years for value in (structure_year_from, structure_year_to)):
                raise FilterUnavailable("Structure year is unavailable")
            end = structure_month_to if structure_month_to is not None else max(
                structure_month_from, generation["structure_months"][permit_kind])
            if structure_month_from > end:
                raise PermitSelectionInvalid("Structure month interval is reversed")
            structure_selection = {"mode": structure_mode, "monthFrom": structure_month_from, "monthTo": end,
                                   "yearFrom": structure_year_from if structure_year_from is not None else min(years, default=2011),
                                   "yearTo": structure_year_to if structure_year_to is not None else max(years, default=2011)}
            if structure_selection["yearFrom"] > structure_selection["yearTo"]:
                raise PermitSelectionInvalid("Structure year interval is reversed")
            selected_forecast = select_forecast(generation["forecast"], permit_kind)
            table = report(generation["data"], controls, permit_kind=permit_kind, month=month)["permits"]
            apply_main_presentation(table, main_presentation(table, generation["main_sources"], selected_forecast,
                                                             permit_kind, month, payload["source"]["date"]))
            area_variants = {}
            for key, living_only in (("living", True), ("full", False)):
                variant_forecast = select_forecast(generation["forecast"], permit_kind, living_only)
                variant_table = copy.deepcopy(table)
                apply_main_presentation(variant_table, main_presentation(
                    variant_table, generation["main_sources"], variant_forecast, permit_kind, month,
                    payload["source"]["date"], living_only=living_only))
                area_variants[key] = {"permits": variant_table, "forecast": variant_forecast}
            result = {
                **payload, "controls": {**controls, "structure": structure_options},
                "selection": {"permitKind": permit_kind, "month": month, "structure": structure_selection},
                "permits": table, "forecast": selected_forecast,
                "areaVariants": area_variants,
                "breakdowns": breakdowns(structures, permit_kind, structure_selection, generation["main_sources"], payload["source"]["date"]),
                "notes": ["Все графики, таблицы и выгрузки используют единую базу: 2011–2021 годы — «Данные с 2011 года» "
                          "(матрицы выдачи РС: жилая площадь, общая площадь жилья, нежильё), с 2022 года — «Реестр ОКС». "
                          "Вспомогат1 и статический файл годовых контролей на этой странице не используются. "
                          "Варианты areaVariants.living/full выбирают соответственно жилую/общую площадь жилья; "
                          "«Всего» добавляет отдельно стоящее нежильё.",
                          "Разрешения относятся только к Москве, без фильтра действующего статуса РС. "
                          "«Жильё» означает жилую площадь; «Всего» включает её, МОП и встроенное нежильё, а также нежилые объекты.",
                          "Презентационный остаток = годовой прогноз либо подтверждённый полный год минус выбранный факт, "
                          "не ниже нуля. Неполный итог текущего года не является годовым ориентиром. "
                          "Прогноз не изменяет фактические значения и рост.",
                          "Полные прошедшие годы используют строку «Год» соответствующей исторической матрицы "
                          "либо сумму 12 месяцев, а с 2022 года — суммы реестра. МОП и нежильё в жилье — разность общей "
                          "и жилой площади выбранного периода, без коэффициентов. В неполном текущем году "
                          "показываются доступные месяцы; actualMonth указывает последний."]}
            json.dumps(result, allow_nan=False)
            return copy.deepcopy(result)
