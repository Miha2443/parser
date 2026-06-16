"""Уведомления через TDM Bot API (мэрия Москвы).

Документация: см. _to_delete/api.pdf

Структура API:
  - Auth: HTTP header `Authorization: <authToken>`
  - Base URL: https://botapi.tdm.mos.ru (или другой — настраивается)
  - REST endpoints под /botapi/v1/...
  - Сообщения требуют 2 ID:
      workspaceId — ID пространства (постоянный для бота)
      groupId    — ID группы/чата куда слать

Эндпоинты которыми пользуемся:
  POST /botapi/v1/groups/getAllUserGroupStates
       → список всех групп бота (workspaceId + groupId + назв.)
  POST /botapi/v1/messages/sendTextMessage/{workspaceId}/{groupId}
       body: {clientRandomId, message}
  POST /botapi/v1/messages/sendFile/{workspaceId}/{groupId}
       body: {clientRandomId, file: {fileName, length, mimeType, resourceRef}, message}
  POST /botapi/v1/s3/upload  — сначала грузим файл, потом sendFile

Настройка через env (.env):
  TDM_BOT_TOKEN     — токен бота (header Authorization)
  TDM_WORKSPACE_ID  — ID пространства (Long)
  TDM_GROUP_ID      — ID группы (Long) — основной чат для уведомлений
  TDM_API_BASE      — base URL (default: https://botapi.tdm.mos.ru)
  TDM_DISABLED      — '1' чтобы выключить

CLI:
  py -m pipeline.tdm_notify --groups          список групп бота
  py -m pipeline.tdm_notify --test            тестовое сообщение
  py -m pipeline.tdm_notify "Привет"          отправить текст
  py -m pipeline.tdm_notify --file file.xlsx  отправить файл
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import random
import sys
import time
from pathlib import Path
from typing import Optional

import requests

DEFAULT_BASE = "https://botapi.tdm.mos.ru"
TIMEOUT = 30
MAX_RETRIES = 3
RETRY_BACKOFF = (2, 4, 8)


def _is_disabled() -> bool:
    return os.environ.get("TDM_DISABLED", "").strip() in ("1", "true", "yes")


def _get_token() -> Optional[str]:
    return (os.environ.get("TDM_BOT_TOKEN") or "").strip() or None


def _get_workspace_id() -> Optional[str]:
    return (os.environ.get("TDM_WORKSPACE_ID") or "").strip() or None


def _get_group_id(override: str | None = None) -> Optional[str]:
    if override:
        return str(override).strip()
    return ((os.environ.get("TDM_GROUP_ID") or "").strip()
            or (os.environ.get("TDM_CHAT_ID") or "").strip()  # обратная совместимость
            or None)


def _api_base() -> str:
    return (os.environ.get("TDM_API_BASE") or "").strip() or DEFAULT_BASE


def _auth_headers(extra: dict | None = None) -> dict:
    token = _get_token()
    if not token:
        raise RuntimeError(
            "TDM_BOT_TOKEN не задан. Установи env-переменную с authToken.")
    h = {"Authorization": token, "Content-Type": "application/json"}
    if extra:
        h.update(extra)
    return h


def _request(method: str, path: str, *, headers: dict | None = None,
             json_body: dict | None = None, files: dict | None = None,
             data: dict | None = None,
             base: str | None = None) -> tuple[int, dict, str]:
    """Запрос с retry. Возвращает (status_code, json/dict, raw_text)."""
    url = f"{(base or _api_base()).rstrip('/')}/{path.lstrip('/')}"
    last_exc: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.request(
                method, url,
                headers=headers, json=json_body, files=files, data=data,
                timeout=TIMEOUT,
            )
            text = resp.text
            try:
                body = resp.json()
                if not isinstance(body, dict):
                    body = {"data": body}
            except (json.JSONDecodeError, ValueError):
                body = {"raw": text[:500] if text else ""}
            # 5xx — retry, остальное возвращаем
            if resp.status_code < 500 or attempt == MAX_RETRIES - 1:
                return resp.status_code, body, text
        except requests.RequestException as exc:
            last_exc = exc
        if attempt < MAX_RETRIES - 1:
            time.sleep(RETRY_BACKOFF[attempt])
    if last_exc:
        raise last_exc
    return 599, {"error": "max retries exceeded"}, ""


def get_all_groups() -> list[dict]:
    """POST /botapi/v1/groups/getAllUserGroupStates — список групп бота."""
    status, body, _ = _request(
        "POST", "/botapi/v1/groups/getAllUserGroupStates",
        headers=_auth_headers(),
    )
    if status >= 400:
        return []
    # API возвращает array — оборачиваем в {"data": [...]} в _request
    return body.get("data", []) if isinstance(body, dict) else []


def _random_client_id() -> int:
    """clientRandomId — Long на основе временных меток + случайной части."""
    return int(time.time() * 1000) * 1000 + random.randint(0, 999)


def notify(text: str, *, group_id: str | None = None,
           workspace_id: str | None = None, silent: bool = False) -> bool:
    """Отправляет текст. Возвращает True при успехе.

    POST /botapi/v1/messages/sendTextMessage/{workspaceId}/{groupId}
    """
    if _is_disabled():
        return False
    if not text:
        return False
    ws = workspace_id or _get_workspace_id()
    grp = _get_group_id(group_id)
    if not ws or not grp:
        if not silent:
            print("⚠️  TDM_WORKSPACE_ID или TDM_GROUP_ID не задан — пропускаю")
        return False
    try:
        status, body, _ = _request(
            "POST", f"/botapi/v1/messages/sendTextMessage/{ws}/{grp}",
            headers=_auth_headers(),
            json_body={
                "clientRandomId": _random_client_id(),
                "message": text[:4000],
            },
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


def _upload_to_s3(path: Path) -> dict | None:
    """Загружает файл в s3.tdm.mos.ru и возвращает resourceRef.

    Эндпоинт сохранения файлов: POST /botapi/v1/files/upload (multipart).
    После загрузки возвращается id/url/key для resourceRef в sendFile.
    Точный путь endpoint'а зависит от реализации TDM — пробуем 2 варианта.
    """
    candidates = ["/botapi/v1/files/upload", "/botapi/v1/s3/upload"]
    for path_str in candidates:
        url = f"{_api_base().rstrip('/')}/{path_str.lstrip('/')}"
        try:
            with path.open("rb") as f:
                mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
                r = requests.post(
                    url,
                    headers={"Authorization": _get_token()},
                    files={"file": (path.name, f, mime)},
                    timeout=300,
                )
            if r.status_code >= 400:
                continue
            try:
                body = r.json()
            except (json.JSONDecodeError, ValueError):
                continue
            # Ищем resourceRef в ответе (может быть на разных уровнях)
            if isinstance(body, dict):
                if "resourceRef" in body:
                    return body["resourceRef"]
                # Иногда возвращается сам resourceRef как корень:
                if "id" in body and "url" in body:
                    return body
            return body if isinstance(body, dict) else None
        except requests.RequestException:
            continue
    return None


def notify_file(path: str | Path, *, caption: str = "",
                group_id: str | None = None,
                workspace_id: str | None = None) -> bool:
    """Отправляет файл с подписью.

    Сначала загружает файл (получает resourceRef), потом sendFile.
    """
    if _is_disabled():
        return False
    p = Path(path)
    if not p.is_file():
        print(f"⚠️  Файл не найден: {p}")
        return False
    ws = workspace_id or _get_workspace_id()
    grp = _get_group_id(group_id)
    if not ws or not grp:
        print("⚠️  TDM_WORKSPACE_ID или TDM_GROUP_ID не задан")
        return False

    resource = _upload_to_s3(p)
    if not resource:
        print(f"⚠️  Не удалось загрузить файл (resourceRef не получен)")
        return False

    mime = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    body_msg = {
        "clientRandomId": _random_client_id(),
        "file": {
            "fileName": p.name,
            "length": p.stat().st_size,
            "mimeType": mime,
            "resourceRef": resource,
        },
    }
    if caption:
        body_msg["message"] = caption[:4000]

    try:
        status, body, _ = _request(
            "POST", f"/botapi/v1/messages/sendFile/{ws}/{grp}",
            headers=_auth_headers(),
            json_body=body_msg,
        )
        if status >= 400:
            print(f"⚠️  TDM notify_file failed [{status}]: {body}")
            return False
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️  notify_file exception: {exc}")
        return False


# === CLI ===

def main():
    parser = argparse.ArgumentParser(description="TDM Bot API (мэрия Москвы)")
    parser.add_argument("text", nargs="?", default=None,
                        help="Текст для отправки")
    parser.add_argument("--groups", action="store_true",
                        help="Получить список групп бота с их workspaceId/groupId")
    parser.add_argument("--test", action="store_true",
                        help="Тестовое сообщение")
    parser.add_argument("--file", help="Отправить файл")
    parser.add_argument("--group-id", help="Override TDM_GROUP_ID")
    args = parser.parse_args()

    if not _get_token():
        print("⚠️  TDM_BOT_TOKEN не задан.\n"
              "    Windows: set TDM_BOT_TOKEN=<authToken>\n"
              "    Также нужны: TDM_WORKSPACE_ID и TDM_GROUP_ID")
        return 1

    if args.groups:
        groups = get_all_groups()
        if not groups:
            print("(пусто) Бот ещё не добавлен в группы.")
            print("Добавь бота в группу TDM и попробуй снова.")
            return 0
        print(f"Найдено {len(groups)} групп(ы) бота:\n")
        for g in groups:
            gid = g.get("groupId")
            ws = g.get("workspaceId")
            title = (g.get("lastMessage", {}).get("group", {}).get("title")
                     or g.get("title") or g.get("groupName") or "—")
            counter = g.get("counter", 0)
            print(f"  groupId={gid}  workspaceId={ws}")
            print(f"    title: «{title}»  непрочитано: {counter}")
            print()
        print("Для уведомлений впиши в .env:")
        if groups:
            g0 = groups[0]
            print(f"  TDM_GROUP_ID={g0.get('groupId', '')}")
            print(f"  TDM_WORKSPACE_ID={g0.get('workspaceId', '')}")
        return 0

    if args.test:
        ok = notify("✅ Тестовое сообщение от parser_bot",
                    group_id=args.group_id)
        print("OK" if ok else "FAIL")
        return 0 if ok else 2

    if args.file:
        ok = notify_file(args.file, caption=args.text or "",
                         group_id=args.group_id)
        print("OK" if ok else "FAIL")
        return 0 if ok else 2

    if args.text:
        ok = notify(args.text, group_id=args.group_id)
        print("OK" if ok else "FAIL")
        return 0 if ok else 2

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
