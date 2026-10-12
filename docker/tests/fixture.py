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
    time.sleep(6)  # Old API must survive an already-overwritten mutable workbook.
    subprocess.run([sys.executable, "-m", "pipeline.build_realty_marts", "--strict"], check=True)
    status = dict(run_id=uuid.uuid4().hex, status="success", current_stage="finished",
                  started_at=datetime.fromtimestamp(started).isoformat(),
                  finished_at=datetime.now().isoformat(), sources_requested=sorted(SOURCES),
                  successes=sorted(SOURCES), failures=[], status_write_errors=[], error="",
                  marts_ok=True, processed_ok=True)
    if os.environ.get("FIXTURE_FAIL") == "1":
        status["successes"].remove("fedstat")
    require_success(status, None, started)
    publish(Path("/app"), Path("/runtime"))


def browser_dom_contract(driver):
    # Actual DOM regression: closed filters change CSS class after selection;
    # the regional chart can render before the developer chart.
    import nashdom_checker as checker
    from urllib.parse import quote
    html = '''<html><body>
      <p>Федеральный округ / Регион</p><div><span class="css-203ko5">Город Москва</span></div>
      <p>Девелопер</p><div><span class="css-wjcl1w">Все</span></div>
      <h2>Объём строительства по регионам</h2>
      <div class="list" style="height:100px;overflow:auto"><button class="css-5nggi1"><div>Город Москва</div><div>10</div><div>20</div></button></div>
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
        driver.get("http://web:8080/?developer=compose+new&region=msk")
        WebDriverWait(driver, 25).until(lambda d: d.find_elements("css selector", ".chart-canvas canvas"))
        driver.save_screenshot("/app/logs/compose-profile.png")
    finally:
        driver.quit()


if __name__ == "__main__":
    guard()
    {"seed": seed, "update": update, "browser": browser}[sys.argv[1]]()
