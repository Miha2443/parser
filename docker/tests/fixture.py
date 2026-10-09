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


def browser():
    from deploy.linux.tests import smoke
    smoke.browser()
    from pipeline.selenium_utils import create_chrome
    from selenium.webdriver.support.ui import WebDriverWait
    driver = create_chrome()
    try:
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
