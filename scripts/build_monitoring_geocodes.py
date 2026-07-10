"""Build cached coordinates for monitoring 2.0 map objects.

Economical strategy:
1. Use coordinates already embedded in monitoring 2.0 `Геометрия` GeoJSON.
2. Keep all results in data/derived/monitoring_geocodes.csv.
3. Geocode only missing addresses when explicitly requested.

Examples:
  python scripts/build_monitoring_geocodes.py
  python scripts/build_monitoring_geocodes.py --provider nominatim --limit 50
  set YANDEX_GEOCODER_API_KEY=...
  python scripts/build_monitoring_geocodes.py --provider yandex --limit all --flush-every 25
  python scripts/build_monitoring_geocodes.py --missing-out data/derived/missing_geocode_addresses.csv
  python scripts/build_monitoring_geocodes.py --import-csv data/derived/geocoded_addresses.csv
"""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
GEOCODER_CONFIG = ROOT / "config" / "geocoder.local.json"

import sys

sys.path.insert(0, str(ROOT))

from app.realty_map import (  # noqa: E402
    GEOCODE_CACHE,
    MAP_ADDRESSES,
    address_key,
    clean_text,
    apply_geocode_cache,
    load_geocode_cache,
    load_monitoring_map_objects,
)


UNIQUE_ADDRESSES = ROOT / "data" / "derived" / "monitoring_map_unique_addresses.csv"


def _query_nominatim(address: str, timeout: int = 20) -> tuple[float | None, float | None, str]:
    query = f"Москва, {address}"
    resp = requests.get(
        "https://nominatim.openstreetmap.org/search",
        params={
            "q": query,
            "format": "json",
            "limit": 1,
            "countrycodes": "ru",
            "viewbox": "36.7,56.05,38.3,55.15",
            "bounded": 1,
        },
        headers={"User-Agent": "moscow-analytics-dashboard/1.0"},
        timeout=timeout,
    )
    resp.raise_for_status()
    payload = resp.json()
    if not payload:
        return None, None, "not_found"
    hit = payload[0]
    return float(hit["lat"]), float(hit["lon"]), str(hit.get("type") or hit.get("class") or "nominatim")


def _query_yandex(address: str, api_key: str, timeout: int = 20) -> tuple[float | None, float | None, str]:
    query = f"Москва, {address}"
    resp = requests.get(
        "https://geocode-maps.yandex.ru/v1/",
        params={
            "apikey": api_key,
            "format": "json",
            "geocode": query,
            "lang": "ru_RU",
            "results": 1,
            "ll": "37.6176,55.7558",
            "spn": "1.2,0.8",
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    members = (
        resp.json()
        .get("response", {})
        .get("GeoObjectCollection", {})
        .get("featureMember", [])
    )
    if not members:
        return None, None, "not_found"
    geo = members[0].get("GeoObject", {})
    pos = geo.get("Point", {}).get("pos", "")
    if not pos:
        return None, None, "not_found"
    lon, lat = [float(x) for x in pos.split()]
    precision = geo.get("metaDataProperty", {}).get("GeocoderMetaData", {}).get("precision", "yandex")
    return lat, lon, str(precision)


def _load_yandex_key() -> str:
    env_key = os.environ.get("YANDEX_GEOCODER_API_KEY", "").strip()
    if env_key:
        return env_key
    if not GEOCODER_CONFIG.exists():
        return ""
    try:
        payload = json.loads(GEOCODER_CONFIG.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON in {GEOCODER_CONFIG}: {exc}") from exc
    return str(payload.get("YANDEX_GEOCODER_API_KEY") or payload.get("yandex_geocoder_api_key") or "").strip()


def _missing_rows(objects: pd.DataFrame, cache: pd.DataFrame) -> pd.DataFrame:
    missing = objects[~objects["has_coords"]].copy()
    if missing.empty:
        return missing
    if not cache.empty:
        cached_keys = set(cache.dropna(subset=["lat", "lon"]).get("address_key", pd.Series(dtype=str)).astype(str))
        missing = missing[~missing["address_key"].astype(str).isin(cached_keys)]
    missing = missing[missing["address"].fillna("").astype(str).str.strip() != ""]
    return missing.drop_duplicates("address_key")


def _first_col(df: pd.DataFrame, names: list[str]) -> str | None:
    lower = {str(c).lower(): str(c) for c in df.columns}
    for name in names:
        if name.lower() in lower:
            return lower[name.lower()]
    return None


def _import_csv_rows(path: Path, objects: pd.DataFrame) -> pd.DataFrame:
    src = pd.read_csv(path)
    address_col = _first_col(src, ["address", "Адрес", "Строительный адрес", "query"])
    key_col = _first_col(src, ["address_key", "key"])
    lat_col = _first_col(src, ["lat", "latitude", "Широта"])
    lon_col = _first_col(src, ["lon", "lng", "longitude", "Долгота"])
    if lat_col is None or lon_col is None:
        raise SystemExit("--import-csv requires lat/lon columns")
    if address_col is None and key_col is None:
        raise SystemExit("--import-csv requires address or address_key column")

    imported = src.copy()
    imported["address_key"] = (
        imported[key_col].map(clean_text) if key_col else imported[address_col].map(address_key)
    )
    imported["lat"] = pd.to_numeric(imported[lat_col], errors="coerce")
    imported["lon"] = pd.to_numeric(imported[lon_col], errors="coerce")
    imported = imported.dropna(subset=["lat", "lon"])
    imported = imported[imported["address_key"].fillna("").astype(str).str.strip().ne("")]
    if imported.empty:
        return pd.DataFrame()

    object_keys = objects[["registry", "object_id", "address", "address_key"]].copy()
    joined = object_keys.merge(
        imported[["address_key", "lat", "lon"]].drop_duplicates("address_key", keep="last"),
        on="address_key",
        how="inner",
    )
    if joined.empty:
        return pd.DataFrame()
    joined["coord_source"] = "import_csv"
    joined["precision"] = ""
    joined["updated_at"] = datetime.now().isoformat(timespec="seconds")
    return joined[[
        "registry", "object_id", "address", "address_key", "lat", "lon",
        "coord_source", "precision", "updated_at",
    ]]


def _normalize_cache(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=[
            "registry", "object_id", "address", "address_key", "lat", "lon",
            "coord_source", "precision", "updated_at",
        ])
    return df.drop_duplicates(["registry", "object_id"], keep="last")


def _write_cache(df: pd.DataFrame, path: Path) -> pd.DataFrame:
    normalized = _normalize_cache(df)
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized.to_csv(path, index=False, encoding="utf-8-sig")
    return normalized


def _join_unique(values: pd.Series, limit: int = 5) -> str:
    items = [clean_text(v) for v in values if clean_text(v)]
    unique = list(dict.fromkeys(items))
    if len(unique) <= limit:
        return "; ".join(unique)
    return "; ".join(unique[:limit]) + f"; +{len(unique) - limit}"


def _first_present(values: pd.Series) -> object:
    nonempty = values.dropna()
    if nonempty.empty:
        return ""
    return nonempty.iloc[0]


def _write_address_exports(
    objects: pd.DataFrame,
    cache: pd.DataFrame,
    addresses_out: Path,
    unique_out: Path,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    enriched = apply_geocode_cache(objects, cache).copy()
    enriched["has_coords"] = enriched["lat"].notna() & enriched["lon"].notna()

    object_cols = [
        "registry", "object_id", "source_sheet", "status", "developer", "builder",
        "object_name", "address", "address_key", "okrug", "district", "year",
        "area_total", "area_living", "apartments", "lat", "lon", "coord_source",
        "precision", "has_coords",
    ]
    object_cols = [c for c in object_cols if c in enriched.columns]
    addresses_out.parent.mkdir(parents=True, exist_ok=True)
    enriched[object_cols].to_csv(addresses_out, index=False, encoding="utf-8-sig")

    address_rows = enriched[
        enriched["address_key"].fillna("").astype(str).str.strip().ne("")
        & enriched["address"].fillna("").astype(str).str.strip().ne("")
    ].copy()
    if address_rows.empty:
        unique = pd.DataFrame(columns=[
            "address_key", "address", "object_count", "registries", "statuses",
            "developers", "okrug", "district", "lat", "lon", "coord_source",
            "precision", "has_coords",
        ])
    else:
        unique = (
            address_rows.sort_values(["has_coords", "registry", "object_id"], ascending=[False, True, True])
            .groupby("address_key", as_index=False)
            .agg(
                address=("address", "first"),
                object_count=("object_id", "size"),
                registries=("registry", _join_unique),
                statuses=("status", _join_unique),
                developers=("developer", _join_unique),
                okrug=("okrug", _first_present),
                district=("district", _first_present),
                lat=("lat", _first_present),
                lon=("lon", _first_present),
                coord_source=("coord_source", _first_present),
                precision=("precision", _first_present),
                has_coords=("has_coords", "max"),
            )
        )
    unique_out.parent.mkdir(parents=True, exist_ok=True)
    unique.to_csv(unique_out, index=False, encoding="utf-8-sig")
    return enriched, unique


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=["none", "nominatim", "yandex"], default="none")
    parser.add_argument(
        "--limit",
        default="0",
        help="Max unique missing addresses to geocode. Use 'all' for every missing address. 0 means no external geocoding.",
    )
    parser.add_argument("--sleep", type=float, default=1.1, help="Delay between external geocoding requests.")
    parser.add_argument("--out", type=Path, default=GEOCODE_CACHE)
    parser.add_argument("--import-csv", type=Path, help="CSV with address/address_key and lat/lon columns to merge into cache.")
    parser.add_argument("--missing-out", type=Path, help="Write unique missing addresses to CSV and exit after cache update.")
    parser.add_argument("--addresses-out", type=Path, default=MAP_ADDRESSES, help="Write all map objects with addresses and coordinates to CSV.")
    parser.add_argument("--unique-addresses-out", type=Path, default=UNIQUE_ADDRESSES, help="Write unique map addresses to CSV.")
    parser.add_argument("--no-address-export", action="store_true", help="Do not write address export files.")
    parser.add_argument("--flush-every", type=int, default=25, help="Save cache every N external geocoder results. 0 saves only at the end.")
    args = parser.parse_args()
    if str(args.limit).lower() == "all":
        limit = None
    else:
        limit = int(args.limit)

    objects = load_monitoring_map_objects()
    cache = load_geocode_cache(args.out)

    geometry_rows = objects[
        objects["coord_source"].fillna("").astype(str).str.startswith("geometry")
        & objects["has_coords"]
    ].copy()
    geometry_cache = geometry_rows[[
        "registry", "object_id", "address", "address_key", "lat", "lon", "coord_source"
    ]].drop_duplicates(["registry", "object_id"])

    frames = []
    if not cache.empty:
        frames.append(cache)
    if not geometry_cache.empty:
        frames.append(geometry_cache)
    combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    if args.import_csv:
        imported_rows = _import_csv_rows(args.import_csv, objects)
        if not imported_rows.empty:
            print(f"imported geocoded rows: {len(imported_rows)} from {args.import_csv}")
            combined = pd.concat([combined, imported_rows], ignore_index=True)
        else:
            print(f"imported geocoded rows: 0 from {args.import_csv}")

    if args.provider != "none" and (limit is None or limit > 0):
        yandex_key = _load_yandex_key()
        if args.provider == "yandex" and not yandex_key:
            raise SystemExit(
                "YANDEX_GEOCODER_API_KEY is required for --provider yandex. "
                f"Set it in the environment or create {GEOCODER_CONFIG}."
            )
        missing = _missing_rows(objects, combined)
        if limit is not None:
            missing = missing.head(limit)
        for i, row in enumerate(missing.to_dict("records"), start=1):
            address = row["address"]
            try:
                if args.provider == "yandex":
                    lat, lon, precision = _query_yandex(address, yandex_key)
                else:
                    lat, lon, precision = _query_nominatim(address)
                source = args.provider
                print(f"[{i}/{len(missing)}] {address} -> {lat}, {lon} ({precision})")
            except Exception as exc:  # noqa: BLE001
                lat, lon, precision, source = None, None, f"error: {exc}", args.provider
                print(f"[{i}/{len(missing)}] {address} -> ERROR {exc}")
            geocoded_row = {
                "registry": row["registry"],
                "object_id": row["object_id"],
                "address": address,
                "address_key": address_key(address),
                "lat": lat,
                "lon": lon,
                "coord_source": source if lat is not None and lon is not None else "",
                "precision": precision,
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            }
            combined = pd.concat([combined, pd.DataFrame([geocoded_row])], ignore_index=True)
            if args.flush_every > 0 and i % args.flush_every == 0:
                combined = _write_cache(combined, args.out)
                with_coords_now = combined[["lat", "lon"]].notna().all(axis=1).sum() if not combined.empty else 0
                print(f"saved checkpoint: rows={len(combined)}, with_coords={with_coords_now}")
            if i < len(missing):
                time.sleep(args.sleep)

    combined = _normalize_cache(combined)

    if args.missing_out:
        preview_objects = apply_geocode_cache(objects, combined)
        preview_objects["has_coords"] = preview_objects["lat"].notna() & preview_objects["lon"].notna()
        missing_for_export = _missing_rows(preview_objects, combined)
        export_cols = ["address", "address_key", "developer", "status", "okrug", "district"]
        missing_export = missing_for_export[[c for c in export_cols if c in missing_for_export.columns]].copy()
        args.missing_out.parent.mkdir(parents=True, exist_ok=True)
        missing_export.to_csv(args.missing_out, index=False, encoding="utf-8-sig")
        print(f"missing addresses written: {len(missing_export)} -> {args.missing_out}")

    combined = _write_cache(combined, args.out)

    if not args.no_address_export:
        exported_objects, exported_unique = _write_address_exports(
            objects,
            combined,
            args.addresses_out,
            args.unique_addresses_out,
        )
        print(f"address objects written: {len(exported_objects)} -> {args.addresses_out}")
        print(f"unique addresses written: {len(exported_unique)} -> {args.unique_addresses_out}")

    with_coords = combined[["lat", "lon"]].notna().all(axis=1).sum() if not combined.empty else 0
    updated_objects = apply_geocode_cache(objects, combined)
    updated_missing = int((updated_objects["lat"].isna() | updated_objects["lon"].isna()).sum())
    updated_missing_rows = _missing_rows(updated_objects.assign(has_coords=updated_objects["lat"].notna() & updated_objects["lon"].notna()), combined)
    print(f"objects: {len(objects)}")
    print(f"cache: {args.out}")
    print(f"cached rows: {len(combined)}, with coords: {with_coords}")
    print(f"missing object coords after cache: {updated_missing}")
    print(f"unique missing addresses after cache: {updated_missing_rows['address_key'].nunique() if not updated_missing_rows.empty else 0}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
