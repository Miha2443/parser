"""Полный прогон по всем источникам недвижимости с автоархивированием.

Запуск:
    py scripts/update_realty.py              # всё
    py scripts/update_realty.py monitoring   # один источник
    py scripts/update_realty.py --no-archive # без архивирования старого
    py scripts/update_realty.py --skip-kvart-per-dev  # без долгого per-dev обхода
    py scripts/update_realty.py --weekly-kvart-per-dev  # per-dev только по понедельникам

Порядок выполнения:
  1. nashdom_checker monitoring_2_0   (~1 мин, requests)
  2. nashdom_checker rasprodannost    (~15-30 мин, selenium по всем регионам)
  3. nashdom_checker kvartirografia   (~5 мин агрегаты + ~90 мин per-dev обход)
  4. erzrf_checker top                (~5 мин)
  5. erzrf_checker cards              (~10 мин, по топ-100)
  → ПОСЛЕ всего: архивируется устаревшее в data/raw/realty/_archive/<date>/
  → Уведомление в TDM с детальным отчётом что обновилось

Escrow (data/raw/realty/escrow_manual/) — РУЧНАЯ выгрузка с
ДОМ.РФ ЕИСЖС, парсера нет. Положи свежий xlsx туда сам.

Источники для аргумента (можно несколько через пробел):
  monitoring     → nashdom monitoring_2_0
  rasprod        → nashdom rasprodannost
  kvart          → nashdom kvartirografia
  erz-top        → erzrf top
  erz-cards      → erzrf cards
  nashdom        → все nashdom-источники
  erzrf          → все erzrf-источники
"""
from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REALTY_ROOT = ROOT / "data" / "raw" / "realty"

# Карта алиасов: алиас → (скрипт, аргументы)
SOURCE_MAP = {
    "monitoring": ("nashdom_checker.py", ["monitoring_2_0"]),
    "rasprod":    ("nashdom_checker.py", ["rasprodannost"]),
    "kvart":      ("nashdom_checker.py", ["kvartirografia"]),
    "erz-top":    ("erzrf_checker.py",   ["top"]),
    "erz-cards":  ("erzrf_checker.py",   ["cards"]),
}

GROUP_MAP = {
    "nashdom": ["monitoring", "rasprod", "kvart"],
    "erzrf":   ["erz-top", "erz-cards"],
    "all":     ["monitoring", "rasprod", "kvart", "erz-top", "erz-cards"],
}


def snapshot_files() -> dict[str, tuple[int, str]]:
    """Snapshot всех файлов в data/raw/realty: {rel_path: (size, sha256_head)}.

    Хеш только первых 256 КБ — достаточно для определения «файл изменился»,
    а считается мгновенно даже для крупных xlsx.
    """
    out: dict[str, tuple[int, str]] = {}
    if not REALTY_ROOT.exists():
        return out
    for f in REALTY_ROOT.rglob("*"):
        if not f.is_file():
            continue
        if "_archive" in f.parts:
            continue
        try:
            size = f.stat().st_size
            h = hashlib.sha256()
            with f.open("rb") as fh:
                h.update(fh.read(256 * 1024))
            rel = str(f.relative_to(REALTY_ROOT)).replace("\\", "/")
            out[rel] = (size, h.hexdigest()[:16])
        except OSError:
            pass
    return out


def diff_snapshots(before: dict, after: dict) -> dict:
    """Сравнение двух snapshot'ов. Возвращает {added, changed, removed}."""
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    changed = sorted(p for p in set(before) & set(after)
                     if before[p] != after[p])
    return {"added": added, "changed": changed, "removed": removed}


def run_source(alias: str, env: dict) -> bool:
    """Запускает один источник. Возвращает True при успехе."""
    if alias not in SOURCE_MAP:
        print(f"⚠️  Неизвестный источник: {alias}")
        return False
    script, args = SOURCE_MAP[alias]
    cmd = [sys.executable, str(ROOT / script), *args]
    print(f"\n{'─'*60}")
    print(f"▶ {alias}: {' '.join(cmd[1:])}")
    print(f"{'─'*60}")
    started = time.time()
    try:
        result = subprocess.run(cmd, cwd=ROOT, env=env, check=False)
        elapsed = time.time() - started
        if result.returncode == 0:
            print(f"✅ {alias}: успех (за {elapsed/60:.1f} мин)")
            return True
        print(f"❌ {alias}: код выхода {result.returncode} (за {elapsed/60:.1f} мин)")
        return False
    except KeyboardInterrupt:
        print(f"\n⚠️  {alias}: прервано пользователем")
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"❌ {alias}: {exc}")
        return False


def archive_old(keep: int = 1) -> None:
    """Перемещает устаревшие выгрузки в _archive/<date>/."""
    cmd = [sys.executable, "-m", "pipeline.archive_old", "--keep", str(keep)]
    print(f"\n{'─'*60}")
    print(f"📦 Архивирование старых файлов (keep={keep})")
    print(f"{'─'*60}")
    subprocess.run(cmd, cwd=ROOT, check=False)


def check_escrow():
    """Подсказка про эскроу."""
    escrow_dir = REALTY_ROOT / "escrow_manual"
    files = list(escrow_dir.glob("*.xlsx")) if escrow_dir.exists() else []
    print(f"\n{'─'*60}")
    print(f"📋 Эскроу (ручная выгрузка)")
    print(f"{'─'*60}")
    if not files:
        print(f"⚠️  Папка пустая: {escrow_dir}")
        print(f"   Скачай «Наполняемость счетов.xlsx» с ДОМ.РФ ЕИСЖС вручную")
    else:
        latest = max(files, key=lambda p: p.stat().st_mtime)
        date = datetime.fromtimestamp(latest.stat().st_mtime).strftime("%d.%m.%Y")
        print(f"✓ Файл есть: {latest.name} (от {date})")


def build_tdm_report(successes: list[str], failures: list[str],
                     diff: dict, total_min: float) -> str:
    """Формирует текст сводки для TDM."""
    icon = "✅" if not failures else "⚠️"
    today = datetime.now().strftime("%d.%m.%Y %H:%M")
    lines = [
        f"{icon} **Прогон realty** {today} (за {total_min:.1f} мин)",
        f"Источники: {len(successes)}/{len(successes) + len(failures)} ОК",
    ]
    if successes:
        lines.append(f"✓ OK: {', '.join(successes)}")
    if failures:
        lines.append(f"✗ FAIL: {', '.join(failures)}")

    added = diff.get("added", [])
    changed = diff.get("changed", [])
    if added or changed:
        lines.append("")
        lines.append("📥 **Изменения в файлах:**")
        for p in added[:8]:
            lines.append(f"  + {p}")
        if len(added) > 8:
            lines.append(f"  + (и ещё {len(added) - 8})")
        for p in changed[:8]:
            lines.append(f"  ✎ {p}")
        if len(changed) > 8:
            lines.append(f"  ✎ (и ещё {len(changed) - 8})")
    else:
        lines.append("")
        lines.append("ℹ️ Новых данных нет — все источники без изменений")
    return "\n".join(lines)


def is_monday() -> bool:
    return datetime.now().weekday() == 0


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("sources", nargs="*", default=["all"],
                        help="Список источников или групп: monitoring, rasprod, kvart, "
                             "erz-top, erz-cards, nashdom, erzrf, all")
    parser.add_argument("--no-archive", action="store_true",
                        help="Не архивировать старые файлы после прогона")
    parser.add_argument("--skip-kvart-per-dev", action="store_true",
                        help="Пропустить долгий per-dev обход квартирографии "
                             "(KVART_PER_DEV=0)")
    parser.add_argument("--weekly-kvart-per-dev", action="store_true",
                        help="Per-dev обход только по понедельникам "
                             "(для daily cron — экономит ~90 мин/день)")
    parser.add_argument("--keep", type=int, default=1,
                        help="Сколько свежих файлов оставить в активной папке (default: 1)")
    parser.add_argument("--no-notify", action="store_true",
                        help="Не отправлять уведомление в TDM")
    args = parser.parse_args()

    # Разворачиваем группы в отдельные источники
    sources: list[str] = []
    for s in args.sources:
        if s in GROUP_MAP:
            for sub in GROUP_MAP[s]:
                if sub not in sources:
                    sources.append(sub)
        elif s in SOURCE_MAP:
            if s not in sources:
                sources.append(s)
        else:
            print(f"⚠️  Игнорирую неизвестный аргумент: {s}")

    if not sources:
        parser.print_help()
        return 1

    # Подготовка env (KVART_PER_DEV)
    env = os.environ.copy()
    if args.skip_kvart_per_dev:
        env["KVART_PER_DEV"] = "0"
    elif args.weekly_kvart_per_dev:
        env["KVART_PER_DEV"] = "1" if is_monday() else "0"
        if not is_monday():
            print("ℹ️  --weekly-kvart-per-dev: сегодня не понедельник → "
                  "KVART_PER_DEV=0 (per-dev пропустится, агрегаты остаются)")

    print(f"\n{'='*60}")
    print(f"Прогон realty | старт {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}")
    print(f"Источники: {', '.join(sources)}")
    print(f"KVART_PER_DEV={env.get('KVART_PER_DEV', '0')}")
    print(f"{'='*60}")

    # SNAPSHOT ДО прогона
    before = snapshot_files()

    started = time.time()
    successes, failures = [], []
    try:
        for alias in sources:
            ok = run_source(alias, env)
            (successes if ok else failures).append(alias)
    except KeyboardInterrupt:
        print(f"\n\n⚠️  Прогон прерван. Готово: {len(successes)} из {len(sources)}")
        sys.exit(130)

    # SNAPSHOT ПОСЛЕ прогона (до архивирования! Архивирование переместит
    # старые файлы, и diff покажет «removed» — это не интересно).
    after = snapshot_files()
    diff = diff_snapshots(before, after)

    # Архивирование
    if not args.no_archive:
        archive_old(keep=args.keep)

    # Эскроу-подсказка
    check_escrow()

    total_min = (time.time() - started) / 60
    print(f"\n{'='*60}")
    print(f"ИТОГ за {total_min:.1f} мин:")
    print(f"  ✅ Успешно: {len(successes)} — {', '.join(successes) if successes else '—'}")
    if failures:
        print(f"  ❌ Ошибки:  {len(failures)} — {', '.join(failures)}")
    if diff["added"]:
        print(f"  📥 Новых файлов:    {len(diff['added'])}")
    if diff["changed"]:
        print(f"  ✎  Обновлено:       {len(diff['changed'])}")
    print(f"{'='*60}\n")

    # === Уведомление в TDM ===
    if not args.no_notify:
        try:
            sys.path.insert(0, str(ROOT))
            from pipeline.tdm_notify import notify
            text = build_tdm_report(successes, failures, diff, total_min)
            notify(text, silent=True)
        except Exception:  # noqa: BLE001
            pass

    return 0 if not failures else 2


if __name__ == "__main__":
    sys.exit(main())
