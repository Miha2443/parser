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


def _row_count(value: Any) -> int | None:
    if isinstance(value, pd.DataFrame):
        return len(value)
    if isinstance(value, dict):
        counts = [_row_count(v) for v in value.values()]
        counts = [c for c in counts if c is not None]
        return sum(counts) if counts else None
    return None


def _shape_summary(value: Any) -> Any:
    if isinstance(value, pd.DataFrame):
        return {"type": "dataframe", "rows": len(value), "cols": len(value.columns)}
    if isinstance(value, dict):
        out: dict[str, Any] = {
            "type": "dict",
            "keys": sorted(map(str, value.keys())),
        }
        rows = _row_count(value)
        if rows is not None:
            out["rows"] = rows
        frames = {
            str(k): {"rows": len(v), "cols": len(v.columns)}
            for k, v in value.items()
            if isinstance(v, pd.DataFrame)
        }
        if frames:
            out["frames"] = frames
        nested_rows = {
            str(k): rows
            for k, v in value.items()
            if isinstance(v, dict) and (rows := _row_count(v)) is not None
        }
        if nested_rows:
            out["nested_rows"] = nested_rows
        lists = {
            str(k): {"items": len(v)}
            for k, v in value.items()
            if isinstance(v, list)
        }
        if lists:
            out["lists"] = lists
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


def check_manifest(*, strict: bool = False) -> int:
    """Validate mart manifest/files without rebuilding anything."""
    da = _prepare_imports()
    specs = {spec.name: spec for spec in _specs(da)}
    failures = 0
    warnings = 0
    tmp = _tmp_files()
    if tmp:
        print("WARNING: raw realty contains temporary download files:")
        for path in tmp:
            print(f"  - {path.relative_to(ROOT)}")
        if strict:
            failures += 1

    if not MANIFEST.exists():
        print(f"ERROR: missing {MANIFEST.relative_to(ROOT)}")
        return 1

    try:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: cannot read manifest: {exc}")
        return 1

    marts = manifest.get("marts") if isinstance(manifest, dict) else {}
    if not isinstance(marts, dict) or not marts:
        print("ERROR: manifest has no marts")
        return 1

    expected_names = set(specs)
    actual_names = set(map(str, marts))
    missing = sorted(expected_names - actual_names)
    unknown = sorted(actual_names - expected_names)
    if missing:
        print(f"ERROR: manifest missing mart(s): {', '.join(missing)}")
        failures += len(missing)
    if unknown:
        print(f"WARNING: manifest has unknown mart(s): {', '.join(unknown)}")
        warnings += len(unknown)

    print(f"checking {len(marts)} realty marts ...")
    manifest_built_at = pd.to_datetime(manifest.get("built_at"), errors="coerce")
    for name, info in sorted(marts.items()):
        if not isinstance(info, dict):
            print(f"  ERROR {name}: invalid manifest entry")
            failures += 1
            continue
        if info.get("error"):
            print(f"  ERROR {name}: {info['error']}")
            failures += 1
            continue
        mart_file = ROOT / str(info.get("file", ""))
        if not mart_file.is_file():
            print(f"  ERROR {name}: missing {mart_file.relative_to(ROOT)}")
            failures += 1
            continue

        built_at = pd.to_datetime(info.get("built_at"), errors="coerce")
        if pd.isna(built_at):
            built_at = manifest_built_at

        latest_source = None
        missing_sources = 0
        spec = specs.get(str(name))
        if spec is not None:
            current_sources = spec.raw_files(da)
            for path in current_sources:
                try:
                    ts = pd.to_datetime(
                        datetime.fromtimestamp(path.stat().st_mtime),
                        errors="coerce",
                    )
                except OSError:
                    continue
                if not pd.isna(ts) and (latest_source is None or ts > latest_source):
                    latest_source = ts
        else:
            for source in info.get("sources") or []:
                if not isinstance(source, dict) or not source.get("path"):
                    continue
                path = ROOT / str(source["path"])
                if not path.exists():
                    missing_sources += 1
                    continue
                ts = pd.to_datetime(source.get("mtime"), errors="coerce")
                if not pd.isna(ts) and (latest_source is None or ts > latest_source):
                    latest_source = ts
        if missing_sources:
            print(f"  WARN  {name}: missing sources listed in manifest: {missing_sources}")
            warnings += 1
        if latest_source is not None and not pd.isna(built_at) and latest_source > built_at:
            print(
                f"  ERROR {name}: stale "
                f"(source {latest_source}, mart {built_at})"
            )
            failures += 1
            continue
        print(f"  ok    {name}: {mart_file.relative_to(ROOT)}")

    if failures:
        print(f"check failed: {failures} error(s), {warnings} warning(s)")
        return 1
    print(f"check ok: {len(marts)} marts, {warnings} warning(s)")
    return 0


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
    specs = _specs(da)
    MART_DIR.mkdir(parents=True, exist_ok=True)

    known_names = {spec.name for spec in specs}
    if only:
        unknown = sorted(only - known_names)
        if unknown:
            print(f"ERROR: unknown realty mart(s): {', '.join(unknown)}")
            print(f"Known marts: {', '.join(sorted(known_names))}")
            return 2

    tmp = _tmp_files()
    if tmp:
        print("WARNING: raw realty contains temporary download files:")
        for path in tmp:
            print(f"  - {path.relative_to(ROOT)}")
        if strict:
            return 2

    existing_marts: dict[str, Any] = {}
    if only and MANIFEST.exists():
        try:
            existing = json.loads(MANIFEST.read_text(encoding="utf-8"))
            if isinstance(existing.get("marts"), dict):
                existing_marts = existing["marts"]
                existing_built_at = existing.get("built_at")
                if existing_built_at:
                    for info in existing_marts.values():
                        if isinstance(info, dict) and "built_at" not in info:
                            info["built_at"] = existing_built_at
        except (OSError, json.JSONDecodeError):
            existing_marts = {}

    manifest: dict[str, Any] = {
        "built_at": datetime.now().isoformat(timespec="seconds"),
        "marts": dict(existing_marts),
        "warnings": {
            "tmp_files": [str(p.relative_to(ROOT)).replace("\\", "/") for p in tmp],
        },
    }

    failures = 0
    for spec in specs:
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
                "built_at": datetime.now().isoformat(timespec="seconds"),
                "duration_sec": round(time.time() - started, 2),
                "summary": _shape_summary(value),
                "sources": _source_summary(raw_files),
            }
            rows = _row_count(value)
            suffix = f" rows={rows}" if rows is not None else ""
            print(f"  ok -> {target.relative_to(ROOT)}{suffix}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            manifest["marts"][spec.name] = {
                "error": f"{type(exc).__name__}: {exc}",
                "built_at": datetime.now().isoformat(timespec="seconds"),
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
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate manifest and mart freshness without rebuilding",
    )
    args = parser.parse_args()
    if args.check:
        return check_manifest(strict=args.strict)
    return build(strict=args.strict, only=set(args.only or []) or None)


if __name__ == "__main__":
    raise SystemExit(main())
