"""Disposable Debian tests; synthetic records never represent real dashboard data."""
from __future__ import annotations

import json
import os
import shutil
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
    if (os.environ.get("PARSER_DEBIAN_SMOKE") != "1"
            or ROOT != Path("/opt/parser-dashboard") or not Path("/.dockerenv").is_file()):
        raise RuntimeError("Only run inside the disposable Debian integration-test container")


def seed(name="DEBIAN SMOKE OLD"):
    import pandas as pd
    directory = ROOT / "data/raw/realty/nashdom"
    directory.mkdir(parents=True, exist_ok=True)
    row = {"УИН": "smoke", "Группа компаний": name, "Общая площадь": 20000,
           "Жилая площадь": 15000, "Строительный адрес": "Тестовый адрес",
           "Отрасли": "Жилые объекты", "Подтип объекта": "МКД", "Группировка": "Жилье", "Назначение": "Жилье"}
    with pd.ExcelWriter(directory / "monitoring_2_0_20260101.xlsx") as writer:
        pd.DataFrame([{**row, "Год ввода по Мосстату": 2025,
                       "Месяц ввода по Мосстату": "июнь",
                       "Дата ввода по Мосстату": pd.Timestamp("2025-06-01")}]).to_excel(
            writer, sheet_name="Реестр РВ", index=False)
        pd.DataFrame([{**row, "Срок выдачи РС": pd.Timestamp("2025-01-01"), "Год выдачи": 2025,
                       "Год ввода по графику": 2026,
                       "Действующее РС": "Да", "Стадия строительства": "Строится",
                       "Дата выдачи": pd.Timestamp("2025-01-01"),
                       "Статус объекта": "Строящийся"}]).to_excel(
            writer, sheet_name="Реестр ОКС", index=False)
        # Test-only monthly source: match the real named housing block/calendar
        # layout, rather than letting an absent history pass publication.
        from pipeline.data_access import MONTH_NAMES_RU
        history = pd.DataFrame([[None] * 18 for _ in range(5)])
        history.iloc[1, 5] = "Ввод жилья"
        history.iloc[2, 5] = "Год"
        history.iloc[2, 6:18] = MONTH_NAMES_RU
        history.iloc[3, 5] = 2011
        history.iloc[3, 6:18] = list(range(10, 22))
        history.to_excel(writer, sheet_name="Данные с 2011 года", header=False, index=False)
    # Full positive publication fixture. Approved manual histories are copied
    # from Git; every dynamic source below has explicit synthetic records.
    repository = Path(__file__).resolve().parents[3]
    for relative in ("data/raw/realty/linear_objects/linear_objects_2026-09-28.json",
                     "data/raw/realty/vvod/static_vvod_rs_2011_2025.xlsx",
                     "data/raw/realty/vvod/vvod.xlsx", "data/raw/realty/vvod/Stroi_111_2025.xls",
                     "data/raw/realty/vvod/emiss_34118_base.xls",
                     "data/raw/realty/escrow_manual/Наполняемость счетов.xlsx",
                     "data/derived/salary_2011_2012.csv", "data/derived/vds_msk_value_2011_2015.csv"):
        target = ROOT / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.resolve() != (repository / relative).resolve():
            shutil.copy2(repository / relative, target)
    from nashdom_checker import _build_kvart_xlsx
    kvart = []
    for region, label in (("rf", "Российская Федерация"), ("msk", "Город Москва")):
        market = {"наименование": name, "квартиры_тыс_шт": "0.1", "площадь_тыс_м²": "5",
                  "доля_1комн_%": "25", "доля_2комн_%": "25", "доля_3комн_%": "25", "доля_4+комн_%": "25"}
        apartments = {"all": {"count": "100", "area": "5"},
                      **{key: {"count": "25", "area": "1.25"} for key in ("ONE", "TWO", "THREE", "FOUR")}}
        kvart.append({"region_key": region, "region": label, "report_date": "01.01.2026",
                      "apartments": [{"тип": "Все квартиры", "количество_шт": "100", "площадь_тыс_м²": "5"},
                                     *[{"тип": kind, "количество_шт": "25", "площадь_тыс_м²": "1.25"}
                                       for kind in ("1-комн", "2-комн", "3-комн", "4+ комнатные")]],
                      "distribution": [{"диапазон": "30–40", "доля": "50"}], "developers": [market],
                      "regions": [{**market, "наименование": "Город Москва"}],
                      "apartments_per_dev": [{"наименование": name, "site_name": name, "monitoring_name": name,
                                              "apartments": apartments, "source_id": "synthetic-" + region, "api_verified": True}],
                      "per_dev_attempts": [{"site_name": name, "status": "ok"}],
                      "per_dev_coverage": {"expected": 1, "completed": 1, "missing": [], "complete": True}})
        from pipeline.downloaders.nashdom_kvart_api import urls
        details = [{"roomCountType": kind, "flatCount": 25, "area": 1250, "percent": 25}
                   for kind in ("ONE", "TWO", "THREE", "FOUR")]
        kvart[-1]["developer_api"] = {"region_key": region, "urls": urls(region), "report_date": "01.01.2026",
            "dictionary": [{"id": "synthetic-" + region, "name": name}],
            "aggregate": [{"id": "synthetic-" + region, "flatCount": 100, "area": 5000, "details": details}],
            "regions": [{"id": 77, "flatCount": 100, "area": 5000, "details": details}],
            "region_dictionary": [{"name": "Центральный ФО", "regions": [{"id": 77, "name": "Город Москва"}]}]}
        from pipeline.downloaders.nashdom_kvart_api import annotate_apartment_totals, annotate_measurements, resolve, resolve_regions, validate_apartment_totals
        annotate_measurements(kvart[-1]["developers"], resolve(kvart[-1]["developers"], kvart[-1]["developer_api"]))
        annotate_measurements(kvart[-1]["regions"], resolve_regions(kvart[-1]["regions"], kvart[-1]["developer_api"]))
        annotate_apartment_totals(kvart[-1]["apartments"], validate_apartment_totals(kvart[-1]["apartments"], kvart[-1]["developer_api"]))
    (directory / "kvartirografia_20260101.json").write_text(json.dumps(kvart, ensure_ascii=False), encoding="utf-8")
    _build_kvart_xlsx(kvart, directory / "kvartirografia_20260101.xlsx")
    construction = {"construction": [], "sales": []}
    kpi, segments = [], []
    for region in ("rf", "msk"):
        for kind in ("living", "total"):
            construction["construction"].append({"region_key": region, "report_date": "01.01.2026",
                "report_period": "Январь 2026", "area_kind": kind, "area_thousand_m2": 20})
        construction["sales"].append({"region_key": region, "report_date": "01.01.2026",
            "report_period": "Январь 2026", "metrics": {"total_living_thousand_m2": 20,
                "sales_open_thousand_m2": 16, "sold_thousand_m2": 10, "sold_pct": 50,
                "unsold_thousand_m2": 6, "unsold_pct": 30, "sales_not_open_thousand_m2": 4,
                "sales_not_open_pct": 20, "price_per_m2_rub": 300000, "funds_million_rub": 2500}})
        for metric, value in (("Объем жилищного строительства", 20), ("Распроданность", 50),
                              ("Стройготовность", 40), ("Отношение распроданности к стройготовности", 125)):
            kpi.append({"region_key": region, "year": 2026, "month": 1, "название": metric,
                        "значение": value, "единица": "тыс. м²" if metric == "Объем жилищного строительства" else "%",
                        "report_period": "Январь 2026", "month_name": "Январь"})
        segments.append({"region_key": region, "year": 2026, "month": 1, "наименование": name,
                         "Объем жил. строительства": 20, "Распроданность": 50,
                         "Стройготовность": 40, "Отношение распроданности к стройготовности": 125})
    (directory / "construction_operational_20260101.json").write_text(json.dumps(construction), encoding="utf-8")
    from pipeline.data_access import RASPROD_TABLE_SHEETS
    with pd.ExcelWriter(directory / "rasprodannost_20260101.xlsx") as writer:
        pd.DataFrame(kpi).to_excel(writer, sheet_name="kpi", index=False)
        for key in RASPROD_TABLE_SHEETS:
            pd.DataFrame(segments).to_excel(writer, sheet_name=key, index=False)
    from pipeline.rasprod_contract import TABLES
    history = [{"region_key": region, "year": 2026, "month_num": 1, "api_verified": True,
                "kpi": [{key: row[key] for key in ("название", "значение", "единица")}
                        for row in kpi if row["region_key"] == region],
                "tables": {section: [{key: value for key, value in row.items() if key not in {"region_key", "year", "month"}}
                                    for row in segments if row["region_key"] == region] for section in TABLES.values()}}
               for region in ("rf", "msk")]
    sales_json = directory / "rasprodannost_20260101.json"
    sales_json.write_text(json.dumps(history), encoding="utf-8")
    from nashdom_checker import _write_rasprod_coverage
    expected = {(region, 2026, 1) for region in ("rf", "msk")}
    _write_rasprod_coverage(sales_json, sales_json.with_suffix(".xlsx"), expected, expected)
    erz = ROOT / "data/raw/realty/erzrf"
    erz.mkdir(parents=True, exist_ok=True)
    for region in ("rf", "msk"):
        for key, metric in (("obyem_stroitelstva", "Строится, м²"), ("obyem_vvoda", "Введено, м²"),
                            ("nakopl_vvod", "Введено, м²"), ("potreb_kachestva", "Средняя оценка"),
                            ("skorost", "Скорость строительства, дней/дом")):
            pd.DataFrame([{"Наименование застройщика": name, "Место": 1, metric: 15000,
                           "Рейтинг ЕРЗ": 5}]).to_excel(erz / f"top_{key}_{region}_20260101.xlsx", index=False)
    card_url = "https://erzrf.ru/zastroyschiki/brand/synthetic"
    card_targets = {"region": "rf", "selection": {"region": "rf", "sorting": "obyem_stroitelstva",
        "url": "https://erzrf.ru/top-zastroyshchikov/rf?topType=0&regionKey=0"},
        "developers": [{"place": "1", "name": name, "card_url": card_url}]}
    target_file = erz / "top_developers_rf_20260101.json"
    target_file.write_text(json.dumps(card_targets, ensure_ascii=False), encoding="utf-8")
    workbook = erz / "cards_20260101.xlsx"
    pd.DataFrame([{"name_card": name, "slug": "brand/synthetic", "source_id": "brand/synthetic", "url": card_url,
                   "page_url": card_url, "regions_count": 2,
                   "Строится_м²": 15000, "Сдано_2025_м²": 12000, "Перенос_2025_м²": 100}]).to_excel(
        workbook, sheet_name="cards", index=False)
    from pipeline.erz_cards_contract import target_keys, validate_workbook
    from pipeline.source_records import file_sha256
    expected = target_keys(card_targets["developers"])
    _, confirmed = validate_workbook(workbook, expected)
    workbook.with_suffix(".xlsx.provenance.json").write_text(json.dumps({
        "schema_version": 1, "complete": True, "limited": False, "failed": [],
        "targets_file": target_file.name, "targets_sha256": file_sha256(target_file),
        "selection": card_targets["selection"], "expected": expected, "confirmed": confirmed,
        "sha256": file_sha256(workbook)}), encoding="utf-8")
    processed = ROOT / "data/processed"
    processed.mkdir(parents=True, exist_ok=True)
    downloads = ROOT / "downloads"
    downloads.mkdir(exist_ok=True)
    sources = {"avg_salary": "Среднемесячная номинальная начисленная заработная плата_fixture.xlsx",
               "ipc": "Индексы потребительских цен_часть1_fixture.xlsx",
               "gdp_rf": "VVP_god_fixture.xlsx", "gdp_per_capita_rf": "VVP_na_dushu_fixture.xlsx",
               "vrp_msk": "ВРП с 1998_fixture.xlsx", "vds_msk": "ВДС годы ОКВЭД2_2016_fixture.xlsx",
               "vds_rf": "VDS_god_OKVED2_s2011_fixture.xlsx", "vds_msk_legacy": "VRP_OKVED2007_fixture.xlsx"}
    metrics = {"gdp_rf": ["gdp_total", "gdp_index"], "gdp_per_capita_rf": ["gdp_pc_total", "gdp_pc_index"],
               "vrp_msk": ["vrp_total", "vrp_per_capita", "vrp_index", "vrp_per_capita_index"],
               "vds_msk": ["vds_value", "vds_index", "vds_structure"],
               "vds_rf": ["vds_value", "vds_index", "vds_structure"], "vds_msk_legacy": ["vds_structure"]}
    for identifier, source in sources.items():
        pd.DataFrame([{"synthetic": True, "value": 1}]).to_excel(downloads / source, index=False)
        from pipeline.download_provenance import record_download
        if identifier in {"avg_salary", "ipc"}:
            source_id = "57824" if identifier == "avg_salary" else "31074_часть1"
            record_download(downloads / source, source_id=source_id)
            extra = ("Среднемесячная номинальная начисленная заработная плата_history_fixture.xlsx"
                     if identifier == "avg_salary" else "Индексы потребительских цен_часть2_fixture.xlsx")
            pd.DataFrame([{"synthetic": True, "value": 1}]).to_excel(downloads / extra, index=False)
            record_download(downloads / extra, source_id="43246" if identifier == "avg_salary" else "31074_часть2")
        rows = []
        populations = ("Москва", "Российская Федерация") if identifier in {"avg_salary", "ipc"} else (
            "Москва" if identifier in {"vrp_msk", "vds_msk", "vds_msk_legacy"} else "Российская Федерация",)
        for region in populations:
            for metric in metrics.get(identifier, [identifier]):
                for view in ("Всего", "Строительство"):
                    rows.append(dict(indicator_id=identifier, section="national_accounts", year=2025, month=1, quarter=1,
                        region=region, view=view, period_type="month_to_month" if identifier == "ipc" else "month" if identifier == "avg_salary" else "year",
                        metric=metric, value=100.5, unit="%", source_file=source, index_base="month_to_month"))
        if identifier in {"avg_salary", "ipc"}:
            rows += [{**row, "source_file": extra, "year": 2024} for row in rows]
        pd.DataFrame(rows).to_pickle(processed / f"{identifier}.pkl")
    for part in (1, 2):
        source = downloads / f"Введено в действие общей площади жилых домов_часть{part}_fixture.xlsx"
        years = (2020, 2021) if part == 1 else (2024, 2025)
        pd.DataFrame([["Регион", "Категория", *years], ["", "", "январь-декабрь", "январь-декабрь"],
                      ["Российская Федерация", "построенные населением", 200, 300],
                      ["Москва", "построенные населением", 2, 3]]).to_excel(
            source, sheet_name="Данные", header=False, index=False)
        record_download(source, source_id=f"34118_часть{part}")


def unit():
    import unittest
    # Historical parity suites need the old exports intentionally excluded from Git.
    # Independent contract tests plus the live service checks below run on clean clones.
    names = ["backend.tests.test_apartments.CalculationTests", "backend.tests.test_apartments.ServiceAndHttpTests",
             "backend.tests.test_economics.ServiceAndHttpTests", "backend.tests.test_generation",
             "backend.tests.test_map", "backend.tests.test_linear", "backend.tests.test_sales.CalculationTests",
             "backend.tests.test_sales.ServiceTests", "backend.tests.test_updates", "backend.tests.test_tdm",
             "backend.tests.test_operational.HistoricalMonthTests", "backend.tests.test_mart_freshness"]
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
