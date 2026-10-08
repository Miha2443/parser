"""Read-only ETL history, monitoring changes, and mart freshness diagnostics."""
import hashlib
import json
from datetime import datetime, timedelta, timezone

import pandas as pd

from app.audit import last_run_summary, last_success_per_indicator, realty_update_status_summary
from backend.profile_service import FilterUnavailable, SourceUnavailable
from pipeline.audit import read_audit
from pipeline.data_access import DataAccess, file_signature
from pipeline.notifier import format_summary
from pipeline.registry import INDICATORS


def safe(value):
    if isinstance(value, dict):
        return {str(key): safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [safe(item) for item in value]
    if value is None or pd.isna(value):
        return None
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if hasattr(value, "item"):
        return safe(value.item())
    return value


class UpdatesService:
    def __init__(self, context):
        self.context = context
        self.access = DataAccess(context)
        self.paths = [context.root / suffix for suffix in (
            "data/processed/etl_audit.jsonl", "data/processed/realty_update_status.json",
            "data/processed/monitoring_2_0_changes.jsonl", "data/marts/realty/manifest.json")]

    def _signature(self):
        return tuple(file_signature(path) if path.exists() else None for path in self.paths)

    @staticmethod
    def _json(path):
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def report(self, days=30, monitoring_days=30, only_errors=False):
        if days not in (7, 30, 90) or monitoring_days not in (7, 30, 90):
            raise FilterUnavailable("History period is unavailable")
        before = self._signature()
        now = datetime.now()
        raw = read_audit(self.paths[0])
        frame = pd.DataFrame(raw)
        titles = {item.id: item.title for item in INDICATORS}
        sources = {item.id: item.source for item in INDICATORS}
        last, per_indicator, events, preview = {}, [], [], ""
        if not frame.empty:
            frame["ts"] = pd.to_datetime(frame["ts"], errors="coerce")
            for column in ("rows", "new_date", "duration_sec"):
                if column not in frame:
                    frame[column] = None
            last = last_run_summary(frame)
            per_indicator = last_success_per_indicator(frame).to_dict("records")
            shown = frame[frame["ts"].ge(now - timedelta(days=days)) & frame["indicator"].ne("_run")]
            if only_errors:
                shown = shown[shown["status"].eq("error")]
            events = shown.sort_values("ts", ascending=False).to_dict("records")
            preview = format_summary(frame[frame["run_id"].eq(last["run_id"])].to_dict("records")) or ""
        for row in [*events, *per_indicator]:
            row["title"] = titles.get(row["indicator"], row["indicator"])
            row["source"] = sources.get(row["indicator"], "")
        status = self._json(self.paths[1])
        status_summary = realty_update_status_summary(status)
        if isinstance(status.get("log_file"), str):
            warnings = [item for item in status_summary.get("warnings", []) if item != "log missing"]
            if status["log_file"] and not (self.context.root / status["log_file"]).is_file():
                warnings.append("log missing")
            status_summary["warnings"] = warnings
        changes = []
        for event in read_audit(self.paths[2]):
            detected = pd.to_datetime(event.get("detected_at"), errors="coerce")
            if pd.isna(detected):
                continue
            for field, action in (("added", "Добавлено"), ("removed", "Удалено")):
                changes.extend({**item, "event_id": event.get("event_id"), "detected_at": detected,
                                "action": action} for item in event.get(field, []) if isinstance(item, dict))
        changes.sort(key=lambda row: row["detected_at"], reverse=True)
        latest_changes = [row for row in changes if row["event_id"] == changes[0]["event_id"]] if changes else []
        selected_changes = [row for row in changes if row["detected_at"] >= now - timedelta(days=monitoring_days)]
        manifest = self._json(self.paths[3])
        marts = []
        for name, info in sorted((manifest.get("marts") or {}).items()):
            if not isinstance(info, dict):
                continue
            summary = info.get("summary") or {}
            count = summary.get("rows")
            if count is None and isinstance(summary.get("frames"), dict):
                count = sum(item.get("rows", 0) for item in summary["frames"].values() if isinstance(item, dict))
            try:
                candidates = self.access.source_files(name)
            except KeyError:
                candidates = []
            newest = max((datetime.fromtimestamp(path.stat().st_mtime) for path in candidates), default=None)
            built = pd.to_datetime(info.get("built_at") or manifest.get("built_at"), errors="coerce")
            stale = newest is not None and not pd.isna(built) and pd.Timestamp(newest) > built
            marts.append({"mart": name, "status": "error" if info.get("error") else "stale" if stale else "ok",
                          "rows": count, "sources": len(info.get("sources") or []), "built_at": built,
                          "latest_source_mtime": newest, "duration_sec": info.get("duration_sec"),
                          "error": info.get("error", "")})
        if before != self._signature():
            raise SourceUnavailable("Update history changed while reading")
        result = safe({"schemaVersion": 1, "version": hashlib.sha256(repr(before).encode()).hexdigest(),
                       "generatedAt": datetime.now(timezone.utc),
                       "source": {"date": None, "files": [path.relative_to(self.context.root).as_posix()
                                                             for path in self.paths if path.exists()], "issues": []},
                       "selection": {"days": days, "monitoringDays": monitoring_days, "onlyErrors": only_errors},
                       "lastRun": last, "realtyStatus": status, "realtySummary": status_summary,
                       "indicators": per_indicator, "events": events, "monitoringChanges": selected_changes,
                       "latestMonitoring": {"date": latest_changes[0]["detected_at"] if latest_changes else None,
                                            "added": sum(row["action"] == "Добавлено" for row in latest_changes),
                                            "removed": sum(row["action"] == "Удалено" for row in latest_changes)},
                       "marts": marts, "telegramPreview": preview})
        json.dumps(result, allow_nan=False)
        return result
