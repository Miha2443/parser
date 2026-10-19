"""Offline regression checks; --live inspects the actually downloaded bundle."""
from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.downloaders import nashdom_operational_api as api
from pipeline.downloaders import nashdom_operational_history as history
from pipeline.download_provenance import receipt_path
from pipeline.parsers.nashdom_construction_operational import parse


def fixture(block, month, *, stamp=None, report_date=""):
    year, num = map(int, month.split("-"))
    responses = {}
    for region in ("rf", "msk"):
        identity = {"repYear": year, "repMonth": num,
                    "subject": "77" if block == "construction" and region == "msk" else "100"}
        if block == "construction":
            values = {"livingSquareSum": 102849 if region == "rf" else 15680,
                      "generalSquareSum": 162274 if region == "rf" else 26418}
        else:
            # Real January 2020 source values: never total * rounded percentage.
            values = {"totalSquare": 102849, "openSquare": 78320, "soldSquare": 42722,
                      "unsoldSquare": 35598, "unopenedSquare": 24530, "soldPerc": 42,
                      "unsoldPerc": 35, "unopenedPerc": 24, "soldAmount": 3346546, "priceAvg": 78333}
        envelope = {"data": [{**identity, **values}], "errcode": "0"}
        response = {"url": api.request_url(block, month, region), "payload": envelope}
        if block == "sales" and region == "msk":
            response["region_identity"] = {"errcode": "0", "data": [{**identity, **values, "subject": "77"}]}
        responses[region] = response
    stamp = stamp or api.utc_now()
    rows = api.normalize(block, month, responses, collected_at=stamp, report_date=report_date)
    return {"schemaVersion": 1, "block": block, "period": month,
            "collected_at": stamp, "rows": rows, "responses": responses}


class FakeSource:
    def __init__(self, bounds=None, missing=()):
        self.bounds = bounds or {"construction": "2020-03", "sales": "2020-02"}
        self.missing = set(missing)
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def collect(self, block, month):
        self.calls.append((block, month))
        if (block, month) in self.missing:
            raise api.ReportError("not published")
        return fixture(block, month)


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch("requests.sessions.Session.request", side_effect=AssertionError("live network forbidden")))
        self.enterContext(patch("nashdom_checker.create_chrome", side_effect=AssertionError("live browser forbidden")))

    def test_selection_gaps_late_publication_and_boundaries(self):
        self.assertEqual(history.months("2020-12", "2021-02"), ["2020-12", "2021-01", "2021-02"])
        self.assertEqual(history.select_months("2020-01", "2020-05", {"2020-01", "2020-03"}, recent=2),
                         ["2020-05", "2020-04", "2020-02"])
        self.assertEqual(history.select_months("2020-02", "2020-01", set()), [])
        self.assertEqual(history.select_months("2020-01", "2020-02", {"2020-01", "2020-02"}, recent=0), ["2020-02"])

    def test_actual_response_period_not_requested_url(self):
        item = fixture("sales", "2020-01")
        item["responses"]["rf"]["payload"]["data"][0]["repYear"] = 2026
        with self.assertRaisesRegex(api.ReportError, "different period"):
            history.validate_artifact(item)
        item = fixture("construction", "2020-01")
        item["responses"]["msk"]["payload"]["data"][0]["subject"] = "100"
        with self.assertRaisesRegex(api.ReportError, "regional identity"):
            history.validate_artifact(item)

    def test_source_values_no_percent_area_derivation(self):
        item = fixture("sales", "2020-01")
        metrics = item["rows"][0]["metrics"]
        self.assertEqual(metrics["sold_thousand_m2"], 42722)
        self.assertNotEqual(metrics["sold_thousand_m2"], metrics["total_living_thousand_m2"] * .42)
        self.assertEqual(metrics["sold_pct"], 42)
        self.assertEqual(metrics["price_per_m2_rub"], 78333)
        self.assertEqual(metrics["funds_million_rub"], 3346546)
        del item["responses"]["rf"]["payload"]["data"][0]["soldSquare"]
        with self.assertRaises(api.ReportError):
            history.validate_artifact(item)

    def test_literal_missing_optional_sales_values_are_not_invented(self):
        item = fixture("sales", "2020-01")
        for response in item["responses"].values():
            for name in ("priceAvg", "soldAmount", "soldPerc"):
                response["payload"]["data"][0][name] = None
        rows = api.normalize("sales", "2020-01", item["responses"], collected_at=item["collected_at"])
        self.assertIsNone(rows[0]["metrics"]["price_per_m2_rub"])
        self.assertIsNone(rows[0]["metrics"]["funds_million_rub"])
        self.assertIsNone(rows[0]["metrics"]["sold_pct"])
        self.assertEqual(rows[0]["metrics"]["sold_thousand_m2"], 42722)
        item["rows"] = rows
        history.validate_artifact(item)

    def test_empty_source_is_not_a_transport_error_or_download(self):
        with self.assertRaises(api.EmptyReport):
            api.source_row({"errcode": "0", "data": []}, "2020-01")
        self.assertFalse(issubclass(api.EmptyReport, api.TransportError))

    def test_moscow_filter_identity_not_aggregate_subject(self):
        item = fixture("sales", "2020-01")
        item["responses"]["msk"]["region_identity"]["data"][0]["totalSquare"] += 1
        with self.assertRaisesRegex(api.ReportError, "differs from Moscow"):
            history.validate_artifact(item)
        self.assertIn("regionCode=77", api.request_url("construction", "2020-01", "msk"))
        self.assertNotIn("regionCd=", api.request_url("sales", "2020-01", "msk"))

    def test_report_dates_are_independent_and_unknown_not_fabricated(self):
        con = fixture("construction", "2020-02", report_date="19.02.2020")
        sale = fixture("sales", "2020-01")
        self.assertEqual(con["rows"][0]["report_period"], "Февраль 2020")
        self.assertEqual(sale["rows"][0]["report_period"], "Январь 2020")
        self.assertEqual(sale["rows"][0]["report_date"], "")
        self.assertEqual(con["rows"][0]["report_date"], "19.02.2020")

    def test_stale_page_report_date_cannot_label_a_new_api_month(self):
        source = api.OfficialClient(Path("unused"))
        source.bounds = {"construction": "2020-02"}
        source.page_periods = {"construction": "2020-01"}
        source.report_dates = {"construction": "19.01.2020"}
        item = fixture("construction", "2020-02")
        replies = {r["url"]: r["payload"] for r in item["responses"].values()}
        with patch.object(source, "get", side_effect=lambda url: replies[url]):
            result = source.collect("construction", "2020-02")
        self.assertTrue(all(row["report_date"] == "" for row in result["rows"]))

    def test_history_startup_failure_preserves_state_and_daily_file(self):
        class StartupFailure(FakeSource):
            def __enter__(self):
                raise api.TransportError("blocked")

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "construction_operational_20261019.json"
            path.write_bytes(b"existing raw snapshot")
            state = Path(tmp) / "nashdom_state.json"
            state.write_bytes(b'{"construction_operational":{"complete":true}}')
            before, modified = state.read_bytes(), path.stat().st_mtime_ns
            with patch.object(history, "publish_current", side_effect=AssertionError("current publication forbidden")) as publish:
                with self.assertRaisesRegex(api.TransportError, "blocked"):
                    history.download_history(tmp, client=StartupFailure(), progress=lambda _: None)
            publish.assert_not_called()
            self.assertEqual(state.read_bytes(), before)
            self.assertEqual(path.read_bytes(), b"existing raw snapshot")
            self.assertEqual(path.stat().st_mtime_ns, modified)
            self.assertFalse((Path(tmp) / history.BUNDLE_NAME).exists())

    def test_atomic_failure_preserves_previous_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "construction_history.json"
            history.atomic_json(target, {"old": 1})
            original = target.read_bytes()
            with patch.object(history.os, "replace", side_effect=OSError("interrupted")):
                with self.assertRaises(OSError):
                    history.atomic_json(target, {"new": 2})
            self.assertEqual(target.read_bytes(), original)
            self.assertEqual(list(Path(tmp).glob("*.tmp")), [])

    def test_resume_after_artifact_before_bundle_preserves_data_and_stamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = history.HistoryStore(tmp)
            item = fixture("construction", "2020-01", stamp="2026-01-01T10:00:00+00:00")
            expected = copy.deepcopy(item["rows"])
            observation = store.accept(item)
            item["rows"][0]["area_thousand_m2"] = 1
            self.assertEqual(store.rows("construction"), expected)
            self.assertTrue(receipt_path(observation).exists())
            recovered = history.HistoryStore(tmp)
            self.assertEqual(recovered.rows("construction"), expected)
            bounds = {"construction": "2020-02", "sales": "2020-01"}
            coverage = recovered.coverage(bounds)
            self.assertEqual(coverage["construction"]["missing_months"], ["2020-02"])
            recovered.publish(coverage)
            self.assertEqual(json.loads(recovered.bundle.read_text(encoding="utf-8"))["construction"], expected)
            self.assertFalse(receipt_path(recovered.bundle).exists(), "recovery is not a new download")

    def test_bundle_corruption_backup_and_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = history.HistoryStore(tmp)
            store.accept(fixture("construction", "2020-01"))
            bounds = {"construction": "2020-01", "sales": "2020-01"}
            store.publish(store.coverage(bounds))
            store.bundle.write_text("broken", encoding="utf-8")
            restored = history.HistoryStore(tmp)
            bad = list((restored.private / "quarantine").glob("*.bad"))
            self.assertEqual(len(bad), 1)
            self.assertEqual(bad[0].read_text(), "broken")
            self.assertEqual(len(restored.rows("construction")), 4)
            restored.publish(restored.coverage(bounds))
            self.assertTrue(json.loads(restored.bundle.read_text(encoding="utf-8"))["construction"])

    def test_portable_bundle_without_private_artifacts_is_not_erased(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as moved:
            store = history.HistoryStore(tmp)
            store.accept(fixture("construction", "2020-01"))
            bounds = {"construction": "2020-01", "sales": "2020-01"}
            store.publish(store.coverage(bounds))
            portable = Path(moved) / history.BUNDLE_NAME
            portable.write_bytes(store.bundle.read_bytes())
            restored = history.HistoryStore(moved)
            self.assertEqual(restored.rows("construction"), store.rows("construction"))

    def test_dedup_revisions_only_replace_the_same_block_month(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = history.HistoryStore(tmp)
            con = fixture("construction", "2020-01", stamp="2026-01-01T00:00:00+00:00")
            sale = fixture("sales", "2020-01", stamp="2026-01-02T00:00:00+00:00")
            store.accept(con)
            store.accept(sale)
            newer = fixture("construction", "2020-01", stamp="2026-01-03T00:00:00+00:00")
            store.accept(newer)
            restored = history.HistoryStore(tmp)
            self.assertEqual(len(restored.rows("construction")), 4)
            self.assertEqual(restored.rows("sales"), sale["rows"])
            self.assertEqual(restored.rows("construction"), newer["rows"])
            self.assertEqual(len(list((store.private / "observations").glob("*.json"))), 3)

    def test_mixed_offset_recovery_and_scraped_at_use_actual_instants(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = history.HistoryStore(tmp)
            fresh = fixture("construction", "2020-01", stamp="2026-01-01T12:00:00+00:00")
            stale = fixture("construction", "2020-01", stamp="2026-01-01T14:00:00+03:00")
            store.accept(fresh)
            store.accept(stale)
            self.assertEqual(store.entries["construction"]["2020-01"]["rows"], fresh["rows"])
            store.accept(fixture("sales", "2020-01", stamp="2026-01-01T10:30:00-03:00"))
            restored = history.HistoryStore(tmp)
            self.assertEqual(restored.entries["construction"]["2020-01"]["rows"], fresh["rows"])
            bounds = {"construction": "2020-01", "sales": "2020-01"}
            restored.publish(restored.coverage(bounds))
            payload = json.loads(restored.bundle.read_text(encoding="utf-8"))
            self.assertEqual(payload["scraped_at"], "2026-01-01T10:30:00-03:00")
            self.assertGreater(history.collected_instant(payload["scraped_at"]),
                               history.collected_instant(fresh["collected_at"]))

    def test_current_guard_rejects_pre_run_offset_timestamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = history.HistoryStore(tmp)
            bounds = {"construction": "2020-01", "sales": "2020-01"}
            for block in bounds:
                store.accept(fixture(block, "2020-01", stamp="2026-01-01T14:00:00+03:00"))
            self.assertIsNone(history.publish_current(store, bounds, "2026-01-01T12:00:00+00:00"))
            self.assertFalse(list(Path(tmp).glob("construction_operational_*.json")))

    def test_observation_retention_keeps_freshest_instant_not_last_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = history.HistoryStore(tmp)
            for stamp in ("2026-01-01T12:00:00+00:00", "2026-01-01T14:00:00+03:00",
                          "2026-01-01T15:00:00+05:00"):
                store.accept(fixture("construction", "2020-01", stamp=stamp))
            kept = [json.loads(p.read_text(encoding="utf-8"))["collected_at"]
                    for p in (store.private / "observations").glob("*.json")]
            self.assertEqual(set(kept), {"2026-01-01T12:00:00+00:00", "2026-01-01T14:00:00+03:00"})
            restored = history.HistoryStore(tmp)
            self.assertEqual(restored.entries["construction"]["2020-01"]["collected_at"],
                             "2026-01-01T12:00:00+00:00")

    def test_current_scraped_at_max_is_timezone_aware(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = history.HistoryStore(tmp)
            bounds = {"construction": "2020-01", "sales": "2020-01"}
            store.accept(fixture("construction", "2020-01", stamp="2026-01-01T12:00:00+00:00"))
            store.accept(fixture("sales", "2020-01", stamp="2026-01-01T10:30:00-03:00"))
            path = history.publish_current(store, bounds, "2026-01-01T11:00:00+00:00")
            self.assertTrue(path)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["scraped_at"],
                             "2026-01-01T10:30:00-03:00")

    def test_nullable_latest_price_does_not_abort_valid_history(self):
        class NullableLatestSource(FakeSource):
            def collect(self, block, month):
                item = super().collect(block, month)
                if block == "sales" and month == self.bounds[block]:
                    for response in item["responses"].values():
                        response["payload"]["data"][0]["priceAvg"] = None
                    item["rows"] = api.normalize(block, month, item["responses"], collected_at=item["collected_at"])
                return item

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / f"construction_operational_{datetime.now():%Y%m%d}.json"
            history.atomic_json(target, {"construction": fixture("construction", "2020-01")["rows"],
                                         "sales": fixture("sales", "2020-01")["rows"]})
            before, modified = target.read_bytes(), target.stat().st_mtime_ns
            source, events = NullableLatestSource(), []
            with patch.object(history, "publish_current", side_effect=AssertionError("history must not publish current")) as publish:
                result = history.download_history(tmp, client=source, recent=1, progress=events.append)
            publish.assert_not_called()
            self.assertEqual(len(source.calls), 5)
            self.assertEqual(result["collected"], 5)
            self.assertEqual(result["attempted"], 5)
            self.assertTrue(result["history_complete"])
            self.assertTrue(result["complete"])
            self.assertIsNone(result["current_publication_error"])
            self.assertEqual(result["errors"], [])
            self.assertEqual(result["current_file"], "")
            self.assertEqual(target.read_bytes(), before)
            self.assertEqual(target.stat().st_mtime_ns, modified)
            self.assertEqual(sum(e["event"] == "saved" for e in events), 5)
            self.assertEqual(sum(e["event"] == "current_publication_error" for e in events), 0)
            self.assertEqual(result["new_files"], [Path(result["history_file"])])
            payload = json.loads(Path(result["history_file"]).read_text(encoding="utf-8"))
            self.assertTrue(payload["history_complete"])
            self.assertTrue(payload["complete"])
            self.assertIsNone(payload["current_publication_error"])
            self.assertTrue(all(row["metrics"]["price_per_m2_rub"] is None
                                for row in payload["sales"] if api.period(row["report_period"]) == source.bounds["sales"]))
            self.assertEqual(len(payload["construction"]), 12)
            self.assertEqual(len(payload["sales"]), 4)

    def test_partial_bootstrap_never_complete_and_resumes(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = FakeSource()
            with patch.object(history, "publish_current", side_effect=AssertionError("history must not publish current")) as publish:
                partial = history.download_history(tmp, client=source, max_periods=2, progress=lambda _: None)
            publish.assert_not_called()
            self.assertFalse(partial["complete"])
            self.assertFalse(partial["history_complete"])
            self.assertEqual(partial["collected"], 2)
            self.assertEqual(partial["current_file"], "")
            self.assertIsNone(partial["current_publication_error"])
            self.assertEqual(partial["errors"], [])
            self.assertEqual(partial["new_files"], [Path(partial["history_file"])])
            self.assertFalse(list(Path(tmp).glob("construction_operational_*.json")))
            observations = {path: path.read_bytes()
                            for path in (Path(tmp) / ".construction_history" / "observations").glob("*.json")}
            # Finish gaps, preserving old observations even though bundle mtime changes.
            result = history.download_history(tmp, client=FakeSource(), recent=1, progress=lambda _: None)
            self.assertTrue(result["complete"])
            self.assertEqual(result["coverage"]["construction"]["collected_months"], 3)
            self.assertEqual(result["coverage"]["sales"]["collected_months"], 2)
            self.assertEqual(result["current_file"], "")
            self.assertIsNone(result["current_publication_error"])
            self.assertEqual(result["new_files"], [Path(result["history_file"])])
            self.assertFalse(list(Path(tmp).glob("construction_operational_*.json")))
            payload = json.loads(Path(result["history_file"]).read_text(encoding="utf-8"))
            self.assertEqual(len(payload["construction"]), 12)
            self.assertEqual(len(payload["sales"]), 4)
            for path, content in observations.items():
                self.assertEqual(path.read_bytes(), content)

    def test_late_publication_keeps_old_gap_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = ("sales", "2020-01")
            first = history.download_history(tmp, client=FakeSource(missing=[missing]), recent=1, progress=lambda _: None)
            self.assertFalse(first["complete"])
            self.assertEqual(first["coverage"]["sales"]["missing_months"], ["2020-01"])
            before = history.HistoryStore(tmp).entries["construction"]["2020-01"]["rows"]
            source = FakeSource()
            second = history.download_history(tmp, client=source, recent=1, progress=lambda _: None)
            self.assertTrue(second["complete"])
            self.assertIn(missing, source.calls)
            self.assertNotIn(("construction", "2020-01"), source.calls)
            self.assertEqual(history.HistoryStore(tmp).entries["construction"]["2020-01"]["rows"], before)

    def test_empty_blocked_source_has_no_fake_materialization(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = FakeSource()
            source.missing = {(b, m) for b in source.bounds for m in history.months("2020-01", source.bounds[b])}
            result = history.download_history(tmp, client=source, progress=lambda _: None)
            self.assertFalse(result["complete"])
            self.assertEqual(result["new_files"], [])
            self.assertEqual(result["collected"], 0)
            self.assertFalse((Path(tmp) / history.BUNDLE_NAME).exists())

    def test_blocked_refresh_does_not_freshen_cached_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = history.download_history(tmp, client=FakeSource(), recent=1, progress=lambda _: None)
            path = Path(first["history_file"])
            previous, modified = path.read_bytes(), path.stat().st_mtime_ns
            source = FakeSource(missing=[("construction", "2020-03"), ("sales", "2020-02")])
            result = history.download_history(tmp, client=source, recent=1, progress=lambda _: None)
            self.assertFalse(result["complete"])
            self.assertEqual(result["collected"], 0)
            self.assertEqual(result["new_files"], [])
            self.assertEqual(path.read_bytes(), previous)
            self.assertEqual(path.stat().st_mtime_ns, modified)

    def test_source_bound_rollback_preserves_verified_bundle_and_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = history.download_history(tmp, client=FakeSource(), progress=lambda _: None)
            bundle = Path(first["history_file"])
            previous = {path: (path.read_bytes(), path.stat().st_mtime_ns)
                        for path in (bundle, receipt_path(bundle))}
            source = FakeSource(bounds={"construction": "2020-02", "sales": "2020-01"})
            with self.assertRaisesRegex(api.ReportError, "exceeds observed source bound"):
                history.download_history(tmp, client=source, progress=lambda _: None)
            for path, (content, modified) in previous.items():
                self.assertEqual(path.read_bytes(), content)
                self.assertEqual(path.stat().st_mtime_ns, modified)
            history.validate_bundle_payload(json.loads(bundle.read_text(encoding="utf-8")))
            attempt = json.loads((Path(tmp) / ".construction_history" / "last_attempt.json").read_text(encoding="utf-8"))
            self.assertEqual(attempt["run_status"], "failed")
            self.assertEqual(attempt["current_snapshot"], "unchanged")
            self.assertFalse(list(Path(tmp).glob("construction_operational_*.json")))

    def test_private_version_retention_is_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = history.HistoryStore(tmp)
            bounds = {"construction": "2020-01", "sales": "2020-01"}
            for num in range(8):
                store.accept(fixture("construction", "2020-01", stamp=f"2026-01-{num + 1:02d}T00:00:00+00:00"))
                store.publish(store.coverage(bounds))
            self.assertEqual(len(list((store.private / "observations").glob("*.json"))), 2)
            self.assertEqual(len(list((store.private / "bundles").glob("*.json"))), 5)

    def test_corrupt_observation_is_not_a_completed_month(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = history.HistoryStore(tmp)
            path = store.accept(fixture("sales", "2020-01"))
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["rows"][0]["metrics"]["sold_thousand_m2"] += 1
            history.atomic_json(path, payload)
            restored = history.HistoryStore(tmp)
            self.assertEqual(restored.entries["sales"], {})
            self.assertTrue(restored.recovery_issues)

    def test_parser_keeps_zero_source_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            item = fixture("sales", "2020-01")
            for row in item["rows"]:
                row["metrics"]["sales_not_open_pct"] = 0
            path = Path(tmp) / history.BUNDLE_NAME
            history.atomic_json(path, {"sales": item["rows"]})
            frame = parse(path)
            self.assertEqual(len(frame), 20)
            self.assertEqual(set(frame.loc[frame["view"].eq("sales_not_open_pct"), "value"]), {0})

    def test_lock_prevents_two_concurrent_writers(self):
        with tempfile.TemporaryDirectory() as tmp:
            with history.history_lock(Path(tmp)):
                with self.assertRaisesRegex(RuntimeError, "active"):
                    with history.history_lock(Path(tmp)):
                        pass


def check_live():
    store = history.HistoryStore(ROOT / "data/raw/realty/nashdom")
    payload = json.loads(store.bundle.read_text(encoding="utf-8"))
    assert payload["complete"], payload["coverage"]
    for block in ("construction", "sales"):
        coverage = payload["coverage"][block]
        assert not coverage["missing_months"]
        assert set(store.entries[block]) == set(history.months("2020-01", coverage["latest_available"]))
        for row in payload[block]:
            assert row["period_verified"] is True
            assert datetime.fromisoformat(row["collected_at"]).tzinfo is not None
    assert receipt_path(store.bundle).is_file()
    frame = parse(store.bundle)
    assert len(frame) == len(payload["construction"]) + 10 * len(payload["sales"])
    print(json.dumps({"complete": True, "rows": len(frame), "coverage": payload["coverage"]}, ensure_ascii=False))


if __name__ == "__main__":
    if "--live" in sys.argv:
        check_live()
    else:
        unittest.main()
