"""Скачивающий слой. Обёртки над существующими fedstat_checker.py / domrf*.py.

Каждый downloader экспортирует `fetch(indicator) -> dict` с полями:
    {
      "new_files": [Path, ...],         # реально скачанные/обновлённые xls
      "prev_date": "DD.MM.YYYY",        # дата на источнике до запуска
      "new_date":  "DD.MM.YYYY",        # дата после запуска
      "skipped":   bool,                # True если не обновился
    }

Если нет интернета или Chrome не установлен — все downloader'ы безопасно возвращают
все файлы по паттернам из downloads/ (offline-режим, см. orchestrator `--skip-download`).
"""
