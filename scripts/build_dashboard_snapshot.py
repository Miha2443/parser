"""Freeze existing developer-profile calculations without importing Streamlit.

Only the requested JSON is written. DataAccess retains its mart/raw selection
rules; file hashes and read evidence make that selection independently auditable.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.metadata
import json
import math
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipeline.data_access import DataAccess, DataContext  # noqa: E402
from pipeline.dev_name_utils import normalize_developer_name as norm  # noqa: E402

PAGE = Path("app/pages/7_Профиль_застройщика.py")
OUTPUT = Path("frontend/public/profile-snapshot.json")
FAMILIES = {
    "monitoring_2_0": ("monitoring_2_0_*.xlsx",),
    "erzrf_top": ("top_obyem_stroitelstva_rf_*.xlsx",),
    "erzrf_cards": ("cards_*.xlsx",),
    "rasprodannost": ("rasprodannost_*.xlsx",),
    "kvartirografia": ("kvartirografia_*.xlsx", "kvartirografia_*.json"),
    "escrow_manual": ("Наполняемость*.xlsx", "наполняемость*.xlsx", "*эскроу*.xlsx"),
}
from pipeline.profile_calculations import (
    CATEGORIES,
    CAT_COLS,
    ROOMS,
    DASH,
    clean,
    number,
    page_float,
    ru_num,
    percent,
    display_percent,
    rows_for,
    records,
    categories,
    category_donut,
    annual_data,
    top_row,
    top_value,
    rating_cell,
    ratings_data,
    cards_row,
    housing_values,
    housing_data,
    rasprod_data,
    apartment_regions,
    apartment_data,
    delay_data,
    escrow_data,
    object_tables,
    make_profile,
)


def digest(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def relative(path, root):
    try:
        return Path(path).resolve().relative_to(root).as_posix()
    except ValueError:
        return str(path)


def file_evidence(path, root):
    stat = path.stat()
    return {"path": relative(path, root), "sizeBytes": stat.st_size, "mtimeNs": stat.st_mtime_ns,
            "modifiedAtUtc": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(), "sha256": digest(path)}


class ObservedAccess(DataAccess):
    def __init__(self, context):
        super().__init__(context)
        self.mart_reads = []

    def _load_realty_mart(self, name, raw_files):
        value = super()._load_realty_mart(name, raw_files)
        self.mart_reads.append({"family": name, "accepted": value is not None,
                                "path": f"data/marts/realty/{name}.pkl"})
        return value


def verify_page_helpers(root, data, profiles):
    """Compile only pure function definitions from the reference page, never imports/UI."""
    tree = ast.parse((root / PAGE).read_text(encoding="utf-8"))
    wanted = {"categorize_sum", "find_dev_rows", "get_rating", "erzrf_value", "get_cards_row", "housing_comparison"}
    definitions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in wanted]
    if {node.name for node in definitions} != wanted:
        raise RuntimeError("Reference-page helper contract changed; review snapshot mapping")
    env = {"pd": pd, "norm": norm, "CAT_LABELS": [label for _, _, label in CATEGORIES],
           "CAT_KEYS": [key for _, key, _ in CATEGORIES], "CAT_COL_PREFIX": "category_",
           "erzrf_top": data["erzrf_top"], "erzrf_cards": data["erzrf_cards"]}
    exec(compile(ast.Module(body=definitions, type_ignores=[]), str(PAGE), "exec"), env)
    checks = 0
    for profile in profiles:
        key = profile["developerKey"]
        env["sel_key"] = key
        rv = env["find_dev_rows"](data["monitoring_2_0"]["rv"], "Группа компаний", key)
        env["rv_dev"] = rv
        env["cards_row"] = env["get_cards_row"]()
        expected = list(env["categorize_sum"](rv).values())
        actual = [segment["valueM2"] for segment in profile["categoryDonuts"][0]["segments"]]
        assert clean(expected) == ([0.0] * 4 if rv.empty else actual), (profile["id"], "category sums")
        checks += 1
        for sorting, row in zip(("nakopl_vvod", "obyem_stroitelstva"), profile["ratings"]["rows"]):
            for region in ("rf", "msk"):
                assert clean(env["get_rating"](sorting, region)) == (row[region]["row"] or {}), (profile["id"], "rating")
                checks += 1
        for item in profile["housingComparison"]:
            expected = env["housing_comparison"](item["years"])
            actual = {seg["label"]: seg["valueM2"] for seg in item["segments"]} if item["segments"] else None
            assert clean(expected) == actual, (profile["id"], "housing comparison")
            checks += 1
        for region in ("rf", "msk"):
            for field, sorting, substring in [
                ("constructionM2", "obyem_stroitelstva", "Строится"),
                ("constructionDelayM2", "obyem_stroitelstva", "С переносом срока"),
                ("currentInputM2", "obyem_vvoda", "Введено"),
                ("currentInputDelayM2", "obyem_vvoda", "С переносом срока"),
            ]:
                assert clean(env["erzrf_value"](sorting, region, substring)) == profile["delays"]["inputs"][region][field]
                checks += 1
    return {"referenceHelperChecks": checks, "referenceHelpers": sorted(wanted),
            "method": "AST-isolated pure functions from current profile page; no Streamlit/facade import",
            "scope": "Company matching, all-input categories, four rating rows, housing comparison, ERZ delay operands. "
                     "Not proof of complete UI, formatting, apartments, escrow or six-card parity."}


def verify_page_output(root, snapshot):
    """Execute the original page with explicit core data and a capturing UI stub.

    Imports are excluded, but all original selection/calculation/render code is
    executed. This checks displayed data, not browser/Streamlit visual behavior.
    """
    import plotly.graph_objects as go

    access = DataAccess(DataContext(root, use_marts=snapshot["provenance"]["context"]["useMarts"],
                                    require_marts=snapshot["provenance"]["context"]["requireMarts"]))
    data = {name: getattr(access, f"load_{name}")() for name in FAMILIES}
    tree = ast.parse((root / PAGE).read_text(encoding="utf-8"))
    tree.body = [node for node in tree.body if not isinstance(node, (ast.Import, ast.ImportFrom))]
    code = compile(tree, str(PAGE), "exec")

    class Capture:
        def __init__(self, profile):
            self.profile = profile
            self.metrics, self.frames, self.figures, self.html = [], [], [], []
            self.sidebar = self.column_config = self

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def columns(self, widths):
            return [self] * (widths if isinstance(widths, int) else len(widths))

        def selectbox(self, label, options, **kwargs):
            assert self.profile["developer"] in options
            return self.profile["developer"]

        def radio(self, label, options, **kwargs):
            assert self.profile["region"] in options
            return self.profile["region"]

        def metric(self, label, value, *args, **kwargs):
            self.metrics.append((label, value))

        def dataframe(self, frame, **kwargs):
            self.frames.append(frame)

        def plotly_chart(self, figure, **kwargs):
            self.figures.append((kwargs["key"], figure))

        def markdown(self, text, **kwargs):
            self.html.append(text)

        def __getattr__(self, name):
            return lambda *args, **kwargs: self

    for profile in snapshot["profiles"]:
        ui = Capture(profile)
        env = {"pd": pd, "go": go, "st": ui, "norm": norm,
               "COLORS": {key: "#333333" for key in ("green", "amber", "blue", "red", "text", "muted", "stroke", "ink", "line", "panel")},
               "apply_theme": lambda: None, "page_header": lambda *a, **k: None,
               "style_plotly": lambda *a, **k: None, "chart_data_expander": lambda *a, **k: None,
               "latest_realty_mart_source_date": access.latest_realty_mart_source_date,
               "latest_raw_source_date": access.latest_raw_source_date}
        env.update({f"load_{name}": (lambda value=value: value) for name, value in data.items()})
        exec(code, env)
        expected_metrics = []
        expected_frames = []
        apartments = profile["apartments"]
        if apartments["status"] == "available":
            expected_frames.append([{"Тип": row["type"], "Количество, шт": row["count"],
                                     "Доля, %": row["sharePercent"], "Площадь, тыс. м²": row["areaThousandM2"]}
                                    for row in apartments["rows"]])
            expected_metrics += [(title, apartments["display"][field]) for title, field in [
                ("Квартиры", "totalCount"), ("Площадь", "totalAreaThousandM2"),
                ("Ср. площадь квартиры", "averageAreaM2"), ("Доля рынка региона", "marketSharePercent")]]
        for name in ("commissioned", "permitted"):
            table = profile["objects"][name]
            if table["rows"]:
                expected_frames.append(table["rows"])
        assert [clean(frame.to_dict("records")) for frame in ui.frames] == clean(expected_frames), (profile["id"], "tables")
        escrow = profile["escrow"]
        if escrow["status"] == "available":
            expected_metrics += [(title, escrow["display"][field]) for title, field in [
                ("Объём займов", "loanRub"), ("Остаток выплат", "debtRub"), ("% Доля остатка", "debtSharePercent"),
                ("Выручка от продаж", "revenueRub"), ("Покрытие займов выручкой", "coveragePercent")]]
        assert ui.metrics == expected_metrics, (profile["id"], "displayed metrics")
        for prefix, donuts in [("structure", profile["categoryDonuts"]), ("housing", profile["housingComparison"])]:
            for donut in donuts:
                if donut["status"] != "available":
                    continue
                figure = next(fig for key, fig in ui.figures if key == f"donut_{prefix}_{donut['title']}")
                assert list(figure.data[0].values) == [s["valueM2"] for s in donut["segments"]], (profile["id"], donut["id"])
        if profile["annual"]["rows"]:
            bar = next(fig for key, fig in ui.figures if key == "dynamics_bar")
            for trace, category in zip(bar.data[:4], snapshot["schema"]["categoryKeys"]):
                assert list(trace.y) == [row["valuesM2"][category] / 1000 for row in profile["annual"]["rows"]], (profile["id"], "annual")
        delay_variables = [("perenos_stroy_msk", "mon_stroy_msk"), ("regiony_stroy_value", "regiony_stroy_base"),
                           ("perenos_msk_2225", "mon_vvod_msk_2225"), ("regiony_2225_value", "regiony_2225_base"),
                           ("perenos_vvod_msk_2026", "mon_vvod_msk_2026"), ("regiony_2026_value", "regiony_2026_base")]
        for card, (value, base) in zip(profile["delays"]["cards"], delay_variables):
            assert clean(env[value]) == card["valueM2"] and clean(env[base]) == card["baseM2"], (profile["id"], card["id"], "operands")
            html = next(text for text in ui.html if card["title"] in text and "font-size:34px" in text)
            assert all(card[field] in html for field in ("displayValue", "displayBase", "displayPercent")), (profile["id"], card["id"], "display")
        if "find_num" in env:
            for region in ("msk", "rf"):
                original_row, _ = env["latest_region_row"](region)
                for field, (_, predicate) in zip(("soldPercent", "readinessPercent", "ratioPercent"), env["preds"]):
                    actual = env["find_num"](original_row, predicate)
                    expected = profile["rasprod"][region]["display"][field]
                    assert actual == expected, (profile["id"], region, field, actual, expected)
        print(f"Original-page capture passed: {profile['id']} (metrics={len(ui.metrics)}, "
              f"tables={len(ui.frames)}, plots={len(ui.figures)}, delay cards=6)")
    print("Captured numeric/display data only; browser layout, source truth and full parity not certified")


def build_snapshot(root, generated_at, use_marts=True, require_marts=False):
    context = DataContext(root, root / "downloads", use_marts=use_marts, require_marts=require_marts)
    access = ObservedAccess(context)
    family_files = {name: access.source_files(name) for name in FAMILIES}
    input_paths = set(path for files in family_files.values() for path in files)
    input_paths.update(root / "data/marts/realty" / f"{name}.pkl" for name in FAMILIES)
    input_paths.add(root / "data/marts/realty/manifest.json")
    input_paths = {path.resolve() for path in input_paths if path.is_file()}
    before = {path: file_evidence(path, root) for path in sorted(input_paths)}
    versions = {name: access.data_version(f"load_{name}") for name in FAMILIES}
    opened, current_family = {name: set() for name in FAMILIES}, [None]
    def audit(event, args):
        if event == "open" and current_family[0] is not None and isinstance(args[0], (str, bytes)):
            path = Path(args[0]).resolve()
            if path in input_paths:
                opened[current_family[0]].add(path)
    sys.addaudithook(audit)
    data = {}
    try:
        for name in FAMILIES:
            current_family[0] = name
            data[name] = getattr(access, f"load_{name}")()
    finally:
        current_family[0] = None
    source_dates = {name: access.latest_realty_mart_source_date(name) or access.latest_raw_source_date(*patterns) or None
                    for name, patterns in FAMILIES.items()}
    mon = data["monitoring_2_0"]
    if not mon.get("developers"):
        raise RuntimeError("Monitoring has no actual developer selector; no profiles can be frozen")
    requested = [("пик", "ПИК"), ("самолет", "САМОЛЕТ"), ("capital group", "CAPITAL GROUP")]
    profiles, developers, excluded = [], [], []
    for key, preferred in requested:
        names = [name for name in mon["developers"] if norm(name) == key]
        if not names:
            excluded.append({"developerKey": key, "reason": "Absent from actual monitoring selector"})
            continue
        canonical = preferred if preferred in names else min(names, key=lambda name: (len(name), name))
        regions = apartment_regions(data["kvartirografia"], key)
        regions = [reg for reg in regions if reg in ("msk", "rf")]
        # No apartment rows must not remove a genuine monitoring profile.
        regions = regions or ["msk"]
        developers.append({"developer": canonical, "developerKey": key, "monitoringAliases": names, "regions": regions})
        for region in regions:
            profiles.append(clean(make_profile(canonical, region, data)))
    verification = verify_page_helpers(root, data, profiles)
    after = {path: file_evidence(path, root) for path in sorted(input_paths)}
    after_versions = {name: access.data_version(f"load_{name}") for name in FAMILIES}
    if before != after or versions != after_versions:
        raise RuntimeError("Source inventory/content/version changed during extraction; artifact not written")
    def portable_quality(value):
        if isinstance(value, dict):
            return {k: relative(v, root) if k == "source" and isinstance(v, str) else portable_quality(v) for k, v in value.items()}
        if isinstance(value, list):
            return [portable_quality(v) for v in value]
        return value
    source_evidence = {}
    for name in FAMILIES:
        source_evidence[name] = {
            "candidateRawFiles": [relative(path, root) for path in family_files[name]],
            "openedInputs": [relative(path, root) for path in sorted(opened[name])],
            "martReadAttempts": [item for item in access.mart_reads if item["family"] == name],
            "dataVersion": [{"path": relative(item[0], root), "signature": list(item[1:])} for item in versions[name]],
            "dateLabel": source_dates[name],
            "dateBasis": "manifest latest source mtime, otherwise raw matching file mtime; same as page, "
                         "not certified observation/download date or necessarily selected raw file date",
        }
    code_paths = [PAGE, Path("pipeline/data_access.py"), Path("pipeline/dev_name_utils.py"),
                  Path("pipeline/paths.py"), Path("pipeline/source_records.py"),
                  Path("pipeline/profile_calculations.py"), Path("scripts/build_dashboard_snapshot.py")]
    schema = {
        "numericPolicy": "Finite JSON numbers only. Unknown/non-finite -> null; no imputation beyond existing loader/page rules. "
                         "Page-calculated defaults/zeroes documented per section; display fields retain dash semantics.",
        "regionPolicy": "profiles[].region controls apartments ONLY; other sections retain explicit msk/rf/otherRf scopes.",
        "profiles": "Array of real monitoring selector developers x actual apartment scopes; id=normalizedName:region.",
        "ratings": "{rows:[{id,title,rf:{place,displayPlace,row},msk:{place,displayPlace,row}}],score,displayScore,quality,sourceNote}",
        "categoryDonuts": "Four {id,title,region,years,status,rowCount,segments:[{key,label,valueM2,displayThousandM2}],totalM2,displayTotalThousandM2,sourceNote}",
        "housingComparison": "Three {id,title,years,status,segments|null,totalM2,displayTotalThousandM2,sourceNote}; segments scopes msk,otherRf.",
        "annual": "{region,rows:[{year,valuesM2,totalM2}],allYears,legend:{valuesM2,totalM2,displayMillionM2,displayTotalMillionM2,years},sourceNote}",
        "categoryKeys": [out for out, _, _ in CATEGORIES],
        "rasprod": "{msk,rf,sourceNote}; each {period:{year,month}|null,values:{soldPercent,readinessPercent,ratioPercent},display,columns,row,status}",
        "apartments": "{region,availableRegions,status,totalCount,totalAreaThousandM2,averageAreaM2,marketTotalAreaThousandM2,marketSharePercent,rows:[{type,count,sharePercent,areaThousandM2,displayCount,displaySharePercent,displayAreaThousandM2}]|null,display,source,aggregateRow,perDevRow,perDevAccepted,reportDate,sourceNote}",
        "delays": "{cards:[{id,region,title,valueM2,baseM2,percent,displayValue,displayBase,displayPercent,sourceNote}],inputs,annualTopRows,annualCardRows,cardRow,sourceNote}",
        "escrow": "{region:'msk',status,loanRub,debtRub,revenueRub,debtSharePercent,coveragePercent,rowCount,columns,display,sourceNote}; display/columns absent when noData.",
        "objects": "{commissioned,permitted}; each {columns:string[],rows:object[],rowCount,sourceNote}; ALL rows, source column names, no sample.",
        "units": {"*M2": "square metres", "*ThousandM2": "thousand square metres", "*Rub": "rubles", "*Percent": "percent points", "*Count": "apartments (aggregate-derived room counts may be fractional)"},
        "displayPolicy": "Explicit display fields are current-page labels. ru_num uses Python rounding, ordinary spaces, decimal comma. "
                         "Percent labels use decimal dot; apartments table uses pandas/Streamlit NumberColumn formatting.",
    }
    snapshot = portable_quality(clean({
        "schemaVersion": 1, "generatedAt": generated_at, "sourceDates": source_dates, "schema": schema,
        "controls": {"developers": developers, "excludedRequestedDevelopers": excluded,
                     "defaultProfileId": profiles[0]["id"] if profiles else None,
                     "regionControl": "apartmentsOnly", "frozen": True, "latestMonitoringYear": mon.get("max_year"),
                     "sourceMonitoringYears": [mon.get("min_year"), mon.get("max_year")],
                     "developerSelectionNote": "Three requested companies only; canonical actual selector name preferred. "
                                               "All same-normalized-name aliases are matched, just like existing page; no invented companies/scopes."},
        "provenance": {"context": {"root": ".", "downloads": "downloads", "useMarts": use_marts, "requireMarts": require_marts},
                       "inputs": list(before.values()), "sources": source_evidence,
                       "code": [{"path": path.as_posix(), "sha256": digest(root / path)} for path in code_paths],
                       "runtime": {"python": sys.version.split()[0], "pandas": pd.__version__, "openpyxl": importlib.metadata.version("openpyxl")},
                       "issues": access.issues, "unchangedInputsVerified": True,
                       "selectionNote": "DataAccess chooses by mtime and validates/falls back as existing UI. openedInputs includes "
                                        "rejected/validated raw reads and manifest reads, not a claim every opened file contributed values. "
                                        "Candidate registry hashes and native dataVersion preserved, including unused candidates."},
        "profiles": profiles, "verification": verification,
    }))
    snapshot["contentSha256"] = hashlib.sha256(json.dumps(snapshot, ensure_ascii=False, sort_keys=True,
                                                          allow_nan=False, separators=(",", ":")).encode("utf-8")).hexdigest()
    return snapshot


def serialize(snapshot):
    return json.dumps(snapshot, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--generated-at", help="ISO-8601 generation timestamp with timezone; pin for byte reproducibility")
    parser.add_argument("--raw", action="store_true", help="Disable marts, keeping existing raw fallback rules")
    parser.add_argument("--require-marts", action="store_true")
    parser.add_argument("--check", action="store_true", help="Rebuild using existing generatedAt and compare full bytes, without writing")
    parser.add_argument("--verify-page", action="store_true", help="Capture and compare original-page values without Streamlit imports (requires Plotly)")
    args = parser.parse_args()
    root = args.root.resolve()
    output = args.output.resolve() if args.output.is_absolute() else root / args.output
    generated_at = args.generated_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    if args.check:
        generated_at = json.loads(output.read_text(encoding="utf-8"))["generatedAt"]
    timestamp = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    if timestamp.tzinfo is None:
        parser.error("--generated-at must include a timezone")
    snapshot = build_snapshot(root, generated_at, not args.raw, args.require_marts)
    if args.verify_page:
        verify_page_output(root, snapshot)
    if "streamlit" in sys.modules or "app.data_access" in sys.modules:
        raise RuntimeError("Snapshot extraction unexpectedly imported Streamlit/facade")
    encoded = serialize(snapshot).encode("utf-8")
    if args.check:
        if output.read_bytes() != encoded:
            raise SystemExit("Snapshot differs from local inputs/code/runtime; inspect/rebuild explicitly")
        print("Reproducibility check passed: full artifact bytes match")
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(encoded)
        print(f"Wrote {relative(output, root)} ({len(encoded):,} bytes)")
    print(f"Profiles: {len(snapshot['profiles'])}; source files: {len(snapshot['provenance']['inputs'])}; "
          f"reference-helper checks: {snapshot['verification']['referenceHelperChecks']}")
    print(f"Content SHA256: {snapshot['contentSha256']}")


if __name__ == "__main__":
    main()
