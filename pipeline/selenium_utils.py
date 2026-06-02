"""Общий Selenium-инструментарий для скачивателей (nashdom, erzrf, …).

Вынесено сюда чтобы три модуля не дублировали одну и ту же инициализацию
Chrome и логику ожидания файла из download_dir. Образец брался с
`_to_delete/domrf_mortgage.py:66-84`.
"""
from __future__ import annotations

import time
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def create_chrome(
    download_dir: Path | None = None,
    *,
    headless: bool = True,
    page_load_strategy: str = "normal",
) -> webdriver.Chrome:
    """Возвращает Chrome-драйвер с настроенной папкой скачивания.

    `headless=False` — для отладки селекторов на локальной машине.
    """
    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument(f"user-agent={USER_AGENT}")
    if download_dir is not None:
        download_dir = Path(download_dir).resolve()
        download_dir.mkdir(parents=True, exist_ok=True)
        opts.add_experimental_option(
            "prefs",
            {
                "download.default_directory": str(download_dir),
                "download.prompt_for_download": False,
                "download.directory_upgrade": True,
                "safebrowsing.enabled": True,
            },
        )
    opts.page_load_strategy = page_load_strategy
    return webdriver.Chrome(options=opts)


def wait_for_download(
    download_dir: Path,
    *,
    before_snapshot: set[Path],
    timeout: int = 120,
    poll_interval: float = 1.0,
) -> Path | None:
    """Ждёт появления нового завершённого файла в download_dir.

    Завершённый = новый файл, у которого больше нет соседа `<name>.crdownload`.
    Возвращает Path нового файла или None, если за `timeout` сек ничего не появилось.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(poll_interval)
        files_after = set(download_dir.glob("*"))
        new_files = files_after - before_snapshot
        crdownload = {f for f in new_files if f.suffix == ".crdownload"}
        completed = [f for f in new_files if f.suffix != ".crdownload"]
        if completed and not crdownload:
            return completed[0]
    return None
