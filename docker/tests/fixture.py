"""Synthetic network payloads in isolated Compose CI; real mart/publication code."""
import json
import os
from pathlib import Path
import subprocess
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


def update():
    from docker.publication import publish, require_success, SOURCES
    started = time.time()
    seed("COMPOSE NEW")
    if os.environ.get("FIXTURE_FAIL") == "missing-source":
        next(Path("/app/data/raw/realty/nashdom").glob("construction_operational_*.json")).unlink()
    time.sleep(6)  # Old API must survive an already-overwritten mutable workbook.
    subprocess.run([sys.executable, "-m", "pipeline.build_realty_marts", "--strict"], check=True)
    if os.environ.get("FIXTURE_FAIL") == "empty-mart":
        import pandas as pd
        from pipeline.build_realty_marts import _shape_summary
        pd.to_pickle({}, Path("/app/data/marts/realty/kvartirografia.pkl"))
        manifest_path = Path("/app/data/marts/realty/manifest.json")
        manifest = json.loads(manifest_path.read_text())
        manifest["marts"]["kvartirografia"]["summary"] = _shape_summary({})
        manifest_path.write_text(json.dumps(manifest))
    status = dict(run_id=uuid.uuid4().hex, status="success", current_stage="finished",
                  started_at=datetime.fromtimestamp(started).isoformat(),
                  finished_at=datetime.now().isoformat(), sources_requested=sorted(SOURCES),
                  successes=sorted(SOURCES), failures=[], status_write_errors=[], error="",
                  marts_ok=True, processed_ok=True)
    if os.environ.get("FIXTURE_FAIL") == "1":
        status["successes"].remove("fedstat")
    require_success(status, None, started)
    publish(Path("/app"), Path("/runtime"))


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

def browser():
    from deploy.linux.tests import smoke
    smoke.browser()
    from pipeline.selenium_utils import create_chrome
    from selenium.webdriver.support.ui import WebDriverWait
    driver = create_chrome()
    try:
        browser_dom_contract(driver)
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
            disclosure = driver.find_element("css selector", ".sources-section > details")
            assert not disclosure.get_attribute("open")
            assert not driver.find_elements("css selector", ".page-heading .snapshot-label")
            source_date = disclosure.find_element("css selector", "p")
            assert not source_date.is_displayed()
            assert driver.execute_script("return !document.querySelector('.sources-section').nextElementSibling?.querySelector('.chart-canvas')")
            disclosure.find_element("css selector", "summary").click()
            try:
                WebDriverWait(driver, 10).until(lambda d: disclosure.get_attribute("open") and source_date.is_displayed())
            except Exception as exc:
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
    {"seed": seed, "update": update, "browser": browser,
     "old-releases": old_releases, "check-archives": check_archives}[sys.argv[1]]()
