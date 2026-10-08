"""Immutable map generations from the two named raw worksheets and local cache."""
import copy
import hashlib
import json
import threading
import time
from datetime import datetime, timezone

import pandas as pd

from backend.profile_service import FilterUnavailable, SourceUnavailable
from pipeline.data_access import DataAccess, file_signature
from pipeline.map_calculations import build_objects, filter_objects
from pipeline.map_coordinates import load_geocode_cache
from pipeline.report_values import records


class MapService:
    def __init__(self, context, check_interval=5):
        self.context = context
        self.access = DataAccess(context)
        self.cache_path = context.root / "data/derived/monitoring_geocodes.csv"
        self.check_interval = check_interval
        self._checked = 0
        self._signature = None
        self._catalog = None
        self._objects = None
        self._lock = threading.RLock()

    def _inputs(self):
        files = self.access.source_files("monitoring_2_0")
        if not files:
            raise SourceUnavailable("Monitoring workbook is missing")
        path = max(files, key=lambda item: item.stat().st_mtime)
        signature = (file_signature(path), file_signature(self.cache_path) if self.cache_path.exists() else None)
        return path, signature

    def _refresh(self):
        now = time.monotonic()
        if self._catalog is not None and now - self._checked < self.check_interval:
            return
        path, signature = self._inputs()
        if self._signature == signature:
            self._checked = now
            return
        # Named sheets only, without the historical-sheet merge or RV year filter.
        sheets = pd.read_excel(path, sheet_name=["Реестр РВ", "Реестр ОКС"])
        cache = load_geocode_cache(self.cache_path)
        objects, scope = build_objects(sheets["Реестр РВ"], sheets["Реестр ОКС"], cache)
        years = objects["year"].dropna()
        files = [path] + ([self.cache_path] if self.cache_path.exists() else [])
        catalog = {
            "schemaVersion": 1, "version": hashlib.sha256(repr(signature).encode()).hexdigest(),
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "source": {"date": datetime.fromtimestamp(path.stat().st_mtime).strftime("%d.%m.%Y"),
                       "files": [item.relative_to(self.context.root).as_posix() for item in files],
                       "issues": [], "fileEvidence": "selected", "dateEvidence": "Workbook modification date"},
            "developers": sorted(value for value in objects["developer"].unique() if value),
            "statuses": sorted(value for value in objects["status"].unique() if value),
            "okrugs": sorted(value for value in objects["okrug"].unique() if value),
            "years": {"min": int(years.min()) if not years.empty else None,
                      "max": int(years.max()) if not years.empty else None},
            "scope": scope,
            "notes": ["ОКС: дата выдачи РС с 01.01.2011; РВ: весь реестр, без Лист4.",
                      "Позиции по центрам районов и округов приблизительные, не координаты зданий.",
                      "Остальные позиции получены из геометрии и локального кэша и требуют проверки.",
                      "Объекты с неизвестным годом не исключаются фильтром лет."],
        }
        if self._inputs() != (path, signature):
            raise SourceUnavailable("Map inputs changed while loading")
        json.dumps(catalog, allow_nan=False)
        self._objects, self._catalog, self._signature, self._checked = objects, catalog, signature, now

    def catalog(self):
        with self._lock:
            self._refresh()
            return copy.deepcopy(self._catalog)

    def report(self, developer=None, statuses=(), okrugs=(), year_from=None, year_to=None,
               quality="all", only_with_coords=True):
        with self._lock:
            self._refresh()
            catalog = self._catalog
            if developer and developer not in catalog["developers"]:
                raise FilterUnavailable("Map developer is unavailable")
            if set(statuses) - set(catalog["statuses"]) or set(okrugs) - set(catalog["okrugs"]):
                raise FilterUnavailable("Map filter is unavailable")
            if quality not in {"all", "located", "approximate", "missing"}:
                raise FilterUnavailable("Coordinate quality is unavailable")
            if year_from is not None and year_to is not None and year_from > year_to:
                raise FilterUnavailable("Invalid year range")
            selected = filter_objects(self._objects, developer, statuses, okrugs, year_from, year_to,
                                      quality, only_with_coords)
            rows = records(selected)
            features = [{"type": "Feature", "id": row["id"],
                         "geometry": {"type": "Point", "coordinates": [row["lon"], row["lat"]]},
                         "properties": row} for row in rows if row["has_coords"]]
            result = {key: copy.deepcopy(catalog[key]) for key in
                      ("schemaVersion", "version", "generatedAt", "source", "notes", "scope")}
            def counts(frame):
                return {"total": len(frame), "onMap": int(frame["has_coords"].sum()),
                        "located": int(frame["quality"].eq("located").sum()),
                        "approximate": int(frame["quality"].eq("approximate").sum()),
                        "missing": int(frame["quality"].eq("missing").sum())}
            result.update({"selection": {"developer": developer, "statuses": list(statuses),
                                           "okrugs": list(okrugs), "yearFrom": year_from, "yearTo": year_to,
                                           "quality": quality, "onlyWithCoords": only_with_coords},
                           "totals": counts(self._objects), "filteredTotals": counts(selected),
                           "rows": rows, "geojson": {"type": "FeatureCollection", "features": features}})
            json.dumps(result, allow_nan=False)
            return result
