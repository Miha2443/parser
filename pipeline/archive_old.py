"""Архивирование устаревших выгрузок недвижимости.

Принцип: в `data/raw/realty/<source>/` лежат файлы с датой в имени
(например `monitoring_2_0_20260608.xlsx`, `top_obyem_vvoda_rf_20260604.xlsx`).
После новой выгрузки старые файлы перемещаются в
`data/raw/realty/_archive/<YYYY-MM-DD>/<source>/`.

Запуск:
    py pipeline/archive_old.py            # архивирует всё кроме самого свежего
    py pipeline/archive_old.py --keep 2   # сохранить 2 свежих
    py pipeline/archive_old.py --dry-run  # показать что бы сделалось

Группировка файлов: по «семейству» — у `top_obyem_vvoda_rf_20260604.xlsx`
семейство = `top_obyem_vvoda_rf` (всё до даты), у `cards_20260604.xlsx`
семейство = `cards`. Внутри семейства сохраняем N свежих по mtime.
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

REALTY_ROOT = Path(__file__).resolve().parent.parent / "data" / "raw" / "realty"
ARCHIVE_ROOT = REALTY_ROOT / "_archive"

# Паттерн даты в имени: 20260604 или 2026-06-04 или 20260604.xlsx
DATE_RE = re.compile(r"[_-]?(\d{8}|\d{4}-\d{2}-\d{2})(?=\.|$)")

# Папки источников для обхода (без _archive)
SOURCE_DIRS = ["nashdom", "erzrf", "erzrf/cards"]


def family_of(path: Path) -> str:
    """`top_obyem_vvoda_rf_20260604.xlsx` → `top_obyem_vvoda_rf.xlsx`.

    Расширение включается в ключ, чтобы парные файлы (.xlsx + .json
    с одинаковой датой, например kvartirografia_20260608.*) попали
    в разные семейства и оба сохранились при keep=1.
    """
    stem = path.stem
    match = DATE_RE.search(stem)
    base = stem[: match.start()].rstrip("_-") if match else stem
    return f"{base}{path.suffix.lower()}"


def scan_source(source_rel: str, prefixes: list[str] | None = None) -> dict[str, list[Path]]:
    """Группирует файлы по семействам внутри одной папки источника.

    Если `prefixes` задан, включаем только файлы, чьё имя начинается
    с одного из префиксов (для архивации только одного источника
    в общей папке: nashdom содержит monitoring_2_0_*, rasprodannost_*,
    kvartirografia_*).
    """
    src_dir = REALTY_ROOT / source_rel
    if not src_dir.exists():
        return {}
    families: dict[str, list[Path]] = {}
    for f in src_dir.iterdir():
        if not f.is_file():
            continue
        if f.suffix.lower() not in (".xlsx", ".xls", ".json", ".csv"):
            continue
        # Игнорируем файлы без даты в имени (например «Наполняемость счетов.xlsx»
        # — ручная выгрузка, не архивируем)
        if not DATE_RE.search(f.stem):
            continue
        if prefixes and not any(f.name.startswith(p) for p in prefixes):
            continue
        fam = family_of(f)
        families.setdefault(fam, []).append(f)
    return families


def archive_directory(source_rel: str, keep: int, dry_run: bool = False,
                      prefixes: list[str] | None = None) -> int:
    """Архивирует устаревшие файлы. Возвращает количество перемещённых."""
    families = scan_source(source_rel, prefixes=prefixes)
    if not families:
        return 0
    today = datetime.now().strftime("%Y-%m-%d")
    moved = 0
    for fam, files in sorted(families.items()):
        if len(files) <= keep:
            continue
        # Сортируем по mtime, самые свежие в конце
        files_sorted = sorted(files, key=lambda p: p.stat().st_mtime)
        stale = files_sorted[:-keep]  # всё кроме `keep` свежих
        if not stale:
            continue
        for old in stale:
            # Извлекаем дату из имени файла для папки архива
            m = DATE_RE.search(old.stem)
            date_tag = m.group(1) if m else "unknown"
            # Нормализуем дату: 20260604 → 2026-06-04
            if len(date_tag) == 8 and date_tag.isdigit():
                date_tag = f"{date_tag[:4]}-{date_tag[4:6]}-{date_tag[6:8]}"
            dest_dir = ARCHIVE_ROOT / date_tag / source_rel
            dest = dest_dir / old.name
            if dry_run:
                print(f"  [DRY] {old.relative_to(REALTY_ROOT)} → _archive/{date_tag}/{source_rel}/{old.name}")
            else:
                dest_dir.mkdir(parents=True, exist_ok=True)
                shutil.move(str(old), str(dest))
                print(f"  📦 {old.name} → _archive/{date_tag}/{source_rel}/")
            moved += 1
    return moved


def main():
    parser = argparse.ArgumentParser(description="Архивирование устаревших выгрузок недвижимости")
    parser.add_argument("--keep", type=int, default=1,
                        help="Сколько свежих оставить в активной папке (default: 1)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Только показать что было бы перемещено")
    parser.add_argument("--source", default=None,
                        help="Только конкретный источник: nashdom / erzrf / erzrf/cards")
    parser.add_argument("--paths", nargs="*", default=None,
                        help="Список относительных путей (например realty/nashdom). "
                             "Если задан, перекрывает --source и SOURCE_DIRS.")
    parser.add_argument("--prefixes", nargs="*", default=None,
                        help="Фильтр по префиксам имени файла "
                             "(например monitoring_2_0_). Архивируем только "
                             "семейства, чьи файлы начинаются с одного из префиксов.")
    args = parser.parse_args()

    if args.paths:
        # --paths приходит как realty/<source>; внутренние scan_source ждут <source>
        sources = [p[len("realty/"):] if p.startswith("realty/") else p
                   for p in args.paths]
    elif args.source:
        sources = [args.source]
    else:
        sources = SOURCE_DIRS
    total = 0
    print(f"\n{'='*60}")
    print(f"Архивирование устаревших файлов  {'(DRY-RUN)' if args.dry_run else ''}")
    print(f"Каталог: {REALTY_ROOT}")
    print(f"Хранить свежих: {args.keep}")
    if args.prefixes:
        print(f"Префиксы: {', '.join(args.prefixes)}")
    print(f"{'='*60}\n")
    for src in sources:
        print(f"📂 {src}")
        n = archive_directory(src, keep=args.keep, dry_run=args.dry_run,
                              prefixes=args.prefixes)
        if n == 0:
            print(f"   ✓ нечего архивировать")
        else:
            print(f"   → перемещено: {n}")
        total += n
    print(f"\n{'='*60}")
    print(f"Итого{' (имитация)' if args.dry_run else ''}: {total} файлов")
    print(f"{'='*60}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
