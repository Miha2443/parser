"""Build fast realty marts for Streamlit pages from raw Excel/JSON files.

The realty dashboard pages historically read large raw files directly from
``data/raw/realty``. This script materializes the same structures returned by
``app.data_access`` into ``data/marts/realty/*.pkl`` so the UI can start and
switch pages without reparsing heavy workbooks.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
MART_DIR = ROOT / "data" / "marts" / "realty"
MANIFEST = MART_DIR / "manifest.json"

logging.getLogger("streamlit").setLevel(logging.ERROR)
logging.getLogger("streamlit.runtime.caching.cache_data_api").setLevel(logging.ERROR)


@dataclass(frozen=True)
class MartSpec:
    name: str
    loader_name: str
    raw_files: Callable[[Any], list[Path]]


def _prepare_imports():
    """Import app.data_access with mart reads disabled to force raw parsing."""
    os.environ["PARSER_USE_REALTY_MARTS"] = "0"
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    with contextlib.redirect_stderr(io.StringIO()):
        from app import data_access  # noqa: PLC0415

    return data_access


def _df_rows(value: Any) -> int | None:
    if isinstance(value, pd.DataFrame):
        return len(value)
    return None


def _shape_summary(value: Any) -> Any:
    if isinstance(value, pd.DataFrame):
        return {"type": "dataframe", "rows": len(value), "cols": len(value.columns)}
    if isinstance(value, dict):
        out: dict[str, Any] = {"type": "dict", "keys": sorted(map(str, value.keys()))}
        frames = {
            str(k): {"rows": len(v), "cols": len(v.columns)}
            for k, v in value.items()
            if isinstance(v, pd.DataFrame)
        }
        if frames:
            out["frames"] = frames
        return out
    return {"type": type(value).__name__}


def _source_summary(files: list[Path]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for path in sorted(files):
        try:
            st = path.stat()
        except OSError:
            continue
        out.append({
            "path": str(path.relative_to(ROOT)).replace("\\", "/"),
            "size_bytes": st.st_size,
            "mtime": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
        })
    return out


def _tmp_files() -> list[Path]:
    raw = ROOT / "data" / "raw" / "realty"
    if not raw.exists():
        return []
    return sorted(
        p for p in raw.rglob("*")
        if p.is_file() and p.suffix.lower() in {".tmp", ".crdownload"}
    )


def _specs(da) -> list[MartSpec]:
    return [
        MartSpec(
            "kvartirografia",
            "load_kvartirografia",
            lambda da: da._raw_files(da.KVART_PATHS, ["kvartirografia_*.json"]),
        ),
        MartSpec(
            "monitoring_2_0",
            "load_monitoring_2_0",
            lambda da: da._raw_files(da.MONITORING_PATHS, ["monitoring_2_0_*.xlsx"]),
        ),
        MartSpec(
            "erzrf_top",
            "load_erzrf_top",
            lambda da: da._raw_files(da.ERZRF_PATHS, ["top_*.xlsx", "top_developers_*.json"]),
        ),
        MartSpec(
            "erzrf_cards",
            "load_erzrf_cards",
            lambda da: da._raw_files(da.ERZRF_PATHS, ["cards_*.xlsx"], recursive=True),
        ),
        MartSpec(
            "escrow_manual",
            "load_escrow_manual",
            lambda da: da._raw_files(da.ESCROW_PATHS, ["*.xlsx"]),
        ),
        MartSpec(
            "rasprodannost",
            "load_rasprodannost",
            lambda da: da._raw_files(da.RASPROD_PATHS, ["rasprodannost_*.xlsx"]),
        ),
        MartSpec(
            "vvod_static",
            "load_vvod_static",
            lambda da: da._raw_files([p for p in da.VVOD_PATHS if p.exists()], ["*.xls*", "*.txt"]),
        ),
        MartSpec(
            "emiss_34118",
            "load_emiss_34118",
            lambda da: (
                da._raw_files([p for p in da.VVOD_PATHS if p.exists()], ["emiss_34118_base.xls"])
                + da._raw_files([ROOT / "downloads"], ["*Введено в действие общей площади жилых домов*.xls*"])
            ),
        ),
    ]


def build(*, strict: bool = False, only: set[str] | None = None) -> int:
    da = _prepare_imports()
    MART_DIR.mkdir(parents=True, exist_ok=True)

    tmp = _tmp_files()
    if tmp:
        print("WARNING: raw realty contains temporary download files:")
        for path in tmp:
            print(f"  - {path.relative_to(ROOT)}")
        if strict:
            return 2

    manifest: dict[str, Any] = {
        "built_at": datetime.now().isoformat(timespec="seconds"),
        "marts": {},
        "warnings": {
            "tmp_files": [str(p.relative_to(ROOT)).replace("\\", "/") for p in tmp],
        },
    }

    failures = 0
    for spec in _specs(da):
        if only and spec.name not in only:
            continue
        started = time.time()
        print(f"building {spec.name} ...", flush=True)
        try:
            loader = getattr(da, spec.loader_name)
            with contextlib.redirect_stderr(io.StringIO()):
                value = loader()
            target = MART_DIR / f"{spec.name}.pkl"
            pd.to_pickle(value, target)
            raw_files = spec.raw_files(da)
            manifest["marts"][spec.name] = {
                "file": str(target.relative_to(ROOT)).replace("\\", "/"),
                "duration_sec": round(time.time() - started, 2),
                "summary": _shape_summary(value),
                "sources": _source_summary(raw_files),
            }
            rows = _df_rows(value)
            suffix = f" rows={rows}" if rows is not None else ""
            print(f"  ok -> {target.relative_to(ROOT)}{suffix}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            manifest["marts"][spec.name] = {
                "error": f"{type(exc).__name__}: {exc}",
                "duration_sec": round(time.time() - started, 2),
            }
            print(f"  ERROR: {type(exc).__name__}: {exc}")

    MANIFEST.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"manifest -> {MANIFEST.relative_to(ROOT)}")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="fail when raw temporary download files are present",
    )
    parser.add_argument(
        "--only",
        nargs="*",
        help="optional mart names to rebuild",
    )
    args = parser.parse_args()
    return build(strict=args.strict, only=set(args.only or []) or None)


if __name__ == "__main__":
    raise SystemExit(main())
