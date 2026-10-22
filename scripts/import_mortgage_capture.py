"""Import complete public monthly responses collected in a confirmed browser."""
from __future__ import annotations

import argparse
from datetime import date, datetime
import json
import os
from pathlib import Path

from pipeline.downloaders.nashdom_mortgage import RAW_PATH, collection_lock, year_bundle
from pipeline.downloaders.nashdom_mortgage_api import REGION_IDS, normalize_month
from pipeline.mortgage_history import (
    SOURCE_URL, load_checkpoint, load_published, publish_history, save_checkpoint, validate_bundle,
)


def prepare_capture(document):
    if (not isinstance(document, dict) or type(document.get("schemaVersion")) is not int
            or document["schemaVersion"] != 1 or document.get("sourceUrl") != SOURCE_URL):
        raise ValueError("Unsupported mortgage capture")
    available = document.get("availableYears")
    if not isinstance(available, dict) or set(available) != set(REGION_IDS):
        raise ValueError("Capture must offer years for Russia and Moscow")
    for years in available.values():
        if (not isinstance(years, list) or not years or len(years) != len(set(years))
                or any(type(year) is not int or not 1900 <= year <= 2200 for year in years)):
            raise ValueError("Invalid source year options")
    rows = document.get("observations")
    if not isinstance(rows, list) or not rows:
        raise ValueError("Capture has no observations")
    groups, offered, seen = {}, {}, set()
    for item in rows:
        if not isinstance(item, dict):
            raise ValueError("Invalid captured observation")
        region, period, selected = item.get("region"), item.get("period"), item.get("selection")
        normalized = normalize_month(region, period, item.get("exchanges"))
        year, month = int(period[:4]), int(period[5:])
        if year not in available[region] or (region, period) in seen:
            raise ValueError("Duplicate or unoffered mortgage month")
        seen.add((region, period))
        if (not isinstance(selected, dict) or selected.get("period") != period
                or selected.get("regionValue") != str(REGION_IDS[region])
                or selected.get("basis") != "month" or selected.get("year") != str(year)
                or sorted(selected.get("years", [])) != sorted(map(str, available[region]))):
            raise ValueError("Captured page selection does not match its response")
        months = selected.get("months")
        if not isinstance(months, list) or not months:
            raise ValueError("Missing source month options")
        month_options = set()
        for option in months:
            if not isinstance(option, dict) or not isinstance(option.get("period"), str):
                raise ValueError("Invalid offered source month")
            parsed = date.fromisoformat(option["period"] + "-01")
            if parsed.year != year or parsed.month in month_options:
                raise ValueError("Source month options have a different year or duplicates")
            month_options.add(parsed.month)
        scope = (region, year)
        if scope in offered and offered[scope] != month_options:
            raise ValueError("Source calendar changed during capture; refresh this year")
        offered[scope] = month_options
        public_selection = {key: selected[key] for key in ("regionValue", "period", "basis", "year", "years")}
        public_selection["months"] = [{"period": option["period"]} for option in months]
        normalized["selectionEvidence"] = {"kind": "browser-network-capture", "selection": public_selection}
        groups.setdefault(scope, []).append((month, normalized))
    expected = {(region, year) for region, years in available.items() for year in years}
    if set(groups) != expected:
        raise ValueError("Mortgage capture does not cover all source-offered years")
    bundles = []
    for (region, year), observations in sorted(groups.items()):
        if {month for month, _ in observations} != offered[(region, year)]:
            raise ValueError(f"Mortgage capture has missing months: {region}/{year}")
        bundle = year_bundle(region, year, sorted(observations))
        bundle["collectedAt"] = document.get("collectedAt")
        bundles.append(validate_bundle(bundle))
    return available, bundles


def import_capture(path, root):
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    available, bundles = prepare_capture(document)
    raw = Path(root) / RAW_PATH
    with collection_lock(raw):
        previous = load_published(raw)
        for bundle in bundles:
            region, year = bundle["region"], bundle["year"]
            existing = load_checkpoint(raw, region, year)
            prior_rows = [row for row in previous["records"] if row["region"] == region
                          and row["period"].startswith(f"{year}-")] if previous else []
            source = previous.get("yearSources", {}).get(region, {}).get(str(year), {}) if previous else {}
            incoming_time = datetime.fromisoformat(bundle["collectedAt"].replace("Z", "+00:00"))
            for old in (existing, source if prior_rows else None):
                if old and incoming_time < datetime.fromisoformat(
                        old.get("collectedAt", previous["collectedAt"] if previous else "").replace("Z", "+00:00")):
                    raise ValueError(f"Capture is older than saved mortgage data: {region}/{year}")
            incoming_months = {row["period"] for row in bundle["records"]}
            known_rows = prior_rows + (existing["records"] if existing else [])
            if any(row["period"] not in incoming_months for row in known_rows):
                raise ValueError(f"Capture would remove saved mortgage months: {region}/{year}")
        for bundle in bundles:
            save_checkpoint(raw, bundle)
        return publish_history(raw, available, requested_years=available,
                               refreshed_years=[(item["region"], item["year"]) for item in bundles])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument("--root", type=Path, default=Path(os.environ.get("PARSER_ROOT", Path(__file__).resolve().parents[1])))
    args = parser.parse_args()
    result = import_capture(args.capture, args.root)
    print(f"Imported {len(result['records'])} exact monthly mortgage observations")


if __name__ == "__main__":
    main()
