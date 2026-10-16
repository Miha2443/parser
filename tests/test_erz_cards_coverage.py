"""ERZ cards publish only a complete, source-identified target cohort."""
from contextlib import ExitStack, redirect_stdout
from datetime import datetime
from io import StringIO
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import erzrf_checker as ec
from pipeline.erz_cards_contract import target_keys, validate_workbook
from pipeline.source_records import file_sha256


class CardsCoverageTests(unittest.TestCase):
    def setUp(self):
        stack = self.enterContext(ExitStack())
        self.root = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        self.raw, self.cards = self.root / "raw/erzrf", self.root / "raw/erzrf/cards"
        self.cards.mkdir(parents=True)
        stack.enter_context(patch.multiple(ec, DOWNLOAD_DIR=self.raw, CARDS_DIR=self.cards, STATE_FILE=self.root / "state.json"))
        stack.enter_context(patch.dict(os.environ, {"ERZ_CARDS_LIMIT": "0"}))
        stack.enter_context(patch.object(ec, "_wait_for_card_content", return_value=True))
        stack.enter_context(patch.object(ec, "_save_debug_snapshot"))
        stack.enter_context(redirect_stdout(StringIO()))
        self.browser = Mock(current_url="https://erzrf.ru")
        self.browser.get.side_effect = lambda url: setattr(self.browser, "current_url", url)
        stack.enter_context(patch.object(ec, "_authenticated_session", return_value=self.browser))
        self.targets = {"region": "rf", "selection": {"region": "rf", "sorting": "obyem_stroitelstva",
            "url": ec._build_top_url(ec.REGIONS[0], "obyem_stroitelstva")}, "developers": [
                {"place": str(index), "name": name, "card_url": "https://erzrf.ru/zastroyschiki/brand/" + name}
                for index, name in enumerate(("a", "b"), 1)]}
        self.targets_file = self.raw / "top_developers_rf_20261013.json"
        self.write_targets()
        self.target = self.cards / f"cards_{datetime.now():%Y%m%d}.xlsx"
        self.proof = self.target.with_suffix(".xlsx.provenance.json")

    def write_targets(self):
        self.targets_file.write_text(json.dumps(self.targets), encoding="utf-8")

    def data(self, driver=None):
        name = self.browser.current_url.rsplit("/", 1)[-1]
        return {"name": name, "regions_count": "2", "regions_as_of": "13.10.2026",
                "deliveries": {"Строится": {"sdano": "125", "perenos_m2": "0", "perenos_pct": "0", "utochn": "0"},
                               "2025": {"sdano": "250", "perenos_m2": "50", "perenos_pct": "20", "utochn": "1.25"}}}

    def previous(self):
        self.target.write_bytes(b"previous successful cards")
        self.proof.write_bytes(b"previous successful proof")

    def assert_previous(self):
        self.assertEqual(self.target.read_bytes(), b"previous successful cards")
        self.assertEqual(self.proof.read_bytes(), b"previous successful proof")

    def test_all_actual_target_urls_publish_verified_workbook_and_success(self):
        state = {}
        with patch.object(ec, "_scrape_card", side_effect=self.data):
            result = ec.fetch_cards(state)
        self.assertTrue(result.complete)
        self.assertEqual(result.files, [self.target])
        expected = target_keys(self.targets["developers"])
        rows, confirmed = validate_workbook(self.target, expected)
        self.assertEqual(confirmed, expected)
        self.assertEqual(len(rows), 2)
        evidence = json.loads(self.proof.read_text(encoding="utf-8"))
        self.assertEqual(evidence["sha256"], file_sha256(self.target))
        self.assertEqual(evidence["targets_sha256"], file_sha256(self.targets_file))
        self.assertEqual(evidence["selection"]["sorting"], "obyem_stroitelstva")
        self.assertFalse(evidence["limited"])
        self.assertTrue(state["erzrf_cards_attempt"]["complete"])
        self.browser.quit.assert_called_once()

    def test_one_empty_card_makes_run_fail_and_preserves_previous_snapshot(self):
        self.previous()
        state = {"erzrf_cards": {"previous": True}}
        def parsed(driver):
            return {"name": "", "deliveries": {}} if driver.current_url.endswith("/b") else self.data()
        with patch.object(ec, "_scrape_card", side_effect=parsed), patch.object(ec, "load_state", return_value=state), \
                patch.object(ec, "save_state"):
            files, success = ec.run(only=["cards"])
        self.assertFalse(success)
        self.assertEqual(files, [])
        self.assert_previous()
        self.assertEqual(state["erzrf_cards"], {"previous": True})
        attempt = state["erzrf_cards_attempt"]
        self.assertEqual(len(attempt["expected"]), 2)
        self.assertEqual(len(attempt["confirmed"]), 1)
        self.assertEqual(attempt["missing"][0]["source_id"], "brand/b")
        self.assertEqual(len(attempt["failed"]), 1)

    def test_limits_name_only_parses_and_wrong_card_pages_are_incomplete(self):
        self.previous()
        for kind in ("limit", "name-only", "wrong-page"):
            with self.subTest(kind=kind), patch.dict(os.environ, {"ERZ_CARDS_LIMIT": "1" if kind == "limit" else "0"}):
                def parsed(driver):
                    if kind == "name-only":
                        return {"name": "a", "deliveries": {}}
                    if kind == "wrong-page":
                        driver.current_url = "https://erzrf.ru/zastroyschiki/brand/wrong"
                    return self.data()
                with patch.object(ec, "_scrape_card", side_effect=parsed):
                    result = ec.fetch_cards({})
                self.assertFalse(result.complete)
                self.assertEqual(result.files, [])
                self.assert_previous()

    def test_missing_duplicate_ambiguous_or_wrong_scope_targets_never_start_browser(self):
        self.previous()
        import copy
        original = copy.deepcopy(self.targets)
        cases = []
        missing = copy.deepcopy(original)
        missing["developers"][1].pop("card_url")
        cases.append(missing)
        duplicate = copy.deepcopy(original)
        duplicate["developers"].append(copy.deepcopy(duplicate["developers"][0]))
        cases.append(duplicate)
        ambiguous = copy.deepcopy(original)
        ambiguous["developers"][1]["card_url"] = ambiguous["developers"][0]["card_url"] + "?other=1"
        cases.append(ambiguous)
        wrong = copy.deepcopy(original)
        wrong["selection"]["sorting"] = "skorost"
        cases.append(wrong)
        for value in cases:
            self.targets = value
            self.write_targets()
            with patch.object(ec, "_authenticated_session") as browser:
                self.assertFalse(ec.fetch_cards({}).complete)
            browser.assert_not_called()
            self.assert_previous()

    def test_missing_top_targets_prints_the_actual_reason_without_starting_browser(self):
        self.previous()
        self.targets_file.unlink()
        output = StringIO()
        state = {}
        with redirect_stdout(output), patch.object(ec, "_authenticated_session") as browser:
            result = ec.fetch_cards(state)
        self.assertFalse(result.complete)
        browser.assert_not_called()
        self.assertIn("ERZ cards need a current RF TOP developer target file", output.getvalue())
        self.assertEqual(state["erzrf_cards_attempt"]["error"], result.error)
        self.assert_previous()

    def test_explicit_identified_no_history_is_recorded_without_zero_and_passes_publication(self):
        from pipeline.erz_cards_contract import NO_HISTORY_EVIDENCE
        self.targets["developers"][1]["card_url"] = "https://erzrf.ru/zastroyschiki/brand/new-developer-101"
        self.write_targets()
        html = f"<app-org-table><h3>New developer</h3><div>ID101</div></app-org-table><p>{NO_HISTORY_EVIDENCE}</p>"
        parsed = ec._parse_card_html(html)
        self.assertEqual(parsed["history_status"], "not_published")
        def data(driver):
            return parsed if driver.current_url.endswith("-101") else self.data()
        with patch.object(ec, "_scrape_card", side_effect=data):
            result = ec.fetch_cards({})
        self.assertTrue(result.complete)
        rows, _ = validate_workbook(self.target, target_keys(self.targets["developers"]))
        self.assertEqual(rows[1]["history_status"], "not_published")
        self.assertEqual(rows[1]["Сдано_2025_м²"], "")
        self.assertEqual(rows[1]["Строится_м²"], "")
        proof = json.loads(self.proof.read_text(encoding="utf-8"))
        self.assertEqual(len(proof["unavailable"]), 1)
        from pipeline.data_access import DataAccess, DataContext
        import shutil
        root = self.root / "loader"
        raw = root / "data/raw/realty/erzrf"
        cards = raw / "cards"
        cards.mkdir(parents=True)
        shutil.copy2(self.targets_file, raw / self.targets_file.name)
        shutil.copy2(self.target, cards / self.target.name)
        shutil.copy2(self.proof, cards / self.proof.name)
        access = DataAccess(DataContext(root, use_marts=False))
        canonical = access.load_erzrf_cards()
        import pandas as pd
        self.assertTrue(pd.isna(canonical.iloc[1]["Сдано_2025_м²_num"]))
        from docker.coverage import validate_erz_cards
        validate_erz_cards(access, canonical)

    def test_no_history_needs_notice_identity_and_hash_and_cannot_contain_invented_zero(self):
        from pipeline.erz_cards_contract import NO_HISTORY_EVIDENCE, validate_rows
        html = f"<app-org-table><h3>New</h3><div>ID101</div></app-org-table><p>{NO_HISTORY_EVIDENCE}</p>"
        parsed = ec._parse_card_html(html)
        url = "https://erzrf.ru/zastroyschiki/brand/new-101"
        row = {**parsed, "name_card": parsed["name"], "url": url, "page_url": url, "source_id": "brand/new-101"}
        expected = [{"source_id": "brand/new-101", "card_url": url}]
        validate_rows([row], expected)
        for change in ({"page_card_id": "102"}, {"page_sha256": ""}, {"history_evidence": ""},
                       {"Сдано_2025_м²": 0}, {"history_status": "unknown"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_rows([{**row, **change}], expected)
        self.assertNotIn("history_status", ec._parse_card_html(html.replace(NO_HISTORY_EVIDENCE, "loading")))
        self.assertNotIn("history_status", ec._parse_card_html(html.replace("ID101", "")))
        for placeholder in ("<app-org-table-deadline></app-org-table-deadline>",
                            "<app-org-table-deadline><div class='clmn'><p><b>Сдано в 2025</b></p><p>—</p></div></app-org-table-deadline>"):
            with self.subTest(placeholder=placeholder):
                value = ec._parse_card_html(html + placeholder)
                self.assertEqual(value["history_status"], "not_published")
                self.assertEqual(value["deliveries"], {})

    def test_unpublished_year_on_measured_card_remains_unknown_but_published_zero_is_zero(self):
        """The union of workbook years must not invent another card's history."""
        import pandas as pd
        from pipeline.data_access import DataAccess, DataContext

        def measured(driver):
            value = self.data()
            if driver.current_url.endswith("/a"):
                value["deliveries"]["2024"] = {"sdano": "0", "perenos_m2": "0"}
            else:
                value["deliveries"]["2023"] = {"sdano": "70", "perenos_m2": "—"}
            return value

        with patch.object(ec, "_scrape_card", side_effect=measured):
            result = ec.fetch_cards({})
        self.assertTrue(result.complete)
        access = DataAccess(DataContext(self.root, use_marts=False))
        access.ERZRF_PATHS = [self.raw]
        canonical = access.load_erzrf_cards().set_index("name_card")
        self.assertEqual(canonical.loc["a", "Сдано_2024_м²_num"], 0)
        self.assertTrue(pd.isna(canonical.loc["b", "Сдано_2024_м²_num"]))
        self.assertTrue(pd.isna(canonical.loc["a", "Сдано_2023_м²_num"]))
        self.assertTrue(pd.isna(canonical.loc["b", "Перенос_2023_м²_num"]))
        from docker.coverage import validate_erz_cards
        validate_erz_cards(access, canonical.reset_index())
        for name, column, invented in (("a", "Сдано_2023_м²_num", 0), ("a", "Сдано_2024_м²_num", 1)):
            corrupted = canonical.copy()
            corrupted.loc[name, column] = invented
            with self.subTest(name=name, column=column), self.assertRaises(RuntimeError):
                validate_erz_cards(access, corrupted.reset_index())

    def test_source_target_change_or_second_artifact_replace_failure_preserves_previous(self):
        self.previous()
        def changed(driver):
            value = self.data()
            if driver.current_url.endswith("/b"):
                self.targets["developers"].append({"name": "c", "card_url": "https://erzrf.ru/zastroyschiki/brand/c"})
                self.write_targets()
            return value
        with patch.object(ec, "_scrape_card", side_effect=changed):
            self.assertFalse(ec.fetch_cards({}).complete)
        self.assert_previous()
        self.targets["developers"].pop()
        self.write_targets()
        replace = Path.replace
        def interrupted(source, target):
            if (source.name == self.proof.name and source.parent.name.startswith(".source-staging-erzrf-cards-")
                    and not source.parent.name.startswith(".source-staging-erzrf-cards-recovery-")):
                raise OSError("provenance replace failed")
            return replace(source, target)
        with patch.object(ec, "_scrape_card", side_effect=self.data), patch.object(Path, "replace", interrupted):
            self.assertFalse(ec.fetch_cards({}).complete)
        self.assert_previous()


if __name__ == "__main__":
    unittest.main()
