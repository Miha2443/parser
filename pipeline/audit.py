"""Audit log событий ETL — JSONL по одной строке на (запуск, показатель).

Формат строки:
    {"run_id": "...", "ts": "...", "indicator": "...", "status": "success|skip|error", ...}

`run_id` группирует строки одного запуска; `app/audit.py` использует его для агрегирования.
"""
from __future__ import annotations

import json
import time
import traceback
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from pipeline.paths import ETL_AUDIT_LOG


class AuditRun:
    """Контекст одного запуска ETL: один run_id, общая запись в JSONL."""

    def __init__(self, path: Path = ETL_AUDIT_LOG):
        self.path = path
        self.run_id = uuid.uuid4().hex[:12]
        self.started_at = datetime.now()
        self._entries: list[dict[str, Any]] = []
        path.parent.mkdir(parents=True, exist_ok=True)

    # ── публичный API ─────────────────────────────────────────────
    def success(self, indicator: str, **fields: Any) -> None:
        self._write(indicator, "success", **fields)

    def skip(self, indicator: str, reason: str = "", **fields: Any) -> None:
        self._write(indicator, "skip", reason=reason, **fields)

    def error(self, indicator: str, exc: BaseException, **fields: Any) -> None:
        self._write(
            indicator,
            "error",
            error=f"{type(exc).__name__}: {exc}",
            traceback=traceback.format_exc(limit=8),
            **fields,
        )

    def info(self, indicator: str, message: str, **fields: Any) -> None:
        self._write(indicator, "info", message=message, **fields)

    def finalize(self) -> dict[str, int]:
        """Финальная запись run-уровня и подсчёт сводки."""
        summary = {
            "success": sum(1 for e in self._entries if e["status"] == "success"),
            "skip": sum(1 for e in self._entries if e["status"] == "skip"),
            "error": sum(1 for e in self._entries if e["status"] == "error"),
        }
        self._write(
            "_run",
            "finished",
            duration_sec=round(time.time() - self.started_at.timestamp(), 1),
            **summary,
        )
        return summary

    @property
    def entries(self) -> list[dict[str, Any]]:
        return list(self._entries)

    # ── внутреннее ────────────────────────────────────────────────
    def _write(self, indicator: str, status: str, **fields: Any) -> None:
        entry = {
            "run_id": self.run_id,
            "ts": datetime.now().isoformat(timespec="seconds"),
            "indicator": indicator,
            "status": status,
            **fields,
        }
        self._entries.append(entry)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read_audit(path: Path = ETL_AUDIT_LOG) -> list[dict[str, Any]]:
    """Читает все записи JSONL. Если файла нет — пустой список."""
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out
