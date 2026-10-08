"""Read-only, atomically published sales/readiness generations."""
from __future__ import annotations

import copy
import hashlib
import json
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone
from numbers import Integral, Real

import pandas as pd

from backend.profile_service import FilterUnavailable, SourceUnavailable
from pipeline.data_access import DataAccess, DataContext
from pipeline.sales_calculations import SECTION_TABS, catalog_regions, sales_detail


class SalesService:
    def __init__(self, context: DataContext, check_interval: float = 5):
        self.context = context
        self.check_interval = check_interval
        self._lock = threading.RLock()
        self._checked_at = 0.0
        self._signature = None
        self._metadata = None
        self._catalog = None
        self._data = None
        self._details = OrderedDict()

    def _versions(self, access):
        return (self.context.cache_key(), access.data_version("load_rasprodannost"))

    def _relative(self, path):
        try:
            return path.resolve().relative_to(self.context.root).as_posix()
        except ValueError:
            return path.name

    @staticmethod
    def _validate(data):
        kpi = data["kpi"]
        if not isinstance(kpi, pd.DataFrame) or kpi.empty:
            raise SourceUnavailable("No sales KPI data")
        for identifier, _ in (("kpi", ""), *SECTION_TABS):
            frame = data.get(identifier, pd.DataFrame())
            if not isinstance(frame, pd.DataFrame):
                raise SourceUnavailable(f"Invalid sales table: {identifier}")
            if frame.empty:
                continue
            required = {"region_key", "year", "month", "название", "значение_num"} if identifier == "kpi" else {
                "region_key", "year", "month", "наименование"}
            if required - set(frame.columns) or not frame.columns.is_unique or any(not isinstance(c, str) for c in frame.columns):
                raise SourceUnavailable(f"Invalid sales columns: {identifier}")
            text_columns = ("region_key", "название") if identifier == "kpi" else ("region_key",)
            for column in text_columns:
                if not frame[column].map(lambda value: isinstance(value, str)).all():
                    raise SourceUnavailable(f"Invalid sales labels: {identifier}.{column}")
            for column, lower, upper in (("year", 1, 9999), ("month", 1, 12)):
                values = frame[column]
                if not pd.api.types.is_integer_dtype(values.dtype) or values.isna().any() or not values.between(lower, upper).all():
                    raise SourceUnavailable(f"Invalid sales period: {identifier}.{column}")
            if identifier == "kpi":
                for column in ["значение_num", *[c for c in frame.columns if c.startswith("прогноз_") and c.endswith("_num")]]:
                    if not pd.api.types.is_numeric_dtype(frame[column]) and not frame[column].dropna().empty:
                        raise SourceUnavailable(f"Invalid sales numeric column: {column}")
            else:
                for column in frame.columns:
                    if column in ("region_key", "year", "month", "month_name", "report_period", "section") or column.endswith("_num"):
                        continue
                    if not frame[column].dropna().map(lambda value: isinstance(value, (str, Real)) and not isinstance(value, bool)).all():
                        raise SourceUnavailable(f"Invalid sales raw field: {identifier}.{column}")
        regions = data["regions_available"]
        by_region = data["periods_by_region"]
        if not isinstance(regions, (list, tuple)) or not all(isinstance(r, str) for r in regions) or not isinstance(by_region, dict):
            raise SourceUnavailable("Invalid sales catalog metadata")
        for region in regions:
            periods = by_region.get(region, [])
            if not isinstance(periods, (list, tuple)):
                raise SourceUnavailable("Invalid sales catalog periods")
            for period in periods:
                if (not isinstance(period, (list, tuple)) or len(period) != 2
                        or any(not isinstance(v, Integral) or isinstance(v, bool) for v in period)
                        or not 1 <= period[0] <= 9999 or not 1 <= period[1] <= 12):
                    raise SourceUnavailable("Invalid sales catalog period")
        if not catalog_regions(data):
            raise SourceUnavailable("No sales regions with valid periods")
        # Validate catalog periods without calculating charts/tables in catalog requests.
        for region in catalog_regions(data):
            for period in region["periods"]:
                year, month = map(int, period["id"].split("-"))
                if not kpi[(kpi["region_key"] == region["id"]) & (kpi["year"] == year) & (kpi["month"] == month)].shape[0]:
                    raise SourceUnavailable("Sales catalog period has no KPI rows")

    def _refresh(self):
        now = time.monotonic()
        if self._metadata is not None and now - self._checked_at < self.check_interval:
            return
        access = DataAccess(self.context)
        signature = self._versions(access)
        if self._metadata is not None and signature == self._signature:
            self._checked_at = now
            return
        data = access.load_rasprodannost()
        if any(level == "error" for level, _ in access.issues):
            raise SourceUnavailable("Sales loader reported an error")
        self._validate(data)
        metadata = {
            "schemaVersion": 1, "version": hashlib.sha256(repr(signature).encode()).hexdigest(),
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "source": {
                "date": access.latest_raw_source_date("rasprodannost_*.xlsx")
                        or access.latest_realty_mart_source_date("rasprodannost") or None,
                "dateEvidence": "Latest candidate date; not proof of the selected mart or raw workbook",
                "files": [self._relative(path) for path in access.source_files("rasprodannost")],
                "issues": [list(issue) for issue in access.issues],
            },
        }
        catalog = {**metadata, "regions": catalog_regions(data)}
        json.dumps(catalog, allow_nan=False)
        owned_data = copy.deepcopy(data)
        if self._versions(access) != signature:
            raise SourceUnavailable("Sales sources changed during loading; retry")
        self._data, self._metadata, self._catalog = owned_data, metadata, catalog
        self._signature, self._checked_at = signature, now
        self._details.clear()

    def catalog(self):
        with self._lock:
            self._refresh()
            return copy.deepcopy(self._catalog)

    def detail(self, region=None, period=None):
        with self._lock:
            self._refresh()
            regions = self._catalog["regions"]
            region = region if region is not None else regions[0]["id"]
            selected_region = next((item for item in regions if item["id"] == region), None)
            if selected_region is None:
                raise FilterUnavailable("Sales region is unavailable")
            periods = selected_region["periods"]
            period = period if period is not None else periods[-1]["id"]
            if not any(item["id"] == period for item in periods):
                raise FilterUnavailable("Sales period is unavailable for this region")
            key = (region, period)
            if key not in self._details:
                year, month = map(int, period.split("-"))
                detail = sales_detail(self._data, region, year, month)
                json.dumps(detail, allow_nan=False)
                self._details[key] = detail
                if len(self._details) > 32:
                    self._details.popitem(last=False)
            self._details.move_to_end(key)
            return copy.deepcopy({**self._metadata, **self._details[key]})
