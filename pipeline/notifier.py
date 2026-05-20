"""Telegram-нотификатор. Silent no-op если нет config/telegram.json.

Шлёт одно сообщение со сводкой запуска ETL. Реальные token/chat_id пользователь
впишет в `config/telegram.json` — модуль сразу начнёт работать без правок кода.
"""
from __future__ import annotations

import json
from pathlib import Path

from pipeline.paths import CONFIG_DIR

CONFIG_PATH = CONFIG_DIR / "telegram.json"

STATUS_EMOJI = {"success": "✅", "skip": "⏭", "error": "❌", "info": "ℹ️"}


def _load_config() -> dict | None:
    if not CONFIG_PATH.exists():
        return None
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if not cfg.get("token") or not cfg.get("chat_id"):
        return None
    return cfg


def format_summary(entries: list[dict]) -> str:
    """Готовит текст сообщения. Используется в тестах и страницей UI для предпросмотра."""
    indicator_entries = [e for e in entries if e.get("indicator") != "_run"]
    finished = next((e for e in entries if e.get("indicator") == "_run"), None)

    success = [e for e in indicator_entries if e["status"] == "success"]
    skipped = [e for e in indicator_entries if e["status"] == "skip"]
    errors = [e for e in indicator_entries if e["status"] == "error"]

    when = (finished or (indicator_entries[-1] if indicator_entries else {})).get("ts", "")
    lines = [f"🔄 ETL {when}"]
    if finished:
        lines.append(f"Длительность: {finished.get('duration_sec', '—')} c")
    lines.append("")

    if success:
        lines.append(f"✅ Обновлено ({len(success)}):")
        for e in success:
            prev_new = ""
            if e.get("prev_date") or e.get("new_date"):
                prev_new = f": {e.get('prev_date', '—')} → {e.get('new_date', '—')}"
            lines.append(f"• {e['indicator']}{prev_new}")
        lines.append("")
    if skipped:
        lines.append(f"⏭ Без изменений ({len(skipped)}): {', '.join(e['indicator'] for e in skipped)}")
        lines.append("")
    if errors:
        lines.append(f"❌ Ошибки ({len(errors)}):")
        for e in errors:
            lines.append(f"• {e['indicator']}: {e.get('error', 'unknown')}")
    return "\n".join(lines).strip()


def send_summary(entries: list[dict]) -> bool:
    """Возвращает True если отправлено, False если конфига нет (silent)."""
    cfg = _load_config()
    if cfg is None:
        return False
    text = format_summary(entries)
    if not text:
        return False
    return _send(cfg["token"], cfg["chat_id"], text)


def send_critical(text: str) -> bool:
    cfg = _load_config()
    if cfg is None:
        return False
    return _send(cfg["token"], cfg["chat_id"], f"🚨 ETL crashed\n\n{text}")


def _send(token: str, chat_id: str, text: str) -> bool:
    """Отправляет одно сообщение через Bot API. requests опционален."""
    try:
        import requests
    except ImportError:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        resp = requests.post(
            url,
            data={"chat_id": chat_id, "text": text, "disable_web_page_preview": "true"},
            timeout=30,
        )
        return resp.ok
    except Exception:
        return False
