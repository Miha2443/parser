"""Disposable Debian tests; synthetic records never represent real dashboard data."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))


def guard():
    if os.environ.get("PARSER_DEBIAN_SMOKE") != "1" or ROOT != Path("/opt/parser-dashboard"):
        raise RuntimeError("Only run inside the disposable Debian integration-test container")


def seed(name="DEBIAN SMOKE OLD"):
    import pandas as pd
    directory = ROOT / "data/raw/realty/nashdom"
    directory.mkdir(parents=True, exist_ok=True)
    row = {"УИН": "smoke", "Группа компаний": name, "Общая площадь": 20000,
           "Жилая площадь": 15000, "Строительный адрес": "Тестовый адрес",
           "Отрасли": "Жилые объекты", "Группировка": "Жилье", "Назначение": "Жилье"}
    with pd.ExcelWriter(directory / "monitoring_2_0_20260101.xlsx") as writer:
        pd.DataFrame([{**row, "Год ввода по Мосстату": 2025,
                       "Месяц ввода по Мосстату": "июнь",
                       "Дата ввода по Мосстату": pd.Timestamp("2025-06-01")}]).to_excel(
            writer, sheet_name="Реестр РВ", index=False)
        pd.DataFrame([{**row, "Срок выдачи РС": pd.Timestamp("2025-01-01"),
                       "Год ввода по графику": 2026,
                       "Действующее РС": "Да", "Стадия строительства": "Строится",
                       "Дата выдачи": pd.Timestamp("2025-01-01"),
                       "Статус объекта": "Строящийся"}]).to_excel(
            writer, sheet_name="Реестр ОКС", index=False)
    processed = ROOT / "data/processed"
    processed.mkdir(parents=True, exist_ok=True)
    for identifier, unit, value in (("avg_salary", "руб", 100000.0), ("ipc", "%", 100.5)):
        pd.DataFrame([dict(year=2025, month=1, quarter=1, region="Город Москва", view="Всего",
                           period_type="month", metric=identifier, value=value, unit=unit,
                           source_file="synthetic-debian-fixture.xlsx", index_base="month_to_month")]).to_pickle(
            processed / f"{identifier}.pkl")


def unit():
    import unittest
    # Historical parity suites need the old exports intentionally excluded from Git.
    # Independent contract tests plus the live service checks below run on clean clones.
    names = ["backend.tests.test_apartments.CalculationTests", "backend.tests.test_apartments.ServiceAndHttpTests",
             "backend.tests.test_economics.ServiceAndHttpTests", "backend.tests.test_generation",
             "backend.tests.test_map", "backend.tests.test_linear", "backend.tests.test_sales.CalculationTests",
             "backend.tests.test_sales.ServiceTests", "backend.tests.test_updates", "backend.tests.test_tdm",
             "backend.tests.test_operational.HistoricalMonthTests"]
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromNames(names))
    if not result.wasSuccessful():
        raise SystemExit(1)


def browser():
    import fedstat_checker
    import nashdom_checker  # noqa: F401
    import erzrf_checker  # noqa: F401
    import rosstat_checker  # noqa: F401
    from pipeline.selenium_utils import create_chrome, wait_for_download

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/file":
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Disposition", 'attachment; filename="linux-download.txt"')
                self.end_headers()
                self.wfile.write(b"debian-download-ok")
            else:
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(b'<title>Debian browser</title><a id="download" href="/file">Download</a>')

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        for factory in (create_chrome, fedstat_checker.create_driver):
            with tempfile.TemporaryDirectory() as directory:
                driver = factory(Path(directory))
                try:
                    driver.get(f"http://127.0.0.1:{server.server_port}/")
                    assert driver.title == "Debian browser"
                    driver.find_element("id", "download").click()
                    path = wait_for_download(Path(directory), before_snapshot=set(), timeout=20)
                    assert path and path.read_bytes() == b"debian-download-ok", path
                    print(f"Browser and download OK: {factory.__module__}")
                finally:
                    driver.quit()
    finally:
        server.shutdown()
        server.server_close()


def source():
    """Probe one live collector without mixing real records into the fixtures."""
    import nashdom_checker
    import pandas as pd

    with tempfile.TemporaryDirectory(prefix="debian-live-source-") as directory:
        nashdom_checker.DOWNLOAD_DIR = Path(directory)
        nashdom_checker.MONITORING_CHANGES_LOG = Path(directory) / "changes.jsonl"
        files, changed = nashdom_checker.fetch_monitoring_2_0({})
        assert files and changed, "The live Monitoring export could not be downloaded/validated"
        with pd.ExcelFile(files[0]) as book:
            counts = {sheet: len(book.parse(sheet)) for sheet in ("Реестр РВ", "Реестр ОКС")}
        assert all(counts.values()), counts
        print("Live Monitoring download and validation on Debian succeeded:", counts)


def get(path):
    import requests
    response = requests.get("http://127.0.0.1:8080" + path, timeout=10)
    response.raise_for_status()
    return response


def wait_api(name):
    for _ in range(60):
        try:
            data = get("/api/v1/catalog").json()
            if any(d["developer"] == name for d in data["controls"]["developers"]):
                return data
        except Exception:
            pass
        time.sleep(0.5)
    raise AssertionError(f"Published API generation not available: {name}")


def api():
    data = wait_api("DEBIAN SMOKE OLD")
    assert get("/api/v1/health").json()["status"] == "ok"
    assert '<div id="root"' in get("/home").text
    assert get("/api/v1/profile?developer=debian+smoke+old&region=msk").json()["profiles"]
    assert get("/api/v1/profile/export?developer=debian+smoke+old&region=msk").content[:2] == b"PK"
    for path in ("/api/v1/map/catalog", "/api/v1/commissioning/annual/catalog", "/api/v1/commissioning/operational/catalog",
                 "/api/v1/construction/catalog", "/api/v1/economics/salary/catalog",
                 "/api/v1/economics/ipc/catalog", "/api/v1/economics/accounts/catalog", "/api/v1/updates"):
        get(path)
    from pipeline.selenium_utils import create_chrome
    from selenium.webdriver.support.ui import WebDriverWait
    driver = create_chrome()
    try:
        driver.get("http://127.0.0.1:8080/home")
        WebDriverWait(driver, 20).until(lambda d: "Рынок недвижимости" in d.find_element("tag name", "body").text)
        driver.save_screenshot(str(ROOT / "logs/debian-home.png"))
        driver.get("http://127.0.0.1:8080/?developer=debian+smoke+old&region=msk")
        WebDriverWait(driver, 20).until(lambda d: d.find_elements("css selector", ".chart-canvas canvas"))
        driver.save_screenshot(str(ROOT / "logs/debian-profile.png"))
        print("React home and profile charts rendered in Debian Chromium")
    finally:
        driver.quit()
    print("Nginx, catalog, profile, Excel and dashboard API routes OK:", data["version"])


def update():
    # Network payloads are simulated; mart building and systemd publication are real.
    time.sleep(6)
    seed("DEBIAN SMOKE NEW")
    subprocess.run([sys.executable, "-m", "pipeline.build_realty_marts", "--strict"],
                   cwd=ROOT, check=True)


def refresh():
    before = (ROOT / "live").resolve()
    subprocess.run(["systemctl", "start", "--no-block", "parser-update.service"], check=True)
    observations = []
    for _ in range(60):
        try:
            observations.append(get("/api/v1/catalog").json())
        except Exception:
            if (ROOT / "live").resolve() == before:
                raise
            break
        if (ROOT / "live").resolve() != before:
            break
        time.sleep(0.2)
    assert any(any(d["developer"] == "DEBIAN SMOKE OLD" for d in p["controls"]["developers"])
               for p in observations), "Old generation disappeared while the updater was working"
    after = wait_api("DEBIAN SMOKE NEW")
    assert (ROOT / "live").resolve() != before
    assert subprocess.check_output(["systemctl", "show", "parser-update.service", "--property=Result", "--value"],
                                   text=True).strip() == "success"
    print("Old generation served throughout refresh; new generation published:", after["version"])


def publication_failure():
    live = (ROOT / "live").resolve()
    manifest = ROOT / "data/marts/realty/manifest.json"
    original = manifest.read_bytes()
    broken = json.loads(original)
    broken["marts"]["monitoring_2_0"]["error"] = "synthetic publication failure"
    try:
        manifest.write_text(json.dumps(broken), encoding="utf-8")
        result = subprocess.run(["bash", "deploy/linux/publish-data.sh"], cwd=ROOT)
        assert result.returncode != 0
        assert (ROOT / "live").resolve() == live
        wait_api("DEBIAN SMOKE NEW")
    finally:
        manifest.write_bytes(original)
    print("Failed data validation preserves the published generation")


if __name__ == "__main__":
    guard()
    {"browser": browser, "source": source, "seed": seed, "unit": unit, "api": api, "update": update,
     "refresh": refresh, "publication_failure": publication_failure}[sys.argv[1]]()
