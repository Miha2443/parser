"""
domrf.py
--------
Скачивает файлы с сайта наш.дом.рф/аналитика/статистические_ряды

Логика:
1. Открывает страницу и читает список файлов с датами
2. Если дата изменилась — скачивает файл
3. Каждый файл скачивается в отдельном браузере (без конфликтов)

Зависимости:
    pip install selenium requests
"""

import json
import time
from datetime import datetime
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


# ─────────────────────────────────────────────
# НАСТРОЙКИ
# ─────────────────────────────────────────────

PAGE_URL = "https://xn--80az8a.xn--d1aqf.xn--p1ai/аналитика/статистические_ряды"

FILES_OF_INTEREST = {
    "Сведения о реализации квартир в строящихся домах": "реализация_квартир",
    "Сведения о реализации нежилых помещений в строящихся домах": "реализация_нежилых_помещений",
}

DOWNLOAD_DIR = Path("downloads")
STATE_FILE = Path("domrf_state.json")
PAGE_TIMEOUT = 30

# ─────────────────────────────────────────────


def load_state():
    if STATE_FILE.exists():
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def create_driver(download_dir=None):
    """Создаёт Chrome драйвер. Если указана папка — настраивает скачивание туда."""
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
    if download_dir:
        prefs = {
            "download.default_directory": str(Path(download_dir).resolve()),
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
            "safebrowsing.enabled": True,
        }
        options.add_experimental_option("prefs", prefs)
    return webdriver.Chrome(options=options)


def get_files_info():
    """
    Открывает страницу и возвращает словарь:
    { "название файла": {"date": "01.04.2026", "url": "https://..."} }
    """
    print(f"  🌐 Открываю страницу для чтения списка файлов...")
    driver = create_driver()

    try:
        driver.set_page_load_timeout(30)
        driver.get(PAGE_URL)
    except Exception:
        print("  ⚠️  Страница не загрузилась за 30 сек")
        driver.quit()
        return {}

    wait = WebDriverWait(driver, PAGE_TIMEOUT)
    time.sleep(3)

    try:
        wait.until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, "div.styles__RowDate-sc-c91nh8-4")
            )
        )
    except Exception:
        print("  ⚠️  Не удалось дождаться загрузки списка файлов")
        driver.quit()
        return {}

    files_info = {}

    try:
        rows = driver.find_elements(
            By.XPATH,
            "//a[.//div[contains(@class,'RowDate')]]"
        )

        for row in rows:
            try:
                file_url = row.get_attribute("href") or ""

                try:
                    name_el = row.find_element(By.XPATH, ".//h3")
                    file_name = name_el.text.strip()
                except Exception:
                    file_name = row.text.strip()

                date_el = row.find_element(
                    By.XPATH, ".//div[contains(@class,'RowDate')]"
                )
                file_date = date_el.text.strip()

                if file_name and file_date:
                    files_info[file_name] = {
                        "date": file_date,
                        "url": file_url,
                    }
            except Exception:
                continue

    except Exception as e:
        print(f"  ❌ Ошибка при парсинге: {e}")
    finally:
        driver.quit()

    print(f"  ℹ️  Найдено файлов на странице: {len(files_info)}")
    return files_info


def find_matching_file(files_info, search_text):
    """Ищет файл по частичному совпадению названия."""
    for name, data in files_info.items():
        if search_text.lower() in name.lower():
            return name, data
    return None, None


def download_file(url, save_name, save_dir):
    """
    Скачивает файл в отдельном браузере.
    Открывает страницу сайта для получения куки, затем инициирует скачивание.
    """
    print(f"  ⬇️  Скачиваю: {url}")

    driver = create_driver(download_dir=save_dir)

    try:
        # Открываем страницу чтобы браузер получил нужные куки
        driver.set_page_load_timeout(30)
        driver.get(PAGE_URL)
        time.sleep(3)

        # Запоминаем файлы до скачивания
        files_before = set(save_dir.glob("*.*"))

        # Инициируем скачивание через JS
        driver.execute_script(f"""
            var a = document.createElement('a');
            a.href = '{url}';
            a.download = '';
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
        """)

        # Ждём появления нового завершённого файла (до 60 сек)
        new_file = None
        for _ in range(60):
            time.sleep(1)
            files_after = set(save_dir.glob("*.*"))
            new_files = files_after - files_before
            crdownload = [f for f in new_files if f.suffix == ".crdownload"]
            completed = [f for f in new_files if f.suffix != ".crdownload"]
            if completed and not crdownload:
                new_file = completed[0]
                break

        if new_file is None:
            print("  ⚠️  Файл не появился в папке за 60 сек")
            return None

        # Переименовываем
        date_str = datetime.now().strftime("%Y%m%d")
        ext = new_file.suffix
        new_name = save_dir / f"{date_str}_{save_name}{ext}"
        new_file.rename(new_name)
        print(f"  ✅ Сохранён: {new_name}")
        return new_name

    except Exception as e:
        print(f"  ❌ Ошибка: {e}")
        return None

    finally:
        driver.quit()


def run():
    """Основной процесс для наш.дом.рф."""
    DOWNLOAD_DIR.mkdir(exist_ok=True)
    state = load_state()
    downloaded_files = []

    print(f"\n{'='*60}")
    print(f"наш.дом.рф | Запуск: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}\n")

    # Шаг 1: получаем список файлов со страницы
    files_info = get_files_info()

    if not files_info:
        print("  ❌ Не удалось получить список файлов\n")
        return []

    # Шаг 2: для каждого нужного файла проверяем дату и скачиваем
    for search_text, save_name in FILES_OF_INTEREST.items():
        print(f"\n📄 Ищу: {search_text[:55]}")

        name, data = find_matching_file(files_info, search_text)

        if name is None:
            print(f"  ⚠️  Файл не найден на странице")
            continue

        remote_date = data["date"]
        file_url = data["url"]
        saved_date = state.get(save_name)

        print(f"  📅 Дата на сайте: {remote_date}")

        if saved_date is None:
            print(f"  ℹ️  Первая загрузка")
        elif remote_date != saved_date:
            print(f"  🔄 Обновился! Было: {saved_date} → Стало: {remote_date}")
        else:
            print(f"  ✔️  Без изменений")
            continue

        # Каждый файл — отдельный браузер
        saved_path = download_file(file_url, save_name, DOWNLOAD_DIR)

        if saved_path:
            downloaded_files.append(saved_path)
            state[save_name] = remote_date
            save_state(state)  # сохраняем после каждого файла

    print(f"\n{'='*60}")
    print(f"наш.дом.рф | Итог: скачано файлов — {len(downloaded_files)}")
    for f in downloaded_files:
        print(f"  • {f}")
    print(f"{'='*60}\n")

    return downloaded_files


if __name__ == "__main__":
    run()