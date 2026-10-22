"""Complete browser captures import atomically; partial or wrong scopes do not."""
import copy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from pipeline.downloaders.nashdom_mortgage import RAW_PATH
from pipeline.mortgage_history import SOURCE_URL
from pipeline.mortgage_calculations import build_report
from scripts.import_mortgage_capture import import_capture, prepare_capture
from tests.test_nashdom_mortgage_api import exchanges


def capture():
    rows = []
    for region, value in (("rf", "1"), ("msk", "1762")):
        for period in ("2026-01", "2026-02"):
            rows.append({"region": region, "period": period, "exchanges": exchanges(region, period),
                         "selection": {"regionValue": value, "period": period, "basis": "month",
                                       "year": "2026", "years": ["2026"], "months": [
                                           {"period": "2026-01", "label": "январь"},
                                           {"period": "2026-02", "label": "февраль"}]}})
    return {"schemaVersion": 1, "sourceUrl": SOURCE_URL, "collectedAt": "2026-10-22T10:00:00Z",
            "availableYears": {"rf": [2026], "msk": [2026]}, "observations": rows}


class CaptureImportTests(unittest.TestCase):
    def test_precise_monthly_values_and_capture_time_are_kept(self):
        years, bundles = prepare_capture(capture())
        self.assertEqual(years, {"rf": [2026], "msk": [2026]})
        self.assertEqual(len(bundles), 2)
        self.assertTrue(all(item["collectedAt"] == "2026-10-22T10:00:00Z" for item in bundles))
        self.assertEqual(bundles[0]["records"][0]["volumeRub"], 392274000000)

    def test_complete_import_publishes_both_regions(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            file = root / "capture.json"
            file.write_text(json.dumps(capture()), encoding="utf-8")
            imported = import_capture(file, root)
            self.assertEqual(len(imported["records"]), 12)
            self.assertTrue((root / RAW_PATH / "mortgage_history.json").is_file())

    def test_partial_or_duplicate_capture_cannot_replace_existing_history(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            file = root / "capture.json"
            original = capture()
            file.write_text(json.dumps(original), encoding="utf-8")
            import_capture(file, root)
            history = root / RAW_PATH / "mortgage_history.json"
            before = history.read_bytes()
            for rows in (original["observations"][:-1], original["observations"] + original["observations"][:1]):
                file.write_text(json.dumps({**original, "observations": rows}), encoding="utf-8")
                with self.assertRaises(ValueError):
                    import_capture(file, root)
                self.assertEqual(history.read_bytes(), before)

    def test_unknown_source_missing_year_and_wrong_selection_rejected(self):
        for mutate in (lambda data: data.update(sourceUrl="wrong"),
                       lambda data: data["availableYears"]["rf"].append(2025),
                       lambda data: data["observations"][0]["selection"].update(basis="year"),
                       lambda data: data["observations"][0]["selection"].update(regionValue="1762")):
            data = capture()
            mutate(data)
            with self.assertRaises(ValueError):
                prepare_capture(data)

    def test_inconsistent_calendar_and_changed_response_rejected(self):
        data = capture()
        data["observations"][0]["selection"]["months"].append({"period": "2026-03"})
        with self.assertRaises(ValueError):
            prepare_capture(data)
        data = capture()
        data["observations"][0]["exchanges"][0]["request"]["periodType"] = "year"
        with self.assertRaises(ValueError):
            prepare_capture(data)

    def test_input_data_is_not_mutated(self):
        data = capture()
        before = copy.deepcopy(data)
        prepare_capture(data)
        self.assertEqual(data, before)

    def test_selection_and_exchange_transport_metadata_are_not_published(self):
        data = capture()
        data["observations"][0]["selection"]["cookies"] = "PRIVATE_CREDENTIAL"
        data["observations"][0]["exchanges"][0]["headers"] = {"Cookie": "PRIVATE_CREDENTIAL"}
        _, bundles = prepare_capture(data)
        self.assertNotIn("PRIVATE_CREDENTIAL", json.dumps(bundles))

    def test_stale_or_shorter_capture_preserves_history_and_all_checkpoints(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            file = root / "capture.json"
            file.write_text(json.dumps(capture()), encoding="utf-8")
            import_capture(file, root)
            raw = root / RAW_PATH
            before = {path: path.read_bytes() for path in raw.rglob("*.json")}
            older = capture()
            older["collectedAt"] = "2026-10-21T10:00:00Z"
            shorter = capture()
            shorter["collectedAt"] = "2026-10-23T10:00:00Z"
            shorter["observations"] = [item for item in shorter["observations"]
                                       if item["period"] == "2026-01"]
            for item in shorter["observations"]:
                item["selection"]["months"] = [{"period": "2026-01"}]
            for document in (older, shorter):
                file.write_text(json.dumps(document), encoding="utf-8")
                with self.assertRaises(ValueError):
                    import_capture(file, root)
                self.assertEqual({path: path.read_bytes() for path in raw.rglob("*.json")}, before)


class ArchivedCaptureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.capture = (Path(__file__).resolve().parents[1] / "data/imports" /
                       "mortgage-capture-2026-10-22-precise.json")
        cls.tmp = TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.root = Path(cls.tmp.name)
        cls.history = import_capture(cls.capture, cls.root)

    def test_reviewed_archive_bytes_are_unchanged(self):
        self.assertEqual(hashlib.sha256(self.capture.read_bytes()).hexdigest(),
                         "b167adc78bb3d8bfb8a406132110c27dbb05662508ce4b13812993e6ffd38aca")
        self.assertEqual(self.history["collectedAt"], "2026-10-22T04:47:44.491Z")

    def test_complete_monthly_calendar_for_both_regions(self):
        self.assertEqual(len(self.history["records"]), 1272)
        expected = {f"{year}-{month:02d}" for year in range(2009, 2027)
                    for month in range(1, 13) if year < 2026 or month <= 8}
        for region in ("rf", "msk"):
            rows = [row for row in self.history["records"] if row["region"] == region]
            self.assertEqual(len(rows), 636)
            for segment in ("all", "primary", "secondary"):
                self.assertEqual({row["period"] for row in rows if row["segment"] == segment}, expected)

    def test_report_retains_exact_values_and_incomplete_latest_year(self):
        report = build_report(self.history, "msk", 12)
        self.assertEqual(report["annual"][-2]["volume"]["annual"], 549849000000)
        self.assertEqual(report["annual"][-1]["volume"]["selected"], 373814000000)
        self.assertEqual(report["annual"][-1]["count"]["selected"], 48116)
        self.assertEqual(report["annual"][-1]["volume"]["actualThroughMonth"], 8)
        self.assertIsNone(report["annual"][-1]["volume"]["annual"])
        self.assertEqual(report["monthly"][-1]["period"], "2026-08")
        self.assertEqual(report["monthly"][-1]["all"]["volumeRub"], 47478000000)

    def test_unavailable_source_values_are_not_invented(self):
        for region in ("rf", "msk"):
            monthly = build_report(self.history, region, 12)["monthly"]
            for period in ("2015-12", "2016-10"):
                row = next(item for item in monthly if item["period"] == period)
                self.assertIsNotNone(row["all"]["volumeRub"])
                self.assertIsNone(row["all"]["termMonths"])
                self.assertIsNone(row["all"]["averageAmountRub"])
            for row in monthly:
                if row["period"] < "2016-01":
                    for segment in ("primary", "secondary"):
                        self.assertTrue(all(value is None for value in row[segment].values()))

    def test_repeated_manual_import_is_idempotent(self):
        path = self.root / RAW_PATH / "mortgage_history.json"
        before = path.read_bytes()
        imported = import_capture(self.capture, self.root)
        self.assertEqual(imported, self.history)
        self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
