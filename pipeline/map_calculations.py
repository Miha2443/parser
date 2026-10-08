"""Map-only registry scope; other reports keep their existing loader rules."""
import pandas as pd

from pipeline.map_coordinates import (
    _aggregate_registry, apply_area_centroids, apply_geocode_cache, apply_local_geometry,
)


def build_objects(rv, oks, cache):
    oks = oks.copy()
    # Date of issue is authoritative: do not substitute planned commissioning year.
    dates = pd.to_datetime(oks["Срок выдачи РС"], errors="coerce", dayfirst=True)
    eligible = dates.ge(pd.Timestamp("2011-01-01"))
    oks = oks.loc[eligible].copy()
    rv = rv.copy()
    rv["source_sheet"] = "Реестр РВ"
    oks["source_sheet"] = "Реестр ОКС"
    rv_objects = _aggregate_registry(rv, "rv")
    oks_objects = _aggregate_registry(oks, "oks")
    building = oks_objects[oks_objects["status"].eq("Строится")] if not oks_objects.empty else oks_objects
    objects = pd.concat([rv_objects, building], ignore_index=True)
    if objects.empty:
        raise ValueError("Map registries contain no eligible objects")
    objects = apply_local_geometry(objects, oks_objects)
    objects = apply_geocode_cache(objects, cache)
    objects = apply_area_centroids(objects)
    valid = objects["lat"].between(-90, 90) & objects["lon"].between(-180, 180)
    objects["has_coords"] = valid
    objects["id"] = objects["registry"] + ":" + objects["object_id"].astype(str)
    objects["quality"] = "missing"
    approximate = objects["coord_source"].fillna("").str.contains("centroid", case=False)
    approximate |= objects["precision"].fillna("").astype(str).str.lower().eq("approximate")
    objects.loc[valid, "quality"] = "located"
    objects.loc[valid & approximate, "quality"] = "approximate"
    return objects, {"rvRows": len(rv), "oksRows": len(oks),
                     "excludedOksRows": int((~eligible).sum()),
                     "missingIssueDateRows": int(dates.isna().sum())}


def filter_objects(objects, developer=None, statuses=(), okrugs=(), year_from=None, year_to=None,
                   quality="all", only_with_coords=True):
    selected = objects.copy()
    if developer:
        selected = selected[selected["developer"].eq(developer)]
    if statuses:
        selected = selected[selected["status"].isin(statuses)]
    if okrugs:
        selected = selected[selected["okrug"].isin(okrugs)]
    if year_from is not None:
        selected = selected[selected["year"].isna() | selected["year"].ge(year_from)]
    if year_to is not None:
        selected = selected[selected["year"].isna() | selected["year"].le(year_to)]
    if quality != "all":
        selected = selected[selected["quality"].eq(quality)]
    if only_with_coords:
        selected = selected[selected["has_coords"]]
    return selected
