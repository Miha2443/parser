"""Уведомления в корпоративный мессенджер (TDM мэрии Москвы / VK Teams /
TamTam — все они используют похожие Bot API).

Поддерживаемые API-стили:
  - MyTeam / VK Teams (api.internal.myteam.mail.ru/bot/v1/):
      auth: ?token=...
      send: /messages/sendText?chatId=...&text=...
      file: /messages/sendFile (multipart)
      self: /self/get
  - TamTam (botapi.tamtam.chat):
      auth: ?access_token=...
      send: POST /messages?chat_id=... body={text:...}
      self: GET /me

TDM Мос.Ру — скорее всего форк VK Teams. Если у тебя другой API
endpoint, поменяй TDM_API_BASE в env.

Настройка через env:
    TDM_BOT_TOKEN   — токен бота (НЕ коммитить!)
    TDM_CHAT_ID     — chat_id куда слать
    TDM_API_BASE    — base URL (default: https://api.internal.myteam.mail.ru/bot/v1)
    TDM_API_STYLE   — 'myteam' (default) или 'tamtam'
    TDM_DISABLED    — '1' чтобы выключить

CLI:
    py -m pipeline.tdm_notify --me                 проверить токен
    py -m pipeline.tdm_notify --detect             автоподбор API
    py -m pipeline.tdm_notify --list-chats         чаты бота
    py -m pipeline.tdm_notify --test               тестовое
    py -m pipeline.tdm_notify "текст"              отправить
    py -m pipeline.tdm_notify --file report.xlsx   файл
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

# Списки API для autodetect: (style, base_url)
API_CANDIDATES = [
    # VK Teams / MyTeam — для корпоративных мессенджеров (mail.ru)
    ("myteam", "https://api.internal.myteam.mail.ru/bot/v1"),
    # TDM мэрии Москвы — наиболее вероятные варианты base URL
    ("myteam", "https://api.tdm.mos.ru/bot/v1"),
    ("myteam", "https://web.tdm.mos.ru/api/bot/v1"),
    ("myteam", "https://tdm.mos.ru/bot/v1"),
    # ICQ (публичный)
    ("myteam", "https://api.icq.net/bot/v1"),
    # TamTam
    ("tamtam", "https://botapi.tamtam.chat"),
]

TIMEOUT = 20
MAX_RETRIES = 3
RETRY_BACKOFF = (2, 4, 8)


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
    return os.environ.get("TDM_API_BASE", "").strip() or API_CANDIDATES[0][1]


def _api_style() -> str:
    return (os.environ.get("TDM_API_STYLE", "").strip().lower()
            or API_CANDIDATES[0][0])


def _auth_params(extra: dict | None = None) -> dict:
    """Параметры авторизации в зависимости от стиля API."""
    token = _get_token()
    if not token:
        raise RuntimeError(
            "TDM_BOT_TOKEN не задан. Установи env-переменную с токеном.")
    style = _api_style()
    key = "token" if style == "myteam" else "access_token"
    out = {key: token}
    if extra:
        out.update(extra)
    return out


def _request(method: str, path: str, *, params: dict | None = None,
             json_body: dict | None = None, files: dict | None = None,
             data: dict | None = None,
             base: str | None = None) -> tuple[int, dict, str]:
    """Запрос с retry. Возвращает (status, json/dict, raw_text)."""
    url = f"{(base or _api_base()).rstrip('/')}/{path.lstrip('/')}"
    last_exc: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.request(
                method, url,
                params=params, json=json_body, files=files, data=data,
                timeout=TIMEOUT,
            )
            text = resp.text
            try:
                body = resp.json()
                if not isinstance(body, dict):
                    body = {"data": body}
            except (json.JSONDecodeError, ValueError):
                body = {"raw": text[:500]}
            if resp.status_code < 500 or attempt == MAX_RETRIES - 1:
                return resp.status_code, body, text
        except requests.RequestException as exc:
            last_exc = exc
        if attempt < MAX_RETRIES - 1:
            time.sleep(RETRY_BACKOFF[attempt])
    if last_exc:
        raise last_exc
    return 599, {"error": "max retries exceeded"}, ""


def me(base: str | None = None) -> dict:
    """Информация о боте — для проверки токена."""
    style = _api_style()
    path = "/self/get" if style == "myteam" else "/me"
    _, body, _ = _request("GET", path, params=_auth_params(), base=base)
    return body


def detect_api() -> tuple[str | None, str | None, dict]:
    """Перебирает API_CANDIDATES, возвращает (style, base, info) первого
    работающего. Полезно при первой настройке.
    """
    if not _get_token():
        return None, None, {"error": "no token"}
    old_style = os.environ.get("TDM_API_STYLE", "")
    old_base = os.environ.get("TDM_API_BASE", "")
    results = []
    try:
        for style, base in API_CANDIDATES:
            os.environ["TDM_API_STYLE"] = style
            try:
                status, body, _ = _request(
                    "GET",
                    "/self/get" if style == "myteam" else "/me",
                    params=_auth_params(),
                    base=base,
                )
                ok = (status == 200 and
                      body.get("ok") is not False and
                      not body.get("description") and
                      "Invalid" not in str(body)[:200])
                results.append({"style": style, "base": base,
                                "status": status, "ok": ok,
                                "body_preview": str(body)[:120]})
                if ok:
                    return style, base, body
            except requests.RequestException as exc:
                results.append({"style": style, "base": base,
                                "status": "network-error",
                                "error": str(exc)[:120]})
    finally:
        if old_style:
            os.environ["TDM_API_STYLE"] = old_style
        else:
            os.environ.pop("TDM_API_STYLE", None)
        if old_base:
            os.environ["TDM_API_BASE"] = old_base
    return None, None, {"tried": results}


def list_chats(count: int = 20) -> list[dict]:
    """Последние чаты — для определения chat_id.

    MyTeam: /chats/getInfo с нет chat-id даёт ошибку → используем
    /events/get для получения недавних чатов из истории.
    """
    style = _api_style()
    if style == "tamtam":
        _, body, _ = _request("GET", "/chats",
                              params=_auth_params({"count": count}))
        return body.get("chats", []) if isinstance(body, dict) else []
    # MyTeam: история событий → извлекаем chat-id
    _, body, _ = _request(
        "GET", "/events/get",
        params=_auth_params({"lastEventId": 0, "pollTime": 1}),
    )
    chats: dict[str, dict] = {}
    for ev in body.get("events", []):
        payload = ev.get("payload", {}) if isinstance(ev, dict) else {}
        chat = payload.get("chat", {})
        cid = chat.get("chatId") or payload.get("from", {}).get("userId")
        if not cid:
            continue
        title = (chat.get("title") or
                 payload.get("from", {}).get("firstName") or
                 chat.get("type") or "—")
        chats[cid] = {"chat_id": cid, "title": title,
                      "type": chat.get("type", "private")}
    return list(chats.values())[:count]


def notify(text: str, *, chat_id: str | None = None,
           silent: bool = False) -> bool:
    """Отправляет текст. Возвращает True при успехе."""
    if _is_disabled():
        return False
    if not text:
        return False
    chat = chat_id or _get_chat_id()
    if not chat:
        if not silent:
            print("⚠️  TDM_CHAT_ID не задан — уведомление пропущено")
        return False
    style = _api_style()
    try:
        if style == "myteam":
            status, body, _ = _request(
                "GET", "/messages/sendText",
                params=_auth_params({"chatId": chat, "text": text[:4000]}),
            )
        else:  # tamtam
            status, body, _ = _request(
                "POST", "/messages",
                params=_auth_params({"chat_id": chat}),
                json_body={"text": text[:4000]},
            )
        if status >= 400 or body.get("ok") is False:
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
    """Отправляет файл с подписью."""
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
    style = _api_style()
    try:
        if style == "myteam":
            # MyTeam: /messages/sendFile, multipart с file
            url = f"{_api_base().rstrip('/')}/messages/sendFile"
            params = _auth_params({"chatId": chat})
            if caption:
                params["caption"] = caption[:1000]
            with p.open("rb") as f:
                r = requests.post(
                    url, params=params,
                    files={"file": (p.name, f)},
                    timeout=120,
                )
            try:
                body = r.json()
            except (json.JSONDecodeError, ValueError):
                body = {"raw": r.text[:500]}
            if r.status_code >= 400 or body.get("ok") is False:
                print(f"⚠️  notify_file failed [{r.status_code}]: {body}")
                return False
            return True
        # TamTam: upload → token → message с attachment
        _, body, _ = _request("POST", "/uploads",
                              params=_auth_params({"type": "file"}))
        if "url" not in body:
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
        status, body2, _ = _request("POST", "/messages",
                                    params=_auth_params({"chat_id": chat}),
                                    json_body=msg)
        if status >= 400:
            print(f"⚠️  notify_file message failed: {body2}")
            return False
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️  notify_file exception: {exc}")
        return False


# === CLI ===

def main():
    parser = argparse.ArgumentParser(description="TDM/MyTeam/TamTam уведомления")
    parser.add_argument("text", nargs="?", default=None,
                        help="Текст для отправки")
    parser.add_argument("--me", action="store_true",
                        help="Инфо о боте (проверка токена)")
    parser.add_argument("--detect", action="store_true",
                        help="Автоподбор API (перебор endpoint'ов)")
    parser.add_argument("--list-chats", action="store_true",
                        help="Последние чаты бота")
    parser.add_argument("--test", action="store_true",
                        help="Тестовое сообщение")
    parser.add_argument("--file", help="Файл для отправки")
    args = parser.parse_args()

    if not _get_token():
        print("⚠️  TDM_BOT_TOKEN не задан.\n"
              "    set TDM_BOT_TOKEN=<токен>")
        return 1

    if args.detect:
        print("Пробую endpoints с твоим токеном…\n")
        style, base, info = detect_api()
        if style:
            print(f"✅ Работает: TDM_API_STYLE={style}  TDM_API_BASE={base}\n")
            print(f"Установи в .env эти значения. Ответ /self/get:")
            print(json.dumps(info, ensure_ascii=False, indent=2))
            return 0
        print("❌ Ни один из стандартных endpoint'ов не подошёл\n")
        for r in info.get("tried", []):
            print(f"  [{r.get('status', '?')}] {r['style']:7} {r['base']}")
            if r.get("body_preview"):
                print(f"     → {r['body_preview']}")
            if r.get("error"):
                print(f"     → ERROR: {r['error']}")
        print("\n🔧 Нужна документация API от поставщика TDM:")
        print("   - base URL для бот-API")
        print("   - формат авторизации (header / query param)")
        print("   - формат запроса на отправку сообщения")
        print("   Покажи мне раздел документации, доточу код.")
        return 2

    if args.me:
        try:
            info = me()
            print(json.dumps(info, ensure_ascii=False, indent=2))
        except Exception as exc:  # noqa: BLE001
            print(f"Ошибка: {exc}")
            print("\nПопробуй автоподбор: py -m pipeline.tdm_notify --detect")
            return 1
        return 0

    if args.list_chats:
        chats = list_chats()
        if not chats:
            print("(пусто) Напиши боту что-нибудь и попробуй ещё раз.")
            return 0
        print(f"Найдено {len(chats)} чатов:")
        for c in chats:
            print(f"  {c.get('chat_id', '?')}  [{c.get('type', '?')}]  "
                  f"{c.get('title', '—')}")
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
