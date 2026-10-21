"""Verified per-year mortgage checkpoints and lossless incremental publication.

The collector calls ``plan_years``, then ``save_checkpoint`` after each successful
``fetch_one(region, year)``. Failed fetches only call ``record_failure``. Finally
``publish_history`` merges verified checkpoints with the last published history.
No function in this module performs network requests or converts YTD into flows.
"""
from __future__ import annotations

import copy
from datetime import date, datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import uuid


SOURCE_URL = "https://\u043d\u0430\u0448.\u0434\u043e\u043c.\u0440\u0444/\u0430\u043d\u0430\u043b\u0438\u0442\u0438\u043a\u0430/\u0438\u043f\u043e\u0442\u0435\u0447\u043d\u043e\u0435_\u043a\u0440\u0435\u0434\u0438\u0442\u043e\u0432\u0430\u043d\u0438\u0435"
REGIONS = ("rf", "msk")
SEGMENTS = ("all", "primary", "secondary")
METRICS = ("volumeRub", "count", "ratePct", "termMonths", "averageAmountRub")
NORMALIZATION_VERSION = 1
PUBLISHED_NAME = "mortgage_history.json"


class HistoryValidationError(ValueError):
    """Invalid source data, incompatible state, or a corrupted checkpoint."""


def _json_bytes(value):
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                          separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise HistoryValidationError("Mortgage data must contain finite JSON values") from exc


def _sha256(value):
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _finite_number(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def _identity(region, year):
    if region not in REGIONS or type(year) is not int or not 1000 <= year <= 9999:
        raise HistoryValidationError("Invalid mortgage region/year identity")
    return region, year


def _timestamp(value):
    if not isinstance(value, str):
        raise HistoryValidationError("Mortgage collection timestamp is missing")
    try:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HistoryValidationError("Invalid mortgage collection timestamp") from exc
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise HistoryValidationError("Mortgage collection timestamp must include a timezone")
    return instant


def _source_metadata(document):
    if document.get("sourceUrl") != SOURCE_URL:
        raise HistoryValidationError("Unexpected mortgage source URL")
    _timestamp(document.get("collectedAt"))
    source_date = document.get("sourceDate")
    if source_date is not None:
        if not isinstance(source_date, str) or not re.fullmatch(r"[1-9]\d{3}-\d{2}-\d{2}", source_date):
            raise HistoryValidationError("Invalid mortgage source date")
        try:
            date.fromisoformat(source_date)
        except ValueError as exc:
            raise HistoryValidationError("Invalid mortgage source date") from exc


def _records(rows, *, region=None, year=None):
    if not isinstance(rows, list) or not rows:
        raise HistoryValidationError("Mortgage data has no observations")
    keys = set()
    for row in rows:
        if not isinstance(row, dict) or row.get("region") not in REGIONS or row.get("segment") not in SEGMENTS:
            raise HistoryValidationError("Invalid mortgage observation region/segment")
        period = row.get("period")
        if not isinstance(period, str) or not re.fullmatch(r"[1-9]\d{3}-(?:0[1-9]|1[0-2])", period):
            raise HistoryValidationError("Invalid mortgage observation month")
        if row.get("basis") != "month":
            raise HistoryValidationError("Mortgage observations must be monthly flows")
        if (region is not None and row["region"] != region) or (year is not None and int(period[:4]) != year):
            raise HistoryValidationError("Mortgage observation does not match its region/year")
        key = (row["region"], period, row["segment"])
        if key in keys:
            raise HistoryValidationError("Duplicate mortgage observation")
        keys.add(key)
        for metric in METRICS:
            if metric not in row:
                raise HistoryValidationError(f"Mortgage metric {metric} is missing")
            value = row[metric]
            if value is not None and not _finite_number(value):
                raise HistoryValidationError(f"Invalid mortgage metric {metric}")


def validate_bundle(bundle, *, region=None, year=None):
    """Detach a year bundle, preserving all JSON evidence and raw provenance.

    Required metadata is ``region, year, records, sourceUrl, collectedAt``;
    ``sourceDate`` may be null. ``availableMonths``, when supplied, is the exact
    set of source-published months, not a synthetic January-to-latest interval.
    A nonempty all-null bundle is rejected rather than erasing known history.
    """
    if not isinstance(bundle, dict):
        raise HistoryValidationError("Mortgage year bundle must be an object")
    bundle_region, bundle_year = _identity(bundle.get("region"), bundle.get("year"))
    if (region is not None and bundle_region != region) or (year is not None and bundle_year != year):
        raise HistoryValidationError("Mortgage bundle identity mismatch")
    _source_metadata(bundle)
    _records(bundle.get("records"), region=bundle_region, year=bundle_year)
    if not any(row[metric] is not None for row in bundle["records"] for metric in METRICS):
        raise HistoryValidationError("Mortgage year bundle has no known measurements")
    if "availableMonths" in bundle:
        months = bundle["availableMonths"]
        if (not isinstance(months, list) or any(type(month) is not int or not 1 <= month <= 12 for month in months)
                or len(set(months)) != len(months)):
            raise HistoryValidationError("Invalid mortgage available months")
        observed = {int(row["period"][5:]) for row in bundle["records"]}
        if set(months) != observed:
            raise HistoryValidationError("Mortgage bundle does not cover its published months")
    _json_bytes(bundle)
    return copy.deepcopy(bundle)


def validate_history(document):
    """Validate a public history without treating missing coverage as confirmed."""
    if (not isinstance(document, dict) or type(document.get("schemaVersion")) is not int
            or document["schemaVersion"] != 1):
        raise HistoryValidationError("Unsupported mortgage history schema")
    _source_metadata(document)
    _records(document.get("records"))
    if "coverage" in document and not isinstance(document["coverage"], dict):
        raise HistoryValidationError("Invalid mortgage history coverage")
    coverage_regions = document.get("coverage", {}).get("regions", {})
    if not isinstance(coverage_regions, dict) or set(coverage_regions) - set(REGIONS):
        raise HistoryValidationError("Invalid mortgage per-region coverage")
    for region, coverage in coverage_regions.items():
        if not isinstance(coverage, dict) or not isinstance(coverage.get("years", {}), dict):
            raise HistoryValidationError("Invalid mortgage region coverage")
        _years({region: coverage.get("requestedYears", [])})
        for year, state in coverage.get("years", {}).items():
            if not isinstance(year, str) or not re.fullmatch(r"[1-9]\d{3}", year) or not isinstance(state, dict):
                raise HistoryValidationError("Invalid mortgage year coverage")
            for flag in ("confirmed", "annualComplete"):
                if flag in state and type(state[flag]) is not bool:
                    raise HistoryValidationError(f"Invalid mortgage coverage {flag} flag")
            if "collectedAt" in state:
                _source_metadata({"sourceUrl": SOURCE_URL, "collectedAt": state["collectedAt"],
                                  "sourceDate": state.get("sourceDate")})
    if "notes" in document and (not isinstance(document["notes"], list)
                                or any(not isinstance(note, str) for note in document["notes"])):
        raise HistoryValidationError("Invalid mortgage history notes")
    _json_bytes(document)
    return copy.deepcopy(document)


def _years(available_years):
    if not isinstance(available_years, dict) or set(available_years) - set(REGIONS):
        raise HistoryValidationError("Mortgage available years must be grouped by region")
    result = {}
    for region in REGIONS:
        years = available_years.get(region, [])
        if not isinstance(years, (list, tuple)):
            raise HistoryValidationError("Mortgage source years must be a list")
        for year in years:
            _identity(region, year)
        result[region] = sorted(set(years))
    return result


def _ordered_records(rows):
    return sorted(rows, key=lambda row: (REGIONS.index(row["region"]), row["period"], SEGMENTS.index(row["segment"])))


def _group_records(document):
    groups = {}
    for row in document["records"]:
        groups.setdefault((row["region"], int(row["period"][:4])), []).append(row)
    return {key: _ordered_records(rows) for key, rows in groups.items()}


def _coverage(bundle):
    rows = _ordered_records(bundle["records"])
    months = sorted({int(row["period"][5:]) for row in rows})
    known = sorted({int(row["period"][5:]) for row in rows if any(row[metric] is not None for metric in METRICS)})
    metrics = {segment: {metric: sorted(int(row["period"][5:]) for row in rows
                                      if row["segment"] == segment and row[metric] is not None)
                         for metric in METRICS} for segment in SEGMENTS}
    return {"confirmed": known == months, "observedMonths": months, "confirmedMonths": known,
            "availableMonths": sorted(bundle.get("availableMonths", months)),
            "latestObservedMonth": max(known, default=None),
            "annualComplete": all(metrics["all"][metric] == list(range(1, 13)) for metric in ("volumeRub", "count")),
            "metricMonths": metrics, "recordsSha256": _sha256(rows),
            "sourceIdentity": {"url": SOURCE_URL, "normalizationVersion": NORMALIZATION_VERSION,
                               "bundleSha256": _sha256(bundle)},
            "sourceDate": bundle.get("sourceDate"), "collectedAt": bundle["collectedAt"]}


def _prior_state(document, region, year, rows):
    state = document.get("coverage", {}).get("regions", {}).get(region, {}).get("years", {}).get(str(year))
    if not isinstance(state, dict) or state.get("recordsSha256") != _sha256(_ordered_records(rows)):
        return None
    identity = state.get("sourceIdentity", {})
    if not isinstance(identity, dict) or (identity.get("url") != SOURCE_URL
                                        or type(identity.get("normalizationVersion")) is not int
                                        or identity["normalizationVersion"] != NORMALIZATION_VERSION
                                        or not isinstance(identity.get("bundleSha256"), str)
                                        or not re.fullmatch(r"[0-9a-f]{64}", identity["bundleSha256"])):
        return None
    # Recheck completeness from the records; a stale flag cannot suppress retry.
    actual = _coverage({"records": rows, "collectedAt": document["collectedAt"]})
    if state.get("confirmed") is not actual["confirmed"] or state.get("annualComplete") is not actual["annualComplete"]:
        return None
    return state


def plan_years(available_years, prior=None, force=False):
    """Return RF then Moscow, ascending years, using only discovered source years.

    First run and ``force`` request every source year. Later runs always refresh
    each region's newest year, any missing/unconfirmed year, and its preceding
    discovered year while its all-market annual volume/count remain incomplete.
    A successfully collected partial year is confirmed but not annual-complete.
    Legacy histories without verifiable per-year coverage are fetched again.
    """
    available = _years(available_years)
    if type(force) is not bool:
        raise HistoryValidationError("Mortgage force flag must be boolean")
    previous = validate_history(prior) if prior is not None else None
    groups = _group_records(previous) if previous else {}
    planned = []
    for region in REGIONS:
        years = available[region]
        newest = years[-1] if years else None
        preceding = years[-2] if len(years) > 1 else None
        for year in years:
            rows = groups.get((region, year), [])
            state = _prior_state(previous, region, year, rows) if previous and rows else None
            if (force or year == newest or not state or state.get("confirmed") is not True
                    or (year == preceding and state.get("annualComplete") is not True)):
                planned.append((region, year))
    return planned


def _safe_path(raw_dir, *parts):
    root = Path(raw_dir).resolve()
    target = root.joinpath(*parts)
    if not target.resolve().is_relative_to(root):
        raise HistoryValidationError("Mortgage history path escapes its raw directory")
    current = target
    while current != root:
        if current.is_symlink():
            raise HistoryValidationError("Mortgage history paths must not be symlinks")
        current = current.parent
    return target


def _atomic_json(path, payload):
    encoded = _json_bytes(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", prefix=f".{path.name}.", suffix=".tmp",
                                         dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _read_json(path):
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise HistoryValidationError(f"Duplicate mortgage JSON key: {key}")
            result[key] = value
        return result

    try:
        document = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
    except FileNotFoundError:
        return None
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise HistoryValidationError(f"Corrupted mortgage JSON: {path}") from exc
    if not isinstance(document, dict):
        raise HistoryValidationError(f"Mortgage JSON must contain an object: {path}")
    return document


def save_checkpoint(raw_dir, bundle):
    """Atomically install one verified ``fetch_one`` bundle; return its path."""
    checked = validate_bundle(bundle)
    region, year = checked["region"], checked["year"]
    path = _safe_path(raw_dir, ".mortgage_history", "checkpoints", region, f"{year}.json")
    _atomic_json(path, {"schemaVersion": 1, "normalizationVersion": NORMALIZATION_VERSION,
                        "region": region, "year": year, "bundleSha256": _sha256(checked), "bundle": checked})
    return path


def load_checkpoint(raw_dir, region, year):
    """Return a hash-verified bundle or None only if absent; corruption raises."""
    _identity(region, year)
    path = _safe_path(raw_dir, ".mortgage_history", "checkpoints", region, f"{year}.json")
    envelope = _read_json(path)
    if envelope is None:
        return None
    if (not isinstance(envelope, dict) or type(envelope.get("schemaVersion")) is not int
            or envelope["schemaVersion"] != 1 or type(envelope.get("normalizationVersion")) is not int
            or envelope["normalizationVersion"] != NORMALIZATION_VERSION
            or envelope.get("region") != region or type(envelope.get("year")) is not int or envelope["year"] != year):
        raise HistoryValidationError(f"Mortgage checkpoint identity/version mismatch: {path}")
    bundle = validate_bundle(envelope.get("bundle"), region=region, year=year)
    if envelope.get("bundleSha256") != _sha256(bundle):
        raise HistoryValidationError(f"Mortgage checkpoint hash mismatch: {path}")
    return bundle


def load_published(raw_dir):
    """Read only the exact public filename, never a diagnostic or arbitrary JSON."""
    document = _read_json(_safe_path(raw_dir, PUBLISHED_NAME))
    return validate_history(document) if document is not None else None


def record_failure(raw_dir, region, year, error, *, at=None, details=None):
    """Write a separate diagnostic without touching checkpoint/public history.

    ``at`` defaults to the actual UTC diagnostic time, never a report date.
    The collector chooses which network exceptions to handle; this module does
    not catch validation, disk or programming failures and pretend success.
    """
    _identity(region, year)
    instant = at if at is not None else datetime.now(timezone.utc).isoformat()
    parsed = _timestamp(instant)
    path = _safe_path(raw_dir, ".mortgage_history", "diagnostics",
                      f"{parsed.strftime('%Y%m%dT%H%M%S%f')}_{region}_{year}_{uuid.uuid4().hex}.json")
    payload = {"schemaVersion": 1, "region": region, "year": year, "sourceUrl": SOURCE_URL,
               "at": instant, "error": {"type": type(error).__name__, "message": str(error)}}
    if details is not None:
        payload["details"] = details
    _atomic_json(path, payload)
    return path


def assemble_history(raw_dir, available_years, *, prior=None, requested_years=None, refreshed_years=None):
    """Merge only verified checkpoints; retain all prior years outside replacements.

    ``raw_dir`` is the nashdom raw directory, including in a staged Docker root.
    ``requested_years`` is a region-to-years mapping for this run (defaults to
    discovered years). It is tracked independently from confirmed observations.
    ``refreshed_years`` optionally lists successful ``(region, year)`` scopes;
    their checkpoints must exist. Without it all discovered existing checkpoints
    are considered, which resumes an interrupted first collection. Failed fetches
    must not overwrite checkpoints and must not appear in this successful list.
    """
    available = _years(available_years)
    requested = _years(requested_years) if requested_years is not None else available
    if any(set(requested[region]) - set(available[region]) for region in REGIONS):
        raise HistoryValidationError("Requested mortgage years were not discovered at the source")
    previous = validate_history(prior) if prior is not None else None
    result = copy.deepcopy(previous) if previous else {}
    groups = _group_records(previous) if previous else {}
    scopes = [(region, year) for region in REGIONS for year in available[region]]
    if refreshed_years is not None:
        scopes = list(refreshed_years)
        if any(not isinstance(scope, (list, tuple)) or len(scope) != 2 for scope in scopes):
            raise HistoryValidationError("Invalid mortgage refreshed scopes")
        scopes = [_identity(*scope) for scope in scopes]
        if len(set(scopes)) != len(scopes) or any(year not in available[region] for region, year in scopes):
            raise HistoryValidationError("Mortgage refreshed scopes must be unique discovered years")
    states, sources = {}, copy.deepcopy(result.get("yearSources", {}))
    if not isinstance(sources, dict):
        raise HistoryValidationError("Invalid mortgage year source metadata")
    for (region, year), rows in groups.items():
        states[(region, year)] = copy.deepcopy(_prior_state(previous, region, year, rows) or
                                             {"confirmed": False, "recordsSha256": _sha256(rows)})
    for region, year in scopes:
        bundle = load_checkpoint(raw_dir, region, year)
        if bundle is None:
            if refreshed_years is not None:
                raise HistoryValidationError(f"Successful mortgage checkpoint is absent: {region}/{year}")
            continue
        groups[(region, year)] = _ordered_records(bundle["records"])
        states[(region, year)] = _coverage(bundle)
        if region not in sources:
            sources[region] = {}
        if not isinstance(sources[region], dict):
            raise HistoryValidationError("Invalid mortgage per-region source metadata")
        sources[region][str(year)] = {key: copy.deepcopy(value) for key, value in bundle.items() if key != "records"}
    rows = _ordered_records([row for group in groups.values() for row in group])
    if any(not any(row["region"] == region and any(row[metric] is not None for metric in METRICS)
                   for row in rows) for region in REGIONS):
        raise HistoryValidationError("Mortgage publication requires known measurements for both RF and Moscow")
    regions = {}
    prior_regions = previous.get("coverage", {}).get("regions", {}) if previous else {}
    if not isinstance(prior_regions, dict):
        raise HistoryValidationError("Invalid mortgage per-region coverage")
    for region in REGIONS:
        prior_region = prior_regions.get(region, {})
        if not isinstance(prior_region, dict):
            raise HistoryValidationError("Invalid mortgage region coverage")
        prior_requested = _years({region: prior_region.get("requestedYears", [])})[region]
        regions[region] = {"requestedYears": sorted(set(prior_requested + requested[region])),
                           "availableYears": available[region],
                           "years": {str(year): state for (key_region, year), state in sorted(states.items())
                                     if key_region == region}}
    timestamps = [state["collectedAt"] for state in states.values() if state.get("collectedAt")]
    if previous:
        timestamps.append(previous["collectedAt"])
    dates = [state.get("sourceDate") for state in states.values()]
    result.update({"schemaVersion": 1, "sourceUrl": SOURCE_URL,
                   "collectedAt": max(timestamps, key=_timestamp),
                   "sourceDate": max(dates) if dates and all(dates) else None,
                   "records": rows, "coverage": {"regions": regions}, "yearSources": sources})
    return validate_history(result)


def publish_history(raw_dir, available_years, *, prior=None, requested_years=None, refreshed_years=None):
    """Atomically write and return the merged public snapshot.

    If ``prior`` is omitted, the exact existing publication is loaded first.
    Validation/corruption/write failures leave that publication unchanged.
    """
    previous = load_published(raw_dir) if prior is None else prior
    document = assemble_history(raw_dir, available_years, prior=previous,
                                requested_years=requested_years, refreshed_years=refreshed_years)
    _atomic_json(_safe_path(raw_dir, PUBLISHED_NAME), document)
    return document
