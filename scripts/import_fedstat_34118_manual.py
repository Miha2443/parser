"""Import manually downloaded Fedstat 34118 Excel files into project downloads.

Use when fedstat.ru blocks automated Chrome/Selenium, but the file can be
downloaded manually in Yandex Browser.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DOWNLOADS_DIR = ROOT / "downloads"
DEFAULT_SOURCE_DIR = Path.home() / "Downloads"
TITLE = "Введено в действие общей площади жилых домов"
PATTERNS = [
    "*34118*.xls*",
    f"*{TITLE}*.xls*",
]


def _load_checker():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    import fedstat_checker as fc  # noqa: PLC0415

    return fc


def _candidates(source_dir: Path) -> list[Path]:
    seen: set[Path] = set()
    out: list[Path] = []
    for pattern in PATTERNS:
        for path in sorted(source_dir.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True):
            if not path.is_file():
                continue
            if path.suffix.lower() in {".crdownload", ".download", ".part", ".tmp"}:
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            out.append(path)
    return out


def _target_name(source: Path, part_label: str) -> str:
    stamp = datetime.fromtimestamp(source.stat().st_mtime).strftime("%Y%m%d")
    suffix = source.suffix if source.suffix.lower() in {".xls", ".xlsx"} else ".xls"
    return f"{stamp}_manual_34118_{part_label}_{TITLE}{suffix}"


def _copy(source: Path, part_label: str) -> Path:
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    target = DOWNLOADS_DIR / _target_name(source, part_label)
    if target.exists():
        target = target.with_name(
            f"{target.stem}_{datetime.now().strftime('%H%M%S')}{target.suffix}"
        )
    shutil.copy2(source, target)
    return target


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Import manual Fedstat 34118 xls/xlsx from Downloads into project downloads."
    )
    parser.add_argument(
        "files",
        nargs="*",
        type=Path,
        help="Optional explicit xls/xlsx files. If omitted, scans ~/Downloads.",
    )
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=DEFAULT_SOURCE_DIR,
        help=f"Directory to scan when files are omitted. Default: {DEFAULT_SOURCE_DIR}",
    )
    args = parser.parse_args()

    files = [p for p in args.files if p.exists() and p.is_file()]
    if not files:
        files = _candidates(args.source_dir)

    if not files:
        print(f"Нет кандидатов в {args.source_dir}")
        print(f"Ищу по маскам: {', '.join(PATTERNS)}")
        return 2

    fc = _load_checker()
    copied: list[Path] = []
    coverage = {"часть1": False, "часть2": False}

    for source in files:
        matched_parts: list[str] = []
        for part_label in ("часть1", "часть2"):
            indicator_id = f"34118_{part_label}"
            if fc._validate_34118_file(indicator_id, source):
                matched_parts.append(part_label)

        if not matched_parts:
            print(f"⚠️  Не подходит под 34118 часть1/часть2: {source}")
            continue

        if set(matched_parts) == {"часть1", "часть2"}:
            target = _copy(source, "full")
            copied.append(target)
            coverage["часть1"] = True
            coverage["часть2"] = True
            print(f"✅ Импортирован полный 34118: {target}")
            continue

        for part_label in matched_parts:
            target = _copy(source, part_label)
            copied.append(target)
            coverage[part_label] = True
            print(f"✅ Импортирована 34118_{part_label}: {target}")

    if not copied:
        print("Не импортировано ни одного файла.")
        return 2

    missing = [part for part, ok in coverage.items() if not ok]
    if missing:
        print(f"⚠️  Не хватает: {', '.join('34118_' + p for p in missing)}")
        return 2

    print("34118 покрыт вручную скачанными файлами. Дальше запусти пересборку без скачивания:")
    print("  python scripts\\manual_ingest.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
