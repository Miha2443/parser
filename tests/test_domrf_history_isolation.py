"""Readiness and DOM.RF reporting must work without collector dependencies."""
from pathlib import Path
import subprocess
import sys
import textwrap
import unittest


class BackendImportIsolationTests(unittest.TestCase):
    def test_backend_and_publication_validate_browser_history_without_collectors(self):
        script = textwrap.dedent('''
            import hashlib
            import json
            from pathlib import Path
            import sys
            from tempfile import TemporaryDirectory

            blocked = ("selenium", "bs4", "nashdom_checker", "pipeline.downloaders.nashdom_operational_api")
            class NoCollectors:
                def find_spec(self, fullname, path=None, target=None):
                    if any(fullname == name or fullname.startswith(name + ".") for name in blocked):
                        raise ModuleNotFoundError("collector dependency forbidden: " + fullname)
            sys.meta_path.insert(0, NoCollectors())

            from backend.domrf_service import DomrfService
            from docker.coverage import validate_construction_sources, validate_domrf_page
            from pipeline.data_access import DataAccess, DataContext
            from pipeline.downloaders.nashdom_operational_browser import REGIONS, normalize_page, page_url
            from pipeline.downloaders.nashdom_operational_history import validate_bundle_payload
            from pipeline.downloaders.nashdom_operational_contract import BASE, request_url, validate_current_snapshot

            metrics = dict(total_living_thousand_m2=100, sales_open_thousand_m2=80,
                           sold_thousand_m2=40, unsold_thousand_m2=40, sales_not_open_thousand_m2=20,
                           sold_pct=40, unsold_pct=40, sales_not_open_pct=20,
                           price_per_m2_rub=10000, funds_million_rub=1000000)
            def rows(block, month):
                observations = []
                for region, label in REGIONS.items():
                    for kind in (("living", "total") if block == "construction" else (None,)):
                        snapshot = dict(region=label, report_period=month, report_date="")
                        snapshot.update(dict(area_kind=kind, area_thousand_m2=100) if kind else dict(metrics=metrics))
                        observations.append(dict(region_key=region, area_kind=kind, url=page_url(block),
                                                 requested_period=month, snapshot=snapshot,
                                                 selection=dict(kind="initial_page", latest_period=month,
                                                                selected_period=month, render_stable=True, pending_requests=0)))
                return normalize_page(block, month, observations, collected_at="2026-10-19T10:00:00Z")

            with TemporaryDirectory() as directory:
                root = Path(directory)
                raw = root / "data/raw/realty/nashdom"
                raw.mkdir(parents=True)
                current = dict(construction=rows("construction", "2026-10"), sales=rows("sales", "2026-08"))
                validate_current_snapshot(current)
                (raw / "construction_operational_20261019.json").write_text(json.dumps(current), encoding="utf-8")
                observations = dict(construction=rows("construction", "2020-03"), sales=rows("sales", "2020-02"))
                coverage = {}
                for block, end, missing in (("construction", "2020-03", ["2020-01", "2020-02"]),
                                             ("sales", "2020-02", ["2020-01"])):
                    coverage[block] = dict(start="2020-01", end=end, latest_available=end,
                                           expected_months=len(missing) + 1, collected_months=1, missing_months=missing)
                digest = hashlib.sha256(json.dumps(observations, ensure_ascii=False, sort_keys=True,
                                                    allow_nan=False).encode("utf-8")).hexdigest()
                bundle = dict(schemaVersion=1, **observations, coverage=coverage, content_sha256=digest,
                              complete=False, history_complete=False, publishable=True, run_status="partial", errors=[])
                validate_bundle_payload(bundle)
                (raw / "construction_history.json").write_text(json.dumps(bundle), encoding="utf-8")
                context = DataContext(root, use_marts=False)
                service = DomrfService(context)
                for region in ("msk", "rf"):
                    report = service.report(region)
                    validate_domrf_page(report, region)
                    assert report["construction"]["period"]["id"] == "2026-10"
                    assert len(report["construction"]["history"]) == 2
                    assert report["source"]["issues"]
                access = DataAccess(context)
                validate_construction_sources(access, access.load_construction_operational(), require_permits=False)
                for block, month in (("construction", "2020-03"), ("sales", "2020-02")):
                    year, number = map(int, month.split("-"))
                    for row in bundle[block]:
                        row.pop("page_evidence")
                        row.pop("evidence_kind")
                        row.update(source_url=BASE + request_url(block, month, row["region_key"]),
                                   period_evidence=dict(repYear=year, repMonth=number))
                bundle["content_sha256"] = hashlib.sha256(json.dumps(
                    {block: bundle[block] for block in ("construction", "sales")}, ensure_ascii=False,
                    sort_keys=True, allow_nan=False).encode("utf-8")).hexdigest()
                validate_bundle_payload(bundle)
                (raw / "construction_history.json").write_text(json.dumps(bundle), encoding="utf-8")
                validate_construction_sources(access, access.load_construction_operational(), require_permits=False)
                assert not any(name == item or name.startswith(item + ".") for name in sys.modules for item in blocked)
        ''')
        result = subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1],
                                capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
