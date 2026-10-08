"""Уведомления через TDM Bot API (мэрия Москвы).

URL'ы из официальной доки (раздел 6, пример Python-клиента):
    REST API:    https://api.tdm.mos.ru
    SSE:         https://pusher.tdm.mos.ru
    File upload: https://fileupload.tdm.mos.ru

Структура API:
  - Auth: HTTP header `Authorization: <authToken>` (формат «BOT-…»)
  - Сообщения требуют 2 ID:
      workspaceId — ID пространства (Long)
      groupId    — ID группы/чата (Long)

Эндпоинты которыми пользуемся:
  POST /botapi/v1/groups/getAllUserGroupStates
       → список всех групп бота (workspaceId + groupId)
  POST /botapi/v1/messages/sendTextMessage/{workspaceId}/{groupId}
       body: {clientRandomId, message}
  POST /botapi/v1/messages/sendFile/{workspaceId}/{groupId}
       body: {clientRandomId, file: {fileName, length, mimeType, resourceRef}}

Настройка через env (.env):
  TDM_BOT_TOKEN     — authToken (с префиксом BOT-)
  TDM_WORKSPACE_ID  — Long
  TDM_GROUP_ID      — Long
  TDM_API_BASE      — base REST URL (default: https://api.tdm.mos.ru)
  TDM_FILE_BASE     — base file-upload URL (default: https://fileupload.tdm.mos.ru)
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


def _load_dotenv() -> None:
    """Загружает переменные из .env (рядом с корнем проекта).

    Простая реализация без зависимости от python-dotenv:
      - читает строки KEY=VALUE
      - пропускает комментарии (#) и пустые строки
      - НЕ перезаписывает уже заданные в os.environ
    """
    # Корень проекта: tdm_notify.py → pipeline/ → ROOT
    root = Path(__file__).resolve().parent.parent
    env_file = root / ".env"
    if not env_file.is_file():
        return
    try:
        for raw in env_file.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            # Снимаем обрамляющие кавычки если есть
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except OSError:
        pass


# Загружаем .env при импорте модуля — все функции _get_* будут видеть
# переменные из файла без явных set-команд.
_load_dotenv()


DEFAULT_BASE = "https://api.tdm.mos.ru"
DEFAULT_FILE_BASE = "https://fileupload.tdm.mos.ru"
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


def _file_base() -> str:
    return (os.environ.get("TDM_FILE_BASE") or "").strip() or DEFAULT_FILE_BASE


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
    """Загружает файл и возвращает resource для resourceRef в sendFile.

    Из доки (5.7) есть 3 endpoint'а:
      1. POST /api/v1/upload/                  — для уже зашифрованных
         (нужен Encryption-Key-Id, WorkspaceId, Content-Disposition)
      2. POST /api/v1/upload/secret            — для уже зашифрованных
         (нужны WorkspaceId, Content-Disposition)
      3. POST /api/v1/upload/secret/encryptable — СЕРВЕР сам шифрует ✓
         (только Authorization + Content-Type + Content-Length)

    Используем (3) — самый простой, не требует клиентского шифрования.
    Ответ: {resource: {id, url, key, transformation}}
    """
    url = f"{_file_base().rstrip('/')}/api/v1/upload/secret/encryptable"
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
            print(f"⚠️  upload failed [{r.status_code}] на {url}")
            print(f"    response: {r.text[:300]}")
            return None
        try:
            body = r.json()
        except (json.JSONDecodeError, ValueError):
            print(f"⚠️  upload returned non-json: {r.text[:300]}")
            return None
        if isinstance(body, dict):
            # из доки: {resource: {id, url, key, transformation}}
            if "resource" in body and isinstance(body["resource"], dict):
                return body["resource"]
            # запасные варианты структуры
            if "id" in body and "url" in body:
                return body
        return None
    except requests.RequestException as exc:
        print(f"⚠️  upload exception: {exc}")
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
            group_obj = g.get("group") or {}
            opp = g.get("opponent") or {}
            ws = group_obj.get("workspaceId")
            grp_type = group_obj.get("type", "—")
            # title: для P2P — имя пользователя из opponent
            title = (group_obj.get("title")
                     or (f"{opp.get('firstName', '')} "
                         f"{opp.get('lastName', '')}").strip()
                     or g.get("groupName") or "—")
            counter = g.get("counter", 0)
            print(f"  groupId={gid}  workspaceId={ws}  type={grp_type}")
            print(f"    title: «{title}»  непрочитано: {counter}")
            print()
        print("Для уведомлений впиши в .env:")
        if groups:
            g0 = groups[0]
            ws0 = (g0.get("group") or {}).get("workspaceId", "")
            print(f"  TDM_GROUP_ID={g0.get('groupId', '')}")
            print(f"  TDM_WORKSPACE_ID={ws0}")
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
