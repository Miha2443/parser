"""Ручная пересборка витрины из xls в `downloads/` без обращения к сети.

Эквивалентно `py pipeline/orchestrator.py --skip-download`, но с более явным выводом.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.orchestrator import run_all

if __name__ == "__main__":
    print("Ручная пересборка из downloads/ — без скачивания.\n")
    sys.exit(run_all(download=False))
