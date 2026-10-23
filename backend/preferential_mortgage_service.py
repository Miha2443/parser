"""Immutable, version-pinned preferential mortgage workbook generations."""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
import threading

from backend.profile_service import FilterUnavailable, SourceUnavailable
from pipeline.data_access import DataContext, file_signature
from pipeline.preferential_mortgage import (MONTH_LABELS, PROGRAMS, REGIONS, SOURCE_FILE,
                                            build_report, region_periods, validate_source)


class PreferentialMortgageService:
    def __init__(self, context: DataContext):
        self.context = context
        self.path = context.root / SOURCE_FILE
        self._lock = threading.RLock()
        self._generation = None
        self._signature = None

    def _refresh(self):
        try:
            before = file_signature(self.path)
            content = self.path.read_bytes()
            after = file_signature(self.path)
            if before != after:
                raise SourceUnavailable("Preferential mortgage source changed during loading")
            signature = after, hashlib.sha256(content).hexdigest()
            if signature == self._signature:
                if file_signature(self.path) != after:
                    raise SourceUnavailable("Preferential mortgage source changed during loading")
                return
            document = validate_source(json.loads(content.decode("utf-8-sig")))
            periods = region_periods(document)
            char_periods = region_periods(document, characteristics=True)
            regions = [{"id": region, "label": label, "periods": periods[region],
                        "characteristicPeriods": char_periods[region]}
                       for region, label in REGIONS.items() if periods[region]]
            if not regions:
                raise SourceUnavailable("Preferential mortgage source has no populated regions")
            metadata = {"schemaVersion": 1,
                        "version": hashlib.sha256(repr(signature).encode("utf-8")).hexdigest(),
                        "generatedAt": datetime.now(timezone.utc).isoformat(),
                        "source": {"url": document["sourceUrl"], "date": document.get("sourceDate"),
                                   "files": [SOURCE_FILE], "collectedAt": document["collectedAt"],
                                   "workbook": document.get("sourceWorkbook")}}
            catalog = {**metadata, "regions": regions, "defaultRegion": regions[0]["id"],
                       "months": [{"id": index, "label": label} for index, label in enumerate(MONTH_LABELS, 1)],
                       "programs": [{"id": key, "label": label} for key, label in PROGRAMS.items()]}
            if file_signature(self.path) != after:
                raise SourceUnavailable("Preferential mortgage source changed during validation")
            json.dumps(catalog, allow_nan=False)
        except (OSError, ValueError, TypeError, OverflowError, SourceUnavailable) as exc:
            self._generation, self._signature = None, None
            raise SourceUnavailable("Preferential mortgage source is missing, invalid or changed during loading") from exc
        self._generation = {"document": document, "catalog": catalog}
        self._signature = signature

    def catalog(self):
        with self._lock:
            self._refresh()
            return copy.deepcopy(self._generation["catalog"])

    def _report(self, region, month, characteristic_month):
        self._refresh()
        catalog = self._generation["catalog"]
        region = catalog["defaultRegion"] if region is None else region
        selected = next((row for row in catalog["regions"] if row["id"] == region), None)
        if selected is None:
            raise FilterUnavailable("Preferential mortgage region or month is unavailable")
        if month is None:
            month = int(selected["periods"][-1][5:])
        if characteristic_month is None:
            characteristic_month = int(selected["characteristicPeriods"][-1][5:]) if selected["characteristicPeriods"] else month
        if any(type(value) is not int or not 1 <= value <= 12 for value in (month, characteristic_month)):
            raise FilterUnavailable("Preferential mortgage region or month is unavailable")
        report = build_report(self._generation["document"], region, month, characteristic_month)
        report.update({key: copy.deepcopy(catalog[key]) for key in ("schemaVersion", "version", "generatedAt", "source")})
        json.dumps(report, allow_nan=False)
        return report

    def report(self, region=None, month=None, characteristic_month=None):
        with self._lock:
            return self._report(region, month, characteristic_month)

    def export_snapshot(self, region=None, month=None, characteristic_month=None):
        with self._lock:
            payload = self._report(region, month, characteristic_month)
            return payload, copy.deepcopy(self._generation["document"])
