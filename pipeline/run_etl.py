"""Обратная совместимость со старой командой `py pipeline/run_etl.py`.

По умолчанию запускает оркестратор без скачивания — собирает витрину из xls в `downloads/`.
Полный цикл со скачиванием — через `py pipeline/orchestrator.py`.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.orchestrator import run_all

if __name__ == "__main__":
    sys.exit(run_all(download=False))
