"""Public link discovery and atomic acquisition, without network or attachments."""
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import types
import unittest
from unittest.mock import MagicMock, patch
from zipfile import ZipFile

import requests

from pipeline.downloaders import domrf_preferential as downloader


SOURCE_URL = "https://xn--d1aqf.xn--p1ai/programmy-gosudarstvennoj-podderzhki/operational-reporting/"
RELATIVE = Path("data/raw/realty/domrf/preferential_mortgage.json")
DOWNLOAD_URL = "https://xn--d1aqf.xn--p1ai/upload/iblock/139/public-regions.xlsx"
HTML = ('<div><a href="/upload/rf.xlsx">Статистические ряды (РФ), XLSX</a>'
        '<a href="/upload/iblock/139/public-regions.xlsx" download="Статистические ряды (регионы).xlsx">'
        '<p>Статистические ряды (регионы), XLSX</p></a></div>')


def document(source_date="2026-10-11", collected="2026-10-23T10:00:00+00:00"):
    return {"sourceDate": source_date, "collectedAt": collected, "records": [{"measured": 123}]}


def validate_contract(value):
    if not isinstance(value, dict) or not value.get("records"):
        raise ValueError("Invalid normalized source")
    downloader.freshness(value)


def parser_contract():
    module = types.ModuleType("pipeline.preferential_mortgage")
    module.SOURCE_URL, module.SOURCE_FILE = SOURCE_URL, RELATIVE
    module.parse_workbook = MagicMock(side_effect=lambda _: document())
    module.validate_source = MagicMock(side_effect=validate_contract)
    return module


def workbook_bytes():
    stream = BytesIO()
    with ZipFile(stream, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("xl/workbook.xml", "<workbook/>")
    return stream.getvalue()


def response(url, content, *, error=None):
    result = MagicMock()
    result.__enter__.return_value = result
    result.url = url
    result.raise_for_status.side_effect = error
    result.iter_content.return_value = iter([content])
    return result


class DiscoveryTests(unittest.TestCase):
    def test_selects_regional_not_national_workbook(self):
        self.assertEqual(downloader.discover_workbook(HTML, SOURCE_URL), DOWNLOAD_URL)
        self.assertEqual(downloader.discover_workbook(HTML + HTML, SOURCE_URL), DOWNLOAD_URL)

    def test_rejects_missing_ambiguous_and_nonofficial_links(self):
        for html in ("<html>Access denied</html>", HTML.replace("public-regions.xlsx", "public-regions.pdf"),
                     HTML + HTML.replace("public-regions.xlsx", "different.xlsx"),
                     HTML.replace("/upload/iblock/139/public-regions.xlsx", "https://example.org/stolen.xlsx")):
            with self.subTest(html=html), self.assertRaises(downloader.AcquisitionError):
                downloader.discover_workbook(html, SOURCE_URL)

    def test_download_attribute_handles_nested_or_empty_link_text(self):
        html = '<a download="Статистические ряды (регионы).xlsx" href="/official.XLSX?version=2"></a>'
        self.assertTrue(downloader.discover_workbook(html, SOURCE_URL).endswith("/official.XLSX?version=2"))


class AcquisitionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.contract = parser_contract()
        contract = patch.dict("sys.modules", {"pipeline.preferential_mortgage": self.contract})
        contract.start()
        self.addCleanup(contract.stop)

    def session(self, workbook=None, error=None):
        session = MagicMock()
        session.get.side_effect = [response(SOURCE_URL, HTML.encode(), error=error),
                                   response(DOWNLOAD_URL, workbook_bytes() if workbook is None else workbook)]
        return session

    def test_http_success_downloads_only_the_advertised_workbook(self):
        session = self.session()
        result = downloader.http_workbook(self.root, session=session)
        self.assertEqual(result["downloadUrl"], DOWNLOAD_URL)
        self.assertEqual(result["transport"], "http")
        self.assertEqual(result["path"].read_bytes(), workbook_bytes())
        self.assertEqual([call.args[0] for call in session.get.call_args_list], [SOURCE_URL, DOWNLOAD_URL])

    def test_http_refusal_uses_public_browser_without_transferring_cookies(self):
        session = self.session(error=requests.HTTPError("403"))
        with patch.object(downloader, "browser_workbook", return_value={"transport": "browser"}) as browser:
            self.assertEqual(downloader.acquire_workbook(self.root, session=session), {"transport": "browser"})
        browser.assert_called_once_with(self.root, browser_factory=None)
        session.cookies.get_dict.assert_not_called()

    def test_html_or_truncated_workbook_cannot_be_accepted(self):
        for payload in (b"<html>CAPTCHA</html>", workbook_bytes()[:40], b""):
            with self.subTest(payload=payload), self.assertRaises(downloader.AcquisitionError):
                downloader.http_workbook(self.root, session=self.session(workbook=payload))

    def test_download_timeout_and_browser_failure_are_not_success(self):
        with patch.object(downloader, "http_workbook", side_effect=requests.Timeout()), \
                patch.object(downloader, "browser_workbook", side_effect=downloader.AcquisitionError("verification required")):
            with self.assertRaisesRegex(downloader.AcquisitionError, "verification"):
                downloader.acquire_workbook(self.root)

    def write_existing(self, value=None):
        path = self.root / RELATIVE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value or document()), encoding="utf-8")
        return path

    def acquire(self, directory):
        path = directory / "download.xlsx"
        path.write_bytes(workbook_bytes())
        return {"path": path, "downloadUrl": DOWNLOAD_URL, "transport": "mock_http"}

    def test_refresh_is_atomic_and_does_not_keep_raw_workbooks(self):
        path = self.write_existing(document("2026-09-25"))
        old = path.read_bytes()
        original_replace = downloader.os.replace
        def replace(source, target):
            self.assertEqual(path.read_bytes(), old)
            validate_contract(json.loads(Path(source).read_text(encoding="utf-8")))
            original_replace(source, target)
        with patch.object(downloader.os, "replace", side_effect=replace) as atomic:
            result = downloader.refresh(self.root, acquire=self.acquire)
        atomic.assert_called_once()
        self.assertEqual(result["status"], "success")
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["downloadUrl"], DOWNLOAD_URL)
        self.assertEqual(list(path.parent.iterdir()), [path])

    def test_transport_parser_validation_and_replace_errors_preserve_source(self):
        path = self.write_existing()
        old, stamp = path.read_bytes(), path.stat().st_mtime_ns
        for stage in ("acquire", "parse", "validate", "replace"):
            def acquire(directory):
                if stage == "acquire":
                    raise requests.ConnectionError("offline")
                return self.acquire(directory)
            with self.subTest(stage=stage), \
                    patch.object(self.contract, "parse_workbook", side_effect=ValueError("schema") if stage == "parse" else lambda _: document()), \
                    patch.object(self.contract, "validate_source", side_effect=ValueError("invalid") if stage == "validate" else validate_contract), \
                    patch.object(downloader.os, "replace", side_effect=OSError("disk full") if stage == "replace" else downloader.os.replace):
                if stage in {"acquire", "parse"}:
                    self.assertEqual(downloader.refresh(self.root, acquire=acquire)["status"], "retained")
                else:
                    with self.assertRaises((ValueError, OSError)):
                        downloader.refresh(self.root, acquire=acquire)
                self.assertEqual(path.read_bytes(), old)
                self.assertEqual(path.stat().st_mtime_ns, stamp)
                self.assertEqual(list(path.parent.iterdir()), [path])

    def test_old_attachment_never_replaces_newer_source_even_if_collected_later(self):
        path = self.write_existing(document("2026-10-20", "2026-10-21T00:00:00Z"))
        old, stamp = path.read_bytes(), path.stat().st_mtime_ns
        workbook = self.root / "attachment.xlsx"
        workbook.write_bytes(workbook_bytes())
        result = downloader.refresh(self.root, workbook=workbook)
        self.assertEqual(result["status"], "retained")
        self.assertEqual(path.read_bytes(), old)
        self.assertEqual(path.stat().st_mtime_ns, stamp)

    def test_undated_candidate_cannot_replace_a_dated_source(self):
        path = self.write_existing()
        before = path.read_bytes()
        self.contract.parse_workbook.side_effect = lambda _: document(None)
        self.assertEqual(downloader.refresh(self.root, acquire=self.acquire)["status"], "retained")
        self.assertEqual(path.read_bytes(), before)

    def test_parser_flows_contract_is_supported_before_atomic_replace(self):
        source = document()
        source["flows"] = source.pop("records")
        self.contract.parse_workbook.side_effect = lambda _: source
        self.contract.validate_source.side_effect = lambda value: value
        result = downloader.refresh(self.root, acquire=self.acquire)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["records"], 1)

    def write_seed(self, name="preferential-mortgage-2026-10-11.json", value=None):
        path = self.root / "data/imports" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value or document()), encoding="utf-8")
        return path

    def blocked(self, directory):
        raise downloader.AcquisitionError("HTTP 403 and browser verification required")

    def test_missing_or_invalid_raw_bootstraps_seed_even_if_live_site_is_blocked(self):
        seed = self.write_seed()
        for case in ("missing", "invalid"):
            raw = self.root / RELATIVE
            raw.parent.mkdir(parents=True, exist_ok=True)
            if case == "missing":
                raw.unlink(missing_ok=True)
            else:
                raw.write_text("{broken", encoding="utf-8")
            with self.subTest(case=case):
                result = downloader.refresh(self.root, acquire=self.blocked)
                self.assertEqual(result["status"], "retained")
                self.assertEqual(result["reason"], "seed_retained_after_refresh_failure")
                self.assertIs(result["liveUpdated"], False)
                self.assertEqual(json.loads(raw.read_text(encoding="utf-8")), json.loads(seed.read_text(encoding="utf-8")))

    def test_valid_raw_is_never_replaced_by_seed_and_newest_valid_seed_is_selected(self):
        self.write_seed("preferential-mortgage-new.json", document("2026-10-20"))
        self.write_seed("preferential-mortgage-old.json", document("2026-09-20"))
        self.write_seed("preferential-mortgage-invalid.json", {"records": []})
        result = downloader.refresh(self.root, acquire=self.blocked)
        self.assertEqual(result["sourceDate"], "2026-10-20")
        path = self.write_existing(document("2026-08-01"))
        before, stamp = path.read_bytes(), path.stat().st_mtime_ns
        result = downloader.refresh(self.root, acquire=self.blocked)
        self.assertEqual(result["sourceDate"], "2026-08-01")
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(path.stat().st_mtime_ns, stamp)

    def test_missing_raw_and_invalid_seed_do_not_fake_available_data(self):
        self.write_seed(value={"records": []})
        with self.assertRaises(downloader.AcquisitionError):
            downloader.refresh(self.root, acquire=self.blocked)
        self.assertFalse((self.root / RELATIVE).exists())

    def test_import_workbook_cli_alias_and_docker_seed_allowlist(self):
        workbook = self.root / "input.xlsx"
        with patch.object(downloader, "refresh", return_value={"status": "success"}) as refresh:
            self.assertEqual(downloader.main(["--root", str(self.root), "--import-workbook", str(workbook)]), 0)
        refresh.assert_called_once_with(self.root, workbook=workbook)
        ignore = (Path(__file__).resolve().parents[1] / ".dockerignore").read_text(encoding="utf-8")
        self.assertIn("!data/imports\ndata/imports/*\n!data/imports/preferential-mortgage-2026-10-11.json", ignore)

    def test_cli_failure_and_stale_import_are_nonzero(self):
        with patch.object(downloader, "refresh", side_effect=requests.Timeout()):
            self.assertEqual(downloader.main(["--root", str(self.root)]), 1)
        with patch.object(downloader, "refresh", return_value={"status": "retained"}):
            self.assertEqual(downloader.main(["--root", str(self.root)]), 2)


class BrowserTransportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.driver = MagicMock()
        self.driver.current_url, self.driver.page_source = SOURCE_URL, HTML
        self.body, self.menu, self.link = MagicMock(), MagicMock(), MagicMock()
        self.body.text = "Official report"
        self.link.get_attribute.return_value = DOWNLOAD_URL
        self.link.is_displayed.return_value = self.link.is_enabled.return_value = True
        self.driver.find_element.side_effect = lambda by, value: self.body if value == "body" else self.menu
        self.driver.find_elements.side_effect = lambda by, value: [] if value == ".starRate__close" else [self.link]
        self.factory = MagicMock(return_value=self.driver)
        self.contract = parser_contract()
        contract = patch.dict("sys.modules", {"pipeline.preferential_mortgage": self.contract})
        contract.start()
        self.addCleanup(contract.stop)

    def test_browser_uses_public_menu_click_and_completed_download(self):
        def download(directory, **kwargs):
            self.assertEqual(kwargs["before_snapshot"], set())
            target = directory / "regions.xlsx"
            target.write_bytes(workbook_bytes())
            return target
        with patch("selenium.webdriver.ActionChains") as actions, \
                patch("selenium.webdriver.support.ui.WebDriverWait") as wait, \
                patch("pipeline.selenium_utils.wait_for_download", side_effect=download):
            wait.return_value.until.side_effect = lambda check: check(self.driver)
            result = downloader.browser_workbook(self.root, browser_factory=self.factory)
        self.assertEqual(result["transport"], "browser")
        self.assertEqual(result["downloadUrl"], DOWNLOAD_URL)
        self.link.click.assert_called_once()
        actions.return_value.move_to_element.assert_called_once_with(self.menu)
        self.driver.quit.assert_called_once()
        self.driver.get_cookies.assert_not_called()

    def test_access_verification_is_not_clicked_and_browser_is_closed(self):
        self.body.text = "Confirm you are not a robot"
        with patch("selenium.webdriver.support.ui.WebDriverWait") as wait:
            wait.return_value.until.side_effect = lambda check: check(self.driver)
            with self.assertRaisesRegex(downloader.AcquisitionError, "no bypass"):
                downloader.browser_workbook(self.root, browser_factory=self.factory)
        self.link.click.assert_not_called()
        self.driver.quit.assert_called_once()

    def test_incomplete_browser_download_is_not_success(self):
        with patch("selenium.webdriver.ActionChains"), \
                patch("selenium.webdriver.support.ui.WebDriverWait") as wait, \
                patch("pipeline.selenium_utils.wait_for_download", return_value=None):
            wait.return_value.until.side_effect = lambda check: check(self.driver)
            with self.assertRaisesRegex(downloader.AcquisitionError, "did not complete"):
                downloader.browser_workbook(self.root, browser_factory=self.factory)
        self.driver.quit.assert_called_once()

    def test_hidden_menu_follows_only_its_advertised_public_url(self):
        target = self.root / "regions.xlsx"
        target.write_bytes(workbook_bytes())
        self.link.is_displayed.return_value = False
        with patch("selenium.webdriver.ActionChains"), \
                patch("selenium.webdriver.support.ui.WebDriverWait") as wait, \
                patch("pipeline.selenium_utils.wait_for_download", return_value=target):
            wait.return_value.until.side_effect = lambda check: check(self.driver)
            result = downloader.browser_workbook(self.root, browser_factory=self.factory)
        self.assertEqual(result["downloadUrl"], DOWNLOAD_URL)
        self.assertEqual([call.args[0] for call in self.driver.get.call_args_list], [SOURCE_URL, DOWNLOAD_URL])
        self.link.click.assert_not_called()
        self.driver.quit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
