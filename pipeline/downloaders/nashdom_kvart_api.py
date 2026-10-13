"""Exact apartment details joined to the public chart's developer population.

The API contains holding and legal-entity records. Exact chart names, rounded
counts/areas and room shares select a single source ID; names alone never do.
"""
from __future__ import annotations

import math
import re

ROOM_FIELDS = {"ONE": "доля_1комн_%", "TWO": "доля_2комн_%", "THREE": "доля_3комн_%", "FOUR": "доля_4+комн_%"}


class ReportError(ValueError):
    pass


def urls(region):
    if region == "rf":
        developer = ["/portal-analytics/api/dashboard/by-developer?isPDF=false", "/portal-analytics/api/dictionaries/developers?"]
    elif region == "msk":
        developer = ["/portal-analytics/api/dashboard/by-developer?regions=77&isPDF=false",
                     "/portal-analytics/api/dictionaries/developers?region=77"]
    else:
        raise ReportError("unknown kvart API region")
    # The regional chart stays global when the apartment region is changed.
    return developer + ["/portal-analytics/api/dashboard/by-region?isPDF=false",
                        "/portal-analytics/api/dictionaries/regions"]


def fetch(driver, region):
    requests = urls(region)
    driver.set_script_timeout(30)
    result = driver.execute_async_script("""
        const done=arguments[arguments.length-1];
        Promise.all(arguments[0].map(async url=>{
            const controller=new AbortController(), timer=setTimeout(()=>controller.abort(),20000);
            try {
                const response=await fetch(url,{credentials:'same-origin',cache:'no-store',signal:controller.signal});
                return {url,status:response.status,data:response.ok ? await response.json() : null};
            } catch(error) { return {url,error:String(error)}; }
            finally { clearTimeout(timer); }
        })).then(done).catch(error=>done([{error:String(error)}]));
    """, requests)
    if not isinstance(result, list) or len(result) != len(requests):
        raise ReportError("incomplete kvart API response batch")
    for requested, response in zip(requests, result):
        if (not isinstance(response, dict) or response.get("url") != requested
                or response.get("status") != 200 or not isinstance(response.get("data"), list)):
            diagnostic = (f"status={response.get('status')} shape={type(response.get('data')).__name__} "
                          f"error={str(response.get('error', ''))[:160]}") if isinstance(response, dict) else "response is not an object"
            raise ReportError("kvart API request was not confirmed: " + requested + " (" + diagnostic + ")")
    return {"region_key": region, "urls": requests, "aggregate": result[0]["data"], "dictionary": result[1]["data"],
            "regions": result[2]["data"], "region_dictionary": result[3]["data"]}


def name_key(value):
    # Whitespace normalization only; legal/group aliases are distinct identities.
    return re.sub(r"\s+", " ", str(value or "")).strip()


def numeric(value, *, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ReportError("invalid numeric kvart API measurement")
    if integer and int(value) != value:
        raise ReportError("noninteger apartment count")
    return value


def measurement_matches(value, display, *, scale=1000):
    label = str(display or "").replace("\xa0", " ").strip()
    match = re.fullmatch(r"(<|≤)?\s*(\d+(?:[.,]\d+)?)", label.replace(" ", ""))
    if not match or not math.isfinite(value) or value <= 0:
        return False
    digits = match.group(2).replace(",", ".")
    center = float(digits) * scale
    if match.group(1) == "<":
        return value < center
    if match.group(1) == "≤":
        return value <= center
    precision = len(digits.split(".")[1]) if "." in digits else 0
    half_unit = scale * 0.5 * 10 ** (-precision)
    return max(0, center - half_unit) - 1e-8 <= value <= center + half_unit + 1e-8


def record_details(row):
    if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"]:
        raise ReportError("kvart API record has no source ID")
    count, area = numeric(row.get("flatCount"), integer=True), numeric(row.get("area"))
    if count <= 0 or area <= 0 or not isinstance(row.get("details"), list):
        raise ReportError("kvart API record has no measured apartments")
    details = {}
    for room in row["details"]:
        if not isinstance(room, dict):
            raise ReportError("invalid kvart API room category")
        kind = room.get("roomCountType")
        if kind not in ROOM_FIELDS or kind in details:
            raise ReportError("invalid/duplicate kvart API room category")
        numeric(room.get("flatCount"), integer=True)
        numeric(room.get("area"))
        if numeric(room.get("percent")) > 100:
            raise ReportError("invalid kvart API room share")
        details[kind] = room
    if sum(room["flatCount"] for room in details.values()) != count:
        raise ReportError("kvart API room counts do not equal the total")
    # Area fields are supplied to one decimal m²; four rounded components can
    # differ from the rounded total by up to 0.25 m².
    if not math.isclose(sum(room["area"] for room in details.values()), area, rel_tol=1e-10, abs_tol=0.25):
        raise ReportError("kvart API room areas do not equal the total")
    return details


def room_distribution_matches(developer, row, details):
    observed = []
    for field in ROOM_FIELDS.values():
        label = str(developer.get(field, "")).strip().replace(",", ".")
        try:
            observed.append(float(label) if label else 0)
        except ValueError:
            return False
    # The chart rounds four count shares to whole percentages, then puts the
    # residual needed for 100% into one category. Which category receives it
    # can change with API array order. The adjusted category's error is at
    # most the other three rounding errors (1.5 percentage points).
    rounded = [math.floor(details.get(kind, {}).get("flatCount", 0) / row["flatCount"] * 100 + 0.5)
               for kind in ROOM_FIELDS]
    residual = 100 - sum(rounded)
    for index, kind in enumerate(ROOM_FIELDS):
        if kind not in details:
            continue
        possible = list(rounded)
        possible[index] += residual
        if all(0 <= value <= 100 for value in possible) and all(abs(a - b) <= 0.01 for a, b in zip(observed, possible)):
            return True
    return False


def resolve(developers, evidence):
    if not isinstance(evidence, dict) or evidence.get("region_key") not in {"rf", "msk"} or evidence.get("urls") != urls(evidence["region_key"]):
        raise ReportError("kvart API evidence has an incorrect region/scope")
    aggregate, dictionary = evidence.get("aggregate"), evidence.get("dictionary")
    if not isinstance(aggregate, list) or not aggregate or not isinstance(dictionary, list):
        raise ReportError("kvart API population is empty")
    names = {}
    for row in dictionary:
        if not isinstance(row, dict):
            raise ReportError("invalid kvart API dictionary record")
        source_id, name = row.get("id"), name_key(row.get("name"))
        if not isinstance(source_id, str) or not source_id or source_id in names or not name:
            raise ReportError("kvart API dictionary has invalid/duplicate IDs")
        names[source_id] = name
    records, by_name = {}, {}
    for row in aggregate:
        details = record_details(row)
        source_id = row["id"]
        if source_id not in names or source_id in records:
            raise ReportError("kvart API population has invalid/duplicate IDs")
        records[source_id] = (row, details)
        by_name.setdefault(names[source_id], []).append((row, details))
    if set(records) != set(names):
        raise ReportError("kvart API dictionary and measurements have different populations")
    if not isinstance(developers, list) or any(not isinstance(row, dict) for row in developers):
        raise ReportError("invalid kvart DOM developer records")
    actual_names = [name_key(row.get("наименование")) for row in developers]
    if not actual_names or len(set(actual_names)) != len(actual_names) or set(actual_names) != set(by_name):
        raise ReportError("kvart DOM developer population differs from the independent API population")
    resolved = []
    for developer in developers:
        candidates = []
        for row, details in by_name[name_key(developer["наименование"])]:
            if not (measurement_matches(row["flatCount"], developer.get("квартиры_тыс_шт"))
                    and measurement_matches(row["area"], developer.get("площадь_тыс_м²"))):
                continue
            candidates.append((row, details))
        # A unique count/area identity does not depend on nondeterministic
        # whole-percent display rounding. Composition disambiguates only
        # otherwise indistinguishable same-name source IDs.
        if len(candidates) > 1:
            candidates = [(row, details) for row, details in candidates if room_distribution_matches(developer, row, details)]
        if len(candidates) != 1:
            raise ReportError("kvart developer identity is unconfirmed or ambiguous: " + developer["наименование"])
        row, details = candidates[0]
        apartments = {"all": {"count": str(row["flatCount"]), "area": str(row["area"] / 1000)}}
        for kind in ROOM_FIELDS:
            room = details.get(kind, {"flatCount": 0, "area": 0})
            apartments[kind] = {"count": str(room["flatCount"]), "area": str(room["area"] / 1000)}
        resolved.append({"site_name": developer["наименование"], "наименование": developer["наименование"],
                         "source_id": row["id"], "apartments": apartments, "api_verified": True})
    return resolved


def region_records(evidence):
    rows, groups = evidence.get("regions"), evidence.get("region_dictionary")
    if not isinstance(rows, list) or not rows or not isinstance(groups, list):
        raise ReportError("kvart API regional population is empty")
    dictionary = []
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get("regions"), list):
            raise ReportError("invalid kvart API regional dictionary")
        for item in group["regions"]:
            if not isinstance(item, dict) or isinstance(item.get("id"), bool) or not isinstance(item.get("id"), int) or item["id"] <= 0:
                raise ReportError("invalid kvart API regional source ID")
            dictionary.append({"id": str(item["id"]), "name": item.get("name")})
    converted = []
    for row in rows:
        if not isinstance(row, dict) or isinstance(row.get("id"), bool) or not isinstance(row.get("id"), int) or row["id"] <= 0:
            raise ReportError("invalid kvart API regional measurements")
        converted.append({**row, "id": str(row["id"])})
    return converted, dictionary


def resolve_regions(regions, evidence):
    """Confirm every global region row, including the selected Moscow button."""
    rows, dictionary = region_records(evidence)
    return resolve(regions, {**evidence, "aggregate": rows, "dictionary": dictionary})


def annotate_measurements(rows, resolved):
    """Retain the provider's display strings alongside confirmed exact values."""
    by_name = {name_key(row["site_name"]): row for row in resolved}
    for row in rows:
        source = by_name[name_key(row["наименование"])]
        row.update(source_id=source["source_id"], api_verified=True,
                   apartments_count_exact=float(source["apartments"]["all"]["count"]),
                   apartments_area_thousand_m2_exact=float(source["apartments"]["all"]["area"]))
        count = float(source["apartments"]["all"]["count"])
        for kind, field in ROOM_FIELDS.items():
            row[field + "_exact"] = float(source["apartments"][kind]["count"]) / count * 100


def apartment_kind(row):
    label = name_key(row.get("тип") or row.get("комнатность"))
    if label.casefold().startswith("все"):
        return "all"
    match = re.match(r"([1-4])(?:\+)?(?:\s|[- ]|ком)", label)
    return {"1": "ONE", "2": "TWO", "3": "THREE", "4": "FOUR"}.get(match[1]) if match else None


def annotate_apartment_totals(rows, totals):
    for row in rows:
        exact = totals[apartment_kind(row)]
        row.update(api_verified=True, apartments_count_exact=exact["count"],
                   apartments_area_thousand_m2_exact=exact["area"] / 1000)


def validate_apartment_totals(apartments, evidence):
    """The main RF/Moscow card must use the same measured source population."""
    records, _ = region_records(evidence)
    selected = records if evidence["region_key"] == "rf" else [row for row in records if row["id"] == "77"]
    if not selected:
        raise ReportError("kvart API population has no Moscow measurements")
    totals = {"all": {"count": 0, "area": 0}, **{kind: {"count": 0, "area": 0} for kind in ROOM_FIELDS}}
    for row in selected:
        details = record_details(row)
        totals["all"]["count"] += row["flatCount"]
        totals["all"]["area"] += row["area"]
        for kind, detail in details.items():
            totals[kind]["count"] += detail["flatCount"]
            totals[kind]["area"] += detail["area"]
    actual = {}
    for row in apartments:
        kind = apartment_kind(row)
        if kind is None or kind in actual:
            raise ReportError("invalid/duplicate kvart main apartment category")
        count = re.sub(r"\s+", "", str(row.get("количество_шт", "")))
        if not re.fullmatch(r"\d+", count) or int(count) != totals[kind]["count"]:
            raise ReportError("kvart main apartment count differs from the regional API population")
        area = row.get("площадь_тыс_м²")
        if totals[kind]["area"] == 0:
            try:
                valid_area = float(str(area).replace(",", ".")) == 0
            except (ValueError, TypeError):
                valid_area = False
        else:
            valid_area = measurement_matches(totals[kind]["area"], area)
        if not valid_area:
            raise ReportError("kvart main apartment area differs from the regional API population")
        actual[kind] = row
    if set(actual) != set(totals):
        raise ReportError("kvart main apartments lack all five room categories")
    return totals
