"""Independent source-ID matching, exact units and virtual-list coverage."""
import copy
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import nashdom_checker as nc
from pipeline.downloaders import nashdom_kvart_api as api


def record(source_id, count, area, shares=None):
    if shares is None:
        details = [{"roomCountType": "ONE", "flatCount": count, "area": area, "percent": 100}]
    else:
        details = shares
    return {"id": source_id, "flatCount": count, "area": area, "details": details}


def evidence(records, names, region="rf"):
    return {"region_key": region, "urls": api.urls(region), "aggregate": records,
            "dictionary": [{"id": source_id, "name": name} for source_id, name in names.items()]}


def dom(name, count, area, shares=(100, 0, 0, 0)):
    return {"наименование": name, "квартиры_тыс_шт": count, "площадь_тыс_м²": area,
            **{field: str(value) for field, value in zip(api.ROOM_FIELDS.values(), shares)}}


class ApiJoinTests(unittest.TestCase):
    def test_real_homonym_uses_unique_measurements_instead_of_first_matching_name(self):
        records = [record("7622_0", 136, 6000), record("5730_0", 6461, 315858.8), record("7367_0", 176, 8000)]
        source = evidence(records, {row["id"]: "Домостроительный комбинат" for row in records})
        result = api.resolve([dom("Домостроительный комбинат", "6,5", "316")], source)
        self.assertEqual(result[0]["source_id"], "5730_0")
        self.assertEqual(result[0]["apartments"]["all"], {"count": "6461", "area": "315.8588"})
        self.assertEqual(result[0]["apartments"]["FOUR"], {"count": "0", "area": "0.0"})

    def test_real_small_homonym_requires_room_composition_to_resolve_the_source_id(self):
        first = [{"roomCountType": "TWO", "flatCount": 92, "area": 6292.4, "percent": 31},
                 {"roomCountType": "ONE", "flatCount": 118, "area": 5662.7, "percent": 39},
                 {"roomCountType": "THREE", "flatCount": 68, "area": 6920.5, "percent": 23},
                 {"roomCountType": "FOUR", "flatCount": 22, "area": 2554, "percent": 7}]
        second = [{"roomCountType": "TWO", "flatCount": 176, "area": 7246.1, "percent": 40},
                  {"roomCountType": "ONE", "flatCount": 111, "area": 3327, "percent": 25},
                  {"roomCountType": "THREE", "flatCount": 134, "area": 8923.1, "percent": 30},
                  {"roomCountType": "FOUR", "flatCount": 22, "area": 1682.4, "percent": 5}]
        records = [record("0_49456", 443, 21178.6, second), record("0_18586", 300, 21429.6, first)]
        source = evidence(records, {row["id"]: "СЗ СТРОЙДОМ" for row in records})
        result = api.resolve([dom("СЗ СТРОЙДОМ", "< 1", "21", (39, 31, 23, 7))], source)
        self.assertEqual(result[0]["source_id"], "0_18586")
        self.assertEqual(result[0]["apartments"]["TWO"]["count"], "92")
        self.assertAlmostEqual(float(result[0]["apartments"]["TWO"]["area"]), 6.2924)

    def test_real_percent_residual_changes_do_not_change_the_confirmed_source_identity(self):
        details = [{"roomCountType": "TWO", "flatCount": 843, "area": 37853, "percent": 46},
                   {"roomCountType": "ONE", "flatCount": 839, "area": 23443.3, "percent": 47},
                   {"roomCountType": "THREE", "flatCount": 121, "area": 6352.3, "percent": 7}]
        actual = record("0_19166", 1803, 67648.6, details)
        source = evidence([actual], {"0_19166": "СЗ НЬЮЛАЙФ"})
        for shares in ((47, 46, 7, 0), (46, 47, 7, 0)):
            result = api.resolve([dom("СЗ НЬЮЛАЙФ", "1,8", "68", shares)], source)
            self.assertEqual(result[0]["source_id"], "0_19166")
            self.assertEqual(result[0]["apartments"]["ONE"]["count"], "839")
        # The provider's whole percent can change with API item order while
        # exact room counts and areas stay fixed. A same-name homonym still
        # needs the matching count distribution to identify it uniquely.
        actual["details"][0]["percent"], actual["details"][1]["percent"] = 47, 46
        other = copy.deepcopy(actual)
        other["id"] = "homonym"
        other["details"][0]["flatCount"], other["details"][1]["flatCount"] = 82, 1600
        source = evidence([actual, other], {"0_19166": "СЗ НЬЮЛАЙФ", "homonym": "СЗ НЬЮЛАЙФ"})
        for shares in ((47, 46, 7, 0), (46, 47, 7, 0)):
            self.assertEqual(api.resolve([dom("СЗ НЬЮЛАЙФ", "1,8", "68", shares)], source)[0]["source_id"], "0_19166")
        with self.assertRaisesRegex(api.ReportError, "unconfirmed"):
            api.resolve([dom("СЗ НЬЮЛАЙФ", "6,8", "68", (47, 46, 7, 0))], source)

    def test_unresolved_same_measurements_are_explicitly_ambiguous(self):
        source = evidence([record("a", 3, 100), record("b", 4, 150)], {"a": "Small", "b": "Small"})
        with self.assertRaisesRegex(api.ReportError, "ambiguous"):
            api.resolve([dom("Small", "< 1", "< 1")], source)

    def test_partial_dom_wrong_region_duplicate_id_and_fuzzy_alias_never_confirm_population(self):
        source = evidence([record("a", 100, 5000), record("b", 200, 10000)], {"a": "A", "b": "B"})
        with self.assertRaisesRegex(api.ReportError, "population differs"):
            api.resolve([dom("A", "0,1", "5")], source)
        changed = {**source, "region_key": "msk"}
        with self.assertRaisesRegex(api.ReportError, "incorrect region"):
            api.resolve([dom("A", "0,1", "5"), dom("B", "0,2", "10")], changed)
        changed = copy.deepcopy(source)
        changed["aggregate"].append(copy.deepcopy(changed["aggregate"][0]))
        with self.assertRaisesRegex(api.ReportError, "duplicate IDs"):
            api.resolve([dom("A", "0,1", "5"), dom("B", "0,2", "10")], changed)
        with self.assertRaisesRegex(api.ReportError, "population differs"):
            api.resolve([dom("ГК A", "0,1", "5"), dom("B", "0,2", "10")], source)

    def test_small_three_apartments_and_whitespace_names_preserve_exact_source_units(self):
        source = evidence([record("a", 3, 150.5)], {"a": "СЗ\u00a0Small"})
        result = api.resolve([dom("СЗ Small", "< 1", "< 1")], source)
        self.assertEqual(result[0]["apartments"]["all"], {"count": "3", "area": "0.1505"})

    def test_invalid_room_counts_areas_or_duplicate_room_categories_fail(self):
        for change in ({"flatCount": 4}, {"area": 250}, {"details": [
            {"roomCountType": "ONE", "flatCount": 3, "area": 150, "percent": 100},
            {"roomCountType": "ONE", "flatCount": 0, "area": 0, "percent": 0}]}):
            source = evidence([{**record("a", 3, 150), **change}], {"a": "Small"})
            with self.subTest(change=change), self.assertRaises(api.ReportError):
                api.resolve([dom("Small", "< 1", "< 1")], source)

    def test_malformed_source_records_fail_as_a_report_error(self):
        source = evidence([record("a", 3, 150)], {"a": "Small"})
        for field, value in (("dictionary", [None]), ("aggregate", [None])):
            with self.subTest(field=field), self.assertRaises(api.ReportError):
                api.resolve([dom("Small", "< 1", "< 1")], {**source, field: value})
        source["aggregate"][0]["details"] = [None]
        with self.assertRaises(api.ReportError):
            api.resolve([dom("Small", "< 1", "< 1")], source)

    def test_fetch_uses_observed_moscow_scope_and_rejects_unconfirmed_responses(self):
        browser = Mock()
        requests = api.urls("msk")
        self.assertIn("regions=77", requests[0])
        self.assertIn("?region=77", requests[1])
        browser.execute_async_script.return_value = [{"url": url, "status": 200, "data": []} for url in requests]
        self.assertEqual(api.fetch(browser, "msk")["urls"], requests)
        browser.set_script_timeout.assert_called_with(30)
        browser.execute_async_script.return_value[0]["status"] = 403
        with self.assertRaises(api.ReportError):
            api.fetch(browser, "msk")

    def test_global_regions_include_selected_moscow_and_bind_main_scope_totals(self):
        regions = [{**record("unused", 4, 150.5), "id": 77}, {**record("unused", 6, 249.5), "id": 50}]
        source = {**evidence([], {}, "rf"), "regions": regions,
                  "region_dictionary": [{"name": "Центральный ФО", "regions": [
                      {"id": 77, "name": "Город Москва"}, {"id": 50, "name": "Московская область"}]}]}
        rows = [dom("Город Москва", "< 1", "< 1"), dom("Московская область", "< 1", "< 1")]
        resolved = api.resolve_regions(rows, source)
        self.assertEqual({row["source_id"] for row in resolved}, {"77", "50"})
        with self.assertRaisesRegex(api.ReportError, "population differs"):
            api.resolve_regions(rows[1:], source)
        def apartments(count, area):
            return [{"тип": "Все квартиры", "количество_шт": str(count), "площадь_тыс_м²": area},
                    {"тип": "1 комнатные", "количество_шт": str(count), "площадь_тыс_м²": area},
                    *[{"тип": kind, "количество_шт": "0", "площадь_тыс_м²": "0"}
                      for kind in ("2 комнатные", "3 комнатные", "4+ комнатные")]]
        api.validate_apartment_totals(apartments(10, "< 1"), source)
        moscow = {**source, "region_key": "msk", "urls": api.urls("msk")}
        api.validate_apartment_totals(apartments(4, "< 1"), moscow)
        with self.assertRaisesRegex(api.ReportError, "count differs"):
            api.validate_apartment_totals(apartments(10, "< 1"), moscow)

    def test_exact_verified_totals_replace_display_bounds_in_both_consumer_formats(self):
        from pipeline.data_access import _load_kvartirografia_file, _parse_kvart_number
        source = evidence([record("a", 4, 150.5)], {"a": "СЗ КОЗИХИНСКИЙ"})
        rows = [dom("СЗ КОЗИХИНСКИЙ", "< 1", "< 1")]
        detailed = api.resolve(rows, source)
        api.annotate_measurements(rows, detailed)
        data = {"region_key": "msk", "report_date": "12.10.2026", "developers": rows,
                "regions": [copy.deepcopy(rows[0])], "apartments_per_dev": detailed}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "kvartirografia_fixture.json"
            path.write_text(json.dumps([data]), encoding="utf-8")
            nc._build_kvart_xlsx([data], path.with_suffix(".xlsx"))
            for file in (path, path.with_suffix(".xlsx")):
                payload = _load_kvartirografia_file(file)
                for table in ("developers", "regions"):
                    row = payload[table].iloc[0]
                    self.assertEqual(row["квартиры_тыс_шт"], "< 1")
                    self.assertEqual(row["квартиры_тыс_шт_num"], 0.004)
                    self.assertEqual(row["площадь_тыс_м²_num"], 0.1505)
            # Previous verified snapshots have exact per-dev rows already.
            data["developers"][0].pop("apartments_count_exact")
            data["developers"][0].pop("apartments_area_thousand_m2_exact")
            path.write_text(json.dumps([data]), encoding="utf-8")
            self.assertEqual(_load_kvartirografia_file(path)["developers"].iloc[0]["квартиры_тыс_шт_num"], 0.004)
        self.assertIsNone(_parse_kvart_number("< 1"))


class VirtualListTests(unittest.TestCase):
    def test_visible_285px_window_is_not_skipped_by_a_requested_600px_step(self):
        class Browser:
            position = 0
            positions = []
            count = 100
            viewport = 285
            def execute_script(self, script, *args):
                if "viewport:" in script:
                    return {"height": self.count * 19, "viewport": self.viewport}
                if "const pos = arguments[1]" in script:
                    self.position = min(args[1], self.count * 19 - self.viewport)
                    self.positions.append(self.position)
                    return
                if "scrollTop = ls[0].scrollHeight" in script:
                    self.position = self.count * 19 - self.viewport
                    return
                if "querySelectorAll('button')" in script:
                    self.assert_semantic_rows(script)
                    start = int(self.position // 19)
                    stop = min(self.count, int((self.position + self.viewport) // 19) + 1)
                    return [{"name": f"D{i}", "val1": "0,1", "val2": "5", "bars": []} for i in range(start, stop)]
                raise AssertionError(script)
            @staticmethod
            def assert_semantic_rows(script):
                if "length>=3" not in script:
                    raise AssertionError("Chart rows must include selected buttons with another CSS class")
        browser = Browser()
        with patch.object(nc, "selenium_sleep"), redirect_stdout(StringIO()):
            rows = nc._scroll_collect_list(browser, 0, step_px=600)
        self.assertEqual({row["наименование"] for row in rows}, {f"D{i}" for i in range(100)})
        self.assertLessEqual(max(b - a for a, b in zip(browser.positions, browser.positions[1:])), 200)


if __name__ == "__main__":
    unittest.main()
