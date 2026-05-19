"""
domrf_mortgage.py
-----------------
Скачивает файлы с сайта дом.рф/programmy-gosudarstvennoj-podderzhki/operational-reporting/

Логика:
1. Открывает страницу через Selenium
2. Читает дату "Отчёт по данным на ..."
3. Кликает кнопку "Скачать отчёт" — появляется меню со ссылками
4. Если дата изменилась — скачивает нужные файлы

Зависимости:
    pip install selenium requests
"""

import json
import re
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

PAGE_URL = "https://xn--d1aqf.xn--p1ai/programmy-gosudarstvennoj-podderzhki/operational-reporting/"

# Файлы которые нас интересуют:
# ключ = часть текста ссылки на странице
# значение = имя для сохранения файла
FILES_OF_INTEREST = {
    "Статистические ряды (регионы)": "ипотека_статряды_регионы",
    # Можно добавить другие при необходимости:
    # "Статистические ряды (РФ)": "ипотека_статряды_рф",
    # "Портфель по регионам": "ипотека_портфель_регионы",
    # "Портфель по РФ": "ипотека_портфель_рф",
    # "Презентация": "ипотека_презентация",
}

DOWNLOAD_DIR = Path("downloads")
STATE_FILE = Path("domrf_mortgage_state.json")
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
    options = Options()
    #options.add_argument("--headless")
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


def get_page_info():
    """
    Открывает страницу, находит дату отчёта и собирает ссылки на файлы
    после клика по кнопке "Скачать отчёт".
    Возвращает (дата, словарь ссылок).
    """
    print(f"  🌐 Открываю страницу...")
    driver = create_driver()

    try:
        driver.set_page_load_timeout(30)
        driver.get(PAGE_URL)
    except Exception:
        print("  ⚠️  Страница не загрузилась за 30 сек")
        driver.quit()
        return None, {}

    time.sleep(15)  # ждём рендера JS

    report_date = None
    links = {}

    try:
           # ─── Дата отчёта ───
        date_text = driver.execute_script("""
            // Ищем элемент содержащий "данным на" — берём весь текст блока
            const all = [...document.querySelectorAll('*')];
            const el = all.find(e => 
                e.innerText && 
                e.innerText.includes('данным на') &&
                e.innerText.length < 200
            );
            return el ? el.innerText : null;
        """)

        if date_text:
            # Извлекаем дату вида "19 апреля 2026"
            match = re.search(r"(\d{1,2}\s+\S+\s+\d{4})", date_text)
            if match:
                report_date = match.group(1).strip()
            else:
                report_date = date_text.strip()
            print(f"  📅 Дата отчёта: {report_date}")
        else:
            print(f"  ⚠️  Дата не найдена")

        if date_text:
            # Извлекаем дату из строки "Отчёт по данным на 19 апреля 2026 г."
            match = re.search(r"на\s+(.+?)\s*г?\.?\s*$", date_text)
            if match:
                report_date = match.group(1).strip()
            else:
                report_date = date_text.strip()
            print(f"  📅 Дата отчёта: {report_date}")
        else:
            print(f"  ⚠️  Дата не найдена")

        # ─── Кликаем кнопку "Скачать отчёт" ───
        try:
            click_result = driver.execute_script("""
                const allButtons = [...document.querySelectorAll('button, a, div[class*="btn"], div[class*="button"]')];
                const btn = allButtons.find(b => b.innerText && b.innerText.includes('Скачать'));
                if (btn) {
                    btn.click();
                    return true;
                }
                return false;
            """)

            if click_result:
                print(f"  ✓ Кнопка 'Скачать отчёт' нажата")
                time.sleep(2)
            else:
                print(f"  ⚠️  Кнопка 'Скачать отчёт' не найдена")
                return report_date, {}

        except Exception as e:
            print(f"  ⚠️  Ошибка при нажатии кнопки: {e}")

        # ─── Собираем ссылки на файлы ───
        result = driver.execute_script("""
            return [...document.querySelectorAll('a[href*=".xlsx"], a[href*=".XLSX"], a[href*=".pdf"]')]
                .map(a => ({text: a.innerText.trim(), href: a.href}))
                .filter(item => item.text && item.href);
        """)

        for item in result:
            text = item.get("text", "").strip()
            href = item.get("href", "").strip()
            if text and href:
                links[text] = href

        print(f"  ℹ️  Найдено ссылок на файлы: {len(links)}")
        for text in links:
            print(f"     • {text}")

    except Exception as e:
        print(f"  ❌ Ошибка при парсинге: {e}")
    finally:
        driver.quit()

    return report_date, links


def find_matching_link(links, search_text):
    """Ищет ссылку по частичному совпадению текста."""
    for text, url in links.items():
        if search_text.lower() in text.lower():
            return url
    return None


def download_file(url, save_name, save_dir):
    """Скачивает файл в отдельном браузере."""
    print(f"  ⬇️  Скачиваю: {url}")

    driver = create_driver(download_dir=save_dir)

    try:
        driver.set_page_load_timeout(30)
        driver.get(PAGE_URL)
        time.sleep(3)

        files_before = set(save_dir.glob("*.*"))

        driver.execute_script(f"""
            var a = document.createElement('a');
            a.href = '{url}';
            a.download = '';
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
        """)

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

        date_str = datetime.now().strftime("%Y%m%d")
        ext = new_file.suffix.lower()
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
    DOWNLOAD_DIR.mkdir(exist_ok=True)
    state = load_state()
    downloaded_files = []

    print(f"\n{'='*60}")
    print(f"дом.рф ипотека | Запуск: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}\n")

    report_date, links = get_page_info()

    if not report_date:
        print("  ❌ Не удалось получить дату\n")
        return []

    if not links:
        print("  ❌ Не удалось получить ссылки\n")
        return []

    saved_date = state.get("report_date")

    if saved_date is None:
        print(f"\n  ℹ️  Первая загрузка")
    elif report_date != saved_date:
        print(f"\n  🔄 Отчёт обновился! Было: {saved_date} → Стало: {report_date}")
    else:
        print(f"\n  ✔️  Без изменений ({report_date})")
        return []

    for search_text, save_name in FILES_OF_INTEREST.items():
        print(f"\n📄 Ищу: {search_text}")

        file_url = find_matching_link(links, search_text)

        if file_url is None:
            print(f"  ⚠️  Ссылка не найдена")
            continue

        saved_path = download_file(file_url, save_name, DOWNLOAD_DIR)

        if saved_path:
            downloaded_files.append(saved_path)

    if downloaded_files:
        state["report_date"] = report_date
        save_state(state)

    print(f"\n{'='*60}")
    print(f"дом.рф ипотека | Итог: скачано — {len(downloaded_files)}")
    for f in downloaded_files:
        print(f"  • {f}")
    print(f"{'='*60}\n")

    return downloaded_files


if __name__ == "__main__":
    run()