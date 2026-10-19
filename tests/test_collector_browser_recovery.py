"""Bounded browser recovery and source-publication regressions, offline."""
from contextlib import ExitStack, redirect_stdout
from datetime import datetime
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, PropertyMock, patch

import erzrf_checker as erz
import fedstat_checker as fed
import nashdom_checker as nd


class ImmediateWait:
    def __init__(self, driver, timeout):
        self.driver = driver

    def until(self, predicate):
        result = predicate(self.driver)
        if not result:
            raise nd.TimeoutException("offline condition not satisfied")
        return result

    def until_not(self, predicate):
        if predicate(self.driver):
            raise nd.TimeoutException("offline condition still satisfied")
        return True


def kvart(region, developers=None):
    return {"region_key": region["key"], "region": region["click_label"],
            "report_date": "12.10.2026",
            "apartments": [{"комнатность": "Все", "количество_шт": "100", "площадь_тыс_м²": "1"}],
            "distribution": [{"площадь_м²": "20–40", "доля": "100"}],
            "developers": developers or [{"наименование": "Fixture developer", "квартиры_тыс_шт": "0.1"}],
            "regions": []}


class AuthBrowser:
    def __init__(self, *, ready=True, password_ok=True, timeout=True):
        self.current_url = erz.BASE
        self.ready = ready
        self.password_ok = password_ok
        self.password_visible = True
        self.get = Mock(side_effect=erz.TimeoutException("late resource") if timeout else None)
        self.quit = Mock()
        self.set_page_load_timeout = Mock()
        self.stop = 0
        self.email = Mock()
        self.password = Mock()
        self.password.is_displayed.side_effect = lambda: self.password_visible
        self.email.is_displayed.return_value = True

    def execute_script(self, script, *args):
        if script == "window.stop();":
            self.stop += 1
            return
        if "const controls=" in script:
            return self.ready
        if "const pw =" in script:
            self.password_visible = not self.password_ok
        return True

    def find_elements(self, by, selector):
        return [self.email]

    def find_element(self, by, selector):
        return self.password


class ErAuthTests(unittest.TestCase):
    def setUp(self):
        stack = self.enterContext(ExitStack())
        stack.enter_context(patch.object(erz, "WebDriverWait", ImmediateWait))
        stack.enter_context(patch.object(erz, "selenium_sleep"))
        stack.enter_context(patch.object(erz, "_load_credentials", return_value={"email": "fixture", "password": "fixture"}))
        stack.enter_context(patch.object(erz, "_save_debug_snapshot"))
        stack.enter_context(redirect_stdout(StringIO()))

    def test_navigation_timeout_with_verified_ui_continues_login_once(self):
        browser = AuthBrowser()
        self.assertTrue(erz._ensure_logged_in(browser))
        browser.get.assert_called_once_with(erz.BASE)
        self.assertEqual(browser.stop, 1)
        self.assertFalse(browser.password_visible)

    def test_error_page_never_reaches_login_or_auth_success(self):
        browser = AuthBrowser(ready=False)
        self.assertFalse(erz._ensure_logged_in(browser))
        browser.get.assert_called_once()
        browser.email.send_keys.assert_not_called()

    def test_russian_provider_maintenance_is_reported_without_password_failure(self):
        class MaintenanceBrowser(AuthBrowser):
            def execute_script(self, script, *args):
                if "providerMaintenance:" in script:
                    return {"title": "ЕРЗ.РФ — технические работы", "bodyLength": 142,
                            "providerMaintenance": True, "authControls": 0, "serverError": False}
                return super().execute_script(script, *args)
        browsers = [MaintenanceBrowser(ready=False), MaintenanceBrowser(ready=False)]
        with patch.object(erz, "create_chrome", side_effect=browsers) as factory:
            with self.assertRaisesRegex(erz.TopExportError, "provider_maintenance"):
                erz._authenticated_session(Path("fixture"))
        self.assertEqual(factory.call_count, 2)
        for browser in browsers:
            browser.email.send_keys.assert_not_called()
            browser.password.send_keys.assert_not_called()
            browser.quit.assert_called_once()

    def test_wrong_origin_rejects_even_ui_shaped_page(self):
        browser = AuthBrowser()
        browser.current_url = "https://example.org/login"
        self.assertFalse(erz._auth_page_ready(browser))

    def test_bad_credentials_fail_after_two_fresh_sessions(self):
        browsers = [AuthBrowser(password_ok=False), AuthBrowser(password_ok=False)]
        with patch.object(erz, "create_chrome", side_effect=browsers) as factory:
            with self.assertRaises(erz.TopExportError):
                erz._authenticated_session(Path("fixture"))
        self.assertEqual(factory.call_count, 2)
        for browser in browsers:
            browser.get.assert_called_once()
            browser.quit.assert_called_once()
        self.assertTrue(all(call.kwargs["page_load_strategy"] == "eager" for call in factory.call_args_list))

    def test_second_session_recovers_without_reusing_broken_browser(self):
        bad, good = AuthBrowser(ready=False), AuthBrowser()
        with patch.object(erz, "create_chrome", side_effect=[bad, good]):
            self.assertIs(erz._authenticated_session(Path("fixture")), good)
        bad.quit.assert_called_once()
        good.quit.assert_not_called()

    def test_debug_read_failure_is_best_effort(self):
        # Exercise the real function separately from the auth stubs above.
        browser = Mock()
        type(browser).page_source = PropertyMock(side_effect=erz.WebDriverException("browser gone"))
        with tempfile.TemporaryDirectory() as directory, patch.object(erz, "DOWNLOAD_DIR", Path(directory) / "raw"):
            # setUp patched the name; recover its saved original via the module-level alias.
            REAL_ER_DEBUG(browser, "original-error")


REAL_ER_DEBUG = erz._save_debug_snapshot


class FedstatBrowserTests(unittest.TestCase):
    def test_timeout_with_passport_dom_reads_real_date_without_second_get(self):
        driver = Mock(current_url="https://www.fedstat.ru/indicator/31074")
        driver.get.side_effect = fed.TimeoutException("late resource")
        body = Mock(text="Паспорт Последнее обновление данных")
        label = Mock()
        label.is_displayed.return_value = True
        label.is_enabled.return_value = True
        label.find_element.return_value = Mock(text="12.10.2026")
        driver.find_elements.return_value = [Mock()]

        def element(by, selector):
            return body if selector == "body" else label

        driver.find_element.side_effect = element
        with patch.object(fed, "WebDriverWait", ImmediateWait), patch.object(fed, "close_popup"), \
                patch.object(fed.time, "sleep"), redirect_stdout(StringIO()):
            self.assertEqual(fed.get_last_update_date(driver, "31074_часть1"), "12.10.2026")
        driver.get.assert_called_once()
        driver.execute_script.assert_called_once_with("window.stop();")

    def test_timeout_with_forbidden_dom_cannot_supply_a_date(self):
        driver = Mock(current_url="https://www.fedstat.ru/indicator/31074")
        driver.get.side_effect = fed.TimeoutException("error response")
        driver.find_element.return_value = Mock(text="403 Forbidden")
        with patch.object(fed, "WebDriverWait", ImmediateWait), redirect_stdout(StringIO()):
            self.assertIsNone(fed.get_last_update_date(driver, "31074_часть1"))
        driver.get.assert_called_once()

    def test_only_failed_indicator_date_is_retried_and_both_drivers_close(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory)
            old, fresh = Mock(), Mock()
            stack.enter_context(patch.multiple(fed, DOWNLOAD_DIR=root, STATE_FILE=root / "state.json",
                                               INDICATORS={"done": "Done", "31074_часть1": "Part 1", "31074_часть2": "Part 2"},
                                               DIRECT_DOWNLOAD_ON_DATE_FAILURE=False))
            stack.enter_context(patch.object(fed, "create_driver", side_effect=[old, fresh]))
            stack.enter_context(patch.object(fed, "load_state", return_value={"done": "old"}))
            stack.enter_context(patch.object(fed, "_load_run_checkpoint", return_value={"run_id": None, "completed": {"done": {}}}))
            stack.enter_context(patch.object(fed, "_mark_run_completed"))
            saved = stack.enter_context(patch.object(fed, "save_state"))
            dates = stack.enter_context(patch.object(fed, "get_last_update_date", side_effect=
                lambda driver, identifier: None if driver is old else "12.10.2026"))
            downloads = stack.enter_context(patch.object(fed, "download_excel", return_value=root / "fixture.xls"))
            stack.enter_context(redirect_stdout(StringIO()))
            files, complete = fed.run(only_ids=["done", "31074_часть1", "31074_часть2"])
            self.assertTrue(complete)
            self.assertEqual(len(files), 2)
            self.assertEqual([(call.args[0], call.args[1]) for call in dates.call_args_list],
                             [(old, "31074_часть1"), (fresh, "31074_часть1"), (fresh, "31074_часть2")])
            self.assertEqual([call.args[0] for call in downloads.call_args_list], ["31074_часть1", "31074_часть2"])
            self.assertEqual(saved.call_args.args[0], {"done": "old", "31074_часть1": "12.10.2026", "31074_часть2": "12.10.2026"})
            old.quit.assert_called_once()
            fresh.quit.assert_called_once()

    def test_failed_fresh_date_never_caches_none_or_advances_state(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory)
            old, fresh = Mock(), Mock()
            stack.enter_context(patch.multiple(fed, DOWNLOAD_DIR=root, INDICATORS={"31074_часть1": "Part 1"},
                                               DIRECT_DOWNLOAD_ON_DATE_FAILURE=False))
            stack.enter_context(patch.object(fed, "load_state", return_value={"31074_часть1": "old"}))
            stack.enter_context(patch.object(fed, "_load_run_checkpoint", return_value={"completed": {}}))
            stack.enter_context(patch.object(fed, "create_driver", side_effect=[old, fresh]))
            stack.enter_context(patch.object(fed, "get_last_update_date", return_value=None))
            save = stack.enter_context(patch.object(fed, "save_state"))
            download = stack.enter_context(patch.object(fed, "download_excel"))
            complete = stack.enter_context(patch.object(fed, "_mark_run_completed"))
            stack.enter_context(redirect_stdout(StringIO()))
            self.assertEqual(fed.run(only_ids=["31074_часть1"]), ([], False))
            save.assert_called_once_with({"31074_часть1": "old"})
            download.assert_not_called()
            complete.assert_not_called()
            old.quit.assert_called_once()
            fresh.quit.assert_called_once()

    def test_ipc_parts_can_download_without_passport_date_but_never_invent_it(self):
        self.assertTrue(fed._should_direct_fallback("31074_часть1"))
        self.assertTrue(fed._should_direct_fallback("31074_часть2"))
        self.assertFalse(fed._should_direct_fallback("33648"))
        for failed in (None, "31074_часть1", "31074_часть2"):
            with self.subTest(failed=failed), tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
                root = Path(directory)
                identifiers = ["31074_часть1", "31074_часть2"]
                initial = {key: "14.10.2026" for key in identifiers}
                browsers = [Mock(), Mock(), Mock()]
                previous = root / "previous.xls"
                previous.write_bytes(b"previous verified source")
                stack.enter_context(patch.multiple(fed, DOWNLOAD_DIR=root,
                    DIRECT_DOWNLOAD_ON_DATE_FAILURE=True))
                stack.enter_context(patch.object(fed, "create_driver", side_effect=browsers))
                stack.enter_context(patch.object(fed, "load_state", return_value=initial.copy()))
                stack.enter_context(patch.object(fed, "_load_run_checkpoint", return_value={"completed": {}}))
                stack.enter_context(patch.object(fed, "get_last_update_date", return_value=None))
                save = stack.enter_context(patch.object(fed, "save_state"))
                complete = stack.enter_context(patch.object(fed, "_mark_run_completed"))
                retire = stack.enter_context(patch.object(fed, "retire_download"))
                download = stack.enter_context(patch.object(fed, "download_excel",
                    side_effect=lambda identifier, *args, **kwargs:
                        None if identifier == failed else root / (identifier + ".xls")))
                stack.enter_context(redirect_stdout(StringIO()))
                files, ok = fed.run(only_ids=identifiers)
                self.assertEqual(ok, failed is None)
                self.assertEqual(len(files), 2 if failed is None else 1)
                self.assertEqual([call.args[0] for call in download.call_args_list], identifiers)
                self.assertTrue(all(call.kwargs["remote_date"] is None for call in download.call_args_list))
                save.assert_called_once_with(initial)
                self.assertTrue(all(call.args[2] is None for call in complete.call_args_list))
                self.assertEqual(complete.call_count, len(files))
                self.assertEqual(retire.call_count, len(files))
                self.assertEqual(previous.read_bytes(), b"previous verified source")
                for browser in browsers:
                    browser.quit.assert_called_once()


class KvartRecoveryTests(unittest.TestCase):
    def setUp(self):
        stack = self.enterContext(ExitStack())
        self.root = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        self.raw = self.root / "raw/nashdom"
        self.raw.mkdir(parents=True)
        stack.enter_context(patch.multiple(nd, DOWNLOAD_DIR=self.raw, STATE_FILE=self.root / "state/nashdom.json"))
        stack.enter_context(patch.object(nd, "_save_debug_snapshot"))
        stack.enter_context(patch.object(nd, "selenium_sleep"))
        stack.enter_context(redirect_stdout(StringIO()))
        self.driver = Mock(current_url="https://наш.дом.рф/аналитика/квартирография", page_source="fixture")
        self.date = datetime.now().strftime("%Y%m%d")

    def perdev_context(self):
        stack = ExitStack()
        stack.enter_context(patch.object(nd, "_per_dev_enabled", return_value=True))
        stack.enter_context(patch.object(nd, "_per_dev_limit", return_value=None))
        stack.enter_context(patch.object(nd, "_per_dev_limit_for_region", return_value=None))
        stack.enter_context(patch.object(nd, "_load_monitoring_devs", return_value=[]))
        stack.enter_context(patch.object(nd, "_load_dev_overrides", return_value={}))
        stack.enter_context(patch.object(nd, "_switch_developer_filter", return_value=True))
        stack.enter_context(patch.object(nd, "_get_developer_filter_label", return_value="Fixture developer"))
        return stack

    def test_old_checkpoint_cannot_mask_missing_current_moscow_and_old_files_survive(self):
        target = self.raw / f"kvartirografia_{self.date}.json"
        old = [kvart(region) for region in nd.KVART_REGIONS]
        original = json.dumps(old, ensure_ascii=False).encode("utf-8")
        target.write_bytes(original)
        xlsx = target.with_suffix(".xlsx")
        xlsx.write_bytes(b"previous good workbook")
        state = {"kvartirografia": {"previous": True}}
        with patch.object(nd, "create_chrome", return_value=self.driver), \
                patch.object(nd, "_per_dev_enabled", return_value=False), \
                patch.object(nd, "_fetch_kvart_region", side_effect=lambda driver, url, region:
                    (driver, kvart(region) if region["key"] == "rf" else None)):
            self.assertEqual(nd.fetch_kvartirografia(state), ([], False))
        self.assertEqual(target.read_bytes(), original)
        self.assertEqual(xlsx.read_bytes(), b"previous good workbook")
        self.assertEqual(state["kvartirografia"], {"previous": True})
        self.assertEqual(state["kvartirografia_attempt"]["missing_regions"], ["msk"])
        self.assertTrue((self.root / f"state/kvartirografia/checkpoint_{self.date}.json").exists())
        self.assertEqual(len(list(self.raw.glob("kvartirografia_*"))), 2)

    def test_current_complete_regions_publish_both_valid_formats(self):
        state = {}
        with self.perdev_context(), patch.object(nd, "create_chrome", return_value=self.driver), \
                patch.object(nd, "_parse_apartments_live", return_value={"all": {"count": "100", "area": "1"}}), \
                patch.object(nd, "_fetch_kvart_region", side_effect=lambda driver, url, region: (driver, kvart(region))):
            files, complete = nd.fetch_kvartirografia(state)
        self.assertTrue(complete)
        self.assertEqual({path.suffix for path in files}, {".json", ".xlsx"})
        self.assertEqual(state["kvartirografia"]["regions"], ["msk", "rf"])

    def test_api_details_publish_exact_source_ids_without_per_developer_filter_clicks(self):
        from tests.test_kvart_api import dom, evidence, record
        from pipeline.downloaders.nashdom_kvart_api import resolve
        from pipeline.data_access import _load_kvartirografia_file
        def aggregate(driver, url, region):
            rows = [dom("Fixture developer", "0,1", "5")]
            proof = evidence([record("source-" + region["key"], 100, 5000)],
                             {"source-" + region["key"]: "Fixture developer"}, region["key"])
            data = kvart(region, rows)
            data["developer_api"] = proof
            data["_api_per_dev"] = resolve(rows, proof)
            return driver, data
        with self.perdev_context(), patch.object(nd, "create_chrome", return_value=self.driver), \
                patch.object(nd, "_fetch_kvart_region", side_effect=aggregate), \
                patch.object(nd, "_switch_developer_filter") as switch, \
                patch.object(nd, "_parse_apartments_live") as live:
            files, complete = nd.fetch_kvartirografia({})
        self.assertTrue(complete)
        switch.assert_not_called()
        live.assert_not_called()
        raw = json.loads(next(path for path in files if path.suffix == ".json").read_text(encoding="utf-8"))
        self.assertEqual({row["apartments_per_dev"][0]["source_id"] for row in raw}, {"source-rf", "source-msk"})
        for path in files:
            detailed = _load_kvartirografia_file(path)["apartments_per_dev"]
            self.assertEqual(set(detailed.source_id), {"source-rf", "source-msk"})
            self.assertTrue(detailed.api_verified.all())
            self.assertTrue(detailed["Все_количество_шт_num"].eq(100).all())
            self.assertTrue(detailed["Все_площадь_тыс_м²_num"].eq(5).all())

    def test_api_is_fetched_before_the_full_scroll_and_not_after_a_failed_request(self):
        region = nd.KVART_REGIONS[0]
        events = []
        fresh = Mock()
        def read(driver, selected):
            events.append("api")
            if driver is self.driver:
                raise ValueError("status=403")
            return {"region_key": selected["key"]}
        def scroll(driver, list_index):
            events.append("scroll")
            return kvart(region)["developers"] if list_index == 0 else []
        with patch.object(nd, "create_chrome", return_value=fresh), \
                patch.object(nd, "_open_kvart_region", return_value=True), \
                patch.object(nd, "_read_kvart_api", side_effect=read), \
                patch.object(nd, "_parse_kvartirografia", return_value=kvart(region)), \
                patch.object(nd, "_scroll_collect_list", side_effect=scroll), patch.object(nd, "_attach_kvart_api"):
            driver, data = nd._fetch_kvart_region(self.driver, "fixture", region)
        self.assertIs(driver, fresh)
        self.assertIsNotNone(data)
        self.assertEqual(events, ["api", "api", "scroll", "scroll"])

    def test_rejected_payload_recovers_on_bounded_second_attempt(self):
        wrong = {"all": {"count": "10", "area": "1"}}
        good = {"all": {"count": "100", "area": "1"}}
        with self.perdev_context(), patch.object(nd, "create_chrome", return_value=self.driver), \
                patch.object(nd, "_parse_apartments_live", side_effect=[wrong, wrong, good, good]) as parse, \
                patch.object(nd, "_fetch_kvart_region", side_effect=lambda d, url, r: (d, kvart(r))):
            files, complete = nd.fetch_kvartirografia({})
        self.assertTrue(complete)
        self.assertEqual(parse.call_count, 4)
        payload = json.loads(next(path for path in files if path.suffix == ".json").read_text(encoding="utf-8"))
        rf = next(row for row in payload if row["region_key"] == "rf")
        self.assertEqual([a["status"] for a in rf["per_dev_attempts"]], ["rejected", "ok"])
        self.assertTrue(rf["per_dev_coverage"]["complete"])

    def test_small_developer_upper_bound_accepts_actual_three_or_four_apartments(self):
        with self.perdev_context(), patch.object(nd, "create_chrome", return_value=self.driver), \
                patch.object(nd, "_parse_apartments_live", side_effect=[{"all": {"count": "3", "area": "1"}},
                                                                      {"all": {"count": "4", "area": "1"}}]), \
                patch.object(nd, "_fetch_kvart_region", side_effect=lambda d, url, r:
                    (d, kvart(r, [{"наименование": "Fixture developer", "квартиры_тыс_шт": "< 1"}]))):
            files, complete = nd.fetch_kvartirografia({})
        self.assertTrue(complete)
        self.assertEqual(len(files), 2)

    def test_disabled_and_limited_runs_preserve_previous_snapshot(self):
        for disabled, limit in ((True, None), (False, 1)):
            with self.subTest(disabled=disabled, limit=limit), self.perdev_context(), \
                    patch.object(nd, "create_chrome", return_value=self.driver), \
                    patch.object(nd, "_per_dev_enabled", return_value=not disabled), \
                    patch.object(nd, "_per_dev_limit", return_value=limit), \
                    patch.object(nd, "_parse_apartments_live", return_value={"all": {"count": "100", "area": "1"}}), \
                    patch.object(nd, "_fetch_kvart_region", side_effect=lambda d, url, r: (d, kvart(r))):
                target = self.raw / f"kvartirografia_{self.date}.json"
                target.write_text("[]")
                self.assertEqual(nd.fetch_kvartirografia({}), ([], False))
                self.assertEqual(target.read_text(), "[]")

    def test_failed_moscow_region_recovers_once_on_fresh_driver(self):
        region = nd.KVART_REGIONS[1]
        fresh = Mock(current_url=self.driver.current_url, page_source="fixture")
        with patch.object(nd, "create_chrome", return_value=fresh) as factory, \
                patch.object(nd, "_read_kvart_api", return_value={}), \
                patch.object(nd, "_attach_kvart_api"), \
                patch.object(nd, "_open_kvart_region", side_effect=[False, True]) as opened, \
                patch.object(nd, "_parse_kvartirografia", return_value=kvart(region)), \
                patch.object(nd, "_scroll_collect_list", side_effect=lambda d, list_index: kvart(region)["developers"] if list_index == 0 else []):
            browser, data = nd._fetch_kvart_region(self.driver, "fixture-url", region)
        self.assertIs(browser, fresh)
        self.assertEqual(data["region_key"], "msk")
        self.assertEqual(opened.call_count, 2)
        factory.assert_called_once()
        self.driver.quit.assert_called_once()

    def test_missing_region_and_developer_labels_are_not_default_selections(self):
        with patch.object(nd, "_get_all_apartments_count", return_value="100"), \
                patch.object(nd, "_get_current_region_label", return_value=""):
            self.assertFalse(nd._switch_region_filter(self.driver, "Российская Федерация"))
        self.driver.execute_script.return_value = None
        with patch.object(nd, "_get_developer_filter_label", return_value=""):
            self.assertFalse(nd._clear_developer_filter(self.driver))

    def test_actual_all_label_is_accepted_without_treating_blank_as_all(self):
        self.assertTrue(nd._all_developers_selected("Все"))
        self.assertTrue(nd._all_developers_selected("Все девелоперы"))
        self.assertFalse(nd._all_developers_selected(""))
        self.assertFalse(nd._all_developers_selected("Все проекты ГК"))
        with patch.object(nd, "_get_developer_filter_label", return_value="Все"):
            self.assertTrue(nd._clear_developer_filter(self.driver))
        self.driver.execute_script.assert_not_called()

    def test_developer_chart_must_be_ready_independently_of_apartment_count(self):
        self.driver.execute_script.return_value = {"developers": [], "regions": True}
        self.assertFalse(nd._kvart_lists_ready(self.driver))
        self.driver.execute_script.return_value = {"developers": ["Город Москва", "Сахалинская область", "Алтайский край"], "regions": True}
        self.assertFalse(nd._kvart_lists_ready(self.driver))
        self.driver.execute_script.return_value = {"developers": ["Fixture developer"], "regions": True}
        self.assertTrue(nd._kvart_lists_ready(self.driver))

    def test_moscow_count_change_alone_does_not_accept_unchanged_developers(self):
        region = nd.KVART_REGIONS[1]
        self.driver.region = "Российская Федерация"

        def switch(driver, *args):
            driver.region = "Город Москва"
            return True

        with patch.object(nd, "WebDriverWait", ImmediateWait), \
                patch.object(nd, "_get_current_region_label", side_effect=lambda d: d.region), \
                patch.object(nd, "_get_all_apartments_count", side_effect=lambda d: "1000" if d.region == "Российская Федерация" else "100"), \
                patch.object(nd, "_get_developer_filter_label", return_value="Все"), \
                patch.object(nd, "_clear_developer_filter", return_value=True), \
                patch.object(nd, "_kvart_lists_ready", return_value=True), \
                patch.object(nd, "_kvart_developers_fingerprint", return_value="unchanged RF developers"), \
                patch.object(nd, "_switch_region_filter", side_effect=switch):
            with self.assertRaises(nd.TimeoutException):
                nd._open_kvart_region(self.driver, "fixture", region)

    def test_developer_option_wait_uses_exact_rendered_popup_actions(self):
        trigger = Mock()
        self.driver.execute_script.return_value = trigger
        actions = []

        def popup(driver, action, value=""):
            actions.append((action, value))
            return True

        with patch.object(nd, "WebDriverWait", ImmediateWait), \
                patch.object(nd, "_clear_developer_filter", return_value=True), \
                patch.object(nd, "_get_all_apartments_count", side_effect=["1000", "100", "100"]), \
                patch.object(nd, "_get_developer_filter_label", return_value="ПИК"), \
                patch.object(nd, "_kvart_popup_action", side_effect=popup):
            self.assertTrue(nd._switch_developer_filter(self.driver, "ПИК"))
        self.assertEqual(actions, [("ready", ""), ("search", "ПИК"), ("top", ""), ("click", "ПИК")])

    def test_failed_perdev_moscow_restore_never_collects_rf_as_moscow(self):
        fresh = Mock()
        rf = kvart(nd.KVART_REGIONS[0], [{"наименование": "RF dev", "квартиры_тыс_шт": "0.1"}])
        msk = kvart(nd.KVART_REGIONS[1], [{"наименование": f"M{i}", "квартиры_тыс_шт": "0.1"} for i in range(6)])
        with ExitStack() as stack:
            stack.enter_context(patch.object(nd, "create_chrome", side_effect=[self.driver, fresh]))
            stack.enter_context(patch.object(nd, "_fetch_kvart_region", side_effect=lambda d, url, r: (d, rf if r["key"] == "rf" else msk)))
            stack.enter_context(patch.object(nd, "_per_dev_enabled", return_value=True))
            stack.enter_context(patch.object(nd, "_per_dev_limit", return_value=0))
            stack.enter_context(patch.object(nd, "_per_dev_limit_for_region", return_value=0))
            stack.enter_context(patch.object(nd, "_load_monitoring_devs", return_value=[]))
            stack.enter_context(patch.object(nd, "_load_dev_overrides", return_value={}))
            stack.enter_context(patch.object(nd, "_build_dev_mapping", side_effect=lambda names, *args:
                [{"site_name": name, "monitoring_name": name, "match_type": "exact"} for name in names]))
            switched = stack.enter_context(patch.object(nd, "_switch_developer_filter", side_effect=lambda d, name, **kw: name in {"RF dev", "Все девелоперы"}))
            stack.enter_context(patch.object(nd, "_get_developer_filter_label", return_value="RF dev"))
            parsed = stack.enter_context(patch.object(nd, "_parse_apartments_live", return_value={"all": {"count": "100", "area": "1"}}))
            stack.enter_context(patch.object(nd, "_open_kvart_region", return_value=False))
            files, complete = nd.fetch_kvartirografia({})
        self.assertFalse(complete)
        self.assertEqual(files, [])
        parsed.assert_called_once()
        self.assertNotIn("M5", [call.args[1] for call in switched.call_args_list])
        payload = json.loads((self.root / f"state/kvartirografia/checkpoint_{self.date}.json").read_text(encoding="utf-8"))
        moscow = next(row for row in payload if row["region_key"] == "msk")
        self.assertEqual(moscow["apartments_per_dev"], [])
        self.assertEqual(len(moscow["per_dev_attempts"]), 5)
        self.assertIn("restore", moscow["per_dev_abort_reason"])

    def test_failed_xlsx_build_preserves_existing_json_and_xlsx(self):
        target = self.raw / "kvartirografia_fixture.json"
        target.write_bytes(b"old-json")
        target.with_suffix(".xlsx").write_bytes(b"old-xlsx")
        with patch.object(nd, "_build_kvart_xlsx", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                nd._publish_kvart_snapshot([kvart(region) for region in nd.KVART_REGIONS], target, target.with_suffix(".xlsx"))
        self.assertEqual(target.read_bytes(), b"old-json")
        self.assertEqual(target.with_suffix(".xlsx").read_bytes(), b"old-xlsx")

    def test_second_format_replace_failure_rolls_back_first_format(self):
        target = self.raw / "kvartirografia_fixture.json"
        workbook = target.with_suffix(".xlsx")
        target.write_bytes(b"old-json")
        workbook.write_bytes(b"old-xlsx")
        replace = Path.replace

        def fail_new_workbook(source, destination):
            if source.name == workbook.name and source.parent.name.startswith(".kvart-publish-"):
                raise OSError("replace failed")
            return replace(source, destination)

        with patch.object(Path, "replace", fail_new_workbook):
            with self.assertRaises(OSError):
                nd._publish_kvart_snapshot([kvart(region) for region in nd.KVART_REGIONS], target, workbook)
        self.assertEqual(target.read_bytes(), b"old-json")
        self.assertEqual(workbook.read_bytes(), b"old-xlsx")


class ConstructionRecoveryTests(unittest.TestCase):
    def test_changed_label_with_unchanged_table_fails_transition_and_retry(self):
        driver = Mock(region="Все", _operational_region_evidence=None)

        def execute(script, *args):
            if "const target=arguments[0], root=" in script:
                driver.region = "Город Москва"
            return True

        driver.execute_script.side_effect = execute
        with patch.object(nd, "WebDriverWait", ImmediateWait), patch.object(nd, "selenium_sleep"), \
                patch.object(nd, "_operational_selected_region", side_effect=lambda d: d.region), \
                patch.object(nd, "_operational_fingerprint", return_value="unchanged RF table"):
            self.assertFalse(nd._switch_operational_region(driver, "Город Москва", "construction"))
            self.assertFalse(nd._switch_operational_region(driver, "Город Москва", "construction"))
        self.assertFalse(driver._operational_region_evidence["confirmed"])

    def test_label_only_failed_transition_cannot_pass_same_target_fastpath(self):
        driver = Mock(_operational_region_evidence={"page": "construction", "target": "Город Москва", "confirmed": False})
        with patch.object(nd, "_operational_selected_region", return_value="Город Москва"), \
                patch.object(nd, "_operational_fingerprint", return_value="RF table"):
            self.assertFalse(nd._switch_operational_region(driver, "Город Москва", "construction"))
        driver.execute_script.assert_not_called()

    def test_failed_transition_recovers_on_one_fresh_page_and_driver(self):
        old, fresh = Mock(region="Все"), Mock(region="Все")
        for driver in (old, fresh):
            driver.get.side_effect = lambda url, driver=driver: setattr(driver, "region", "Все")

        def switch(driver, target, page):
            driver.region = target
            return driver is fresh

        with patch.object(nd, "WebDriverWait", ImmediateWait), \
                patch.object(nd, "create_chrome", return_value=fresh) as factory, \
                patch.object(nd, "_operational_selected_region", side_effect=lambda d: d.region), \
                patch.object(nd, "_operational_fingerprint", return_value="real table"), \
                patch.object(nd, "_switch_operational_region", side_effect=switch), redirect_stdout(StringIO()):
            driver, ready = nd._load_operational_region(old, "fixture", "construction", "Город Москва")
        self.assertTrue(ready)
        self.assertIs(driver, fresh)
        factory.assert_called_once()
        old.get.assert_called_once()
        fresh.get.assert_called_once()
        old.quit.assert_called_once()

    def test_unconfirmed_construction_never_replaces_previous_same_day_file(self):
        from pipeline.downloaders import nashdom_operational_history as history

        class UnconfirmedSource:
            bounds = {"construction": "2020-01", "sales": "2020-01"}

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def collect(self, block, month):
                raise history.ReportError("requested period/region was not confirmed")

        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory)
            target = root / f"construction_operational_{datetime.now():%Y%m%d}.json"
            target.write_bytes(b"old snapshot")
            stack.enter_context(patch.object(nd, "DOWNLOAD_DIR", root))
            factory = stack.enter_context(patch.object(history, "OfficialClient", return_value=UnconfirmedSource()))
            stack.enter_context(redirect_stdout(StringIO()))
            state = {"construction_operational": {"previous": True}}
            self.assertEqual(nd.fetch_construction_operational(state), ([], False))
            self.assertEqual(state, {"construction_operational": {"previous": True}})
            self.assertEqual(target.read_bytes(), b"old snapshot")
            factory.assert_called_once()


if __name__ == "__main__":
    unittest.main()
