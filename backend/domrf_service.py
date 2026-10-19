"""Literal DOM.RF snapshots and observed history from the active source registry."""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
import math
import re
import threading
import time

from backend.profile_service import FilterUnavailable, SourceUnavailable
from pipeline.data_access import DataAccess, DataContext, MONTH_NAMES_RU, file_signature


REGIONS = {"msk": "Город Москва", "rf": "Российская Федерация"}
_SUFFIX = r"(?:%|тыс\.?\s*м[²2]|млн\.?\s*руб\.?|руб\.?)?"
_NUMBER = re.compile(r"^\s*([+-]?(?:\d[\d\s]*)(?:[.,]\d+)?)\s*" + _SUFFIX + r"\s*$", re.I)


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value if math.isfinite(value) else None
    if not isinstance(value, str) or not value.strip():
        return None
    match = _NUMBER.fullmatch(value)
    if not match:
        return None
    result = float(re.sub(r"\s", "", match[1]).replace(",", "."))
    return result if math.isfinite(result) else None


def _period(value):
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"\s*([а-яё]+)\s+(\d{4})\s*", value, re.I)
    if match and match[1].casefold() in MONTH_NAMES_RU:
        return f"{match[2]}-{MONTH_NAMES_RU.index(match[1].casefold()) + 1:02d}"
    return None


def _report_date(value):
    if not isinstance(value, str) or not value.strip():
        return None
    for pattern in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value.strip(), pattern).date().isoformat()
        except ValueError:
            pass
    return None


def _reject_constant(value):
    raise ValueError(f"Nonfinite JSON constant: {value}")


def _collected_ns(row, payload, fallback):
    for value in (row.get("collected_at"), payload.get("scraped_at")):
        if not isinstance(value, str):
            continue
        try:
            collected = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if collected.tzinfo is not None:
                return int(collected.timestamp() * 1_000_000_000)
        except (ValueError, OverflowError, OSError):
            pass
    return fallback


class DomrfService:
    def __init__(self, context: DataContext, check_interval: float = 5):
        self.context = context
        self.check_interval = check_interval
        self._lock = threading.RLock()
        self._checked = 0.0
        self._signature = None
        self._generation = None

    def _inputs(self):
        access = DataAccess(self.context)
        files = sorted({path for path in access.source_files("construction_operational")
                        if path.suffix.lower() == ".json" and path.name.startswith("construction_operational_")}
                       | set(access.source_files("construction_history")))
        signatures = tuple(file_signature(path) for path in files)
        if not files or any(signature[1] is None for signature in signatures):
            raise SourceUnavailable("DOM.RF JSON snapshots are missing")
        return files, signatures

    def _relative(self, path):
        try:
            return path.resolve().relative_to(self.context.root).as_posix()
        except ValueError:
            return path.name

    @staticmethod
    def _metric(value, issues, reference):
        parsed = _number(value)
        if parsed is None and value is not None and str(value).strip():
            issues.append({"level": "warning", "message": f"{reference}: некорректное число; показано null."})
        return parsed

    def _snapshots(self, payload, block, path, signature, issues):
        rows = payload.get(block)
        if rows is None:
            rows = []
        if not isinstance(rows, list):
            raise SourceUnavailable(f"Invalid {block} block")
        grouped = {}
        for row in rows:
            if not isinstance(row, dict):
                raise SourceUnavailable(f"Invalid {block} row")
            region = row.get("region_key")
            if region not in REGIONS:
                continue
            if block == "construction" and row.get("area_kind") not in ("living", "total"):
                continue
            label = row.get("report_period")
            if label is None:
                label = ""
            if not isinstance(label, str):
                raise SourceUnavailable("Invalid report period")
            period = _period(label)
            key = (region, period)
            if block == "sales" and key in grouped:
                raise SourceUnavailable("Duplicate sales snapshot within one file")
            snapshot = grouped.setdefault(key, {"period": period, "label": label, "dates": set(),
                                                "metrics": {}, "sourceFile": self._relative(path),
                                                "mtime": signature[2], "freshness": _collected_ns(row, payload, signature[2])})
            snapshot["freshness"] = max(snapshot["freshness"], _collected_ns(row, payload, signature[2]))
            date = _report_date(row.get("report_date"))
            snapshot["dates"].add(date)
            reference = f"{self._relative(path)}: {block}/{region}/{label}"
            if period is None:
                issues.append({"level": "warning", "message": reference + ": период отсутствует или не распознан; "
                               "запись не включена в месячную историю."})
            if block == "construction":
                area = row.get("area_kind")
                if area in snapshot["metrics"]:
                    raise SourceUnavailable("Duplicate construction metric within one snapshot")
                snapshot["metrics"][area] = self._metric(row.get("area_thousand_m2"), issues, reference + "/" + area)
            else:
                metrics = row.get("metrics")
                if metrics is None:
                    metrics = {}
                if not isinstance(metrics, dict):
                    raise SourceUnavailable("Invalid or duplicate sales snapshot")
                snapshot["metrics"] = {name: self._metric(value, issues, reference + "/" + name)
                                       for name, value in metrics.items()}
        for (region, _), snapshot in grouped.items():
            dates = snapshot.pop("dates")
            snapshot["reportDate"] = next(iter(dates)) if len(dates) == 1 else None
            if len(dates) > 1:
                issues.append({"level": "warning", "message": snapshot["sourceFile"] +
                               ": разные даты строк одного блока; дата блока показана как null."})
            if block == "construction":
                snapshot["metrics"] = {key: snapshot["metrics"].get(key) for key in ("living", "total")}
            yield region, snapshot

    @staticmethod
    def _block(snapshots, block):
        if not snapshots:
            return {"period": None, "metrics": {"living": None, "total": None} if block == "construction" else None,
                    "history": []}
        observed = [row for row in snapshots if row["period"] is not None]
        latest = max(observed, key=lambda row: row["period"]) if observed else None
        unknown = [row for row in snapshots if row["period"] is None]
        # A newly published block without a period cannot silently inherit old chronology.
        if unknown and (latest is None or max(row["freshness"] for row in unknown) > latest["freshness"]):
            latest = max(unknown, key=lambda row: (row["freshness"], row["sourceFile"]))
        history = []
        for row in sorted(observed, key=lambda row: row["period"]):
            item = {key: row[key] for key in ("period", "label", "reportDate", "sourceFile")}
            item.update(row["metrics"] if block == "construction" else {"metrics": row["metrics"]})
            history.append(item)
        return {"period": {"id": latest["period"], "label": latest["label"], "reportDate": latest["reportDate"]},
                "metrics": latest["metrics"], "history": history}

    def _refresh(self):
        now = time.monotonic()
        if self._generation is not None and now - self._checked < self.check_interval:
            return
        files, signatures = self._inputs()
        if signatures == self._signature:
            self._checked = now
            return
        issues, snapshots = [], {region: {block: {} for block in ("construction", "sales")} for region in REGIONS}
        for path, signature in zip(files, signatures):
            payload = json.loads(path.read_text(encoding="utf-8-sig"), parse_constant=_reject_constant)
            if not isinstance(payload, dict):
                raise SourceUnavailable("Invalid DOM.RF JSON document")
            for block in ("construction", "sales"):
                for region, snapshot in self._snapshots(payload, block, path, signature, issues):
                    current = snapshots[region][block].get(snapshot["period"])
                    if current is None or (snapshot["freshness"], snapshot["sourceFile"]) > (current["freshness"], current["sourceFile"]):
                        snapshots[region][block][snapshot["period"]] = snapshot
        current_files = [(path, sig) for path, sig in zip(files, signatures) if re.fullmatch(r"construction_operational_\d{8}", path.stem)]
        latest_path = max(current_files, key=lambda pair: (pair[1][2], str(pair[0])))[0] if current_files else None
        match = re.fullmatch(r"construction_operational_(\d{8})", latest_path.stem) if latest_path else None
        source_date = None
        if match:
            try:
                source_date = datetime.strptime(match[1], "%Y%m%d").date().isoformat()
            except ValueError:
                pass
        metadata = {"schemaVersion": 1, "version": hashlib.sha256(repr(signatures).encode()).hexdigest(),
                    "generatedAt": datetime.now(timezone.utc).isoformat(),
                    "source": {"date": source_date, "files": [self._relative(path) for path in files],
                               "fileEvidence": "selected", "issues": issues,
                               "dateEvidence": "Дата файла относится к текущему срезу. Исторические наблюдения имеют собственные отчётные периоды и даты; время обновления накопительного файла не меняет их свежесть."}}
        generation = {}
        for region, blocks in snapshots.items():
            generation[region] = {**metadata, "region": {"id": region, "label": REGIONS[region]},
                                  **{block: self._block(list(rows.values()), block) for block, rows in blocks.items()},
                                  "notes": ["История содержит только наблюдавшиеся периоды зарегистрированных JSON; "
                                            "период определяется report_period, а не датой отчёта.",
                                            "Одинаковый период: выбран полный снимок с последним временем сбора записи, иначе mtime; поля разных "
                                            "файлов не смешиваются. construction и sales имеют независимые периоды и даты.",
                                            "Площади construction living/total и sales *_thousand_m2: тыс. м²; *_pct: "
                                            "исходные проценты; price_per_m2_rub: руб./м²; funds_million_rub: млн руб. "
                                            "Никаких пересчётов цены, денежных средств или восстановления истории из процентов.",
                                            "Распроданность, стройготовность, прогноз и шесть прежних таблиц "
                                            "остаются в отдельном старом API распроданности."]}
        json.dumps(generation, allow_nan=False)
        if self._inputs()[1] != signatures:
            raise SourceUnavailable("DOM.RF sources changed during loading")
        self._generation, self._signature, self._checked = generation, signatures, now

    def report(self, region="msk"):
        if region not in REGIONS:
            raise FilterUnavailable("DOM.RF region is unavailable")
        with self._lock:
            try:
                self._refresh()
            except (OSError, ValueError, TypeError, LookupError, RuntimeError, OverflowError) as exc:
                self._generation = self._signature = None
                self._checked = 0.0
                raise SourceUnavailable("DOM.RF snapshots are unavailable") from exc
            return copy.deepcopy(self._generation[region])
