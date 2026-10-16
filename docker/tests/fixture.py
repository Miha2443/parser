"""Synthetic network payloads in isolated Compose CI; real mart/publication code."""
import json
import os
from pathlib import Path
import sys
import time
import uuid
from datetime import datetime


def guard():
    if (os.environ.get("PARSER_COMPOSE_SMOKE") != "1"
            or Path.cwd() != Path("/app") or not Path("/.dockerenv").is_file()):
        raise RuntimeError("Fixture requires a disposable Compose test container")


def seed(name="COMPOSE OLD"):
    from deploy.linux.tests import smoke
    smoke.ROOT = Path("/app")
    smoke.seed(name)


def seed_no_erz():
    guard()
    seed("COMPOSE NO ERZ")
    for file in Path("/app/data/raw/realty/erzrf").iterdir():
        if file.is_symlink() or not file.is_file():
            raise RuntimeError("Unexpected fixture ERZ path")
        file.unlink()


def check_no_erz():
    guard()
    from urllib.request import urlopen
    from urllib.parse import urlencode
    from docker.coverage import validate_pages
    def get(route):
        with urlopen("http://backend:8000" + route, timeout=20) as response:
            return json.load(response)
    assert get("/api/v1/health")["status"] == "ok"
    catalog = get("/api/v1/catalog")
    for family in ("erzrf_top", "erzrf_cards"):
        assert catalog["provenance"]["sources"][family]["availability"] == "unavailable"
    selected = catalog["controls"]["developers"][0]
    profile = get("/api/v1/profile?" + urlencode({"developer": selected["developerKey"], "region": "msk"}))["profiles"][0]
    assert all(row["valueM2"] is None for row in profile["delays"]["cards"])
    validate_pages(get)


def update():
    from docker import publication
    from docker.publication import publish, require_success, SOURCES
    started = time.time()
    seed("COMPOSE NEW")
    if os.environ.get("FIXTURE_FAIL") == "missing-source":
        next(Path("/app/data/raw/realty/nashdom").glob("construction_operational_*.json")).unlink()
    time.sleep(6)  # Old API must survive an already-overwritten mutable workbook.
    status = dict(run_id=uuid.uuid4().hex, status="success", current_stage="finished",
                  started_at=datetime.fromtimestamp(started).isoformat(),
                  finished_at=datetime.now().isoformat(), sources_requested=sorted(SOURCES),
                  successes=sorted(SOURCES), failures=[], status_write_errors=[], error="",
                  marts_ok=True, processed_ok=True)
    from pipeline.source_availability import outcome
    status["source_outcomes"] = {alias: outcome(alias, status["run_id"], "success") for alias in SOURCES}
    if os.environ.get("FIXTURE_FAIL") == "1":
        status["successes"].remove("fedstat")
    require_success(status, None, started, expected_sources=SOURCES)
    if os.environ.get("FIXTURE_FAIL") == "empty-mart":
        from unittest.mock import patch
        original = publication.build_candidate
        def tamper_after_build(candidate):
            original(candidate)
            import pandas as pd
            from pipeline.build_realty_marts import _shape_summary
            pd.to_pickle({}, candidate / "data/marts/realty/kvartirografia.pkl")
            manifest_path = candidate / "data/marts/realty/manifest.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["marts"]["kvartirografia"]["summary"] = _shape_summary({})
            manifest_path.write_text(json.dumps(manifest))
        with patch.object(publication, "build_candidate", side_effect=tamper_after_build):
            publish(Path("/app"), Path("/runtime"), run_status=status, collector_returncode=0)
    else:
        publish(Path("/app"), Path("/runtime"), run_status=status, collector_returncode=0)


def old_releases():
    for day in range(1, 4):
        name = f"200001{day:02d}T060002-{'b' * 12}"
        folder = Path("/runtime/releases") / name
        folder.mkdir()
        (folder / "old-input.xlsx").write_bytes(name.encode())


def check_archives():
    from docker.publication import require_target
    runtime = Path("/runtime")
    reserve = json.loads((runtime / "previous-good.json").read_text())
    for target in (os.readlink(runtime / "live"), reserve["previous"]):
        require_target(runtime, target)
    for day in range(1, 4):
        name = f"200001{day:02d}T060002-{'b' * 12}"
        assert not (runtime / "releases" / name).exists()
        assert (runtime / "_archive/releases" / name / "old-input.xlsx").read_bytes() == name.encode()
        record = json.loads((runtime / "_archive/releases" / (name + ".json")).read_text())
        assert record["status"] == "archived"


def recovery_fixture(kind):
    """Create crash boundaries only in the explicitly disposable CI runtime."""
    import shutil
    from docker.publication import live_target, swap
    runtime = Path("/runtime")
    current = live_target(runtime)
    state = runtime / ".fixture-recovery.json"
    if kind == "clear":
        original = json.loads(state.read_text())["original"]
        swap(runtime, original)
        (runtime / "pending.json").unlink(missing_ok=True)
        state.unlink()
        return
    state.write_text(json.dumps({"original": current}))
    if kind == "next":
        reserve = runtime / "previous-good.json"
        previous = json.loads(reserve.read_text()).get("previous", "") if reserve.exists() else ""
        next_target = current
    else:
        next_target = "releases/fixture-" + uuid.uuid4().hex
        shutil.copytree(runtime / current, runtime / next_target, copy_function=shutil.copy2)
        previous = "" if kind == "empty" else current
        if kind != "unrelated":
            swap(runtime, next_target)
        else:
            previous = "releases/fixture-" + uuid.uuid4().hex
            shutil.copytree(runtime / current, runtime / previous, copy_function=shutil.copy2)
    (runtime / "pending.json").write_text(json.dumps({"previous": previous, "next": next_target}))


def browser_dom_contract(driver):
    # Actual DOM regression: closed filters change CSS class after selection;
    # the regional chart can render before the developer chart.
    import nashdom_checker as checker
    from urllib.parse import quote
    html = '''<html><body>
      <p>Федеральный округ / Регион</p><div><span class="css-203ko5">Город Москва</span></div>
      <p>Девелопер</p><div><span class="css-wjcl1w">Все</span></div>
      <h2>Объём строительства по регионам</h2>
      <div class="list" style="height:100px;overflow:auto"><button class="selected-region"><div>Город Москва</div><div>10</div><div>20</div></button></div>
    </body></html>'''
    driver.get("data:text/html;charset=utf-8," + quote(html))
    assert checker._get_current_region_label(driver) == "Город Москва"
    assert checker._clear_developer_filter(driver)
    assert not checker._kvart_lists_ready(driver)
    driver.execute_script('''document.body.insertAdjacentHTML('beforeend',
      '<h2>Объём строительства по девелоперам</h2><div class="list" style="height:100px;overflow:auto"><button class="css-5nggi1"><div>Fixture developer</div><div>1</div><div>2</div></button></div>');''')
    assert checker._kvart_lists_ready(driver)
    developers = checker._scroll_collect_list(driver, 0)
    assert [row["наименование"] for row in developers] == ["Fixture developer"]
    regions = checker._scroll_collect_list(driver, 1)
    assert [row["наименование"] for row in regions] == ["Город Москва"]
    driver.execute_script('''document.body.insertAdjacentHTML('beforeend',
      '<div style="display:none"><input id="hidden-region-search" placeholder="Поиск по названию" value="Москва"><div onclick="document.body.dataset.choice=\\'hidden\\'"><span class="css-1ogbkfc">Fixture developer</span></div></div>'+
      '<div style="position:fixed;top:20px;left:20px;width:300px;height:150px;background:white"><input id="visible-developer-search" placeholder="Поиск по названию"><div onclick="document.body.dataset.choice=\\'developer\\'"><span class="css-1ogbkfc">Fixture developer</span></div></div>');''')
    assert checker._kvart_popup_action(driver, "ready")
    assert checker._kvart_popup_action(driver, "search", "Fixture developer")
    assert driver.execute_script("return document.querySelector('#hidden-region-search').value") == "Москва"
    assert driver.execute_script("return document.querySelector('#visible-developer-search').value") == "Fixture developer"
    assert checker._kvart_popup_action(driver, "click", "Fixture developer")
    assert driver.execute_script("return document.body.dataset.choice") == "developer"

def erz_top_dom_contract(driver):
    """Execute the collector JS against sanitized markup from a real failure."""
    import erzrf_checker as checker
    from urllib.parse import quote
    html = (Path(__file__).parent / "fixtures/erzrf_top_20261016.html").read_text(encoding="utf-8")
    driver.get("data:text/html;charset=utf-8," + quote(html))
    rows = checker._scrape_developers_from_table(driver)
    assert len(rows) == 20
    assert [row["place"] for row in rows] == [str(index) for index in range(1, 21)]
    assert all(len(row["cells"]) == 15 for row in rows)
    assert rows[0]["name"] == "ГК Самолет"
    assert "/zastroyschiki/brand/" in rows[0]["card_url"]
    assert checker._visible_metric(rows[0]) == 4532580
    assert checker._visible_metric(rows[-1]) == 776687
    assert all("Строится" not in row["cells"][3] for row in rows)
    assert checker._wait_for_top_content(driver, timeout=2)
    selection = checker._read_top_selection(driver)
    assert checker._top_ui_count(selection)[0] == 2954
    assert checker._sorting_control(selection)[1] == checker.TOP_TYPES
    # The global site region is Moscow, while the TOP/filter regions are RF.
    assert next(s for s in selection["selects"] if s["id"] == "select7")["value"] == "143443001"
    assert all(s["value"] == "0" for s in selection["selects"] if s["id"] in {"select1", "select4"})
    digest = checker._top_table_digest(driver)
    driver.execute_script("document.querySelector('li.developer .developer-td-4 b').textContent='4 532 581'")
    assert checker._top_table_digest(driver) != digest
    # DOM order and hidden template cells must not change column identities.
    driver.execute_script('''const row=document.querySelector('li.developer .progress__wrap');
      const metric=row.querySelector('.developer-td-4'); row.prepend(metric);
      const hidden=metric.cloneNode(true); hidden.style.display='none'; row.append(hidden);''')
    rows = checker._scrape_developers_from_table(driver)
    assert len(rows[0]["cells"]) == 15 and checker._visible_metric(rows[0]) == 4532581
    driver.execute_script("document.querySelector('li.developer').style.display='none'")
    assert len(checker._scrape_developers_from_table(driver)) == 19
    assert not checker._wait_for_top_content(driver, timeout=0)
    driver.get("data:text/html;charset=utf-8," + quote(html))
    driver.execute_script("document.querySelector('li.developer .developer-td-1').remove()")
    assert checker._scrape_developers_from_table(driver)[0]["place"] == ""
    assert not checker._wait_for_top_content(driver, timeout=0)
    driver.get("data:text/html;charset=utf-8," + quote('''<table><tr><th>Место</th><th>Изменение</th>
      <th>Застройщик</th><th>Строится</th></tr><tr><td>1</td><td>0</td>
      <td><a href="https://erzrf.ru/zastroyschiki/brand/fixture-1">Fixture</a></td><td>125</td></tr></table>'''))
    rows = checker._scrape_developers_from_table(driver)
    assert len(rows) == 1 and checker._visible_metric(rows[0]) == 125
    print("ERZ actual Angular TOP DOM: 20 rows, ranks, metrics, selectors and rejection checks passed")


def browser():
    from deploy.linux.tests import smoke
    smoke.browser()
    # Keep provider transport/export verification independent of dashboard UI
    # timing: a slow unrelated chart must not hide the ERZ replay result.
    from docker.tests.erz_browser import check_download_replay, check_provider_replay
    check_provider_replay()
    check_download_replay()
    from pipeline.selenium_utils import create_chrome
    from selenium.webdriver.support.ui import WebDriverWait
    driver = create_chrome()
    try:
        browser_dom_contract(driver)
        erz_top_dom_contract(driver)
        from docker.tests.erz_browser import check_matrix
        check_matrix(driver)
        driver.get("http://web:8080/home")
        WebDriverWait(driver, 25).until(lambda d: "Рынок недвижимости" in d.find_element("tag name", "body").text)
        assert driver.execute_script("return document.documentElement.dataset.theme") == "light"
        assert not driver.find_elements("css selector", ".theme-button")
        driver.execute_script("localStorage.setItem('dashboard.theme','dark')")
        # Check the HTML prepaint before scripts execute, including legacy storage.
        driver.execute_cdp_cmd("Emulation.setScriptExecutionDisabled", {"value": True})
        driver.get("http://web:8080/apartments")
        assert driver.execute_script("return document.documentElement.dataset.theme") == "light"
        driver.execute_cdp_cmd("Emulation.setScriptExecutionDisabled", {"value": False})
        driver.refresh()
        WebDriverWait(driver, 25).until(lambda d: d.find_elements("css selector", ".chart-canvas canvas"))
        assert driver.execute_script("return localStorage.getItem('dashboard.theme')") == "light"
        driver.set_window_size(390, 844)
        driver.get("http://web:8080/sales")
        WebDriverWait(driver, 25).until(lambda d: d.find_elements("css selector", ".chart-canvas canvas"))
        assert driver.execute_script("return document.documentElement.dataset.theme") == "light"
        assert driver.execute_script("return document.documentElement.scrollWidth <= innerWidth + 1")
        driver.set_window_size(1440, 1000)
        for route in ("/apartments", "/commissioning/operational", "/economics/salary", "/economics/ipc", "/economics/accounts"):
            driver.get("http://web:8080" + route)
            WebDriverWait(driver, 25).until(lambda d: d.find_elements("css selector", ".sources-section > details"))
            if route.startswith("/economics/"):
                # Evidence renders from the catalog before the report's charts
                # arrive. Clicking while those charts are inserted can move
                # the summary out from under the pointer (accounts has six).
                WebDriverWait(driver, 25).until(lambda d:
                    not d.find_elements("css selector", ".economics-chart-loading") and
                    len(d.find_elements("css selector", ".economics-chart-section .chart-canvas canvas")) ==
                    len(d.find_elements("css selector", ".economics-chart-section")) > 0)
            disclosure = driver.find_element("css selector", ".sources-section > details")
            assert not disclosure.get_attribute("open")
            assert not driver.find_elements("css selector", ".page-heading .snapshot-label")
            source_date = disclosure.find_element("css selector", "p")
            assert not source_date.is_displayed()
            assert driver.execute_script("return !document.querySelector('.sources-section').nextElementSibling?.querySelector('.chart-canvas')")
            summary = disclosure.find_element("css selector", "summary")
            driver.execute_script("arguments[0].scrollIntoView({block:'center', behavior:'instant'})", summary)
            summary.click()
            try:
                WebDriverWait(driver, 10).until(lambda d: disclosure.get_attribute("open") and source_date.is_displayed())
            except Exception as exc:
                driver.save_screenshot("/app/logs/compose-sources-failed.png")
                Path("/app/logs/compose-sources-failed.html").write_text(driver.page_source, encoding="utf-8")
                raise AssertionError(f"Sources disclosure did not open after click: route={route}, url={driver.current_url}, "
                                     f"open={disclosure.get_attribute('open')}, date_visible={source_date.is_displayed()}") from exc
            assert driver.execute_script("return document.documentElement.dataset.theme") == "light"
            if route == "/apartments":
                driver.find_element("css selector", ".chart-data .data-toggle").click()
                cells = driver.find_elements("css selector", ".chart-data-panel td")
                import re
                assert any(re.fullmatch(r"[\d\s\u00a0]+,\d{2}", cell.text) for cell in cells)
            if route == "/commissioning/operational":
                result = driver.execute_async_script('''const done=arguments[arguments.length-1];
                  fetch('/api/v1/commissioning/operational').then(r=>r.json()).then(done);''')
                for table in result["tables"]:
                    for index, year in enumerate(table["chart"]["x"]):
                        row = next(row for row in table["rows"] if str(row["Год"]) == year)
                        if row["За выбранный период, млн м²"] is None:
                            assert table["chart"]["period"][index]["value"] is None
                            if row["За год, млн м²"] is not None:
                                assert table["chart"]["remainder"][index]["value"] == row["За год, млн м²"]
        driver.get("http://web:8080/?developer=compose+new&region=msk")
        WebDriverWait(driver, 25).until(lambda d: d.find_elements("css selector", ".chart-canvas canvas"))
        driver.save_screenshot("/app/logs/compose-profile.png")
    finally:
        driver.quit()


if __name__ == "__main__":
    guard()
    if sys.argv[1] == "recovery":
        recovery_fixture(sys.argv[2])
        sys.exit(0)
    {"seed": seed, "seed-no-erz": seed_no_erz, "check-no-erz": check_no_erz, "update": update, "browser": browser,
     "old-releases": old_releases, "check-archives": check_archives}[sys.argv[1]]()
