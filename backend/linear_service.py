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

from app.linear_objects import latest_source, period_summary, read_linear_objects, source_date
from backend.profile_service import FilterUnavailable, SourceUnavailable
from pipeline.data_access import DataAccess, DataContext, file_signature
from pipeline.linear_dashboard import (HISTORY_SHEET, STATIC_DEPOT_HISTORY, STATIC_DEPOT_SOURCE,
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
        path = latest_source(self.directory)
        if path is None:
            raise SourceUnavailable("DSTI source is missing")
        signature = file_signature(path)
        if signature == self._signature:
            self._checked = now
            return
        try:
            data = read_linear_objects(path)
        except BadZipFile as exc:
            raise SourceUnavailable("DSTI workbook is corrupt") from exc
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
            "source": {"date": source_date(path).isoformat(), "files": [path.relative_to(self.root).as_posix()],
                       "issues": [], "fileEvidence": "selected"},
            "years": years,
            "indicators": [{"id": str(code), "label": str(group.iloc[0]["indicator"]),
                            "unit": str(group.iloc[0]["unit"])} for code, group in data.groupby("code", sort=True)],
        }
        if latest_source(self.directory) != path or file_signature(path) != signature:
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

    def _dashboard_inputs(self, access):
        dsti = latest_source(self.directory)
        files = access.source_files("commissioning_forecasts")
        monitoring = max(files, key=lambda path: path.stat().st_mtime) if files else None
        return dsti, monitoring, (file_signature(dsti) if dsti else None,
                                  access.data_version("load_commissioning_forecasts"), date.today().isoformat(),
                                  STATIC_DEPOT_HISTORY, tuple(STATIC_DEPOT_SOURCE.items()))

    def dashboard(self):
        with self._lock:
            access = DataAccess(self.context)
            dsti, monitoring, signature = self._dashboard_inputs(access)
            if signature == self._dashboard_signature:
                return copy.deepcopy(self._dashboard)
            history, notes, data = [], [], None
            if dsti:
                try:
                    data = read_linear_objects(dsti)
                except (OSError, ValueError, BadZipFile) as exc:
                    raise SourceUnavailable("DSTI source cannot be read") from exc
            else:
                notes.append("Источник ДСТИ отсутствует; квартальные факты недоступны.")
            forecasts = access.load_commissioning_forecasts()
            if monitoring:
                with monitoring.open("rb") as stream:
                    book = load_workbook(stream, read_only=True, data_only=True)
                    try:
                        history, history_notes = read_history(book)
                        notes.extend(history_notes)
                    finally:
                        book.close()
            else:
                notes.append("Мониторинг отсутствует; годовая история и прогнозы недоступны.")
            def relative(path):
                if path is None:
                    return None
                try:
                    return path.relative_to(self.root).as_posix()
                except ValueError:
                    return path.name
            sources = [
                {"id": "dsti", "file": relative(dsti), "date": source_date(dsti).isoformat() if dsti else None,
                 "sheet": None},
                {"id": "monitoring", "file": relative(monitoring), "date": forecasts.get("source_date"),
                 "sheet": HISTORY_SHEET},
                {"id": "forecast", "file": forecasts.get("source_file"), "date": forecasts.get("source_date"),
                 "sheet": "Прогнозы"},
                dict(STATIC_DEPOT_SOURCE),
            ]
            payload = {"schemaVersion": 1, "version": hashlib.sha256(repr(signature).encode()).hexdigest(),
                       "generatedAt": datetime.now(timezone.utc).isoformat(), "sources": sources,
                       "notes": notes + forecasts.get("notes", []) + [
                           "Прогноз не является фактом. Пустые значения не считаются нулём.",
                           "Процент выполнения и остаток рассчитаны только от годового прогноза листа «Прогнозы»; без прогноза они неизвестны.",
                           "Факт текущего года неполный.",
                           "История электродепо за 2011–2025 годы закреплена как статичные годовые значения из файла «электродепо11-25.xlsx», лист «Лист1», ячейки D1:R2; нули сохранены."],
                       **build_dashboard(data, history, forecasts, sources, date.today(),
                                         static_depots=STATIC_DEPOT_HISTORY)}
            if self._dashboard_inputs(access) != (dsti, monitoring, signature):
                raise SourceUnavailable("Linear dashboard sources changed during loading")
            json.dumps(payload, allow_nan=False)
            self._dashboard, self._dashboard_signature = payload, signature
            return copy.deepcopy(payload)
