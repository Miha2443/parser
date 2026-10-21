"""Mortgage history uses explicit source years and durable verified year scopes."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pipeline import mortgage_history as mh


def observation(region="rf", year=2020, month=1, segment="all", value=100):
    return {"region": region, "period": f"{year:04d}-{month:02d}", "segment": segment,
            "basis": "month", "volumeRub": value, "count": 10 if value is not None else None,
            "ratePct": 6.5 if value is not None else None,
            "termMonths": 180 if value is not None else None,
            "averageAmountRub": None, "raw": {"table": "source", "row": month}}


def bundle(region="rf", year=2020, through=12, *, collected="2026-10-21T10:00:00+03:00", value=100):
    return {"region": region, "year": year, "sourceUrl": mh.SOURCE_URL,
            "sourceDate": "2026-10-20", "collectedAt": collected,
            "records": [observation(region, year, month, segment, value)
                        for month in range(1, through + 1) for segment in mh.SEGMENTS],
            "availableMonths": list(range(1, through + 1)),
            "evidence": {"sourceEndpoint": "/original", "response": {"preserve": [1, 2, None]}}}


class MortgageHistoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.raw = Path(self.directory.name) / "data/raw/realty/nashdom"

    def checkpoint(self, region="rf", year=2020, through=12, **kwargs):
        item = bundle(region, year, through, **kwargs)
        mh.save_checkpoint(self.raw, item)
        return item

    def history(self, years=None):
        available = years or {"rf": [2020], "msk": [2020]}
        for region, region_years in available.items():
            for year in region_years:
                self.checkpoint(region, year)
        return mh.publish_history(self.raw, available)

    def test_first_run_uses_only_discovered_years_and_independent_regions(self):
        years = {"msk": [2020, 2017, 2020], "rf": [2019, 2010]}
        self.assertEqual(mh.plan_years(years), [("rf", 2010), ("rf", 2019), ("msk", 2017), ("msk", 2020)])
        self.assertEqual(mh.plan_years({"rf": []}), [])
        self.assertEqual(mh.plan_years({"rf": [2040]}), [("rf", 2040)])

    def test_incremental_refreshes_latest_and_new_years_not_all_history(self):
        previous = self.history({"rf": [2018, 2019, 2020], "msk": [2019, 2020]})
        self.assertEqual(mh.plan_years({"rf": [2018, 2019, 2020], "msk": [2019, 2020]}, previous),
                         [("rf", 2020), ("msk", 2020)])
        self.assertEqual(mh.plan_years({"rf": [2017, 2018, 2019, 2020, 2021], "msk": [2019, 2020]}, previous),
                         [("rf", 2017), ("rf", 2021), ("msk", 2020)])
        self.assertEqual(mh.plan_years({"rf": [2018, 2019, 2020]}, previous, force=True),
                         [("rf", 2018), ("rf", 2019), ("rf", 2020)])

    def test_confirmed_partial_year_is_not_annual_complete_and_previous_retried(self):
        self.checkpoint("rf", 2019, through=10)
        self.checkpoint("rf", 2020, through=10)
        self.checkpoint("msk", 2020, through=8)
        years = {"rf": [2019, 2020], "msk": [2020]}
        previous = mh.publish_history(self.raw, years)
        state = previous["coverage"]["regions"]["rf"]["years"]["2019"]
        self.assertTrue(state["confirmed"])
        self.assertFalse(state["annualComplete"])
        self.assertEqual(state["latestObservedMonth"], 10)
        self.assertEqual(state["metricMonths"]["all"]["volumeRub"], list(range(1, 11)))
        self.assertEqual(mh.plan_years(years, previous), [("rf", 2019), ("rf", 2020), ("msk", 2020)])
        self.checkpoint("rf", 2019)
        previous = mh.publish_history(self.raw, years, requested_years={"rf": [2019]}, refreshed_years=[("rf", 2019)])
        self.assertEqual(mh.plan_years(years, previous), [("rf", 2020), ("msk", 2020)])

    def test_missing_unconfirmed_and_changed_records_are_requested(self):
        previous = self.history({"rf": [2018, 2019, 2020], "msk": [2019, 2020]})
        coverage = previous["coverage"]["regions"]
        del coverage["rf"]["years"]["2018"]
        coverage["msk"]["years"]["2019"]["confirmed"] = False
        next(row for row in previous["records"] if row["region"] == "rf" and row["period"] == "2019-01")["count"] = 99
        self.assertEqual(mh.plan_years({"rf": [2018, 2019, 2020], "msk": [2019, 2020]}, previous),
                         [("rf", 2018), ("rf", 2019), ("rf", 2020), ("msk", 2019), ("msk", 2020)])

    def test_legacy_without_coverage_is_not_trusted_for_skipping_history(self):
        previous = self.history({"rf": [2019, 2020], "msk": [2020]})
        del previous["coverage"]
        self.assertEqual(mh.plan_years({"rf": [2019, 2020], "msk": [2020]}, previous),
                         [("rf", 2019), ("rf", 2020), ("msk", 2020)])

    def test_checkpoint_roundtrip_preserves_evidence_and_detaches_values(self):
        original = self.checkpoint()
        read = mh.load_checkpoint(self.raw, "rf", 2020)
        self.assertEqual(read, original)
        read["evidence"]["response"]["preserve"].append("mutation")
        self.assertEqual(mh.load_checkpoint(self.raw, "rf", 2020), original)
        self.assertIsNone(mh.load_checkpoint(self.raw, "rf", 2019))

    def test_tampered_checkpoint_hash_identity_and_version_are_rejected(self):
        path = mh.save_checkpoint(self.raw, bundle())
        envelope = json.loads(path.read_text(encoding="utf-8"))
        mutations = [lambda item: item["bundle"]["records"][0].update(volumeRub=11),
                     lambda item: item.update(year=2021), lambda item: item.update(schemaVersion=2),
                     lambda item: item.update(normalizationVersion=2), lambda item: item.update(normalizationVersion=True),
                     lambda item: item["bundle"].update(region="msk"),
                     lambda item: item["bundle"].update(evidence={"changed": True})]
        for mutate in mutations:
            changed = copy.deepcopy(envelope)
            mutate(changed)
            path.write_text(json.dumps(changed), encoding="utf-8")
            with self.subTest(changed=changed.keys()), self.assertRaises(mh.HistoryValidationError):
                mh.load_checkpoint(self.raw, "rf", 2020)
        path.write_text("{truncated", encoding="utf-8")
        with self.assertRaisesRegex(mh.HistoryValidationError, "Corrupted"):
            mh.load_checkpoint(self.raw, "rf", 2020)

    def test_invalid_numeric_values_missing_metrics_and_basis_never_install(self):
        for bad in (True, False, float("inf"), float("nan"), -1, 10 ** 400, "2", {}, []):
            for metric in mh.METRICS:
                item = bundle()
                item["records"][0][metric] = bad
                with self.subTest(metric=metric, value=bad), self.assertRaises(mh.HistoryValidationError):
                    mh.save_checkpoint(self.raw, item)
        for mutate in (lambda item: item["records"][0].pop("ratePct"),
                       lambda item: item["records"][0].update(basis="ytd"),
                       lambda item: item["records"][0].update(period="2020-13"),
                       lambda item: item["records"][0].update(period="2021-01"),
                       lambda item: item["records"].append(item["records"][0]),
                       lambda item: item.update(sourceUrl="https://example.invalid"),
                       lambda item: item.update(year=True),
                       lambda item: item.update(collectedAt="2026-10-20T10:00:00"),
                       lambda item: item.update(sourceDate="2026-02-30"),
                       lambda item: item.update(evidence={"bad": float("nan")})):
            item = bundle()
            mutate(item)
            with self.assertRaises(mh.HistoryValidationError):
                mh.save_checkpoint(self.raw, item)
        self.assertFalse((self.raw / ".mortgage_history").exists())

    def test_all_null_bundle_rejected_but_zero_and_missing_segment_are_real(self):
        item = bundle()
        for row in item["records"]:
            row.update({metric: None for metric in mh.METRICS})
        with self.assertRaisesRegex(mh.HistoryValidationError, "no known"):
            mh.save_checkpoint(self.raw, item)
        item = bundle(through=1, value=0)
        item["records"] = item["records"][:1]
        mh.save_checkpoint(self.raw, item)
        self.assertEqual(mh.load_checkpoint(self.raw, "rf", 2020)["records"], item["records"])

    def test_source_months_must_be_observed_without_filling_gaps(self):
        for months in ([1, 1], [True], [0], [13], [1, 2], "1"):
            item = bundle(through=1)
            item["availableMonths"] = months
            with self.subTest(months=months), self.assertRaises(mh.HistoryValidationError):
                mh.validate_bundle(item)
        item = bundle(through=3)
        item["availableMonths"] = [1, 3]
        item["records"] = [row for row in item["records"] if row["period"] != "2020-02"]
        mh.save_checkpoint(self.raw, item)
        self.checkpoint("msk", 2020)
        history = mh.assemble_history(self.raw, {"rf": [2020], "msk": [2020]})
        state = history["coverage"]["regions"]["rf"]["years"]["2020"]
        self.assertEqual(state["observedMonths"], [1, 3])
        self.assertFalse(state["annualComplete"])
        self.assertFalse(any(row["period"] == "2020-02" and row["region"] == "rf" for row in history["records"]))

    def test_requested_years_and_confirmed_years_are_independent(self):
        self.checkpoint("rf", 2020)
        self.checkpoint("msk", 2019)
        history = mh.assemble_history(self.raw, {"rf": [2018, 2020], "msk": [2019, 2021]})
        rf = history["coverage"]["regions"]["rf"]
        msk = history["coverage"]["regions"]["msk"]
        self.assertEqual(rf["requestedYears"], [2018, 2020])
        self.assertEqual(msk["requestedYears"], [2019, 2021])
        self.assertEqual(list(rf["years"]), ["2020"])
        self.assertEqual(list(msk["years"]), ["2019"])
        self.assertEqual(mh.plan_years({"rf": [2018, 2020], "msk": [2019, 2021]}, history),
                         [("rf", 2018), ("rf", 2020), ("msk", 2021)])

    def test_null_month_does_not_confirm_year_or_complete_annual_flow(self):
        item = bundle("rf", 2019)
        for row in item["records"]:
            if row["period"] == "2019-06":
                row.update({metric: None for metric in mh.METRICS})
        mh.save_checkpoint(self.raw, item)
        self.checkpoint("rf", 2020)
        self.checkpoint("msk", 2020)
        history = mh.publish_history(self.raw, {"rf": [2019, 2020], "msk": [2020]})
        state = history["coverage"]["regions"]["rf"]["years"]["2019"]
        self.assertFalse(state["confirmed"])
        self.assertFalse(state["annualComplete"])
        self.assertEqual(state["observedMonths"], list(range(1, 13)))
        self.assertNotIn(6, state["confirmedMonths"])
        self.assertEqual(mh.plan_years({"rf": [2019, 2020], "msk": [2020]}, history),
                         [("rf", 2019), ("rf", 2020), ("msk", 2020)])

    def test_resume_publishes_verified_cached_years_and_keeps_all_provenance(self):
        rf = self.checkpoint("rf", 2020)
        msk = self.checkpoint("msk", 2019)
        history = mh.publish_history(self.raw, {"rf": [2020], "msk": [2019]})
        self.assertEqual(len(history["records"]), len(rf["records"]) + len(msk["records"]))
        self.assertEqual(history["yearSources"]["rf"]["2020"]["evidence"], rf["evidence"])
        self.assertEqual(history["records"][0]["raw"], rf["records"][0]["raw"])
        self.assertEqual(mh.load_published(self.raw), history)
        self.assertEqual(history["coverage"]["regions"]["rf"]["years"]["2020"]["sourceIdentity"]["url"], mh.SOURCE_URL)

    def test_failed_year_retains_prior_scope_and_diagnostic_does_not_change_publication(self):
        previous = self.history({"rf": [2019, 2020], "msk": [2020]})
        before = (self.raw / mh.PUBLISHED_NAME).read_bytes()
        prior_rf = [row for row in previous["records"] if row["region"] == "rf"]
        diagnostic = mh.record_failure(self.raw, "rf", 2020, TimeoutError("source did not respond"),
                                       at="2026-10-21T12:00:00+03:00", details={"stage": "download"})
        self.assertEqual((self.raw / mh.PUBLISHED_NAME).read_bytes(), before)
        self.assertEqual(json.loads(diagnostic.read_text())["error"]["type"], "TimeoutError")
        self.checkpoint("msk", 2020, value=200, collected="2026-10-21T11:00:00+03:00")
        changed = mh.publish_history(self.raw, {"rf": [2020], "msk": [2020]},
                                     requested_years={"rf": [2020], "msk": [2020]},
                                     refreshed_years=[("msk", 2020)])
        self.assertEqual([row for row in changed["records"] if row["region"] == "rf"], prior_rf)
        self.assertEqual(changed["records"][-1]["volumeRub"], 200)
        self.assertEqual(changed["coverage"]["regions"]["rf"]["requestedYears"], [2019, 2020])
        self.assertEqual(changed["yearSources"]["rf"], previous["yearSources"]["rf"])

    def test_both_regions_need_measurements_and_failure_keeps_previous_bytes(self):
        self.checkpoint("rf", 2020)
        with self.assertRaisesRegex(mh.HistoryValidationError, "both RF and Moscow"):
            mh.publish_history(self.raw, {"rf": [2020], "msk": [2020]})
        self.assertFalse((self.raw / mh.PUBLISHED_NAME).exists())
        self.checkpoint("msk", 2020)
        mh.publish_history(self.raw, {"rf": [2020], "msk": [2020]})
        before = (self.raw / mh.PUBLISHED_NAME).read_bytes()
        self.checkpoint("rf", 2020, value=300)
        with patch.object(mh.os, "replace", side_effect=OSError("disk denied")), self.assertRaises(OSError):
            mh.publish_history(self.raw, {"rf": [2020], "msk": [2020]})
        self.assertEqual((self.raw / mh.PUBLISHED_NAME).read_bytes(), before)
        self.assertFalse(list(self.raw.glob("*.tmp")))

    def test_bad_refresh_cannot_replace_valid_checkpoint(self):
        item = self.checkpoint()
        path = self.raw / ".mortgage_history/checkpoints/rf/2020.json"
        before = path.read_bytes()
        item["records"][0]["count"] = -1
        with self.assertRaises(mh.HistoryValidationError):
            mh.save_checkpoint(self.raw, item)
        self.assertEqual(path.read_bytes(), before)
        item["records"][0]["count"] = 12
        with patch.object(mh.os, "replace", side_effect=OSError("disk denied")), self.assertRaises(OSError):
            mh.save_checkpoint(self.raw, item)
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse(list(path.parent.glob("*.tmp")))

    def test_corrupt_published_file_and_checkpoint_raise_instead_of_falling_back(self):
        self.history()
        published = self.raw / mh.PUBLISHED_NAME
        published.write_text("broken", encoding="utf-8")
        with self.assertRaises(mh.HistoryValidationError):
            mh.publish_history(self.raw, {"rf": [2020], "msk": [2020]})
        self.assertEqual(published.read_text(), "broken")
        checkpoint = self.raw / ".mortgage_history/checkpoints/rf/2020.json"
        checkpoint.write_text("broken", encoding="utf-8")
        with self.assertRaises(mh.HistoryValidationError):
            mh.assemble_history(self.raw, {"rf": [2020], "msk": [2020]})

    def test_null_nonobject_duplicate_key_and_bad_metadata_are_corruption(self):
        previous = self.history()
        published = self.raw / mh.PUBLISHED_NAME
        checkpoint = self.raw / ".mortgage_history/checkpoints/rf/2020.json"
        for path in (published, checkpoint):
            for invalid in ("null", "[]", '{"region":"rf","region":"msk"}'):
                path.write_text(invalid, encoding="utf-8")
                with self.subTest(path=path, invalid=invalid), self.assertRaises(mh.HistoryValidationError):
                    mh.load_published(self.raw) if path == published else mh.load_checkpoint(self.raw, "rf", 2020)
        for wrong in ([], None, "bad"):
            changed = copy.deepcopy(previous)
            changed["coverage"]["regions"]["rf"]["years"] = wrong
            with self.assertRaises(mh.HistoryValidationError):
                mh.plan_years({"rf": [2020]}, changed)
        changed = copy.deepcopy(previous)
        changed["coverage"]["regions"]["rf"]["years"]["2020"]["confirmed"] = 1
        with self.assertRaises(mh.HistoryValidationError):
            mh.plan_years({"rf": [2020]}, changed)

    def test_source_date_is_not_invented_and_timestamps_keep_timezone(self):
        item = self.checkpoint("rf", 2020)
        item["sourceDate"] = None
        mh.save_checkpoint(self.raw, item)
        self.checkpoint("msk", 2020, collected="2026-10-21T08:30:00Z")
        history = mh.assemble_history(self.raw, {"rf": [2020], "msk": [2020]})
        self.assertIsNone(history["sourceDate"])
        self.assertEqual(history["collectedAt"], "2026-10-21T08:30:00Z")
        self.assertIsNone(history["yearSources"]["rf"]["2020"]["sourceDate"])

    def test_invalid_plans_scopes_and_paths_are_rejected(self):
        for years in ({"unknown": [2020]}, {"rf": [True]}, {"rf": [20]}, {"rf": "2020"}):
            with self.subTest(years=years), self.assertRaises(mh.HistoryValidationError):
                mh.plan_years(years)
        for region, year in (("../rf", 2020), ("rf", "../2020")):
            with self.assertRaises(mh.HistoryValidationError):
                mh.load_checkpoint(self.raw, region, year)
        self.history()
        for scopes in ([('rf', 2020), ('rf', 2020)], [('rf', 2021)], [('rf',)], ['rf']):
            with self.subTest(scopes=scopes), self.assertRaises(mh.HistoryValidationError):
                mh.assemble_history(self.raw, {"rf": [2020], "msk": [2020]}, refreshed_years=scopes)
        with self.assertRaises(mh.HistoryValidationError):
            mh.assemble_history(self.raw, {"rf": [2020]}, requested_years={"rf": [2019]})
        with self.assertRaisesRegex(mh.HistoryValidationError, "absent"):
            mh.assemble_history(self.raw, {"rf": [2021]}, refreshed_years=[("rf", 2021)])


if __name__ == "__main__":
    unittest.main()
