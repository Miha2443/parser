"""Read-only, immutable mortgage generations from one pinned local source file."""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
import threading

from backend.profile_service import FilterUnavailable, SourceUnavailable
from pipeline.data_access import DataContext, file_signature
from pipeline.mortgage_calculations import (MONTH_LABELS, REGIONS, SOURCE_FILE, build_report,
                                             region_periods, validate_source)


class MortgageService:
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
                raise SourceUnavailable("Mortgage source changed during loading")
            signature = (after, hashlib.sha256(content).hexdigest())
            if signature == self._signature:
                if file_signature(self.path) != after:
                    raise SourceUnavailable("Mortgage source changed during loading")
                return
            document = validate_source(json.loads(content.decode("utf-8-sig")))
            periods = region_periods(document)
            regions = [{"id": region, "label": label, "periods": periods[region]}
                       for region, label in REGIONS.items() if periods[region]]
            if not regions:
                raise SourceUnavailable("Mortgage source has no populated regions")
            metadata = {"schemaVersion": 1,
                        "version": hashlib.sha256(repr(signature).encode("utf-8")).hexdigest(),
                        "generatedAt": datetime.now(timezone.utc).isoformat(),
                        "source": {"url": document["sourceUrl"], "date": document.get("sourceDate"),
                                   "files": [SOURCE_FILE], "collectedAt": document["collectedAt"]}}
            catalog = {**metadata, "regions": regions, "defaultRegion": regions[0]["id"],
                       "months": [{"id": index, "label": label} for index, label in enumerate(MONTH_LABELS, 1)]}
            if file_signature(self.path) != after:
                raise SourceUnavailable("Mortgage source changed during validation")
            json.dumps(catalog, allow_nan=False)
        except (OSError, ValueError, TypeError, OverflowError, SourceUnavailable) as exc:
            self._generation, self._signature = None, None
            raise SourceUnavailable("Mortgage source is missing, invalid or changed during loading") from exc
        self._generation = {"document": document, "catalog": catalog}
        self._signature = signature

    def catalog(self):
        with self._lock:
            self._refresh()
            return copy.deepcopy(self._generation["catalog"])

    def _report(self, region, month):
        self._refresh()
        catalog = self._generation["catalog"]
        if region is None:
            region = catalog["defaultRegion"]
        if region not in [row["id"] for row in catalog["regions"]] or type(month) is not int or not 1 <= month <= 12:
            raise FilterUnavailable("Mortgage region or month is unavailable")
        report = build_report(self._generation["document"], region, month)
        report.update({key: copy.deepcopy(catalog[key]) for key in ("schemaVersion", "version", "generatedAt", "source")})
        json.dumps(report, allow_nan=False)
        return report

    def report(self, region=None, month=12):
        with self._lock:
            return self._report(region, month)

    def export_snapshot(self, region=None, month=12):
        with self._lock:
            payload = self._report(region, month)
            return payload, copy.deepcopy(self._generation["document"])
