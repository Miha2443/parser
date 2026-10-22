"""Opt-in, localhost-only Chromium checks: PARSER_BROWSER_TESTS=1."""
import os
from pathlib import Path
import shutil
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from tempfile import TemporaryDirectory
from threading import Thread
import unittest


def chrome_binary():
    candidates = [os.environ.get("CHROME_BINARY")]
    candidates += [shutil.which(name) for name in (
        "google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome",
    )]
    candidates += [str(Path(os.environ[root]) / "Google/Chrome/Application/chrome.exe")
                   for root in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")
                   if root in os.environ]
    candidates += ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]
    return next((path for path in candidates if path and Path(path).is_file()), None)


CHROME = chrome_binary()
CONTROLS = """
<header id="base-header"><div class="main-filters">
  <label>Region <input type="radio" name="region" value="1" checked></label>
  <div class="range-select">
    <label>Month <input type="radio" name="month" value="2026-08-01" checked></label>
    <label>Year <input type="radio" name="year" value="2026" checked></label>
  </div>
</div></header>
"""


class LocalPage(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"<p>Local fixture frame</p>" if self.path == "/captcha-frame" else self.server.html
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@unittest.skipUnless(os.environ.get("PARSER_BROWSER_TESTS") == "1", "Set PARSER_BROWSER_TESTS=1")
@unittest.skipUnless(CHROME, "Local Chrome/Chromium is unavailable")
class MortgageAccessBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from selenium import webdriver
            from selenium.common.exceptions import WebDriverException
            from selenium.webdriver.chrome.service import Service
            from selenium.webdriver.common.selenium_manager import SeleniumManager
        except ImportError as exc:
            raise unittest.SkipTest("Selenium is unavailable") from exc

        class LocalService(Service):
            def stop(self):
                # Older Selenium stops the service again during interpreter teardown.
                if self.process is not None and self.process.poll() is None:
                    super().stop()

        from pipeline.downloaders.nashdom_mortgage_access import read_access_state
        cls.access_state = staticmethod(read_access_state)
        profile = TemporaryDirectory(prefix="mortgage-access-test-")
        cls.addClassCleanup(profile.cleanup)
        options = webdriver.ChromeOptions()
        options.binary_location = CHROME
        for argument in (
            "--headless=new", "--no-sandbox", "--disable-dev-shm-usage",
            "--disable-background-networking", "--disable-component-update", "--disable-sync",
            "--no-first-run", "--no-default-browser-check", "--no-proxy-server",
            "--window-size=1280,1000",
            "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1, EXCLUDE localhost",
            f"--user-data-dir={profile.name}",
        ):
            options.add_argument(argument)
        try:
            paths = SeleniumManager().binary_paths([
                "--browser", "chrome", "--browser-path", CHROME, "--offline", "--avoid-stats",
            ])
            if not paths.get("driver_path"):
                raise unittest.SkipTest("No cached local ChromeDriver (downloads disabled)")
            cls.driver = webdriver.Chrome(service=LocalService(paths["driver_path"]), options=options)
        except WebDriverException as exc:
            raise unittest.SkipTest("Local Chrome/ChromeDriver is unavailable") from exc
        cls.addClassCleanup(cls.driver.quit)
        cls.driver.set_page_load_timeout(10)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), LocalPage)
        cls.server.html = b""
        cls.addClassCleanup(cls.server.server_close)
        thread = Thread(target=cls.server.serve_forever, daemon=True)
        thread.start()
        cls.addClassCleanup(thread.join, 5)
        cls.addClassCleanup(cls.server.shutdown)
        cls.origin = f"http://127.0.0.1:{cls.server.server_port}"
        cls.url = cls.origin + "/mortgage/"

    def load(self, markup="", *, controls=True, title="Mortgage fixture", path="/mortgage/"):
        self.server.html = (f"<!doctype html><html><head><title>{title}</title></head><body>"
                            + (CONTROLS if controls else "") + markup + "</body></html>").encode()
        self.driver.get(self.origin + path)

    def check(self, *, expected_url=None, **expected):
        state = self.access_state(self.driver, expected_url=expected_url or self.url)
        self.assertGreaterEqual(state.keys(), {
            "ready", "sameOrigin", "expectedPath", "controlsReady", "readyState",
            "loaderVisible", "captchaVisible", "denied", "title",
        })
        for field, value in expected.items():
            self.assertEqual(state[field], value, (field, state))

    def test_hidden_roots_and_captcha_iframes_do_not_block_visible_controls(self):
        for hidden in ("hidden", 'style="display:none"', 'style="visibility:hidden"', 'style="opacity:0"'):
            with self.subTest(hidden=hidden):
                self.load(f'<div id="captcha_root" {hidden}><button>Verify</button></div>'
                          f'<div id="id_captcha_loaded" {hidden}><canvas></canvas></div>'
                          f'<iframe src="/captcha-frame" {hidden}></iframe>')
                self.check(ready=True, captchaVisible=False, controlsReady=True,
                           sameOrigin=True, expectedPath=True, readyState="complete",
                           loaderVisible=False, denied=False, title="Mortgage fixture")

    def test_visible_captcha_widgets_block_ready_page(self):
        for markup in ('<div id="captcha_root"><button>Verify</button></div>',
                       '<div id="id_captcha_loaded"><canvas width="80" height="40"></canvas></div>',
                       '<iframe src="/captcha-frame" title="Local captcha"></iframe>'):
            with self.subTest(markup=markup):
                self.load(markup)
                self.check(ready=False, captchaVisible=True, controlsReady=True)

    def test_empty_visible_service_roots_are_not_captcha(self):
        self.load(''.join(f'<div id="{root}" style="width:80px;height:40px"></div>'
                          for root in ("captcha_root", "id_captcha_loaded", "id_captcha_frame_div")))
        self.check(ready=True, captchaVisible=False)

    def test_ancestor_visibility_hides_captcha_widgets(self):
        self.load('<section style="display:none"><div id="captcha_root"><button>Verify</button>'
                  '</div><iframe src="/captcha-frame"></iframe></section>')
        self.check(ready=True, captchaVisible=False)

    def test_loader_visibility_blocks_readiness_but_is_not_captcha(self):
        for style, visible in (("", True), ("display:none", False), ("visibility:hidden", False)):
            with self.subTest(style=style):
                self.load('<js-challenge-loader style="display:block;width:80px;height:40px;'
                          + style + '"></js-challenge-loader>')
                self.check(ready=not visible, loaderVisible=visible, captchaVisible=False)

    def test_access_denied_title_or_heading_blocks_readiness(self):
        for title, markup in (("Access denied", ""), ("Mortgage fixture", "<h1>Access denied</h1>")):
            with self.subTest(title=title, markup=markup):
                self.load(markup, title=title)
                self.check(ready=False, denied=True, captchaVisible=False)

    def test_missing_or_hidden_controls_are_not_ready(self):
        for mutation in ("document.querySelector('#base-header').remove()",
                         "document.querySelector('.main-filters').style.display='none'",
                         "document.querySelector('.range-select').remove()",
                         "document.querySelector('[name=month]').checked=false",
                         "document.querySelector('[name=year]').checked=false"):
            with self.subTest(mutation=mutation):
                self.load()
                self.driver.execute_script(mutation)
                self.check(ready=False, controlsReady=False, captchaVisible=False)

    def test_wrong_path_is_not_ready(self):
        self.load(path="/unrelated/")
        self.check(ready=False, sameOrigin=True, expectedPath=False, controlsReady=True)

    def test_wrong_origin_is_not_ready(self):
        self.load()
        self.check(expected_url=self.url.replace("127.0.0.1", "localhost"),
                   ready=False, sameOrigin=False, expectedPath=True, controlsReady=True)

    def test_loading_document_is_not_ready_even_with_controls(self):
        self.load()
        try:
            self.driver.execute_script("document.open(); document.write(arguments[0]);", CONTROLS)
            self.check(ready=False, readyState="loading", controlsReady=True, captchaVisible=False)
        finally:
            self.driver.execute_script("document.close();")

    def test_computed_visibility_is_kept_in_parser_snapshot_without_changing_live_page(self):
        from bs4 import BeautifulSoup
        from tests.test_mortgage_dom import monthly
        from pipeline.downloaders.nashdom_mortgage import BrowserClient
        from pipeline.downloaders.nashdom_mortgage_dom import parse_page
        document = monthly()
        document.select_one(".main-filters").wrap(document.new_tag("header", id="base-header"))
        document.head.append(BeautifulSoup('<style>.service-hidden{display:none}</style>', "html.parser"))
        document.body.append(BeautifulSoup(
            '<div id="captcha_root" class="service-hidden"><button>Verify</button></div>'
            '<div id="id_captcha_loaded" style="width:80px;height:40px">'
            '<button class="service-hidden">Подтвердите, что вы не робот</button>'
            '<canvas class="service-hidden"></canvas></div>'
            '<js-challenge-loader class="service-hidden"></js-challenge-loader>', "html.parser"))
        self.server.html = str(document).encode("utf-8")
        self.driver.get(self.url)
        self.check(ready=True, captchaVisible=False, loaderVisible=False)
        client = BrowserClient("unused")
        client.driver = self.driver
        snapshot = client._html()
        parsed = parse_page(snapshot, expected_region="msk", expected_period="2026-08")
        self.assertEqual(parsed["period"], "2026-08")
        self.assertFalse(self.driver.execute_script(
            "return document.querySelector('#captcha_root').hasAttribute('data-mortgage-visible')"))


if __name__ == "__main__":
    unittest.main()
