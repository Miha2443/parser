"""Уведомления через TDM (TamTam-совместимый Bot API).

Использование:
    from pipeline.tdm_notify import notify, notify_file

    notify("Прогон realty завершён: 5/5 ОК")
    notify_file("data/processed/report.xlsx", caption="Отчёт за июнь")

Настройка через env-переменные:
    TDM_BOT_TOKEN   — токен бота (НЕ коммитить в git!)
    TDM_CHAT_ID     — ID чата куда слать (или TDM_USER_ID)
    TDM_API_BASE    — base URL API (default: https://botapi.tamtam.chat)
    TDM_DISABLED    — '1' чтобы временно выключить (полезно в dev)

Как узнать chat_id:
    1. Добавь бота в нужный чат / напиши ему лично
    2. Запусти: py -m pipeline.tdm_notify --list-chats
       (выведет последние диалоги бота с их ID)
    3. Скопируй нужный ID в TDM_CHAT_ID

Утилиты для CLI:
    py -m pipeline.tdm_notify --test            # отправить тестовое
    py -m pipeline.tdm_notify --list-chats      # последние чаты бота
    py -m pipeline.tdm_notify --me              # инфо о боте (проверка токена)
    py -m pipeline.tdm_notify "Текст сообщения" # быстрая отправка
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

import requests

API_BASE_DEFAULT = "https://botapi.tamtam.chat"
TIMEOUT = 20
MAX_RETRIES = 3
RETRY_BACKOFF = (2, 4, 8)  # секунды


def _is_disabled() -> bool:
    return os.environ.get("TDM_DISABLED", "").strip() in ("1", "true", "yes")


def _get_token() -> Optional[str]:
    token = os.environ.get("TDM_BOT_TOKEN", "").strip()
    return token or None


def _get_chat_id() -> Optional[str]:
    chat = os.environ.get("TDM_CHAT_ID", "").strip()
    user = os.environ.get("TDM_USER_ID", "").strip()
    return chat or user or None


def _api_base() -> str:
    return os.environ.get("TDM_API_BASE", "").strip() or API_BASE_DEFAULT


def _params(extra: dict | None = None) -> dict:
    token = _get_token()
    if not token:
        raise RuntimeError(
            "TDM_BOT_TOKEN не задан. Установи env-переменную с токеном бота.")
    out = {"access_token": token}
    if extra:
        out.update(extra)
    return out


def _request(method: str, path: str, *, params: dict | None = None,
             json_body: dict | None = None, files: dict | None = None,
             data: dict | None = None) -> tuple[int, dict]:
    """Запрос с retry на сетевые/5xx ошибки. Возвращает (status, json)."""
    url = f"{_api_base().rstrip('/')}/{path.lstrip('/')}"
    last_exc: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.request(
                method, url,
                params=params, json=json_body, files=files, data=data,
                timeout=TIMEOUT,
            )
            if resp.status_code < 500 or attempt == MAX_RETRIES - 1:
                try:
                    body = resp.json()
                except json.JSONDecodeError:
                    body = {"raw": resp.text}
                return resp.status_code, body
        except requests.RequestException as exc:
            last_exc = exc
        if attempt < MAX_RETRIES - 1:
            time.sleep(RETRY_BACKOFF[attempt])
    if last_exc:
        raise last_exc
    return 599, {"error": "max retries exceeded"}


def me() -> dict:
    """Информация о боте — проверка что токен валиден."""
    _, body = _request("GET", "/me", params=_params())
    return body


def list_chats(count: int = 20) -> list[dict]:
    """Последние чаты бота — чтобы узнать chat_id."""
    _, body = _request("GET", "/chats", params=_params({"count": count}))
    return body.get("chats", []) if isinstance(body, dict) else []


def notify(text: str, *, chat_id: str | None = None,
           silent: bool = False) -> bool:
    """Отправляет текст. Возвращает True при успехе.

    silent=True — не печатает ошибки в stdout (для авто-вызовов
    из скриптов чтобы не загромождать лог).
    """
    if _is_disabled():
        return False
    if not text:
        return False
    chat = chat_id or _get_chat_id()
    if not chat:
        if not silent:
            print("⚠️  TDM_CHAT_ID не задан — уведомление пропущено")
        return False
    try:
        status, body = _request(
            "POST", "/messages",
            params=_params({"chat_id": chat}),
            json_body={"text": text[:4000]},  # лимит длины сообщения
        )
        if status >= 400:
            if not silent:
                print(f"⚠️  TDM notify failed [{status}]: {body}")
            return False
        return True
    except Exception as exc:  # noqa: BLE001
        if not silent:
            print(f"⚠️  TDM notify exception: {exc}")
        return False


def notify_file(path: str | Path, *, caption: str = "",
                chat_id: str | None = None) -> bool:
    """Отправляет файл (xlsx, png, pdf, ...) как вложение."""
    if _is_disabled():
        return False
    p = Path(path)
    if not p.is_file():
        print(f"⚠️  Файл не найден: {p}")
        return False
    chat = chat_id or _get_chat_id()
    if not chat:
        print("⚠️  TDM_CHAT_ID не задан")
        return False
    # TamTam: получаем upload URL → грузим → шлём сообщение с
    # attachment.upload.token
    try:
        st, body = _request(
            "POST", "/uploads",
            params=_params({"type": "file"}),
        )
        if st >= 400 or "url" not in body:
            print(f"⚠️  upload init failed: {body}")
            return False
        upload_url = body["url"]
        with p.open("rb") as f:
            r = requests.post(upload_url, files={"data": (p.name, f)},
                              timeout=120)
            r.raise_for_status()
            up_body = r.json()
        token = up_body.get("token") or (up_body.get("file") or {}).get("token")
        if not token:
            print(f"⚠️  upload returned no token: {up_body}")
            return False
        msg = {
            "text": caption[:4000] if caption else "",
            "attachments": [{"type": "file", "payload": {"token": token}}],
        }
        st2, body2 = _request(
            "POST", "/messages",
            params=_params({"chat_id": chat}),
            json_body=msg,
        )
        if st2 >= 400:
            print(f"⚠️  notify_file failed: {body2}")
            return False
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️  notify_file exception: {exc}")
        return False


# === CLI ===

def main():
    parser = argparse.ArgumentParser(description="TDM (TamTam-bot) уведомления")
    parser.add_argument("text", nargs="?", default=None,
                        help="Текст для отправки")
    parser.add_argument("--me", action="store_true",
                        help="Инфо о боте (проверка токена)")
    parser.add_argument("--list-chats", action="store_true",
                        help="Последние чаты бота (для chat_id)")
    parser.add_argument("--test", action="store_true",
                        help="Отправить тестовое сообщение")
    parser.add_argument("--file", help="Отправить файл (приклади --text для caption)")
    args = parser.parse_args()

    if not _get_token():
        print("⚠️  TDM_BOT_TOKEN не задан в env.\n"
              "    Windows: set TDM_BOT_TOKEN=<твой_токен>\n"
              "    Powershell: $env:TDM_BOT_TOKEN='<твой_токен>'")
        return 1

    if args.me:
        info = me()
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return 0

    if args.list_chats:
        chats = list_chats()
        if not chats:
            print("(пусто) Напиши боту что-нибудь — потом запусти ещё раз.")
            return 0
        print(f"Найдено {len(chats)} чатов:")
        for c in chats:
            cid = c.get("chat_id") or c.get("id")
            title = c.get("title") or c.get("dialog_with_user", {}).get("name") or "—"
            ctype = c.get("type") or "—"
            print(f"  {cid}  [{ctype}]  {title}")
        return 0

    if args.test:
        ok = notify("✅ Тестовое сообщение от parser_bot")
        print("OK" if ok else "FAIL")
        return 0 if ok else 2

    if args.file:
        ok = notify_file(args.file, caption=args.text or "")
        print("OK" if ok else "FAIL")
        return 0 if ok else 2

    if args.text:
        ok = notify(args.text)
        print("OK" if ok else "FAIL")
        return 0 if ok else 2

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
