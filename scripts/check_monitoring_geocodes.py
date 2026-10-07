"""Validate monitoring map coordinates and write QA reports."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]

import sys

sys.path.insert(0, str(ROOT))

from app.realty_map import GEOCODE_CACHE, load_monitoring_map_objects  # noqa: E402


REPORT_DIR = ROOT / "data" / "derived"
MISSING_REPORT = REPORT_DIR / "monitoring_geocodes_missing.csv"
SUSPICIOUS_REPORT = REPORT_DIR / "monitoring_geocodes_suspicious.csv"


def _valid_moscow_region(lat: pd.Series, lon: pd.Series) -> pd.Series:
    return lat.between(54.0, 57.0, inclusive="both") & lon.between(35.0, 40.0, inclusive="both")


def _low_precision(value: object) -> bool:
    text = "" if pd.isna(value) else str(value).strip().lower()
    if not text:
        return False
    return text in {"street", "near", "other", "not_found"} or text.startswith("error:")


def main() -> int:
    objects = load_monitoring_map_objects().copy()
    objects["lat"] = pd.to_numeric(objects["lat"], errors="coerce")
    objects["lon"] = pd.to_numeric(objects["lon"], errors="coerce")
    objects["has_coords"] = objects["lat"].notna() & objects["lon"].notna()
    objects["coord_in_region"] = _valid_moscow_region(objects["lat"], objects["lon"])

    missing = objects[~objects["has_coords"]].copy()
    suspicious = objects[
        objects["has_coords"]
        & (
            ~objects["coord_in_region"]
            | objects.get("precision", pd.Series(index=objects.index, dtype=object)).map(_low_precision)
        )
    ].copy()

    report_cols = [
        "registry", "object_id", "status", "developer", "object_name", "address",
        "okrug", "district", "year", "lat", "lon", "coord_source",
    ]
    if "precision" in objects.columns:
        report_cols.append("precision")
    report_cols = [c for c in report_cols if c in objects.columns]

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    missing[report_cols].to_csv(MISSING_REPORT, index=False, encoding="utf-8-sig")
    suspicious[report_cols].to_csv(SUSPICIOUS_REPORT, index=False, encoding="utf-8-sig")

    unique_missing_addresses = (
        missing[missing["address"].fillna("").astype(str).str.strip().ne("")]["address_key"].nunique()
        if "address_key" in missing.columns
        else 0
    )
    print(f"objects: {len(objects)}")
    print(f"with coords: {int(objects['has_coords'].sum())}")
    print(f"missing coords: {len(missing)}")
    print(f"unique missing addresses: {unique_missing_addresses}")
    print(f"suspicious coords: {len(suspicious)}")
    print(f"cache: {GEOCODE_CACHE}")
    print(f"missing report: {MISSING_REPORT}")
    print(f"suspicious report: {SUSPICIOUS_REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
