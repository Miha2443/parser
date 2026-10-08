"""Immutable read-only generations for reports backed by several existing loaders."""
from __future__ import annotations

import copy
import hashlib
import json
import threading
import time
from datetime import datetime, timezone

from backend.profile_service import SourceUnavailable
from pipeline.data_access import DataAccess


class GenerationService:
    def __init__(self, context, loaders, families, catalog_builder, report_builder, check_interval=5):
        self.context = context
        self.loaders = loaders
        self.families = families
        self.catalog_builder = catalog_builder
        self.report_builder = report_builder
        self.check_interval = check_interval
        self._lock = threading.RLock()
        self._metadata = self._data = self._catalog = self._signature = None
        self._checked = 0.0

    def _versions(self, access):
        return self.context.cache_key(), tuple((name, access.data_version(name)) for name in self.loaders)

    def _refresh(self):
        now = time.monotonic()
        if self._metadata is not None and now - self._checked < self.check_interval:
            return
        access = DataAccess(self.context)
        signature = self._versions(access)
        if signature == self._signature:
            self._checked = now
            return
        data = {name: getattr(access, name)() for name in self.loaders}
        if any(level == "error" for level, _ in access.issues):
            raise SourceUnavailable("Report loader failed")
        catalog = self.catalog_builder(data)
        files = []
        dates = []
        for family in self.families:
            paths = access.source_files(family)
            date = (max(datetime.fromtimestamp(path.stat().st_mtime) for path in paths).strftime("%d.%m.%Y")
                    if paths else access.latest_realty_mart_source_date(family))
            if date:
                dates.append(date)
            for path in paths:
                try:
                    name = path.resolve().relative_to(self.context.root).as_posix()
                except ValueError:
                    name = path.name
                if name not in files:
                    files.append(name)
        metadata = {"schemaVersion": 1, "version": hashlib.sha256(repr(signature).encode()).hexdigest(),
                    "generatedAt": datetime.now(timezone.utc).isoformat(),
                    "source": {"date": max(dates, key=lambda value: datetime.strptime(value, "%d.%m.%Y")) if dates else None, "files": files,
                               "issues": [list(item) for item in access.issues], "fileEvidence": "candidates",
                               "dateEvidence": "Latest candidate modification date, not proof of selected input"}}
        json.dumps({**metadata, **catalog}, allow_nan=False)
        if signature != self._versions(access):
            raise SourceUnavailable("Report sources changed while loading")
        self._data, self._catalog, self._metadata = data, catalog, metadata
        self._signature, self._checked = signature, now

    def catalog(self):
        with self._lock:
            self._refresh()
            return copy.deepcopy({**self._metadata, **self._catalog})

    def report(self, **filters):
        with self._lock:
            self._refresh()
            result = {**self._metadata, **self.report_builder(self._data, self._catalog, **filters)}
            json.dumps(result, allow_nan=False)
            return copy.deepcopy(result)
