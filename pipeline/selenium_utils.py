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
        # Стабильность в headless при заблокированном экране (Win+L):
        # GPU не нужен, throttling background-вкладок ломает обход
        # виртуальных таблиц с долгими паузами.
        opts.add_argument("--disable-gpu")
        opts.add_argument("--disable-software-rasterizer")
        opts.add_argument("--mute-audio")
        opts.add_argument("--disable-background-timer-throttling")
        opts.add_argument("--disable-renderer-backgrounding")
        opts.add_argument("--disable-backgrounding-occluded-windows")
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
                # Без этого Chrome 148 при множественных скачиваниях
                # показывает модальное окно «Разрешить скачивание
                # нескольких файлов» — парсер erzrf зависает.
                "profile.default_content_setting_values.automatic_downloads": 1,
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


def retry_with_refresh(driver, fn, *, attempts: int = 5, refresh: bool = True,
                       pause: float = 3.0, label: str = ""):
    """Запускает fn() до `attempts` раз. Между попытками driver.refresh().

    fn возвращает истинное значение при успехе или False/None/raise при неудаче.
    Возвращает результат fn или None если все попытки провалились.

    Используется когда страница временно не грузится / селектор не появляется:
        result = retry_with_refresh(driver, lambda: parse_table(driver),
                                     attempts=5, label="rasprod-table")
    """
    import time as _t

    last_exc = None
    for i in range(attempts):
        try:
            result = fn()
            if result is not None and result is not False:
                return result
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            tag = f"[{label}] " if label else ""
            print(f"  ⚠️  {tag}попытка {i + 1}/{attempts}: {exc}")
        if i < attempts - 1 and refresh:
            try:
                driver.refresh()
            except Exception:  # noqa: BLE001
                pass
            _t.sleep(pause)
    if last_exc:
        tag = f"[{label}] " if label else ""
        print(f"  ❌ {tag}все {attempts} попыток провалились")
    return None
