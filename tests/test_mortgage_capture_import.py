"""Complete browser captures import atomically; partial or wrong scopes do not."""
import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from pipeline.downloaders.nashdom_mortgage import RAW_PATH
from pipeline.mortgage_history import SOURCE_URL
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


if __name__ == "__main__":
    unittest.main()
