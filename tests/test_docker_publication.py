"""Publication contracts without Docker or network access."""
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from docker import publication as p

REPO = Path(__file__).resolve().parents[1]


class PublicationTests(unittest.TestCase):
    def test_snapshot_is_independent_and_preserves_manifest_timestamps(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, target = root / "source", root / "snapshot"
            source.mkdir()
            original = source / "input.xlsx"
            original.write_bytes(b"old workbook")
            os.utime(original, ns=(1700000000123456700, 1700000000123456700))
            (source / "_archive").mkdir()
            (source / "_archive/old.xlsx").write_bytes(b"archive")
            (source / "partial.crdownload").write_bytes(b"partial")
            for directory in (".fedstat_resume", ".source-staging-interrupted", "_debug"):
                (source / directory).mkdir()
                (source / directory / "working.xlsx").write_bytes(b"private working data")
            (source / ".download_receipts").mkdir()
            (source / ".download_receipts/input.xlsx.json").write_text("confirmed provenance")
            p.copy_tree(source, target)
            self.assertEqual(original.stat().st_mtime_ns, (target / original.name).stat().st_mtime_ns)
            original.write_bytes(b"new mutable workbook")
            self.assertEqual((target / original.name).read_bytes(), b"old workbook")
            self.assertFalse((target / "_archive").exists())
            self.assertFalse((target / "partial.crdownload").exists())
            for directory in (".fedstat_resume", ".source-staging-interrupted", "_debug"):
                self.assertFalse((target / directory).exists())
            self.assertEqual((target / ".download_receipts/input.xlsx.json").read_text(), "confirmed provenance")

    def test_seed_never_overwrites_operator_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, target = root / "seed", root / "work"
            (source / "data/derived").mkdir(parents=True)
            (source / "data/derived/a.csv").write_text("image")
            p.seed(target, source)
            (target / "data/derived/a.csv").write_text("operator")
            p.seed(target, source)
            self.assertEqual((target / "data/derived/a.csv").read_text(), "operator")

    def test_only_current_complete_success_can_publish(self):
        started = time.time()
        valid = dict(run_id="new", status="success", current_stage="finished",
                     started_at=datetime.fromtimestamp(started).isoformat(),
                     finished_at=datetime.now().isoformat(), sources_requested=list(p.SOURCES),
                     successes=list(p.SOURCES), failures=[], status_write_errors=[], error="",
                     marts_ok=True, processed_ok=True)
        p.require_success(valid, "old", started, expected_sources=p.SOURCES)
        cases = ({"status": "running"}, {"run_id": "old"}, {"successes": ["monitoring"]},
                 {"sources_requested": ["monitoring"]}, {"processed_ok": False},
                 {"marts_ok": False}, {"status_write_errors": ["disk full"]},
                 {"started_at": "2020-01-01T00:00:00"}, {"failures": ["fedstat"]})
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(RuntimeError):
                p.require_success({**valid, **changes}, "old", started, expected_sources=p.SOURCES)

    def test_failed_validation_does_not_switch_pointer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            working, runtime = root / "work", root / "runtime"
            (runtime / "releases/old").mkdir(parents=True)
            try:
                (runtime / "live").symlink_to("releases/old", target_is_directory=True)
            except OSError:
                self.skipTest("Host does not allow directory symlinks")
            with patch.object(p, "build_candidate"), patch.object(p, "validate", side_effect=RuntimeError("stale mart")):
                with self.assertRaises(RuntimeError):
                    p.publish(working, runtime)
            self.assertEqual(os.readlink(runtime / "live"), "releases/old")
            self.assertFalse((runtime / "pending.json").exists())
            self.assertEqual(list((runtime / "releases").iterdir()), [runtime / "releases/old"])
            with self.assertRaises(RuntimeError):
                p.swap(runtime, "releases/..")

    def test_candidate_uses_real_all_mart_loaders_and_detects_ns_mutation(self):
        from deploy.linux.tests import smoke
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(smoke, "ROOT", root):
                smoke.seed("PUBLICATION CONTRACT")
            # Approved static files used by the route smoke; no mutable local data.
            for relative in ("data/raw/realty/linear_objects/linear_objects_2026-09-28.json",
                             "data/raw/realty/vvod/static_vvod_rs_2011_2025.xlsx",
                             "data/raw/realty/vvod/emiss_34118_base.xls",
                             "data/derived/salary_2011_2012.csv",
                             "data/derived/vds_msk_value_2011_2015.csv"):
                destination = root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(REPO / relative, destination)
            environment = {**os.environ, "PARSER_ROOT": str(root), "PARSER_DOWNLOADS": str(root / "downloads"),
                           "PARSER_REQUIRE_REALTY_MARTS": "1", "PYTHONUTF8": "1"}
            result = subprocess.run([sys.executable, "-m", "pipeline.build_realty_marts", "--strict"],
                                    cwd=REPO, env=environment, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            p.validate(root)
            raw = next((root / "data/raw/realty/nashdom").glob("*.xlsx"))
            stat = raw.stat()
            # Change only ns within the same CLI's microsecond timestamp bucket.
            os.utime(raw, ns=(stat.st_atime_ns, stat.st_mtime_ns - 100))
            with self.assertRaises(subprocess.CalledProcessError):
                p.validate(root)

    def test_rollback_keeps_recovery_journal_until_acknowledged(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            (runtime / "releases/old").mkdir(parents=True)
            (runtime / "releases/new").mkdir()
            try:
                (runtime / "live").symlink_to("releases/new", target_is_directory=True)
            except OSError:
                self.skipTest("Host does not allow directory symlinks")
            journal = runtime / "pending.json"
            journal.write_text(json.dumps({"previous": "releases/old", "next": "releases/new"}))
            p.transaction(runtime, "rollback")
            self.assertEqual(p.live_target(runtime), "releases/old")
            self.assertTrue(journal.exists())
            p.transaction(runtime, "rollback")  # Recovery is safe to retry after interruption.
            p.transaction(runtime, "ack")
            self.assertFalse(journal.exists())

    def test_coherent_but_empty_quart_mart_with_raw_source_cannot_publish(self):
        from deploy.linux.tests import smoke
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root, runtime = base / "work", base / "runtime"
            with patch.object(smoke, "ROOT", root):
                smoke.seed("PUBLICATION QUART DEFENSE")
            environment = {**os.environ, "PARSER_ROOT": str(root), "PARSER_DOWNLOADS": str(root / "downloads"),
                           "PARSER_REQUIRE_REALTY_MARTS": "1", "PYTHONUTF8": "1"}
            for arguments in (("--strict",), ("--check", "--strict")):
                result = subprocess.run([sys.executable, "-m", "pipeline.build_realty_marts", *arguments],
                                        cwd=REPO, env=environment, capture_output=True, text=True, encoding="utf-8")
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            # Keep a coherent source manifest while replacing the canonical
            # mart with an empty payload; raw fallback must not hide it.
            import pandas as pd
            pd.to_pickle({}, root / "data/marts/realty/kvartirografia.pkl")
            result = subprocess.run([sys.executable, "-m", "docker.publication", "validate-api"],
                                    cwd=REPO, env=environment, capture_output=True, text=True, encoding="utf-8")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("kvartirografia/apartments is empty", result.stderr)
            # Publication rebuilds private marts now; a raw coverage defect must
            # still fail rather than being repaired by rebuilding an empty mart.
            source = next((root / "data/raw/realty/nashdom").glob("kvartirografia_*.json"))
            payload = json.loads(source.read_text(encoding="utf-8"))
            payload[0]["per_dev_coverage"]["complete"] = False
            source.write_text(json.dumps(payload), encoding="utf-8")
            (runtime / "releases/old").mkdir(parents=True)
            # Works on Windows without symlink privilege, while exercising the
            # real copy and candidate validator before the pointer-swap boundary.
            with patch.object(p, "live_target", return_value="releases/old"), patch.object(p, "swap") as swap:
                with self.assertRaises(subprocess.CalledProcessError):
                    p.publish(root, runtime)
            swap.assert_not_called()
            self.assertFalse((runtime / "pending.json").exists())
            self.assertEqual(list((runtime / "releases").iterdir()), [runtime / "releases/old"])


class CoverageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from deploy.linux.tests import smoke
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.seed_root = Path(cls.temp.name) / "full"
        with patch.object(smoke, "ROOT", cls.seed_root):
            smoke.seed("COMPLETE COVERAGE")
        cls.build(cls.seed_root)

    @staticmethod
    def build(root):
        env = {**os.environ, "PARSER_ROOT": str(root), "PARSER_DOWNLOADS": str(root / "downloads"), "PYTHONUTF8": "1"}
        result = subprocess.run([sys.executable, "-m", "pipeline.build_realty_marts", "--strict"],
                                cwd=REPO, env=env, capture_output=True, text=True, encoding="utf-8")
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)

    def setUp(self):
        self.temp = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.temp) / "work"
        shutil.copytree(self.seed_root, self.root, copy_function=shutil.copy2)

    def access(self):
        from pipeline.data_access import DataAccess, DataContext
        return DataAccess(DataContext(self.root, downloads=self.root / "downloads", use_marts=True, require_marts=True))

    def test_full_positive_fixture_and_shared_live_page_contract(self):
        from docker.coverage import validate_sources, validate_pages
        from backend.app import create_app
        from fastapi.testclient import TestClient
        validate_sources(self.access())
        self.assertEqual(len(self.access().load_monitoring_operational_history()["housing_monthly"]), 12)
        with patch.dict(os.environ, {"PARSER_ROOT": str(self.root), "PARSER_DOWNLOADS": str(self.root / "downloads"),
                                     "PARSER_REQUIRE_REALTY_MARTS": "1"}), TestClient(create_app()) as client:
            def get(route):
                response = client.get(route)
                self.assertEqual(response.status_code, 200, route)
                return response.json()
            validate_pages(get)
            for missing in ("apartments", "sales", "construction", "commissioning/operational", "economics/accounts"):
                def broken(route):
                    if route.split("?")[0] == "/api/v1/" + missing:
                        raise RuntimeError("page absent")
                    return get(route)
                with self.subTest(missing=missing), self.assertRaises(RuntimeError):
                    validate_pages(broken)

    def test_missing_housing_title_header_or_sheet_cannot_publish_coherent_marts(self):
        from docker.coverage import validate_sources
        from openpyxl import load_workbook
        source = next((self.root / "data/raw/realty/nashdom").glob("monitoring_*.xlsx"))
        original = source.read_bytes()
        for missing in ("title", "header", "sheet"):
            source.write_bytes(original)
            workbook = load_workbook(source)
            sheet = workbook["Данные с 2011 года"]
            if missing == "title":
                sheet["F2"] = "Другой показатель"
            elif missing == "header":
                sheet["G3"] = "Нет месяца"
            else:
                workbook.remove(sheet)
            workbook.save(source)
            workbook.close()
            with self.subTest(missing=missing):
                self.build(self.root)  # Fresh source metadata and valid structural marts.
                self.assertTrue(self.access().load_monitoring_operational_history()["housing_monthly"].empty)
                with self.assertRaisesRegex(RuntimeError, "operational housing monthly history is empty"):
                    validate_sources(self.access())

    def test_primary_rv_repeat_or_changed_raw_area_cannot_pass_a_coherent_mart(self):
        import pandas as pd
        from docker.coverage import validate_sources
        mart = self.root / "data/marts/realty/monitoring_2_0.pkl"
        payload = pd.read_pickle(mart)
        original = payload["rv"].copy()
        for mutation in ("duplicate", "area", "subtype", "year", "measurement"):
            payload["rv"] = pd.concat([original, original.iloc[[0]]], ignore_index=True) if mutation == "duplicate" else original.copy()
            if mutation == "area":
                payload["rv"].loc[payload["rv"].index[0], "Жилая площадь"] += 10
            elif mutation == "subtype":
                payload["rv"].loc[payload["rv"].index[0], "Подтип объекта"] = "ИЖС"
            elif mutation == "year":
                payload["rv"].loc[payload["rv"].index[0], "Год ввода по Мосстату"] += 1
            elif mutation == "measurement":
                payload["rv"].loc[payload["rv"].index[0], "Жилая площадь_measured"] = False
            pd.to_pickle(payload, mart)
            with self.subTest(mutation=mutation), self.assertRaisesRegex(RuntimeError, "physical primary|physical source|measurement evidence"):
                validate_sources(self.access())

    def test_annual_ihs_cannot_be_relabelled_mkd_with_consistent_source_manifest(self):
        import pandas as pd
        from docker.coverage import validate_sources
        mart = self.root / "data/marts/realty/emiss_34118.pkl"
        frame = pd.read_pickle(mart)
        frame.loc[frame.year.eq(2025), "ИЖС"] = frame.loc[frame.year.eq(2025), "МКД"]
        pd.to_pickle(frame, mart)
        with self.assertRaisesRegex(RuntimeError, "annual category differs"):
            validate_sources(self.access())

    def test_each_required_dynamic_source_missing_fails_with_coherent_manifest(self):
        from docker.coverage import validate_sources
        patterns = {"monitoring": "data/raw/realty/nashdom/monitoring_*.xlsx",
                    "kvart": "data/raw/realty/nashdom/kvartirografia_*",
                    "construction": "data/raw/realty/nashdom/construction_operational_*",
                    "rasprod": "data/raw/realty/nashdom/rasprodannost_*",
                    "fedstat": "downloads/*начисленная*", "rosstat": "downloads/VVP_god*"}
        for name, pattern in patterns.items():
            with self.subTest(source=name):
                deleted = {path: (path.read_bytes(), path.stat()) for path in self.root.glob(pattern)}
                self.assertTrue(deleted)
                for path in deleted:
                    path.unlink()
                try:
                    self.build(self.root)  # Empty/error-free marts can still build successfully.
                    with self.assertRaises(RuntimeError):
                        validate_sources(self.access())
                finally:
                    for path, (content, stat) in deleted.items():
                        path.write_bytes(content)
                        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))

    def test_every_canonical_mart_must_have_content_even_with_consistent_manifest(self):
        import pandas as pd
        from docker.coverage import validate_sources
        from pipeline.build_realty_marts import _shape_summary
        manifest_path = self.root / "data/marts/realty/manifest.json"
        original = json.loads(manifest_path.read_text(encoding="utf-8"))
        for name, entry in original["marts"].items():
            with self.subTest(mart=name):
                mart_path = self.root / entry["file"]
                content = mart_path.read_bytes()
                payload = pd.read_pickle(mart_path)
                empty = payload.iloc[:0] if isinstance(payload, pd.DataFrame) else {}
                pd.to_pickle(empty, mart_path)
                manifest = json.loads(json.dumps(original))
                manifest["marts"][name]["summary"] = _shape_summary(empty)
                manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
                try:
                    with self.assertRaises(RuntimeError):
                        validate_sources(self.access())
                finally:
                    mart_path.write_bytes(content)
                    manifest_path.write_text(json.dumps(original), encoding="utf-8")

    def test_rf_or_moscow_cannot_be_missing_from_canonical_apartments(self):
        import pandas as pd
        from docker.coverage import validate_sources
        path = self.root / "data/marts/realty/kvartirografia.pkl"
        original = path.read_bytes()
        for region in ("rf", "msk"):
            payload = pd.read_pickle(path)
            payload["apartments"] = payload["apartments"].loc[lambda frame: frame.region_key.ne(region)]
            pd.to_pickle(payload, path)
            with self.subTest(region=region), self.assertRaisesRegex(RuntimeError, "lacks " + region):
                validate_sources(self.access())
            path.write_bytes(original)

    def test_kvart_missing_independent_population_wrong_identity_or_report_date_are_rejected(self):
        import copy
        from docker.coverage import validate_sources
        from nashdom_checker import _build_kvart_xlsx
        path = self.root / "data/raw/realty/nashdom/kvartirografia_20260101.json"
        original = json.loads(path.read_text(encoding="utf-8"))
        changes = []
        missing_proof = copy.deepcopy(original)
        missing_proof[0].pop("developer_api")
        changes.append(missing_proof)
        partial_population = copy.deepcopy(original)
        proof = partial_population[0]["developer_api"]
        proof["dictionary"].append({"id": "extra", "name": "Missing developer"})
        proof["aggregate"].append({**copy.deepcopy(proof["aggregate"][0]), "id": "extra"})
        changes.append(partial_population)
        missing_selected_region = copy.deepcopy(original)
        missing_selected_region[1]["regions"] = []
        changes.append(missing_selected_region)
        wrong_id = copy.deepcopy(original)
        wrong_id[0]["apartments_per_dev"][0]["source_id"] = "wrong"
        changes.append(wrong_id)
        stale_date = copy.deepcopy(original)
        stale_date[1]["developer_api"]["report_date"] = "31.12.2025"
        changes.append(stale_date)
        wrong_total = copy.deepcopy(original)
        wrong_total[1]["apartments"][0]["количество_шт"] = "200"
        changes.append(wrong_total)
        for index, entries in enumerate(changes):
            path.write_text(json.dumps(entries), encoding="utf-8")
            _build_kvart_xlsx(entries, path.with_suffix(".xlsx"))
            self.build(self.root)
            with self.subTest(case=index), self.assertRaises(RuntimeError):
                validate_sources(self.access())

    def test_kvart_canonical_guessed_display_value_cannot_replace_exact_api_measurement(self):
        import pandas as pd
        from docker.coverage import validate_sources
        path = self.root / "data/marts/realty/kvartirografia.pkl"
        original = path.read_bytes()
        for table in ("developers", "regions", "apartments"):
            payload = pd.read_pickle(path)
            payload[table].loc[payload[table].index[0], "площадь_тыс_м²_num"] = 0.5
            pd.to_pickle(payload, path)
            with self.subTest(table=table), self.assertRaisesRegex(RuntimeError, "exact API measurement"):
                validate_sources(self.access())
            path.write_bytes(original)

    def test_historical_derived_cannot_substitute_missing_fresh_economics(self):
        from docker.coverage import validate_sources
        for path in (self.root / "data/processed").glob("*.pkl"):
            path.unlink()
        self.assertTrue((self.root / "data/derived/salary_2011_2012.csv").is_file())
        with self.assertRaisesRegex(RuntimeError, "processed file is missing"):
            validate_sources(self.access())

    def test_each_used_fedstat_export_needs_verified_dynamic_input(self):
        from docker.coverage import validate_sources
        receipts = self.root / "downloads/.download_receipts"
        for receipt in receipts.glob("*.json"):
            content = receipt.read_bytes()
            identifier = json.loads(content)["source_id"]
            receipt.unlink()
            with self.subTest(source_id=identifier), self.assertRaisesRegex(RuntimeError, "fedstat lacks verified downloads"):
                validate_sources(self.access())
            receipt.write_bytes(content)

    def test_downloaded_salary_part_with_no_processed_contribution_is_rejected(self):
        import pandas as pd
        from docker.coverage import validate_sources
        path = self.root / "data/processed/avg_salary.pkl"
        frame = pd.read_pickle(path)
        frame = frame[~frame.source_file.str.contains("history")]
        frame.to_pickle(path)
        with self.assertRaisesRegex(RuntimeError, "no processed contribution from 43246"):
            validate_sources(self.access())

    def test_empty_parser_skip_cannot_reuse_processed_older_than_new_download(self):
        from docker.coverage import validate_sources
        source = next((self.root / "downloads").glob("VVP_god*"))
        processed = self.root / "data/processed/gdp_rf.pkl"
        os.utime(source, ns=(source.stat().st_atime_ns, processed.stat().st_mtime_ns + 1000000))
        with self.assertRaisesRegex(RuntimeError, "predates its downloaded sources"):
            validate_sources(self.access())

    def test_rasprod_legacy_unverified_or_mismatched_api_history_cannot_publish(self):
        from docker.coverage import validate_sources
        from nashdom_checker import _write_rasprod_coverage
        path = self.root / "data/raw/realty/nashdom/rasprodannost_20260101.json"
        original = path.read_bytes()
        entries = json.loads(original)
        expected = {(region, 2026, 1) for region in ("rf", "msk")}
        entries[0]["api_verified"] = False
        path.write_text(json.dumps(entries), encoding="utf-8")
        _write_rasprod_coverage(path, path.with_suffix(".xlsx"), expected, expected)
        with self.assertRaisesRegex(RuntimeError, "lacks verified expected periods"):
            validate_sources(self.access())
        path.write_bytes(original)
        with self.assertRaisesRegex(RuntimeError, "coverage does not match"):
            validate_sources(self.access())

    def test_rasprod_canonical_mart_missing_expected_period_is_rejected(self):
        import pandas as pd
        from docker.coverage import validate_sources
        from nashdom_checker import _write_rasprod_coverage
        path = self.root / "data/raw/realty/nashdom/rasprodannost_20260101.json"
        entries = json.loads(path.read_text(encoding="utf-8"))
        import copy
        entries.append({**copy.deepcopy(entries[1]), "year": 2025, "month_num": 12})
        path.write_text(json.dumps(entries), encoding="utf-8")
        expected = {(region, 2026, 1) for region in ("rf", "msk")} | {("msk", 2025, 12)}
        _write_rasprod_coverage(path, path.with_suffix(".xlsx"), expected, expected)
        with self.assertRaisesRegex(RuntimeError, "canonical mart period set differs"):
            validate_sources(self.access())

    def test_wrong_construction_sales_schema_cannot_pass_on_nonempty_rows(self):
        import pandas as pd
        from docker.coverage import validate_sources
        path = self.root / "data/marts/realty/construction_operational.pkl"
        payload = pd.read_pickle(path)
        payload["sales"] = payload["sales"][["region_key", "report_period"]].assign(sold=50, readiness=40)
        pd.to_pickle(payload, path)
        with self.assertRaisesRegex(RuntimeError, "lacks measured total_living_thousand_m2"):
            validate_sources(self.access())

    def test_erz_cards_failed_missing_duplicate_or_unverified_targets_cannot_publish(self):
        import pandas as pd
        from docker.coverage import validate_sources
        from pipeline.source_records import file_sha256
        workbook = self.root / "data/raw/realty/erzrf/cards_20260101.xlsx"
        proof = workbook.with_suffix(".xlsx.provenance.json")
        targets = workbook.parent / "top_developers_rf_20260101.json"
        original_workbook, original_proof, original_targets = workbook.read_bytes(), proof.read_bytes(), targets.read_bytes()
        for case in ("failed", "missing", "duplicate", "identity", "limited", "no-proof"):
            workbook.write_bytes(original_workbook)
            proof.write_bytes(original_proof)
            targets.write_bytes(original_targets)
            metadata = json.loads(original_proof)
            rows = pd.read_excel(workbook, sheet_name="cards")
            failed = None
            if case == "failed":
                failed = pd.DataFrame([{"url": rows.iloc[0]["url"], "reason": "empty parse"}])
            elif case == "missing":
                population = json.loads(original_targets)
                population["developers"].append({"name": "Missing", "card_url": "https://erzrf.ru/zastroyschiki/brand/missing"})
                targets.write_text(json.dumps(population), encoding="utf-8")
                metadata["targets_sha256"] = file_sha256(targets)
                metadata["expected"].append({"source_id": "brand/missing", "card_url": population["developers"][-1]["card_url"]})
                metadata["confirmed"] = metadata["expected"]
            elif case == "duplicate":
                rows = pd.concat([rows, rows], ignore_index=True)
            elif case == "identity":
                rows.loc[0, "source_id"] = "brand/wrong"
            elif case == "limited":
                metadata["limited"] = True
            with pd.ExcelWriter(workbook) as writer:
                rows.to_excel(writer, sheet_name="cards", index=False)
                if failed is not None:
                    failed.to_excel(writer, sheet_name="failed", index=False)
            metadata["sha256"] = file_sha256(workbook)
            proof.write_text(json.dumps(metadata), encoding="utf-8")
            if case == "no-proof":
                proof.unlink()
            self.build(self.root)
            with self.subTest(case=case), self.assertRaisesRegex(RuntimeError, "ERZ card|erz-cards"):
                validate_sources(self.access())

    def test_erz_cards_canonical_missing_target_cannot_pass_with_coherent_nonempty_mart(self):
        import pandas as pd
        from docker.coverage import validate_sources
        path = self.root / "data/marts/realty/erzrf_cards.pkl"
        frame = pd.read_pickle(path)
        frame.loc[frame.index[0], "url"] = "https://erzrf.ru/zastroyschiki/brand/wrong"
        pd.to_pickle(frame, path)
        with self.assertRaisesRegex(RuntimeError, "ERZ card"):
            validate_sources(self.access())

    def test_actual_ihs_only_dynamic_parts_are_consumed_and_each_need_rf_and_moscow(self):
        import pandas as pd
        from docker.coverage import validate_sources
        from pipeline.download_provenance import record_download
        from pipeline.data_access import _parse_emiss_34118_periods_file
        validate_sources(self.access())  # Fixture deliberately contains IHS only.
        sources = sorted((self.root / "downloads").glob("Введено в действие*.xlsx"))
        for region in ("РФ", "Москва"):
            original = {path: (path.read_bytes(), path.stat()) for path in sources}
            for index, source in enumerate(sources, 1):
                raw = pd.read_excel(source, sheet_name="Данные", header=None)
                label = "Российская Федерация" if region == "РФ" else "Москва"
                raw = raw[raw[0].ne(label)]
                raw.to_excel(source, sheet_name="Данные", header=False, index=False)
                record_download(source, source_id=f"34118_часть{index}")
            self.build(self.root)
            with self.subTest(region=region), self.assertRaisesRegex(RuntimeError, "lacks measured IHS for " + region):
                validate_sources(self.access())
            for index, (path, (content, stat)) in enumerate(original.items(), 1):
                path.write_bytes(content)
                os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
                record_download(path, source_id=f"34118_часть{index}")

    def test_rasprod_extra_unverified_duplicate_or_incomplete_month_is_rejected(self):
        import copy
        from docker.coverage import validate_sources
        from nashdom_checker import _write_rasprod_coverage
        path = self.root / "data/raw/realty/nashdom/rasprodannost_20260101.json"
        original = json.loads(path.read_text(encoding="utf-8"))
        expected = {(region, 2026, 1) for region in ("rf", "msk")}
        cases = [original + [{**copy.deepcopy(original[1]), "month_num": 2, "api_verified": False}],
                 original + [{**copy.deepcopy(original[1]), "month_num": 2}], original + [copy.deepcopy(original[1])]]
        missing_metric = copy.deepcopy(original)
        missing_metric[0]["kpi"].pop()
        cases.append(missing_metric)
        missing_table = copy.deepcopy(original)
        missing_table[0]["tables"].pop(next(iter(missing_table[0]["tables"])))
        cases.append(missing_table)
        for entries in cases:
            path.write_text(json.dumps(entries), encoding="utf-8")
            _write_rasprod_coverage(path, path.with_suffix(".xlsx"), expected, expected)
            with self.subTest(last_entry=entries[-1].get("month_num")), self.assertRaises(RuntimeError):
                validate_sources(self.access())

    def test_extra_xlsx_period_and_missing_metric_or_table_in_one_mart_month_are_rejected(self):
        import pandas as pd
        from docker.coverage import validate_sources
        from nashdom_checker import _write_rasprod_coverage
        xlsx = self.root / "data/raw/realty/nashdom/rasprodannost_20260101.xlsx"
        original = xlsx.read_bytes()
        data = pd.read_excel(xlsx, sheet_name=None)
        extra = data["kpi"].loc[data["kpi"].region_key.eq("msk")].assign(month=2)
        data["kpi"] = pd.concat([data["kpi"], extra], ignore_index=True)
        with pd.ExcelWriter(xlsx) as writer:
            for name, rows in data.items():
                rows.to_excel(writer, sheet_name=name, index=False)
        _write_rasprod_coverage(xlsx.with_suffix(".json"), xlsx, {("rf", 2026, 1), ("msk", 2026, 1)}, {("rf", 2026, 1), ("msk", 2026, 1)})
        self.build(self.root)
        with self.assertRaisesRegex(RuntimeError, "period set differs"):
            validate_sources(self.access())
        xlsx.write_bytes(original)
        _write_rasprod_coverage(xlsx.with_suffix(".json"), xlsx, {("rf", 2026, 1), ("msk", 2026, 1)}, {("rf", 2026, 1), ("msk", 2026, 1)})
        self.build(self.root)
        mart = self.root / "data/marts/realty/rasprodannost.pkl"
        payload = pd.read_pickle(mart)
        payload["kpi"] = payload["kpi"].drop(index=payload["kpi"].index[0])
        pd.to_pickle(payload, mart)
        with self.assertRaisesRegex(RuntimeError, "lacks four distinct KPIs"):
            validate_sources(self.access())
        self.build(self.root)
        payload = pd.read_pickle(mart)
        payload["by_class"] = payload["by_class"].loc[lambda frame: frame.region_key.eq("rf")]
        pd.to_pickle(payload, mart)
        with self.assertRaises(RuntimeError):
            validate_sources(self.access())

    def test_collector_two_versions_archive_build_and_validate_keep_all_three_latest_files(self):
        from datetime import datetime as RealDatetime
        from contextlib import redirect_stdout
        from io import StringIO
        from pipeline import archive_old
        from scripts.check_nashdom_rasprod_api import Checks, series, payload
        from urllib.parse import parse_qs, urlsplit
        import nashdom_checker as nc
        from docker.coverage import validate_sources
        folder = self.root / "data/raw/realty/nashdom"
        def callback(driver, urls):
            if "/dynamics?" in urls[0]:
                return [series()]
            return list(payload(int(parse_qs(urlsplit(urls[0]).query)["repMonth"][0])))
        with patch.object(archive_old, "REALTY_ROOT", self.root / "data/raw/realty"), \
                patch.object(archive_old, "ARCHIVE_ROOT", self.root / "data/raw/realty/_archive"), redirect_stdout(StringIO()):
            for day in (12, 13):
                class FixtureDate(RealDatetime):
                    @classmethod
                    def now(cls, tz=None):
                        return cls(2026, 10, day)
                with patch.object(nc, "datetime", FixtureDate):
                    (files, complete), _ = Checks().collect(folder, callback)
                self.assertTrue(complete)
                self.assertEqual(archive_old.archive_directory("nashdom", keep=1, prefixes=["rasprodannost_"]), 3)
                latest = {f"rasprodannost_202610{day}." + suffix for suffix in ("json", "xlsx", "coverage.json")}
                self.assertEqual({path.name for path in folder.glob("rasprodannost_*")}, latest)
                self.build(self.root)
                validate_sources(self.access())
        records = [json.loads(line) for line in (self.root / "data/raw/realty/_archive/index.jsonl").read_text(encoding="utf-8").splitlines()]
        old = [row for row in records if "20261012" in row["original_path"]]
        self.assertEqual(len(old), 3)
        self.assertEqual({row["indicator_key"].split(":")[-1] for row in old},
                         {"rasprodannost.json", "rasprodannost.xlsx", "rasprodannost.coverage.json"})
        for record in old:
            self.assertTrue((self.root / record["archive_path"]).is_file())

    def test_existing_partial_release_identity_allows_replacement_but_full_readiness_fails(self):
        from docker.readiness import probe
        from io import StringIO
        health = {"status": "ok", "release": "/runtime/releases/old"}
        with patch("docker.readiness.urlopen", side_effect=lambda *a, **kw: StringIO(json.dumps(health))), \
                patch("docker.readiness.validate_pages", side_effect=RuntimeError("apartments absent")) as full:
            probe(expected=health["release"], identity_only=True)
            full.assert_not_called()
            with self.assertRaises(RuntimeError):
                probe(expected=health["release"])
        with patch("docker.readiness.urlopen", return_value=StringIO(json.dumps(health))):
            with self.assertRaises(RuntimeError):
                probe(expected="/runtime/releases/other", identity_only=True)


if __name__ == "__main__":
    unittest.main()
