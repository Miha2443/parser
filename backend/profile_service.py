"""Read-only, version-aware access to the existing dashboard calculations."""
from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
from collections import OrderedDict
from datetime import datetime, timezone

from pipeline.data_access import DataAccess, DataContext
from pipeline.dev_name_utils import normalize_developer_name as norm
from pipeline.profile_calculations import apartment_regions, clean, make_profile

FAMILIES = {
    "monitoring_2_0": ("monitoring_2_0_*.xlsx",),
    "erzrf_top": ("top_obyem_stroitelstva_rf_*.xlsx",),
    "erzrf_cards": ("cards_*.xlsx",),
    "rasprodannost": ("rasprodannost_*.xlsx",),
    "kvartirografia": ("kvartirografia_*.xlsx", "kvartirografia_*.json"),
    "escrow_manual": ("Наполняемость*.xlsx", "наполняемость*.xlsx", "*эскроу*.xlsx"),
}


class SourceUnavailable(RuntimeError):
    pass


class FilterUnavailable(LookupError):
    pass


class ProfileService:
    def __init__(self, context: DataContext, check_interval: float = 5):
        self.context = context
        self.check_interval = check_interval
        self._lock = threading.RLock()
        self._checked_at = 0.0
        self._signature = None
        self._metadata = None
        self._data = None
        self._profiles = OrderedDict()

    def _versions(self, access):
        return tuple((name, access.data_version(f"load_{name}")) for name in FAMILIES)

    def _relative(self, path):
        try:
            return path.resolve().relative_to(self.context.root).as_posix()
        except ValueError:
            return path.name

    def _portable(self, value):
        if isinstance(value, dict):
            return {key: self._relative(Path(item)) if key == "source" and isinstance(item, str)
                    else self._portable(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._portable(item) for item in value]
        return value

    def _refresh(self):
        now = time.monotonic()
        if self._metadata is not None and now - self._checked_at < self.check_interval:
            return
        access = DataAccess(self.context)
        versions = self._versions(access)
        if versions == self._signature:
            self._checked_at = now
            return
        # Publish only an internally consistent generation; failed refreshes
        # never silently serve a previous generation as current.
        data = {name: getattr(access, f"load_{name}")() for name in FAMILIES}
        if any(level == "error" for level, _ in access.issues):
            raise SourceUnavailable("A source loader reported an error; data generation was not published")
        if self._versions(access) != versions:
            raise SourceUnavailable("Source files changed during loading; retry the request")
        monitoring = data["monitoring_2_0"]
        names = monitoring.get("developers", [])
        if not names:
            raise SourceUnavailable("Monitoring data is absent or contains no developers")
        aliases = {}
        for name in names:
            key = norm(name)
            if key:
                aliases.setdefault(key, []).append(name)
        developers = []
        for key, options in aliases.items():
            canonical = min(options, key=lambda name: (len(name), name))
            regions = [r for r in apartment_regions(data["kvartirografia"], key) if r in ("msk", "rf")] or ["msk"]
            developers.append({"developer": canonical, "developerKey": key, "regions": regions})
        developers.sort(key=lambda item: (item["developerKey"] != "пик", item["developerKey"]))
        dates = {name: access.latest_realty_mart_source_date(name) or access.latest_raw_source_date(*patterns) or None
                 for name, patterns in FAMILIES.items()}
        evidence = {}
        for name in FAMILIES:
            candidates = [self._relative(path) for path in access.source_files(name)]
            evidence[name] = {"candidateRawFiles": candidates,
                              "selectionNote": "Source registry candidates, not proof of contribution; DataAccess selects mart/raw inputs."}
        self._metadata = {"schemaVersion": 1, "generatedAt": datetime.now(timezone.utc).isoformat(),
                          "version": hashlib.sha256(repr(versions).encode()).hexdigest(), "sourceDates": dates,
                          "controls": {"developers": developers, "frozen": False, "regionControl": "apartmentsOnly"},
                          "provenance": {"sources": evidence, "issues": clean(access.issues)}}
        self._data = data
        self._signature = versions
        self._checked_at = now
        self._profiles.clear()

    def catalog(self):
        with self._lock:
            self._refresh()
            return self._metadata

    def profile(self, developer: str, region: str):
        with self._lock:
            self._refresh()
            selected = next((d for d in self._metadata["controls"]["developers"] if d["developerKey"] == developer), None)
            if selected is None:
                raise FilterUnavailable("Unknown developer")
            if region not in selected["regions"]:
                raise FilterUnavailable("Apartment region is not available for this developer")
            key = (developer, region)
            if key not in self._profiles:
                profile = self._portable(clean(make_profile(selected["developer"], region, self._data)))
                # Validate JSON before caching: unknown/non-finite remains null.
                json.dumps(profile, allow_nan=False)
                self._profiles[key] = profile
                if len(self._profiles) > 64:
                    self._profiles.popitem(last=False)
            self._profiles.move_to_end(key)
            return {**self._metadata, "profiles": [self._profiles[key]]}
