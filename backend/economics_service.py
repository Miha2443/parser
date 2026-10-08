"""Atomic read-only generations for salary, CPI and national accounts."""
from __future__ import annotations

import copy
import hashlib
import json
import threading
import time
from datetime import datetime, timezone
from numbers import Real
from pathlib import Path

import pandas as pd

from backend.profile_service import FilterUnavailable, SourceUnavailable
from pipeline.data_access import DataAccess, file_signature, MONTH_NAMES_RU, QUARTER_NAMES_RU
from pipeline.download_provenance import source_provenance, download_summary, receipt_path
from pipeline.economics_calculations import (
    ACCOUNT_BLOCKS, INDEX_BASES, MSK, RF, VIEWS, aggregate_quarter,
    accounts_report, industry_options, ipc_report, salary_report,
)
from pipeline.profile_calculations import records

LOADERS = {"salary": "load_salary", "ipc": "load_ipc", "accounts": "load_national_accounts"}


def options(values):
    return [{"id": value, "label": str(value)} for value in values]


def region_order(df):
    return sorted(df["region"].dropna().unique().tolist(),
                  key=lambda name: 0 if "моск" in str(name).casefold() or name == "msk" else 1)


def default_industries(values):
    return ["Строительство"] if "Строительство" in values else values[:1]


def build_catalog(family, df):
    required = {"year", "region", "value", "view", "unit", "source_file"}
    required |= {"metric"} if family == "accounts" else {"month", "quarter", "period_type"}
    if df.empty or not required.issubset(df.columns):
        raise SourceUnavailable("Economics input is empty or has an invalid schema")
    for column in ("year", "value"):
        if not all(isinstance(value, Real) for value in df[column].dropna()):
            raise SourceUnavailable(f"Economics {column} must be numeric")
    if family == "accounts":
        df = df[df["year"] >= 2011]
        if df.empty:
            raise SourceUnavailable("National accounts have no data since 2011")
        structure = {region: {mode: options(industry_options(df, region, metric))
                              for mode, metric in (("value", "vds_value"), ("share", "vds_structure"))}
                     for region in (MSK, RF)}
        indices = {region: options(industry_options(df, region, "vds_index")) for region in (MSK, RF)}
        defaults = {f"block{i}_regions": [MSK] for i in range(1, 5)}
        defaults.update({"structure_region": MSK, "structure_mode": "value",
                         "structure_industries": default_industries([item["id"] for item in structure[MSK]["value"]]),
                         "index_region": MSK,
                         "index_industries": default_industries([item["id"] for item in indices[MSK]]), "show_total": True})
        blocks = [{"id": f"na_block{i}", "title": block[-1], "kind": block[-2], "unit": block[4],
                   "regionControl": f"block{i}_regions"} for i, block in enumerate(ACCOUNT_BLOCKS, 1)]
        blocks += [{"id": "na_block5", "title": "Структура ВРП", "kind": "bar", "stack": True,
                    "regionControl": "structure_region", "industryControl": "structure_industries"},
                   {"id": "na_block6", "title": "Индекс физического объема по отраслям", "kind": "line", "unit": "%",
                    "regionControl": "index_region", "industryControl": "index_industries"}]
        controls = [{"id": f"block{i}_regions", "type": "multiselect", "options": options([MSK, RF]),
                     "default": [MSK]} for i in range(1, 5)]
        controls += [{"id": name, "type": "select", "options": options([MSK, RF]), "default": MSK}
                     for name in ("structure_region", "index_region")]
        controls += [{"id": "structure_mode", "type": "select",
                      "options": [{"id": "value", "label": "В рублях", "unit": "трлн руб"},
                                  {"id": "share", "label": "Доля, %", "unit": "%"}], "default": "value"},
                     {"id": "structure_industries", "type": "multiselect", "optionsByRegionMode": structure,
                      "default": defaults["structure_industries"]},
                     {"id": "index_industries", "type": "multiselect", "optionsByRegion": indices,
                      "default": defaults["index_industries"]},
                     {"id": "show_total", "type": "checkbox", "default": True}]
        return {"family": family, "minimumYear": 2011, "years": sorted(int(y) for y in df.year.dropna().unique()),
                "blocks": blocks, "controls": controls, "defaults": defaults,
                "structureIndustries": structure, "indexIndustries": indices}
    regions = region_order(df)
    if not regions:
        raise SourceUnavailable("Economics regions are missing")
    defaults = {"period": "month", "months": list(range(1, 13)), "quarters": [1, 2, 3, 4]}
    controls = [{"id": "period", "type": "select", "options": [
        {"id": "year", "label": "Год"}, {"id": "quarter", "label": "Квартал"}, {"id": "month", "label": "Месяц"}],
        "default": "month"},
        {"id": "months", "type": "multiselect", "options": [{"id": i, "label": label} for i, label in enumerate(MONTH_NAMES_RU, 1)],
         "default": defaults["months"], "periods": ["month"]},
        {"id": "quarters", "type": "multiselect", "options": [{"id": i, "label": label} for i, label in enumerate(QUARTER_NAMES_RU, 1)],
         "default": defaults["quarters"], "periods": ["quarter"]}]
    if family == "salary":
        defaults.update({"region": regions[0], "views": VIEWS.copy(), "ytd": False})
        quarter_options = {region: {mode: sorted(int(q) for q in aggregate_quarter(df, mode == "ytd").loc[
            lambda frame: frame["region"].eq(region), "quarter"].dropna().unique()) for mode in ("direct", "ytd")}
                           for region in regions}
        controls += [{"id": "region", "type": "select", "options": options(regions), "default": regions[0]},
                     {"id": "views", "type": "multiselect", "options": options(VIEWS), "default": VIEWS.copy()},
                     {"id": "ytd", "type": "checkbox", "default": False, "periods": ["month", "quarter"]}]
        return {"family": family, "regions": options(regions), "controls": controls, "defaults": defaults,
                "quarterOptionsByRegionMode": quarter_options, "years": sorted(int(y) for y in df.year.dropna().unique())}
    defaults.update({"regions": regions[:1], "index_base": "month_to_month"})
    controls += [{"id": "regions", "type": "multiselect", "options": options(regions), "default": regions[:1]},
                 {"id": "index_base", "type": "select", "options": [{"id": key, "label": value} for key, value in INDEX_BASES.items()],
                  "default": "month_to_month", "periods": ["month", "quarter"]}]
    return {"family": family, "regions": options(regions), "controls": controls, "defaults": defaults,
            "years": sorted(int(y) for y in df.year.dropna().unique())}


def chosen_list(value, available, default):
    if value is None:
        return list(default)
    # A blank repeated parameter explicitly represents an empty UI multiselect.
    value = [] if value == [""] else list(value)
    if any(item not in available for item in value):
        raise FilterUnavailable("Economics filter is unavailable")
    return list(dict.fromkeys(value))


class EconomicsService:
    def __init__(self, context, family, check_interval=5):
        self.context, self.family = context, family
        self.loader = LOADERS[family]
        self.check_interval = check_interval
        self._lock = threading.RLock()
        self._signature = self._data = self._catalog = self._metadata = None
        self._source_names = []
        self._checked = 0.0

    def _references(self, names):
        paths = []
        for name in names:
            if Path(name).name == name and name not in {".", ".."}:
                path = self.context.downloads / name
                paths += [path, receipt_path(path)]
        return tuple(file_signature(path) for path in sorted(set(paths)))

    def _versions(self, access, names):
        return self.context.cache_key(), access.data_version(self.loader), self._references(names)

    def _relative(self, path):
        try:
            return Path(path).resolve().relative_to(self.context.root).as_posix()
        except ValueError:
            return str(path)

    def _refresh(self):
        now = time.monotonic()
        if self._metadata is not None and now - self._checked < self.check_interval:
            return
        access = DataAccess(self.context)
        before = self._versions(access, self._source_names)
        if before == self._signature:
            self._checked = now
            return
        data = getattr(access, self.loader)().copy(deep=True)
        if any(level == "error" for level, _ in access.issues):
            raise SourceUnavailable("Economics loader failed")
        catalog = build_catalog(self.family, data)
        display_data = data[data.year.ge(2011)].copy() if self.family == "accounts" else data
        names = sorted(str(name) for name in display_data.source_file.dropna().unique())
        signature = self._versions(access, names)
        if before[:2] != signature[:2]:
            raise SourceUnavailable("Economics inputs changed while loading")
        provenance = source_provenance(display_data, self.context.downloads)
        dates = pd.to_datetime(provenance["Дата файла (МСК)"].replace("неизвестно", None),
                               format="%d.%m.%Y %H:%M", errors="coerce")
        files = [self._relative(item[0]) for item in signature[1] if item[1] is not None]
        metadata = {"schemaVersion": 1, "version": hashlib.sha256(repr(signature).encode()).hexdigest(),
                    "generatedAt": datetime.now(timezone.utc).isoformat(),
                    "source": {"date": dates.max().strftime("%d.%m.%Y") if dates.notna().any() else None,
                               "files": files, "issues": [list(item) for item in access.issues], "fileEvidence": "candidates",
                               "recordedSources": names, "recordedSourceEvidence": "source_file values in loaded rows, not verified raw selection",
                               "dateEvidence": "Modification date of row-recorded local sources (Moscow), not a download date",
                               "downloadSummary": download_summary(provenance),
                               "provenance": {"columns": [{"id": col, "label": col} for col in provenance.columns],
                                              "rows": records(provenance)}}}
        for row in metadata["source"]["provenance"]["rows"]:
            row["Исходный файл"] = self._relative(row["Исходный файл"])
        json.dumps({**metadata, **catalog}, allow_nan=False)
        if signature != self._versions(access, names):
            raise SourceUnavailable("Economics sources changed while reading provenance")
        self._data, self._catalog, self._metadata = data, catalog, metadata
        self._signature, self._source_names, self._checked = signature, names, now

    def catalog(self):
        with self._lock:
            self._refresh()
            return copy.deepcopy({**self._metadata, **self._catalog})

    def _selection(self, filters):
        defaults = self._catalog["defaults"]
        selected = {key: filters.get(key) if filters.get(key) is not None else copy.deepcopy(value)
                    for key, value in defaults.items()}
        if self.family == "accounts":
            for i in range(1, 5):
                key = f"block{i}_regions"
                selected[key] = chosen_list(filters.get(key), [MSK, RF], defaults[key])
            for prefix in ("structure", "index"):
                region = selected[f"{prefix}_region"]
                if region not in (MSK, RF):
                    raise FilterUnavailable("Accounts region is unavailable")
                if prefix == "structure":
                    mode = selected["structure_mode"]
                    if mode not in ("value", "share"):
                        raise FilterUnavailable("Structure mode is unavailable")
                    available = self._catalog["structureIndustries"][region][mode]
                else:
                    available = self._catalog["indexIndustries"][region]
                available = [item["id"] for item in available]
                key = f"{prefix}_industries"
                selected[key] = chosen_list(filters.get(key), available, default_industries(available))
            return selected
        if selected["period"] not in ("year", "quarter", "month"):
            raise FilterUnavailable("Economics period is unavailable")
        regions = [item["id"] for item in self._catalog["regions"]]
        if self.family == "salary":
            if selected["region"] not in regions:
                raise FilterUnavailable("Salary region is unavailable")
            selected["views"] = chosen_list(filters.get("views"), VIEWS, VIEWS)
            sub = aggregate_quarter(self._data, selected["ytd"])
            available_q = sorted(int(q) for q in sub.loc[
                sub.region.eq(selected["region"]) & sub.view.isin(selected["views"]), "quarter"].dropna().unique())
        else:
            selected["regions"] = chosen_list(filters.get("regions"), regions, defaults["regions"])
            if selected["index_base"] not in INDEX_BASES:
                raise FilterUnavailable("IPC index base is unavailable")
            available_q = [1, 2, 3, 4]
        selected["quarters"] = chosen_list(filters.get("quarters"), available_q if selected["period"] == "quarter" else [1, 2, 3, 4],
                                          available_q if selected["period"] == "quarter" else [1, 2, 3, 4])
        selected["months"] = chosen_list(filters.get("months"), list(range(1, 13)), list(range(1, 13)))
        return selected

    def report(self, **filters):
        with self._lock:
            self._refresh()
            selection = self._selection(filters)
            builder = {"salary": salary_report, "ipc": ipc_report, "accounts": accounts_report}[self.family]
            result = {**self._metadata, "family": self.family, "selection": selection,
                      **builder(self._data, **selection)}
            json.dumps(result, allow_nan=False)
            return copy.deepcopy(result)
