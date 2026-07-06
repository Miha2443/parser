"""Дедупликация скачанных файлов по контенту.

Используется парсерами и update_realty: после скачивания нового файла
смотрим на последний в архиве. Если содержимое идентичное —
удаляем новый, не засчитываем как обновление.

API:
    is_duplicate_of_latest(new_file) -> Path | None
        Возвращает путь к идентичному файлу в архиве или None.

    deduplicate(new_file, *, log_prefix="") -> tuple[Path | None, bool]
        Если новый файл — дубль:
          → удаляет new_file
          → возвращает (existing_path, False)  [не обновление]
        Если новый/изменился:
          → возвращает (new_file, True)  [обновление]

Сравнение: размер + SHA256 (полный хеш, не первых 256 КБ как в
snapshot_files — там скорость важнее точности).
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

REALTY_ROOT = Path(__file__).resolve().parent.parent / "data" / "raw" / "realty"
ARCHIVE_ROOT = REALTY_ROOT / "_archive"

DATE_RE = re.compile(r"[_-]?(\d{8}|\d{4}-\d{2}-\d{2})(?=\.|$)")
_SHA256_CACHE: dict[str, tuple[tuple[int, int, int], str]] = {}


def _family_key(path: Path) -> str:
    """`top_obyem_vvoda_rf_20260616.xlsx` → `top_obyem_vvoda_rf.xlsx`."""
    stem = path.stem
    m = DATE_RE.search(stem)
    base = stem[: m.start()].rstrip("_-") if m else stem
    return f"{base}{path.suffix.lower()}"


def _file_sha256(path: Path) -> str:
    stat = path.stat()
    cache_key = str(path.resolve())
    stat_key = (
        int(stat.st_size),
        int(getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000))),
        int(getattr(stat, "st_ctime_ns", int(stat.st_ctime * 1_000_000_000))),
    )
    cached = _SHA256_CACHE.get(cache_key)
    if cached and cached[0] == stat_key:
        return cached[1]
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    digest = h.hexdigest()
    _SHA256_CACHE[cache_key] = (stat_key, digest)
    return digest


def _prune_sha256_cache() -> None:
    stale = [path for path in _SHA256_CACHE if not Path(path).exists()]
    for path in stale:
        _SHA256_CACHE.pop(path, None)


def find_archive_versions(family: str) -> list[Path]:
    """Все файлы того же семейства в архиве (отсортированы по mtime)."""
    if not ARCHIVE_ROOT.exists():
        return []
    matches = []
    for f in ARCHIVE_ROOT.rglob("*"):
        if not f.is_file():
            continue
        if _family_key(f) == family:
            matches.append(f)
    matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return matches


def find_active_siblings(family: str, exclude: Path | None = None) -> list[Path]:
    """Файлы того же семейства в активной папке (для проверки рядом-лежащих)."""
    if not REALTY_ROOT.exists():
        return []
    matches = []
    for f in REALTY_ROOT.rglob("*"):
        if not f.is_file():
            continue
        if "_archive" in f.parts:
            continue
        if exclude is not None and f == exclude:
            continue
        if _family_key(f) == family:
            matches.append(f)
    matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return matches


def is_duplicate_of_latest(new_file: Path) -> Path | None:
    """Сравнивает new_file с самым свежим файлом того же семейства.

    Семейство = имя без даты + расширение. Проверяет ВНЕ активной папки
    (архив) и среди соседей в активной (на случай если архивирование
    отложено).

    Возвращает путь к найденному дублю или None.
    """
    if not new_file.is_file():
        return None
    family = _family_key(new_file)

    # 1) Проверяем активные «соседи» того же семейства
    candidates = find_active_siblings(family, exclude=new_file)
    # 2) Затем — последний из архива
    candidates.extend(find_archive_versions(family))

    if not candidates:
        return None

    new_size = new_file.stat().st_size
    new_hash: str | None = None
    for cand in candidates:
        try:
            if cand.stat().st_size != new_size:
                continue
            if new_hash is None:
                new_hash = _file_sha256(new_file)
            if _file_sha256(cand) == new_hash:
                return cand
        except OSError:
            continue
    return None


def deduplicate(new_file: Path, *,
                log_prefix: str = "       ") -> tuple[Path | None, bool]:
    """Если new_file — дубль предыдущей версии, удаляет и возвращает старый.

    Возвращает (path, is_update):
      (existing_path, False) — был дубль, new_file удалён
      (new_file, True)       — новая/изменённая версия, ничего не делаем
      (None, False)          — new_file нет на диске
    """
    if not new_file.is_file():
        return None, False
    dup = is_duplicate_of_latest(new_file)
    if dup is not None:
        try:
            new_file.unlink()
        except OSError:
            pass
        _prune_sha256_cache()
        print(f"{log_prefix}↩️  без изменений (дубль {dup.name}) — удалён {new_file.name}")
        return dup, False
    print(f"{log_prefix}✨ новые данные: {new_file.name}")
    return new_file, True
