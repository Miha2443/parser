"""Read-only linear-object reports using the existing DSTI parser and summaries."""
from __future__ import annotations

import copy
import hashlib
import json
import threading
import time
from zipfile import BadZipFile
from datetime import date, datetime, timezone

from openpyxl import load_workbook

from app.linear_objects import (DSTI_SHEET, latest_source, period_summary, read_linear_objects,
                                read_linear_sheet, source_date)
from backend.profile_service import FilterUnavailable, SourceUnavailable
from pipeline.data_access import DataAccess, DataContext, file_signature
from pipeline.commissioning_forecasts import filename_date
from pipeline.linear_dashboard import (HISTORY_SHEET, LINEAR_CALCULATION_REVISION, STATIC_DEPOT_HISTORY, STATIC_DEPOT_SOURCE,
                                       build_dashboard, read_history)
from pipeline.report_values import records


class LinearService:
    def __init__(self, context: DataContext, check_interval: float = 5):
        self.directory = context.root / "data/raw/realty/linear_objects"
        self.root = context.root
        self.context = context
        self.check_interval = check_interval
        self._lock = threading.RLock()
        self._checked = 0.0
        self._signature = None
        self._data = None
        self._catalog = None
        self._dashboard = None
        self._dashboard_signature = None

    def _refresh(self):
        now = time.monotonic()
        if self._catalog is not None and now - self._checked < self.check_interval:
            return
        access = DataAccess(self.context)
        monitoring = self._monitoring_source(access)
        legacy = (latest_source(self.directory) if self._catalog is None or self._catalog["source"]["sheet"] is None
                  else None)
        signature = (LINEAR_CALCULATION_REVISION, file_signature(monitoring) if monitoring else None,
                     file_signature(legacy) if legacy else None)
        if signature == self._signature:
            self._checked = now
            return
        path, data, sheet, notes = monitoring, None, None, []
        try:
            if monitoring:
                with monitoring.open("rb") as stream:
                    book = load_workbook(stream, read_only=True, data_only=True)
                    try:
                        if DSTI_SHEET in book.sheetnames:
                            data = read_linear_sheet(book[DSTI_SHEET], monitoring=True)
                            sheet = DSTI_SHEET
                            signature = (*signature[:2], None)
                    finally:
                        book.close()
            if data is None:
                # Compatibility for the old quarterly API only. The dashboard
                # never substitutes a separately supplied financial workbook.
                path = latest_source(self.directory)
                if path is None:
                    raise SourceUnavailable("Monitoring DSTI sheet and legacy quarterly source are missing")
                signature = (*signature[:2], file_signature(path))
                data = read_linear_objects(path)
                notes.append("Лист «ДСТИ» мониторинга отсутствует; квартальный API использует старый отдельный источник.")
        except (OSError, ValueError, BadZipFile) as exc:
            raise SourceUnavailable("DSTI source cannot be read") from exc
        if data.empty:
            raise SourceUnavailable("DSTI report is empty")
        years = []
        for year in sorted(data["year"].unique(), reverse=True):
            selected = data[data["year"].eq(year)]
            quarters = sorted(int(q) for q in selected["quarter"].unique())
            complete = [q for q in quarters if selected[selected["quarter"].eq(q)]["fact"].notna().all()]
            years.append({"id": str(int(year)), "label": str(int(year)),
                          "quarters": quarters, "defaultQuarter": (complete or quarters)[-1]})
        catalog = {
            "schemaVersion": 1, "version": hashlib.sha256(repr(signature).encode()).hexdigest(),
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "source": {"date": filename_date(path) if path == monitoring else source_date(path).isoformat(),
                       "files": [self._relative(path)], "sheet": sheet,
                       "issues": notes, "fileEvidence": "selected"},
            "years": years,
            "indicators": [{"id": str(code), "label": str(group.iloc[0]["indicator"]),
                            "unit": str(group.iloc[0]["unit"])} for code, group in data.groupby("code", sort=True)],
        }
        current_monitoring = self._monitoring_source(access)
        current_legacy = latest_source(self.directory) if sheet is None else None
        if (LINEAR_CALCULATION_REVISION, file_signature(current_monitoring) if current_monitoring else None,
                file_signature(current_legacy) if current_legacy else None) != signature:
            raise SourceUnavailable("DSTI changed during loading")
        json.dumps(catalog, allow_nan=False)
        self._catalog, self._data, self._signature, self._checked = catalog, data, signature, now

    def catalog(self):
        with self._lock:
            self._refresh()
            return copy.deepcopy(self._catalog)

    def report(self, year=None, quarter=None, cumulative=False, indicator=None):
        with self._lock:
            self._refresh()
            chosen = next((item for item in self._catalog["years"] if year is None or item["id"] == str(year)), None)
            if chosen is None:
                raise FilterUnavailable("DSTI year is unavailable")
            year = int(chosen["id"])
            quarter = chosen["defaultQuarter"] if quarter is None else quarter
            if quarter not in chosen["quarters"]:
                raise FilterUnavailable("DSTI quarter is unavailable")
            indicator = indicator or self._catalog["indicators"][0]["id"]
            if indicator not in [item["id"] for item in self._catalog["indicators"]]:
                raise FilterUnavailable("DSTI indicator is unavailable")
            summary = records(period_summary(self._data, year, quarter, cumulative))
            all_periods = [row for q in chosen["quarters"]
                           for row in records(period_summary(self._data, year, q, cumulative))]
            result = {key: copy.deepcopy(self._catalog[key]) for key in
                      ("schemaVersion", "version", "generatedAt", "source")}
            result.update({"selection": {"year": year, "quarter": quarter, "cumulative": cumulative,
                                          "indicator": indicator},
                           "summary": summary, "trend": [row for row in all_periods if row["code"] == indicator],
                           "allPeriods": all_periods})
            json.dumps(result, allow_nan=False)
            return result

    def _relative(self, path):
        if path is None:
            return None
        try:
            return path.relative_to(self.root).as_posix()
        except ValueError:
            return path.name

    def _monitoring_source(self, access):
        files = access.source_files("commissioning_forecasts")
        return max(files, key=lambda path: path.stat().st_mtime) if files else None

    def _dashboard_inputs(self, access):
        monitoring = self._monitoring_source(access)
        return monitoring, (LINEAR_CALCULATION_REVISION,
                            access.data_version("load_commissioning_forecasts"), date.today().isoformat(),
                            STATIC_DEPOT_HISTORY, tuple(STATIC_DEPOT_SOURCE.items()))

    def dashboard(self):
        with self._lock:
            access = DataAccess(self.context)
            monitoring, signature = self._dashboard_inputs(access)
            if signature == self._dashboard_signature:
                return copy.deepcopy(self._dashboard)
            history, notes, data = [], [], None
            try:
                forecasts = access.load_commissioning_forecasts()
            except (OSError, ValueError, BadZipFile) as exc:
                raise SourceUnavailable("Monitoring forecast source cannot be read") from exc
            if monitoring:
                try:
                    with monitoring.open("rb") as stream:
                        book = load_workbook(stream, read_only=True, data_only=True)
                        try:
                            history, history_notes = read_history(book)
                            notes.extend(history_notes)
                            if DSTI_SHEET in book.sheetnames:
                                try:
                                    data = read_linear_sheet(book[DSTI_SHEET], monitoring=True)
                                except ValueError as exc:
                                    notes.append(f"Лист «ДСТИ» мониторинга не прошёл проверку: {exc}")
                            else:
                                notes.append("Лист «ДСТИ» отсутствует в выбранном мониторинге.")
                        finally:
                            book.close()
                except (OSError, ValueError, BadZipFile) as exc:
                    raise SourceUnavailable("Monitoring linear source cannot be read") from exc
            else:
                notes.append("Мониторинг отсутствует; годовая история и прогнозы недоступны.")
            sources = [
                {"id": "dsti", "file": self._relative(monitoring), "date": forecasts.get("source_date"),
                 "sheet": DSTI_SHEET},
                {"id": "monitoring", "file": self._relative(monitoring), "date": forecasts.get("source_date"),
                 "sheet": HISTORY_SHEET},
                {"id": "forecast", "file": forecasts.get("source_file"), "date": forecasts.get("source_date"),
                 "sheet": "Прогнозы"},
                dict(STATIC_DEPOT_SOURCE),
            ]
            dashboard = build_dashboard(data, history, forecasts, sources, date.today(),
                                        static_depots=STATIC_DEPOT_HISTORY)
            for metric in dashboard["metrics"][:3]:
                if metric["factSource"] != "dsti":
                    fallback = ("используется годовой блок мониторинга" if metric["factSource"] == "monitoring"
                                else "факт недоступен")
                    notes.append(f"{metric['label']}: подтверждённый факт текущего года на листе «ДСТИ» отсутствует; {fallback}.")
            payload = {"schemaVersion": 1, "version": hashlib.sha256(repr(signature).encode()).hexdigest(),
                       "calculationRevision": LINEAR_CALCULATION_REVISION,
                       "generatedAt": datetime.now(timezone.utc).isoformat(), "sources": sources,
                       "notes": notes + forecasts.get("notes", []) + [
                           "Прогноз не является фактом. Пустые значения не считаются нулём.",
                           "Процент выполнения и остаток рассчитаны только от годового прогноза листа «Прогнозы»; без прогноза они неизвестны.",
                           "Факт текущего года неполный.",
                           "Текущие факты дорог, мостов/тоннелей/эстакад и пешеходных переходов берутся из листа «ДСТИ» выбранной книги «Мониторинг 2.0»; резервный источник — её годовой блок. Отдельные файлы финансовой дисциплины не используются в dashboard. Дата книги не заменяет дату измерения показателя.",
                           "История электродепо за 2011–2025 годы закреплена как статичные годовые значения из файла «электродепо11-25.xlsx», лист «Лист1», ячейки D1:R2; нули сохранены."],
                       **dashboard}
            if self._dashboard_inputs(access) != (monitoring, signature):
                raise SourceUnavailable("Linear dashboard sources changed during loading")
            json.dumps(payload, allow_nan=False)
            self._dashboard, self._dashboard_signature = payload, signature
            return copy.deepcopy(payload)
