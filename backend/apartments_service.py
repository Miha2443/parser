"""Read-only apartment generations using the existing DataAccess selection rules."""
from __future__ import annotations

import copy
import hashlib
import json
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone

import pandas as pd

from backend.profile_service import FilterUnavailable, SourceUnavailable
from pipeline.apartment_calculations import (
    AREA, NAME, QUANTITY, REGION_LABELS, ROOM_COLUMNS, available_regions,
    developer_detail, overview,
)
from pipeline.data_access import DataAccess, DataContext


class ApartmentsService:
    def __init__(self, context: DataContext, check_interval: float = 5):
        self.context = context
        self.check_interval = check_interval
        self._lock = threading.RLock()
        self._checked_at = 0.0
        self._signature = None
        self._metadata = None
        self._data = None
        self._overviews = {}
        self._details = OrderedDict()
        self._catalog = None

    def _versions(self, access):
        return (self.context.cache_key(), access.data_version("load_kvartirografia"))

    def _relative(self, path):
        try:
            return path.resolve().relative_to(self.context.root).as_posix()
        except ValueError:
            return path.name

    @staticmethod
    def _validate(data):
        requirements = {
            "apartments": {"region_key", "тип", "количество_шт_num", AREA},
            "distribution": {"region_key", "диапазон", "доля_num"},
            "developers": {"region_key", NAME, QUANTITY, AREA, *[c for _, c in ROOM_COLUMNS]},
            "regions": {"region_key", NAME, QUANTITY, AREA, *[c for _, c in ROOM_COLUMNS]},
        }
        for table, columns in requirements.items():
            frame = data[table]
            if not isinstance(frame, pd.DataFrame):
                raise SourceUnavailable(f"Invalid apartment table: {table}")
            if frame.empty:
                continue
            if columns - set(frame.columns):
                raise SourceUnavailable(f"Missing apartment columns: {table}")
            text_columns = [c for c in ("region_key", NAME, "тип", "диапазон") if c in columns]
            if any(not frame[c].map(lambda value: isinstance(value, str)).all() for c in text_columns):
                raise SourceUnavailable(f"Invalid apartment labels: {table}")
            for column in columns - set(text_columns):
                if not pd.api.types.is_numeric_dtype(frame[column]) and not frame[column].dropna().empty:
                    raise SourceUnavailable(f"Invalid apartment numeric column: {table}.{column}")
        if not available_regions(data):
            raise SourceUnavailable("No apartment regions rf/msk")
        if data["apartments"].empty and data["developers"].empty:
            raise SourceUnavailable("No apartment or developer data")

    def _refresh(self):
        now = time.monotonic()
        if self._metadata is not None and now - self._checked_at < self.check_interval:
            return
        access = DataAccess(self.context)
        signature = self._versions(access)
        if self._metadata is not None and signature == self._signature:
            self._checked_at = now
            return
        data = access.load_kvartirografia()
        if any(level == "error" for level, _ in access.issues):
            raise SourceUnavailable("Apartment loader reported an error")
        self._validate(data)
        regions = available_regions(data)
        overviews = {key: overview(data, key) for key in regions}
        metadata = {
            "schemaVersion": 1, "version": hashlib.sha256(repr(signature).encode()).hexdigest(),
            "generatedAt": datetime.now(timezone.utc).isoformat(), "reportDate": data["report_date"],
            "source": {
                "date": access.latest_realty_mart_source_date("kvartirografia")
                        or access.latest_raw_source_date("kvartirografia_*.json", "kvartirografia_*.xlsx") or None,
                "files": [self._relative(path) for path in access.source_files("kvartirografia")],
                "issues": [list(issue) for issue in access.issues],
            },
        }
        developers_by_region = {}
        for key in regions:
            unique = {}
            for row in overviews[key]["developers"]:
                unique.setdefault(row["id"], {"id": row["id"], "name": row["name"], "place": row["place"]})
            developers_by_region[key] = list(unique.values())
        catalog = {
            **metadata, "regions": [{"id": key, "label": REGION_LABELS[key]} for key in regions],
            "developersByRegion": developers_by_region,
        }
        json.dumps([catalog, overviews], allow_nan=False)
        if self._versions(access) != signature:
            raise SourceUnavailable("Apartment sources changed during loading; retry")
        # Only publish after calculations, metadata and both signatures agree.
        self._data, self._metadata, self._catalog = data, metadata, catalog
        self._overviews = overviews
        self._signature = signature
        self._checked_at = now
        self._details.clear()

    def _region(self, region):
        regions = self._overviews
        if region is None:
            return next(iter(regions))
        if region not in regions:
            raise FilterUnavailable("Apartment region is unavailable")
        return region

    def catalog(self):
        with self._lock:
            self._refresh()
            return copy.deepcopy(self._catalog)

    def overview(self, region=None):
        with self._lock:
            self._refresh()
            if self._data["apartments"].empty:
                raise SourceUnavailable("Apartment overview data is absent")
            region = self._region(region)
            return copy.deepcopy({**self._metadata, **self._overviews[region]})

    def developer(self, developer, region=None):
        with self._lock:
            self._refresh()
            if self._data["developers"].empty:
                raise SourceUnavailable("Apartment developer data is absent")
            region = self._region(region)
            if not any(row["id"] == developer for row in self._overviews[region]["developers"]):
                raise FilterUnavailable("Unknown apartment developer")
            key = (region, developer)
            if key not in self._details:
                detail = developer_detail(self._data, region, developer)
                json.dumps(detail, allow_nan=False)
                self._details[key] = detail
                if len(self._details) > 128:
                    self._details.popitem(last=False)
            self._details.move_to_end(key)
            return copy.deepcopy({**self._metadata, **self._details[key]})
