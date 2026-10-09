"""Offline ERZ collector contracts: real XLSX fixtures, no browser or notifications."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from datetime import datetime
from io import StringIO
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import pandas as pd

os.environ["TDM_DISABLED"] = "1"
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import erzrf_checker as ec  # noqa: E402
from pipeline.data_access import DataAccess, DataContext  # noqa: E402


# These schema signatures come from the existing 20260702 raw files, whose
# historical names were incorrect. Fixtures use invented developer values.
def fixture_frame(kind: str) -> pd.DataFrame:
    row = {"Место": 1, "Наименование, регион": "Fixture developer, регион"}
    if kind == "obyem_stroitelstva":
        row.update({"Строится, м²": 125, "С переносом срока, м²": 25,
                    "Уточнение срока, мес.": 1.5})
    elif kind in {"obyem_vvoda", "nakopl_vvod"}:
        row.update({"Введено, м²": 250, "С переносом срока, м²": 50,
                    "Уточнение срока, мес.": 1.25})
    elif kind == "potreb_kachestva":
        row.update({"Строится, м²": 500, "Средняя оценка": 79.84,
                    "ЖК/ПТ, всего в расчете": 15})
    elif kind == "skorost":
        row.update({"Скорость строительства, дней/дом": 266.58,
                    "Введено МКД по ДДУ за 3 года": 100201,
                    "Средняя площадь дома, м²": 8350,
                    "Средняя оценка ЖК по м²": None})
    return pd.DataFrame([row])


def selection(request: ec.TopExport) -> dict:
    return {"url": ec._build_top_url(request.region, request.sorting_key),
            "all_developers_shown": request.sorting_key == "potreb_kachestva",
            "developer_totals": [1], "selects": [
        {"id": "sorting", "value": str(ec.TOP_TYPES[request.sorting_key]),
         "selected_text": next(item["label"] for item in ec.SORTINGS if item["key"] == request.sorting_key),
         "options": [{"value": str(ec.TOP_TYPES[item["key"]]), "text": item["label"]} for item in ec.SORTINGS]},
        {"id": "region", "value": "0" if request.region_key == "rf" else "143443001",
         "selected_text": "РФ" if request.region_key == "rf" else "г.Москва",
         "options": [{"value": "0", "text": "РФ"}, {"value": "143443001", "text": "г.Москва"}]},
        {"id": "year", "value": str(request.year or 2026), "selected_text": str(request.year or 2026),
         "options": [{"value": str(year), "text": str(year)} for year in range(2022, 2027)]},
    ]}


class FakeDriver:
    def __init__(self):
        self.request = ec.TopExport("rf", "obyem_stroitelstva")
        self.closed = False
        self.destinations = []

    def get(self, url):
        parsed = urlparse(url)
        kind = next(key for key, value in ec.TOP_TYPES.items()
                    if str(value) == parse_qs(parsed.query)["topType"][0])
        self.request = ec.TopExport("msk" if "moskva" in parsed.path else "rf", kind)

    def set_page_load_timeout(self, timeout):
        pass

    def execute_cdp_cmd(self, command, parameters):
        assert command == "Browser.setDownloadBehavior"
        self.destinations.append(Path(parameters["downloadPath"]))

    def quit(self):
        self.closed = True


class CollectorContract(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.base = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.object(ec, "DOWNLOAD_DIR", self.base / "raw" / "erzrf"))
        self.stack.enter_context(patch.object(ec, "STATE_FILE", self.base / "state.json"))
        self.stack.enter_context(redirect_stdout(StringIO()))
        self.request = ec.TopExport("rf", "obyem_stroitelstva")

    def workbook(self, kind, name="download.xlsx", frame=None):
        path = self.base / name
        path.parent.mkdir(parents=True, exist_ok=True)
        (fixture_frame(kind) if frame is None else frame).to_excel(path, index=False)
        return path

    def browser_stubs(self, driver):
        self.stack.enter_context(patch.object(ec, "create_chrome", return_value=driver))
        self.stack.enter_context(patch.object(ec, "_ensure_logged_in", return_value=True))
        self.stack.enter_context(patch.object(ec, "_wait_for_top_content", return_value=True))
        self.stack.enter_context(patch.object(ec, "_read_top_selection", side_effect=lambda d: selection(d.request)))
        self.stack.enter_context(patch.object(ec, "_click_download_excel", return_value={"clicked": True}))
        self.stack.enter_context(patch.object(ec, "_scrape_developers_from_table", return_value=[
            {"place": "1", "name": "Fixture developer", "card_url": "https://erzrf.ru/zastroyschiki/fixture",
             "cells": ["1", "Fixture developer", "125"]},
        ]))

    def download(self, driver=None):
        staging = self.base / "staging"
        staging.mkdir(exist_ok=True)
        return ec._download_top_export(driver or FakeDriver(), self.request, "20260928", staging, ec.TOP_TYPES)

    def test_unknown_sorting_and_region_never_fall_back(self):
        with self.assertRaises(ValueError):
            ec._build_top_url(ec.REGIONS[0], "typo")
        with self.assertRaises(ValueError):
            ec._build_top_url({**ec.REGIONS[0], "path": "moskva"}, "obyem_stroitelstva")
        with self.assertRaises(ValueError):
            ec._build_top_url(ec.REGIONS[0], "nakopl_vvod", {"obyem_stroitelstva": 0})

    def test_verified_mapping_and_dynamic_options(self):
        self.assertEqual(ec.TOP_TYPES, {"obyem_stroitelstva": 0, "obyem_vvoda": 1,
                                      "nakopl_vvod": 4, "potreb_kachestva": 2, "skorost": 3})
        self.assertEqual(ec._sorting_control(selection(self.request))[1], ec.TOP_TYPES)

    def test_quality_requires_all_developers_in_url_and_checkbox(self):
        request = ec.TopExport("rf", "potreb_kachestva")
        good = selection(request)
        self.assertEqual(parse_qs(urlparse(good["url"]).query)["isAllDevelopersShown"], ["true"])
        with patch.object(ec, "_read_top_selection", return_value=good):
            self.assertTrue(ec._top_request_evidence(None, request, ec.TOP_TYPES)["all_developers_shown"])
        for value in (False, None):
            bad = {**good, "all_developers_shown": value}
            with patch.object(ec, "_read_top_selection", return_value=bad):
                with self.assertRaisesRegex(ec.TopExportError, "all developers"):
                    ec._top_request_evidence(None, request, ec.TOP_TYPES)
        bad = {**good, "url": good["url"].replace("&isAllDevelopersShown=true", "")}
        with patch.object(ec, "_read_top_selection", return_value=bad):
            with self.assertRaises(ec.TopExportError):
                ec._top_request_evidence(None, request, ec.TOP_TYPES)

    def test_moscow_quality_has_no_all_developers_checkbox(self):
        request = ec.TopExport("msk", "potreb_kachestva")
        current = {**selection(request), "all_developers_shown": None}
        self.assertNotIn("isAllDevelopersShown", parse_qs(urlparse(current["url"]).query))
        with patch.object(ec, "_read_top_selection", return_value=current):
            self.assertEqual(ec._top_request_evidence(None, request, ec.TOP_TYPES)["region"], "msk")

    def test_current_live_sorting_labels_are_recognised(self):
        current = selection(self.request)
        current["selects"][0]["options"] = [
            {"value": "0", "text": "По объему текущего строительства"},
            {"value": "1", "text": "По объему ввода жилья"},
            {"value": "4", "text": "По накопленному вводу жилья (с 2016 г.)"},
            {"value": "2", "text": "По потребительским качествам ЖК"},
            {"value": "3", "text": "По скорости строительства"},
            {"value": "6", "text": "По зеленому строительству"},
        ]
        self.assertEqual(ec._sorting_control(current)[1], ec.TOP_TYPES)

    def test_mapping_missing_ambiguous_or_changed_fails(self):
        original = selection(self.request)
        cases = []
        missing = copy.deepcopy(original)
        missing["selects"][0]["options"].pop()
        cases.append(missing)
        duplicate = copy.deepcopy(original)
        duplicate["selects"].append(copy.deepcopy(duplicate["selects"][0]))
        cases.append(duplicate)
        changed = copy.deepcopy(original)
        changed["selects"][0]["options"][0]["value"] = "99"
        cases.append(changed)
        for case in cases:
            with self.subTest(case=case), self.assertRaises(ec.TopExportError):
                ec._sorting_control(case)

    def test_request_requires_matching_selected_sort_region_url_and_year(self):
        request = ec.TopExport("msk", "obyem_vvoda", 2024)
        good = selection(request)
        with patch.object(ec, "_read_top_selection", return_value=good):
            self.assertEqual(ec._top_request_evidence(None, request, ec.TOP_TYPES)["selected_year"], 2024)
        cases = []
        for index, value in [(0, "0"), (1, "0"), (2, "2025")]:
            bad = copy.deepcopy(good)
            bad["selects"][index]["value"] = value
            cases.append(bad)
        for suffix in ["&topType=0", "&regionKey=0"]:
            bad = copy.deepcopy(good)
            bad["url"] += suffix
            cases.append(bad)
        bad = copy.deepcopy(good)
        bad["url"] = bad["url"].replace("moskva?", "rf?")
        cases.append(bad)
        for case in cases:
            with self.subTest(case=case), patch.object(ec, "_read_top_selection", return_value=case):
                with self.assertRaises(ec.TopExportError):
                    ec._top_request_evidence(None, request, ec.TOP_TYPES)

    def test_duplicate_synchronised_region_controls_are_accepted(self):
        request = ec.TopExport("msk", "obyem_stroitelstva")
        current = selection(request)
        duplicate = copy.deepcopy(current["selects"][1])
        duplicate["id"] = "region-copy"
        current["selects"].append(duplicate)
        with patch.object(ec, "_read_top_selection", return_value=current):
            evidence = ec._top_request_evidence(None, request, ec.TOP_TYPES)
        self.assertEqual(evidence["region_control_ids"], ["region", "region-copy"])

        current["selects"][-1]["value"] = "0"
        current["selects"][-1]["selected_text"] = "РФ"
        with patch.object(ec, "_read_top_selection", return_value=current), \
                self.assertRaises(ec.TopExportError):
            ec._top_request_evidence(None, request, ec.TOP_TYPES)

    def test_all_five_real_workbook_signatures_pass(self):
        for kind in ec.TOP_TYPES:
            with self.subTest(kind=kind):
                report = ec._excel_contract(self.workbook(kind), kind)
                self.assertEqual(report["rows"], 1)

    def test_construction_and_input_schemas_require_both_deadline_columns(self):
        for kind in ("obyem_stroitelstva", "obyem_vvoda", "nakopl_vvod"):
            for missing in ("С переносом срока, м²", "Уточнение срока, мес."):
                frame = fixture_frame(kind).drop(columns=[missing])
                with self.subTest(kind=kind, missing=missing), self.assertRaises(ValueError):
                    ec._excel_contract(self.workbook(kind, frame=frame), kind)

    def test_annual_and_accumulated_input_share_schema_but_browser_evidence_differs(self):
        for actual, requested in (("obyem_vvoda", "nakopl_vvod"),
                                  ("nakopl_vvod", "obyem_vvoda")):
            with self.subTest(actual=actual, requested=requested):
                report = ec._excel_contract(self.workbook(actual), requested)
                self.assertEqual(report["schema_family"], "commissioned_with_deadlines")

        annual = selection(ec.TopExport("rf", "obyem_vvoda", 2026))
        accumulated_request = ec.TopExport("rf", "nakopl_vvod")
        with patch.object(ec, "_read_top_selection", return_value=annual), \
                self.assertRaises(ec.TopExportError):
            ec._top_request_evidence(None, accumulated_request, ec.TOP_TYPES)

    def test_historical_three_mislabelled_schemas_fail(self):
        cases = [("potreb_kachestva", "nakopl_vvod"), ("skorost", "potreb_kachestva"),
                 ("nakopl_vvod", "skorost"), ("potreb_kachestva", "obyem_stroitelstva")]
        for actual, requested in cases:
            with self.subTest(actual=actual, requested=requested), self.assertRaises(ValueError):
                ec._excel_contract(self.workbook(actual), requested)

    def test_fake_zip_and_html_are_invalid_xlsx(self):
        path = self.base / "download.xlsx"
        for content in [b"PK\x03\x04not-a-workbook", b"<html>login</html>"]:
            path.write_bytes(content)
            self.assertFalse(ec._validate_downloaded_excel(path, "obyem_stroitelstva"))

    def test_empty_missing_invalid_and_nonfinite_metric_fail(self):
        good = fixture_frame("obyem_stroitelstva")
        cases = [good.iloc[:0], good.drop(columns=["Строится, м²"])]
        for value in [None, -1, "NaN", "inf", True, "no data"]:
            bad = good.copy()
            bad["Строится, м²"] = value
            cases.append(bad)
        for case in cases:
            with self.subTest(frame=case.to_dict()), self.assertRaises(ValueError):
                ec._excel_contract(self.workbook("obyem_stroitelstva", frame=case), "obyem_stroitelstva")

    def test_invalid_names_and_ranks_fail(self):
        for column, value in [("Наименование, регион", None), ("Место", 0), ("Место", 1.5)]:
            bad = fixture_frame("obyem_stroitelstva")
            bad[column] = value
            with self.subTest(column=column, value=value), self.assertRaises(ValueError):
                ec._excel_contract(self.workbook("obyem_stroitelstva", frame=bad), "obyem_stroitelstva")

    def test_rank_sequence_must_be_continuous_unique_and_ordered(self):
        good = pd.concat([fixture_frame("obyem_stroitelstva")] * 3, ignore_index=True)
        good["Наименование, регион"] = ["A, регион", "B, регион", "C, регион"]
        for ranks in ([1, 3, 4], [1, 2, 2], [2, 1, 3]):
            bad = good.copy()
            bad["Место"] = ranks
            with self.subTest(ranks=ranks), self.assertRaises(ValueError):
                ec._excel_contract(
                    self.workbook("obyem_stroitelstva", frame=bad), "obyem_stroitelstva"
                )

    def test_success_publishes_semantic_xlsx_and_provenance(self):
        driver = FakeDriver()
        self.browser_stubs(driver)
        def wait(folder, **kwargs):
            path = folder / "site.xlsx"
            fixture_frame("obyem_stroitelstva").to_excel(path, index=False)
            return path
        with patch.object(ec, "wait_for_download", side_effect=wait):
            target = self.download(driver)
        evidence = json.loads(target.with_suffix(".xlsx.provenance.json").read_text(encoding="utf-8"))
        self.assertEqual(evidence["sha256"], hashlib.sha256(target.read_bytes()).hexdigest())
        self.assertFalse(evidence["period_verified"])
        self.assertEqual(evidence["workbook"]["rows"], 1)

    def test_invalid_schema_does_not_replace_previous_file(self):
        driver = FakeDriver()
        self.browser_stubs(driver)
        target = ec.DOWNLOAD_DIR / self.request.filename("20260928")
        target.parent.mkdir(parents=True)
        target.write_bytes(b"previous-version-preserved")
        def wait(folder, **kwargs):
            path = folder / "site.xlsx"
            fixture_frame("potreb_kachestva").to_excel(path, index=False)
            return path
        with patch.object(ec, "wait_for_download", side_effect=wait), self.assertRaises(ValueError):
            self.download(driver)
        self.assertEqual(target.read_bytes(), b"previous-version-preserved")
        self.assertFalse(target.with_suffix(".xlsx.provenance.json").exists())

    def test_incomplete_full_list_workbook_is_not_published(self):
        driver = FakeDriver()
        self.browser_stubs(driver)
        expected = selection(driver.request)
        expected["developer_totals"] = [100]
        def wait(folder, **kwargs):
            path = folder / "site.xlsx"
            fixture_frame("obyem_stroitelstva").to_excel(path, index=False)
            return path
        with patch.object(ec, "_read_top_selection", return_value=expected), \
                patch.object(ec, "wait_for_download", side_effect=wait), self.assertRaises(ec.TopExportError):
            self.download(driver)
        self.assertFalse(ec.DOWNLOAD_DIR.exists())

    def test_workbook_larger_than_ui_count_is_accepted_with_provenance(self):
        driver = FakeDriver()
        self.browser_stubs(driver)
        frame = pd.concat([fixture_frame("obyem_stroitelstva")] * 2, ignore_index=True)
        frame["Место"] = [1, 2]
        frame["Наименование, регион"] = ["Fixture developer, регион", "B, регион"]

        def wait(folder, **kwargs):
            path = folder / "site.xlsx"
            frame.to_excel(path, index=False)
            return path

        with patch.object(ec, "wait_for_download", side_effect=wait):
            target = self.download(driver)
        evidence = json.loads(
            target.with_suffix(".xlsx.provenance.json").read_text(encoding="utf-8")
        )
        self.assertEqual(evidence["ui_developer_count"], 1)
        self.assertEqual(evidence["workbook_developer_count"], 2)
        self.assertEqual(evidence["row_count_discrepancy"], 1)
        self.assertTrue(evidence["workbook"]["rank_sequence_complete"])

    def test_workbook_not_matching_visible_ranking_is_rejected(self):
        driver = FakeDriver()
        self.browser_stubs(driver)
        frame = fixture_frame("obyem_stroitelstva")
        frame["Наименование, регион"] = "Another developer, регион"

        def wait(folder, **kwargs):
            path = folder / "site.xlsx"
            frame.to_excel(path, index=False)
            return path

        with patch.object(ec, "wait_for_download", side_effect=wait), \
                self.assertRaises(ec.TopExportError):
            self.download(driver)
        self.assertFalse(ec.DOWNLOAD_DIR.exists())

    def test_annual_metric_must_be_non_increasing_by_rank(self):
        frame = pd.concat([fixture_frame("obyem_vvoda")] * 2, ignore_index=True)
        frame["Место"] = [1, 2]
        frame["Наименование, регион"] = ["A, регион", "B, регион"]
        frame["Введено, м²"] = [100, 101]
        path = self.workbook("obyem_vvoda", frame=frame)
        with self.assertRaisesRegex(ValueError, "non-increasing"):
            ec._excel_contract(path, "obyem_vvoda")

    def test_partial_visible_table_cannot_correlate_a_full_page(self):
        visible = [{"place": "1", "name": "Fixture developer"}]
        preview = [{"rank": index, "name": f"Developer {index}"}
                   for index in range(1, 21)]
        preview[0]["name"] = "Fixture developer, регион"
        with self.assertRaises(ec.TopExportError):
            ec._assert_visible_prefix_matches_workbook(
                visible, {"row_preview": preview}, required_rows=20
            )

    def test_fresher_current_annual_export_can_reorder_visible_leaders(self):
        names = [f"Developer {index}" for index in range(1, 21)]
        visible = [{"place": str(index), "name": name,
                    "cells": [str(index), "0", name + ", регион", str(1000 - index)]}
                   for index, name in enumerate(names, 1)]
        reordered = names[5:] + names[:5]
        preview = [{"rank": index, "name": name + ", регион",
                    "metric": float(1000 - names.index(name) - 1
                                    + (0 if names.index(name) < 5 else 10))}
                   for index, name in enumerate(reordered, 1)]
        evidence = ec._assert_visible_prefix_matches_workbook(
            visible, {"row_preview": preview, "rows": 110}, required_rows=20,
            request=ec.TopExport("rf", "obyem_vvoda", datetime.now().year),
            ui_total_rows=100,
        )
        self.assertEqual(evidence["mode"], "fresh_export_ahead_of_visible_page")
        self.assertEqual(evidence["matched_rows"], 20)
        self.assertEqual(evidence["exact_metric_anchors"], 5)

    def test_reordered_current_annual_export_with_lower_metric_is_rejected(self):
        visible = [{"place": str(index), "name": f"Developer {index}",
                    "cells": [str(index), "0", f"Developer {index}, регион", "100"]}
                   for index in range(1, 21)]
        preview = [{"rank": index, "name": f"Developer {21 - index}, регион",
                    "metric": 99.0 if index == 20 else 110.0}
                   for index in range(1, 21)]
        with self.assertRaises(ec.TopExportError):
            ec._assert_visible_prefix_matches_workbook(
                visible, {"row_preview": preview, "rows": 110}, required_rows=20,
                request=ec.TopExport("rf", "obyem_vvoda", datetime.now().year),
                ui_total_rows=100,
            )

    def test_reordered_export_is_not_allowed_for_past_year(self):
        visible = [{"place": str(index), "name": f"Developer {index}",
                    "cells": [str(index), "0", f"Developer {index}, регион", "100"]}
                   for index in range(1, 21)]
        preview = [{"rank": index, "name": f"Developer {21 - index}, регион", "metric": 110.0}
                   for index in range(1, 21)]
        with self.assertRaises(ec.TopExportError):
            ec._assert_visible_prefix_matches_workbook(
                visible, {"row_preview": preview, "rows": 110}, required_rows=20,
                request=ec.TopExport("rf", "obyem_vvoda", datetime.now().year - 1),
                ui_total_rows=100,
            )

    def test_all_higher_metrics_cannot_masquerade_as_same_snapshot(self):
        visible = [{"place": str(index), "name": f"Developer {index}",
                    "cells": [str(index), "0", f"Developer {index}, регион", "100"]}
                   for index in range(1, 21)]
        preview = [{"rank": index, "name": f"Developer {21 - index}, регион", "metric": 200.0}
                   for index in range(1, 21)]
        with self.assertRaises(ec.TopExportError):
            ec._assert_visible_prefix_matches_workbook(
                visible, {"row_preview": preview, "rows": 110}, required_rows=20,
                request=ec.TopExport("rf", "obyem_vvoda", datetime.now().year),
                ui_total_rows=100,
            )

    def test_ui_and_excel_row_count_difference_is_informational(self):
        request = ec.TopExport("rf", "obyem_vvoda", datetime.now().year)
        exact = {"mode": "exact_visible_prefix", "matched_rows": 20}
        for workbook_rows in (986, 985, 984, 900):
            evidence = ec._row_count_reconciliation(request, workbook_rows, 986, exact)
            self.assertIn("match" if workbook_rows == 986 else str(workbook_rows), evidence)

    def test_row_count_difference_is_informational_for_other_ratings(self):
        cases = [
            (ec.TopExport("rf", "obyem_vvoda", datetime.now().year - 1),
             {"mode": "exact_visible_prefix"}),
            (ec.TopExport("rf", "obyem_stroitelstva"),
             {"mode": "exact_visible_prefix"}),
            (ec.TopExport("rf", "obyem_vvoda", datetime.now().year),
             {"mode": "fresh_export_ahead_of_visible_page"}),
            (ec.TopExport("rf", "obyem_vvoda", datetime.now().year),
             {"mode": "exact_visible_prefix", "matched_rows": 19}),
            (ec.TopExport("rf", "obyem_vvoda", datetime.now().year),
             {"mode": "exact_visible_prefix"}),
        ]
        for request, correlation in cases:
            with self.subTest(request=request, correlation=correlation):
                evidence = ec._row_count_reconciliation(request, 984, 986, correlation)
                self.assertIn("984", evidence)

    def test_current_annual_fallback_accepts_narrow_gk_display_alias(self):
        names = [f"Developer {index}" for index in range(1, 20)] + ["ГК ИНСИТИ девелопмент"]
        visible = [{"place": str(index), "name": name,
                    "cells": [str(index), "0", name + ", регион", "100"]}
                   for index, name in enumerate(names, 1)]
        workbook_names = names[5:] + names[:5]
        workbook_names = ["ИНСИТИ девелопмент" if name.startswith("ГК ИНСИТИ") else name
                          for name in workbook_names]
        preview = [{"rank": index, "name": name + ", регион",
                    "metric": 100.0 if name in names[:5] else 110.0}
                   for index, name in enumerate(workbook_names, 1)]
        evidence = ec._assert_visible_prefix_matches_workbook(
            visible, {"row_preview": preview, "rows": 110}, required_rows=20,
            request=ec.TopExport("rf", "obyem_vvoda", datetime.now().year),
            ui_total_rows=100,
        )
        self.assertEqual(evidence["matched_rows"], 20)

    def test_late_file_outside_current_attempt_is_rejected(self):
        self.browser_stubs(FakeDriver())
        late = self.workbook("obyem_stroitelstva", "earlier_attempt/site.xlsx")
        with patch.object(ec, "wait_for_download", return_value=late), self.assertRaises(ec.TopExportError):
            self.download()
        self.assertTrue(late.exists())
        self.assertFalse(ec.DOWNLOAD_DIR.exists())

    def test_multiple_candidates_are_rejected(self):
        self.browser_stubs(FakeDriver())
        def wait(folder, **kwargs):
            for name in ("one.xlsx", "two.xlsx"):
                fixture_frame("obyem_stroitelstva").to_excel(folder / name, index=False)
            return folder / "two.xlsx"
        with patch.object(ec, "wait_for_download", side_effect=wait), self.assertRaises(ec.TopExportError):
            self.download()

    def test_postclick_selection_change_is_rejected(self):
        driver = FakeDriver()
        self.browser_stubs(driver)
        def wait(folder, **kwargs):
            path = folder / "site.xlsx"
            fixture_frame("obyem_stroitelstva").to_excel(path, index=False)
            driver.request = ec.TopExport("msk", "obyem_stroitelstva")
            return path
        with patch.object(ec, "wait_for_download", side_effect=wait), self.assertRaises(ec.TopExportError):
            self.download(driver)

    def test_timeout_closes_session_without_next_export_and_keeps_success_state(self):
        driver = FakeDriver()
        self.browser_stubs(driver)
        state = {"erzrf_top": {"last_run": "previous-success"}}
        with patch.object(ec, "wait_for_download", return_value=None) as waiter:
            result = ec.fetch_top(state)
        self.assertFalse(result.complete)
        self.assertEqual(waiter.call_count, 1)
        self.assertEqual(len(driver.destinations), 1)
        self.assertTrue(driver.closed)
        self.assertFalse(driver.destinations[0].exists())
        self.assertEqual(state["erzrf_top"]["last_run"], "previous-success")
        self.assertFalse(state["erzrf_top_attempt"]["complete"])
        self.assertEqual(
            len(state["erzrf_top_attempt"]["missing_files"]),
            len(ec._top_export_plan()) + len(ec.REGIONS),
        )

    def test_full_plan_has_current_year_exports_and_no_redundant_current_annual(self):
        plan = ec._top_export_plan()
        year_count = datetime.now().year - 2021
        expected = 8 + 2 * year_count
        self.assertEqual(len(plan), expected)
        self.assertEqual(sum(request.year is not None for request in plan), 2 * year_count)
        self.assertEqual(len({request.filename("20260928") for request in plan}), expected)
        self.assertEqual(max(request.year for request in plan if request.year), datetime.now().year)
        self.assertFalse(any(request.sorting_key == "obyem_vvoda" and request.year is None
                             for request in plan))

    def test_latest_explicit_annual_export_is_used_as_current(self):
        root = self.base / "loader-root"
        raw = root / "data" / "raw" / "realty" / "erzrf"
        raw.mkdir(parents=True)
        for name, developer, value in [
            ("top_obyem_vvoda_rf_2025_20260930.xlsx", "Year 2025, регион", 25),
            ("top_obyem_vvoda_rf_2026_20261001.xlsx", "Year 2026, регион", 26),
            ("top_obyem_vvoda_rf_20260930.xlsx", "Stale unscoped, регион", 99),
        ]:
            frame = fixture_frame("obyem_vvoda")
            frame["Наименование, регион"] = developer
            frame["Введено, м²"] = value
            frame.to_excel(raw / name, index=False)
        access = DataAccess(DataContext(root, use_marts=False))
        result = access.load_erzrf_top()
        self.assertEqual(
            result["obyem_vvoda"]["rf"].iloc[0]["Наименование, регион"],
            "Year 2026, регион",
        )
        self.assertEqual(set(result["obyem_vvoda_by_year"]["rf"]), {2025, 2026})

    def test_current_annual_uses_newest_common_rf_moscow_year(self):
        root = self.base / "paired-loader-root"
        raw = root / "data" / "raw" / "realty" / "erzrf"
        raw.mkdir(parents=True)
        for region, year in [("rf", 2025), ("rf", 2026), ("msk", 2025)]:
            frame = fixture_frame("obyem_vvoda")
            frame["Наименование, регион"] = f"{region}-{year}, регион"
            frame.to_excel(
                raw / f"top_obyem_vvoda_{region}_{year}_20261001.xlsx", index=False
            )
        result = DataAccess(DataContext(root, use_marts=False)).load_erzrf_top()
        self.assertEqual(result["obyem_vvoda_current_year"], {"rf": 2025, "msk": 2025})
        self.assertEqual(
            result["obyem_vvoda"]["rf"].iloc[0]["Наименование, регион"],
            "rf-2025, регион",
        )

    def test_explicit_annual_never_mixes_with_other_region_legacy_unscoped(self):
        root = self.base / "mixed-loader-root"
        raw = root / "data" / "raw" / "realty" / "erzrf"
        raw.mkdir(parents=True)
        for name, developer in [
            ("top_obyem_vvoda_rf_2026_20261001.xlsx", "Fresh RF, регион"),
            ("top_obyem_vvoda_msk_20260930.xlsx", "Stale Moscow, регион"),
        ]:
            frame = fixture_frame("obyem_vvoda")
            frame["Наименование, регион"] = developer
            frame.to_excel(raw / name, index=False)
        result = DataAccess(DataContext(root, use_marts=False)).load_erzrf_top()
        self.assertEqual(result["obyem_vvoda_current_year"], {"rf": 2026})
        self.assertNotIn("msk", result["obyem_vvoda"])
        self.assertNotIn("Stale Moscow, регион", result["all_developers"])

    def test_year_switch_waits_for_changed_table_not_just_assigned_value(self):
        driver = FakeDriver()
        driver.request = ec.TopExport("rf", "obyem_vvoda")
        initial = selection(driver.request)
        selected = selection(ec.TopExport("rf", "obyem_vvoda", 2024))
        driver.execute_script = lambda *args: True

        class ImmediateWait:
            def __init__(self, browser, timeout):
                self.browser = browser
            def until(self, predicate):
                if not predicate(self.browser):
                    raise ec.TimeoutException("fixture: no response table change")

        for changed in (False, True):
            with self.subTest(changed=changed), patch.object(ec, "WebDriverWait", ImmediateWait), \
                    patch.object(ec, "_read_top_selection", side_effect=[initial, selected]), \
                    patch.object(ec, "_top_table_digest", side_effect=["before", "after" if changed else "before"]):
                self.assertEqual(ec._switch_year_filter(driver, 2024), changed)
                self.assertEqual(bool(driver._erzrf_year_evidence), changed)

    def test_no_authorization_preserves_previous_success_and_closes_browser(self):
        driver = FakeDriver()
        self.browser_stubs(driver)
        state = {"erzrf_top": {"last_run": "previous"}}
        with patch.object(ec, "_ensure_logged_in", return_value=False), patch.object(ec, "wait_for_download") as waiter:
            result = ec.fetch_top(state)
        self.assertFalse(result.complete)
        self.assertEqual(state["erzrf_top"]["last_run"], "previous")
        self.assertEqual(waiter.call_count, 0)
        self.assertTrue(driver.closed)

    def test_partial_export_failure_remains_retryable_and_keeps_previous_success(self):
        driver = FakeDriver()
        self.browser_stubs(driver)
        calls = []
        def wait(folder, **kwargs):
            calls.append(folder)
            if len(calls) > 1:
                return None
            path = folder / "site.xlsx"
            fixture_frame(driver.request.sorting_key).to_excel(path, index=False)
            return path
        def switch(browser, year):
            browser.request = ec.TopExport(browser.request.region_key, browser.request.sorting_key, year)
            browser._erzrf_year_evidence = {"year": year, "table_changed": True}
            return True
        state = {"erzrf_top": {"last_run": "previous"}}
        with patch.object(ec, "wait_for_download", side_effect=wait), \
                patch.object(ec, "_switch_year_filter", side_effect=switch):
            result = ec.fetch_top(state)
        self.assertEqual(len(result.files), 0)
        self.assertFalse(result.complete)
        self.assertEqual(len(result.missing_files), len(ec._top_export_plan()) + len(ec.REGIONS))
        self.assertEqual(len(calls), 2)
        self.assertNotEqual(calls[0], calls[1])
        self.assertEqual(state["erzrf_top"]["last_run"], "previous")
        self.assertFalse(any(ec.DOWNLOAD_DIR.iterdir()))
        self.assertTrue(driver.closed)

    def test_batch_publish_rolls_back_replaced_and_new_artifacts(self):
        batch = self.base / "batch"
        destination = self.base / "published"
        batch.mkdir()
        destination.mkdir()
        excel = batch / "a.xlsx"
        sidecar = batch / "a.xlsx.provenance.json"
        payload = batch / "b.json"
        excel.write_bytes(b"new-excel")
        sidecar.write_text("{}", encoding="utf-8")
        payload.write_text("{}", encoding="utf-8")
        (destination / "a.xlsx").write_bytes(b"old-excel")
        original_replace = Path.replace

        def fail_on_last(source, target):
            if source == payload:
                raise OSError("fixture publish failure")
            return original_replace(source, target)

        with patch.object(Path, "replace", fail_on_last), self.assertRaises(OSError):
            ec._publish_top_batch(
                [excel, payload], destination, self.base / "rollback"
            )
        self.assertEqual((destination / "a.xlsx").read_bytes(), b"old-excel")
        self.assertFalse((destination / sidecar.name).exists())
        self.assertFalse((destination / payload.name).exists())

    def test_batch_publish_rolls_back_keyboard_interrupt(self):
        batch = self.base / "batch-interrupt"
        destination = self.base / "published-interrupt"
        batch.mkdir()
        destination.mkdir()
        excel = batch / "a.xlsx"
        sidecar = batch / "a.xlsx.provenance.json"
        payload = batch / "b.json"
        excel.write_bytes(b"new-excel")
        sidecar.write_text("{}", encoding="utf-8")
        payload.write_text("{}", encoding="utf-8")
        (destination / "a.xlsx").write_bytes(b"old-excel")
        original_replace = Path.replace

        def interrupt_on_last(source, target):
            if source == payload:
                original_replace(source, target)
                raise KeyboardInterrupt
            return original_replace(source, target)

        with patch.object(Path, "replace", interrupt_on_last), self.assertRaises(KeyboardInterrupt):
            ec._publish_top_batch([excel, payload], destination, self.base / "rollback-interrupt")
        self.assertEqual((destination / "a.xlsx").read_bytes(), b"old-excel")
        self.assertFalse((destination / sidecar.name).exists())
        self.assertFalse((destination / payload.name).exists())

    def test_batch_restores_backup_when_interrupted_after_backup_move(self):
        batch = self.base / "batch-backup-interrupt"
        destination = self.base / "published-backup-interrupt"
        batch.mkdir()
        destination.mkdir()
        excel = batch / "a.xlsx"
        sidecar = batch / "a.xlsx.provenance.json"
        payload = batch / "b.json"
        excel.write_bytes(b"new-excel")
        sidecar.write_text("{}", encoding="utf-8")
        payload.write_text("{}", encoding="utf-8")
        old_excel = destination / "a.xlsx"
        old_excel.write_bytes(b"old-excel")
        original_replace = Path.replace

        def interrupt_after_backup(source, target):
            result = original_replace(source, target)
            if source == old_excel:
                raise KeyboardInterrupt
            return result

        with patch.object(Path, "replace", interrupt_after_backup), \
                self.assertRaises(KeyboardInterrupt):
            ec._publish_top_batch(
                [excel, payload], destination, self.base / "rollback-backup-interrupt"
            )
        self.assertEqual(old_excel.read_bytes(), b"old-excel")
        self.assertTrue(excel.exists())

    def test_failed_restore_preserves_old_file_in_durable_recovery(self):
        batch = self.base / "batch-restore-failure"
        destination = self.base / "published-restore-failure"
        recovery = self.base / "durable-recovery"
        batch.mkdir()
        destination.mkdir()
        excel = batch / "a.xlsx"
        sidecar = batch / "a.xlsx.provenance.json"
        payload = batch / "b.json"
        excel.write_bytes(b"new-excel")
        sidecar.write_text("{}", encoding="utf-8")
        payload.write_text("{}", encoding="utf-8")
        (destination / "a.xlsx").write_bytes(b"old-excel")
        original_replace = Path.replace

        def fail_publish_and_restore(source, target):
            if source == payload:
                raise OSError("fixture publish failure")
            if source == recovery / "a.xlsx":
                raise OSError("fixture restore failure")
            return original_replace(source, target)

        with patch.object(Path, "replace", fail_publish_and_restore), \
                self.assertRaisesRegex(ec.TopExportError, "recovery files preserved"):
            ec._publish_top_batch([excel, payload], destination, recovery)
        self.assertEqual((recovery / "a.xlsx").read_bytes(), b"old-excel")
        self.assertFalse((destination / "a.xlsx").exists())

    def test_batch_rejects_unexpected_artifact_before_publication(self):
        batch = self.base / "batch-stray"
        destination = self.base / "published-stray"
        batch.mkdir()
        excel = batch / "a.xlsx"
        sidecar = batch / "a.xlsx.provenance.json"
        payload = batch / "b.json"
        for path in (excel, sidecar, payload, batch / "stray.tmp"):
            path.write_bytes(b"fixture")
        with self.assertRaises(ec.TopExportError):
            ec._publish_top_batch([excel, payload], destination, self.base / "rollback-stray")
        self.assertFalse(destination.exists())

    def test_complete_fixture_collection_advances_success_state(self):
        driver = FakeDriver()
        self.browser_stubs(driver)
        def wait(folder, **kwargs):
            path = folder / "site.xlsx"
            fixture_frame(driver.request.sorting_key).to_excel(path, index=False)
            return path
        def switch(browser, year):
            browser.request = ec.TopExport(browser.request.region_key, browser.request.sorting_key, year)
            browser._erzrf_year_evidence = {"year": year, "table_changed": True}
            return True
        developers = [{"name": f"Fixture {i}", "card_url": f"https://erzrf.ru/zastroyschiki/fixture-{i}"}
                      for i in range(100)]
        state = {}
        with patch.object(ec, "wait_for_download", side_effect=wait), \
                patch.object(ec, "_switch_year_filter", side_effect=switch), \
                patch.object(ec, "_collect_top_n_developers", return_value=developers):
            result = ec.fetch_top(state)
        self.assertTrue(result.complete)
        expected_exports = len(ec._top_export_plan())
        expected_files = expected_exports + len(ec.REGIONS)
        self.assertEqual(len(result.files), expected_files)
        self.assertEqual(
            len(list(ec.DOWNLOAD_DIR.glob("*.xlsx.provenance.json"))), expected_exports
        )
        self.assertTrue(state["erzrf_top_attempt"]["complete"])
        self.assertEqual(len(state["erzrf_top"]["files"]), expected_files)
        self.assertEqual(len(set(driver.destinations)), expected_exports)
        self.assertTrue(driver.closed)

    def test_run_rejects_partial_json_only_empty_and_error_results(self):
        required = {"one.xlsx", "two.xlsx", "developers.json"}
        cases = [ec.TopFetchResult(required),
                 ec.TopFetchResult(required, [Path("developers.json")]),
                 ec.TopFetchResult(required, [Path("one.xlsx"), Path("developers.json")]),
                 ec.TopFetchResult(required, [Path(name) for name in required], "failed")]
        for result in cases:
            with self.subTest(result=result), patch.object(ec, "fetch_top", return_value=result):
                files, ok = ec.run(["top"])
                self.assertFalse(ok)
                self.assertEqual(files, result.files)
        with patch.object(ec, "fetch_top", return_value=ec.TopFetchResult(required, [Path(name) for name in required])):
            self.assertTrue(ec.run(["top"])[1])


if __name__ == "__main__":
    unittest.main()
