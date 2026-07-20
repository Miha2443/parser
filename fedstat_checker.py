"""
fedstat_checker.py
------------------
1. Открывает страницу каждого индикатора на fedstat.ru через Selenium
2. Читает дату "Последнее обновление данных" из раздела "Паспорт показателя"
3. Сравнивает с сохранённой датой
4. Если изменилась — скачивает Excel-файл

Зависимости:
    pip install selenium requests
Запуск:
    py fedstat_checker.py
"""

import atexit
import base64
import html
import os
import re
import shutil
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
import requests
import pandas as pd
from datetime import datetime
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from pipeline.file_utils import stream_response_atomic, validate_excel_file, write_bytes_atomic
from pipeline.selenium_utils import wait_for_download
from pipeline.state_utils import load_json_state, write_json_atomic


try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass


# ─────────────────────────────────────────────
# НАСТРОЙКИ
# ─────────────────────────────────────────────

INDICATORS = {
    "33648": "Индекс предпринимательской уверенности в строительстве (процент)",

    "61497": "Валовой региональный продукт в основных ценах (ОКВЭД 2) (тысяча рублей, значение показателя за год)",

    "59449": "Индексы физического объема валового регионального продукта (ОКВЭД 2) (процент, значение показателя за год)",

    "61483": "Валовой региональный продукт на душу населения (ОКВЭД 2)",

    "45341": "Индекс физического объема валового регионального продукта на душу населения (процент, значение показателя за год)",

    "59450": "Структура валового регионального продукта (ОКВЭД 2) (процент, значение показателя за год)",

    "55370": "Доля продукции высокотехнологичных и наукоемких отраслей в валовом региональном продукте относительно уровня 2011 года (процент, значение показателя за год, К базисному периоду)",

    "55373": "Индекс производительности труда относительно уровня 2011 года (процент, значение показателя за год, К базисному периоду)",

    "31557": "Численность постоянного населения на 1 января (Раздел 1. Муниципальные образования субъектов Российской Федерации)",

    "31556": "Численность постоянного населения в среднем за год (человек, значение показателя за год)",

    "36730": "Среднегодовая численность трудовых ресурсов (значение показателя за год)",

    "58994": "Среднегодовая численность занятых в экономике (расчеты на основе интеграции данных) с 2017 г. (человек, значение показателя за год)",

    "43211": "Среднегодовая численность занятых в экономике (расчеты на основе интеграции данных) по 2016 г. (человек, значение показателя за год)",

    "57824": "Среднемесячная номинальная начисленная заработная плата работающих в экономике с 2017 г.  (рубль)",

    "43246": "Среднемесячная номинальная начисленная заработная плата работающих в экономике по 2016 г. (рубль)",

    "34475": "Инвестиции в основной капитал по полному кругу организаций по 2016 г. (миллион рублей, значение показателя за год)",

    "59047": "Инвестиции в основной капитал по полному кругу хозяйствующих субъектов с 2017 г. (тысяча рублей, значение показателя за год)",

    "36078": "Индекс физического объема инвестиций в основной капитал по полному кругу организаций по 2016 г. (процент)",

    "34129": "Индекс физического объема инвестиций в основной капитал (процент, значение показателя за год)",

    "58089": "Индекс физического объема инвестиций в основной капитал с 2017 г. (оперативные данные) (процент)",

    "34118_часть1": "Введено в действие общей площади жилых домов часть1 (2015-2022)",
    "34118_часть2": "Введено в действие общей площади жилых домов часть2 (с 2023)",

    "33575": "Введено в действие общей площади жилых домов, построенных населением (оперативные данные) (тысяча квадратных метров общей площади)",

    "33574": "Введено в действие общей площади жилых домов на 1000 человек населения (квадратный метр, значение показателя за год)",

    "33738": "Количество построенных квартир (значение показателя за год)",

    "40466": "Общая площадь жилых помещений, приходящаяся в среднем на одного жителя (квадратный метр, значение показателя за год)",

    "31074_часть1": "Индексы потребительских цен на товары и услуги часть1 (процент)",
    "31074_часть2": "Индексы потребительских цен на товары и услуги часть2 (процент)",

    "31452": "Средняя цена 1 кв. м  общей площади квартир на рынке жилья  (рубль)",

    "31448": "Средние потребительские цены (тарифы) на товары и услуги (рубль)",

}

DOWNLOAD_DIR = Path("downloads")
STATE_FILE = Path("fedstat_state.json")
PAGE_TIMEOUT = int(os.environ.get("FEDSTAT_PAGE_TIMEOUT", "20"))
PAGE_LOAD_TOTAL_TIMEOUT = int(os.environ.get("FEDSTAT_PAGE_LOAD_TOTAL_TIMEOUT", "120"))
PAGE_LOAD_ATTEMPT_TIMEOUT = int(os.environ.get("FEDSTAT_PAGE_LOAD_ATTEMPT_TIMEOUT", "20"))
DIRECT_DOWNLOAD_ON_DATE_FAILURE = (
    os.environ.get("FEDSTAT_DIRECT_DOWNLOAD_ON_DATE_FAILURE", "1").strip().lower()
    not in {"0", "false", "no"}
)
DIRECT_FALLBACK_IDS = {
    item.strip()
    for item in os.environ.get("FEDSTAT_DIRECT_FALLBACK_IDS", "57824,34118").split(",")
    if item.strip()
}
BROWSER_POST_TIMEOUT = int(os.environ.get("FEDSTAT_BROWSER_POST_TIMEOUT", "120"))
BROWSER_FETCH_TIMEOUT = int(os.environ.get("FEDSTAT_BROWSER_FETCH_TIMEOUT", "180"))
FEDSTAT_34118_CHUNK_SIZE = max(1, int(os.environ.get("FEDSTAT_34118_CHUNK_SIZE", "1")))
FEDSTAT_34118_YEAR_CHUNK_SIZE = max(1, int(os.environ.get("FEDSTAT_34118_YEAR_CHUNK_SIZE", "99")))
FEDSTAT_34118_CHUNK_RETRIES = max(1, int(os.environ.get("FEDSTAT_34118_CHUNK_RETRIES", "2")))
FEDSTAT_34118_CHUNK_RETRY_SLEEP = max(0.0, float(os.environ.get("FEDSTAT_34118_CHUNK_RETRY_SLEEP", "2")))
FEDSTAT_USER_AGENT = os.environ.get(
    "FEDSTAT_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
)

# ─────────────────────────────────────────────


def load_state():
    return load_json_state(STATE_FILE, label="fedstat")


def save_state(state):
    write_json_atomic(STATE_FILE, state)


def create_driver(download_dir: Path | None = None):
    options = Options()

    # Уникальный профиль на инстанс. update_realty.py гоняет fedstat
    # параллельно с rasprod/kvart/erz-top — без своего user-data-dir
    # все Chrome'ы лезут в дефолтный профиль, упираются в Singleton-lock
    # и в headless=new рендерят битый DOM → `appendChild on null` у
    # тяжёлого React-фронта fedstat.
    profile_dir = tempfile.mkdtemp(prefix="chrome-fedstat-")
    atexit.register(shutil.rmtree, profile_dir, ignore_errors=True)
    options.add_argument(f"--user-data-dir={profile_dir}")

    # HEADLESS_MODE=new → новый headless (быстрее, но в Chrome 149 ломает
    # тяжёлый React-DOM fedstat с `appendChild on null`). По умолчанию
    # `=old` — старый headless надёжен, разница в скорости несущественна
    # для 29 индикаторов.
    mode = os.environ.get("HEADLESS_MODE", "old").lower()
    if mode == "new":
        options.add_argument("--headless=new")
        print("  🛠  headless=new (HEADLESS_MODE=new)", flush=True)
    else:
        options.add_argument("--headless")
        print("  🛠  headless=old (HEADLESS_MODE=old, default)", flush=True)
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-features=Translate")
    options.add_argument("--disable-popup-blocking")
    options.add_argument("--lang=ru-RU")
    options.add_argument(f"user-agent={FEDSTAT_USER_AGENT}")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    if download_dir is not None:
        download_dir = Path(download_dir).resolve()
        download_dir.mkdir(parents=True, exist_ok=True)
        options.add_experimental_option(
            "prefs",
            {
                "download.default_directory": str(download_dir),
                "download.prompt_for_download": False,
                "download.directory_upgrade": True,
                "safebrowsing.enabled": True,
                "profile.default_content_setting_values.automatic_downloads": 1,
            },
        )
    driver = webdriver.Chrome(options=options)
    try:
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {
                "source": """
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
                Object.defineProperty(navigator, 'languages', {get: () => ['ru-RU', 'ru']});
                """
            },
        )
    except Exception:
        pass
    if download_dir is not None:
        _set_driver_download_dir(driver, download_dir)
    _orig_quit = driver.quit
    def _quit_and_cleanup():
        try:
            _orig_quit()
        finally:
            shutil.rmtree(profile_dir, ignore_errors=True)
    driver.quit = _quit_and_cleanup  # type: ignore[method-assign]
    return driver


def _set_driver_download_dir(driver, download_dir: Path) -> None:
    download_dir = Path(download_dir).resolve()
    download_dir.mkdir(parents=True, exist_ok=True)
    try:
        driver.execute_cdp_cmd(
            "Page.setDownloadBehavior",
            {"behavior": "allow", "downloadPath": str(download_dir)},
        )
    except Exception:
        pass


def close_popup(driver):
    """Закрывает всплывающее окно с ошибкой если оно появилось.

    1) Сначала пытается найти кнопку «×» и кликнуть.
    2) Если кнопки нет, или клик не сработал — принудительно скрывает
       через JS любые видимые .modal/#serverMessages. Без этого
       bootstrap-модал «Server error» от fedstat перехватывает клик на
       «Паспорт показателя» с ElementClickInterceptedException.
    """
    try:
        close_btn = driver.find_element(
            By.XPATH,
            "//div[contains(@class,'modal') or contains(@class,'popup') or contains(@class,'dialog')]"
            "//button[contains(@class,'close') or contains(text(),'×') or @aria-label='Close']"
        )
        close_btn.click()
        time.sleep(1)
        print("  ℹ️  Закрыл всплывающее окно")
        return
    except Exception:  # noqa: BLE001
        pass
    # Fallback: скрываем все .modal через JS + убираем .modal-backdrop
    try:
        hidden = driver.execute_script(
            """
            let n = 0;
            document.querySelectorAll('.modal, #serverMessages').forEach(el => {
                if (el.offsetParent !== null || el.style.display !== 'none') {
                    el.style.display = 'none';
                    el.classList.remove('in', 'show');
                    el.setAttribute('aria-hidden', 'true');
                    n++;
                }
            });
            document.querySelectorAll('.modal-backdrop').forEach(el => { el.remove(); n++; });
            document.body.classList.remove('modal-open');
            document.body.style.removeProperty('padding-right');
            return n;
            """
        )
        if hidden:
            print(f"  ℹ️  Скрыл {hidden} модалей через JS")
    except Exception:  # noqa: BLE001
        pass


def get_last_update_date(driver, indicator_id):
    # Убираем суффикс типа "_часть1" если есть — для URL нужен только числовой ID
    real_id = indicator_id.split("_")[0]
    url = f"https://www.fedstat.ru/indicator/{real_id}"
    print(f"  🌐 Открываю: {url}")

    start_time = time.time()
    loaded = False

    while time.time() - start_time < PAGE_LOAD_TOTAL_TIMEOUT:
        try:
            driver.set_page_load_timeout(PAGE_LOAD_ATTEMPT_TIMEOUT)
            driver.get(url)
            loaded = True
            break
        except Exception:
            elapsed = int(time.time() - start_time)
            print(f"  ⏳ Не загрузилось за {PAGE_LOAD_ATTEMPT_TIMEOUT}с (всего {elapsed}с), повторяю...")
            try:
                driver.execute_script("window.stop();")
            except Exception:
                pass

    if not loaded:
        print(f"  ⚠️  Страница не загрузилась за {PAGE_LOAD_TOTAL_TIMEOUT}с, пропускаю")
        return None

    wait = WebDriverWait(driver, PAGE_TIMEOUT)
    page_text = ""
    try:
        page_text = driver.find_element(By.TAG_NAME, "body").text.strip()
    except Exception:
        pass
    if "forbidden" in page_text.lower():
        print(f"  ❌ Fedstat отклонил страницу: {' '.join(page_text.split())[:240]}")
        return None

    # Ретраи на flaky-ошибки: appendChild (Chrome 148 JS-инициализация
    # fedstat) и element click intercepted (всплывающее модальное окно
    # перехватывает клик). На каждой ретре делаем refresh.
    RETRYABLE_MARKERS = (
        "appendchild",
        "cannot read properties of null",
        "element click intercepted",
    )

    for attempt in range(3):
        try:
            # Закрываем попап если появился
            time.sleep(2)
            close_popup(driver)

            passport_tab = wait.until(
                EC.element_to_be_clickable(
                    (By.XPATH, "//a[contains(text(),'ПАСПОРТ') or contains(text(),'Паспорт')]")
                )
            )
            passport_tab.click()
            time.sleep(2)

            # Закрываем попап если появился после клика
            close_popup(driver)

            date_label = wait.until(
                EC.presence_of_element_located(
                    (By.XPATH, "//*[contains(text(),'Последнее обновление данных')]")
                )
            )

            # Вариант 1: следующий sibling
            try:
                el = date_label.find_element(By.XPATH, "following-sibling::*[1]")
                text = el.text.strip()
                if re.search(r"\d{2}\.\d{2}\.\d{4}", text):
                    return text
            except Exception:
                pass

            # Вариант 2: следующий sibling родителя
            try:
                parent = date_label.find_element(By.XPATH, "..")
                el = parent.find_element(By.XPATH, "following-sibling::*[1]")
                text = el.text.strip()
                if re.search(r"\d{2}\.\d{2}\.\d{4}", text):
                    return text
            except Exception:
                pass

            # Вариант 3: regex по тексту родителя
            parent_text = date_label.find_element(By.XPATH, "..").text
            match = re.search(r"\d{2}\.\d{2}\.\d{4}", parent_text)
            if match:
                return match.group(0)

            print("  ⚠️  Не удалось извлечь дату")
            return None

        except Exception as e:
            msg = str(e).lower()
            if attempt < 2 and any(m in msg for m in RETRYABLE_MARKERS):
                print(f"  🔄 Попытка {attempt + 2}/3 после ошибки: {str(e)[:80]}")
                try:
                    driver.refresh()
                except Exception:
                    pass
                time.sleep(3)
                continue
            print(f"  ❌ Ошибка при чтении страницы: {e}")
            return None
    return None


def _parse_remote_date_to_yyyymmdd(s: str | None) -> str:
    """«12.05.2026» / «2026-05-12» / «12.05.2026 14:30» → '20260512'.

    Если не парсится — возвращает текущую дату как fallback (чтобы файл
    точно записался). Используется в имени xls-файла, чтобы оно отражало
    реальную дату обновления данных с сайта, а не день скачивания.
    """
    if s:
        s = str(s).strip()
        # Пробуем популярные форматы
        for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d.%m.%Y %H:%M",
                    "%d-%m-%Y", "%d/%m/%Y"):
            try:
                return datetime.strptime(s[:len(fmt) + 6], fmt).strftime("%Y%m%d")
            except (ValueError, TypeError):
                continue
    return datetime.now().strftime("%Y%m%d")


def _compact_html_preview(text: str, limit: int = 300) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text[:limit]


def _raise_if_html_response(response: requests.Response) -> None:
    content_type = (response.headers.get("Content-Type") or "").lower()
    if "html" in content_type or content_type.startswith("text/"):
        preview = _compact_html_preview(response.text)
        raise ValueError(f"Fedstat вернул HTML вместо Excel: {preview}")


def _looks_like_excel_bytes(content: bytes) -> bool:
    return (
        content.startswith(b"PK\x03\x04")
        or content.startswith(b"PK\x05\x06")
        or content.startswith(b"PK\x07\x08")
        or content.startswith(b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1")
    )


def _looks_like_xml_bytes(content: bytes) -> bool:
    head = content[:200].lstrip().lower()
    return head.startswith(b"<?xml") or head.startswith(b"<")


def _to_float(v) -> float:
    try:
        if v is None or pd.isna(v):
            return float("nan")
    except TypeError:
        if v is None:
            return float("nan")
    s = str(v).strip().replace("\xa0", "").replace(" ", "").replace(",", ".")
    if not s:
        return float("nan")
    try:
        return float(s)
    except ValueError:
        return float("nan")


def _xml_local_name(tag: str) -> str:
    return str(tag).rsplit("}", 1)[-1]


def _strip_field_prefix(field_id: str, value: str | None) -> str | None:
    if value is None:
        return None
    value = str(value)
    prefix = f"{field_id}_"
    if value.startswith(prefix):
        return value[len(prefix):]
    return value


def _sdmx_code_labels(root: ET.Element) -> dict[str, dict[str, str]]:
    labels: dict[str, dict[str, str]] = {}
    for elem in root.iter():
        if _xml_local_name(elem.tag) not in {"CodeList", "Codelist"}:
            continue
        field_id = elem.attrib.get("id")
        if not field_id:
            continue
        field_labels: dict[str, str] = {}
        for child in elem:
            if _xml_local_name(child.tag) != "Code":
                continue
            value = _strip_field_prefix(field_id, child.attrib.get("value") or child.attrib.get("id"))
            if not value:
                continue
            texts = [
                (node.text or "").strip()
                for node in child.iter()
                if _xml_local_name(node.tag) in {"Name", "Description"} and (node.text or "").strip()
            ]
            field_labels[value] = texts[0] if texts else value
        if field_labels:
            labels[field_id] = field_labels
    return labels


def _sdmx_attr(elem: ET.Element, *names: str) -> str | None:
    lowered = {name.lower() for name in names}
    for key, value in elem.attrib.items():
        if _xml_local_name(key).lower() in lowered:
            return value
    return None


def _sdmx_records(content: bytes) -> tuple[list[dict[str, str]], dict[str, dict[str, str]]]:
    root = ET.fromstring(content)
    labels = _sdmx_code_labels(root)
    records: list[dict[str, str]] = []

    def read_values(parent: ET.Element) -> dict[str, str]:
        out: dict[str, str] = {}
        for node in parent.iter():
            if _xml_local_name(node.tag) != "Value":
                continue
            key = _sdmx_attr(node, "id", "concept")
            value = _sdmx_attr(node, "value")
            if key and value:
                out[key] = value
        return out

    for series in root.iter():
        if _xml_local_name(series.tag) != "Series":
            continue
        series_values: dict[str, str] = {}
        for child in series:
            if _xml_local_name(child.tag) in {"SeriesKey", "Attributes"}:
                series_values.update(read_values(child))
        for obs in series:
            if _xml_local_name(obs.tag) != "Obs":
                continue
            record = dict(series_values)
            for key, value in obs.attrib.items():
                record[_xml_local_name(key)] = value
            for child in obs:
                child_name = _xml_local_name(child.tag)
                if child_name == "ObsDimension":
                    key = _sdmx_attr(child, "id", "concept") or "TIME_PERIOD"
                    value = _sdmx_attr(child, "value")
                    if value:
                        record[key] = value
                elif child_name == "ObsValue":
                    value = _sdmx_attr(child, "value")
                    if value:
                        record["ObsValue"] = value
                elif child_name in {"Attributes", "Value"}:
                    record.update(read_values(child))
            if "ObsValue" in record or "OBS_VALUE" in record:
                records.append(record)

    # Some SDMX writers emit flat Obs elements without a Series wrapper.
    if not records:
        for obs in root.iter():
            if _xml_local_name(obs.tag) != "Obs":
                continue
            record = {_xml_local_name(k): v for k, v in obs.attrib.items()}
            for child in obs:
                if _xml_local_name(child.tag) == "Value":
                    key = _sdmx_attr(child, "id", "concept")
                    value = _sdmx_attr(child, "value")
                    if key and value:
                        record[key] = value
                elif _xml_local_name(child.tag) == "ObsValue":
                    value = _sdmx_attr(child, "value")
                    if value:
                        record["ObsValue"] = value
            if "ObsValue" in record or "OBS_VALUE" in record:
                records.append(record)
    return records, labels


def _label_from_sdmx(labels: dict[str, dict[str, str]], field_id: str, value_id: str) -> str:
    value_id = _strip_field_prefix(field_id, value_id) or value_id
    fallback_labels = {
        ("57831", "1688487"): "Российская Федерация",
        ("57831", "1688506"): "Москва",
        ("57831", "1849012"): "Российская Федерация без учета новых субъектов (с 01.01.2023)",
        ("58389", "1754554"): "Жилые дома,построенные населением",
        ("58389", "1754555"): "Жилые дома",
        ("58389", "1754556"): "Жилые здания многоквартирные",
        ("33560", "1540222"): "январь-декабрь",
    }
    return labels.get(field_id, {}).get(value_id) or fallback_labels.get((field_id, value_id), value_id)


def _record_value(record: dict[str, str], field_id: str) -> str | None:
    candidates = (field_id, f"s_{field_id}", f"{field_id}_code")
    for candidate in candidates:
        if candidate in record and record[candidate] not in {"", None}:
            return str(record[candidate])
    for key, value in record.items():
        if str(key).split("-", 1)[0] == field_id and value not in {"", None}:
            return str(value)
    return None


def _sdmx_34118_to_excel(content: bytes, payload_template: dict, save_path: Path) -> Path | None:
    records, labels = _sdmx_records(content)
    if not records:
        return None
    selected = list(payload_template.get("selectedFilterIds", []))
    years = [item.split("_", 1)[1] for item in selected if _is_34118_year_filter(item)]
    periods = [item.split("_", 1)[1] for item in selected if _is_34118_period_filter(item)]
    regions = [item.split("_", 1)[1] for item in selected if str(item).startswith("57831_")]
    categories = [item.split("_", 1)[1] for item in selected if str(item).startswith("58389_")]
    if not years or not periods or not regions or not categories:
        return None

    value_by_key: dict[tuple[str, str, str, str], float] = {}
    for record in records:
        year = _strip_field_prefix("3", _record_value(record, "3") or _record_value(record, "TIME_PERIOD"))
        if year and re.fullmatch(r"\d{4}-.+", year):
            year = year[:4]
        period = _strip_field_prefix("33560", _record_value(record, "33560")) or (
            periods[0] if len(periods) == 1 else None
        )
        region = _strip_field_prefix("57831", _record_value(record, "57831"))
        category = _strip_field_prefix("58389", _record_value(record, "58389"))
        raw_value = record.get("ObsValue") or record.get("OBS_VALUE")
        if not (year and period and region and category and raw_value is not None):
            continue
        value = _to_float(raw_value)
        if pd.isna(value):
            continue
        value_by_key[(str(region), str(category), str(year), str(period))] = value

    if not value_by_key:
        return None

    columns = [(year, period) for year in years for period in periods]
    rows: list[list[object]] = [
        [payload_template.get("title", "")] + [None] * (len(columns) + 1),
        [None] * (len(columns) + 2),
        [None, None] + [int(year) if str(year).isdigit() else year for year, _ in columns],
        [None, None] + [_label_from_sdmx(labels, "33560", period) for _, period in columns],
    ]
    for region in regions:
        for category in categories:
            row = [
                _label_from_sdmx(labels, "57831", region),
                _label_from_sdmx(labels, "58389", category),
            ]
            row.extend(value_by_key.get((region, category, year, period), "") for year, period in columns)
            if any(v != "" for v in row[2:]):
                rows.append(row)

    if len(rows) <= 4:
        return None
    final_path = save_path.with_suffix(".xlsx")
    final_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = final_path.with_name(f"{final_path.stem}.tmp{final_path.suffix}")
    try:
        with pd.ExcelWriter(tmp_path, engine="openpyxl") as writer:
            pd.DataFrame(rows).to_excel(writer, sheet_name="Данные", header=False, index=False)
        validate_excel_file(tmp_path)
        tmp_path.replace(final_path)
        return final_path
    finally:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass


def _post_data_with_format(post_data: list[tuple[str, str]], data_format: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    replaced = False
    for key, value in post_data:
        if key == "format":
            out.append((key, data_format))
            replaced = True
        else:
            out.append((key, value))
    if not replaced:
        out.insert(0, ("format", data_format))
    return out


def _download_34118_sdmx_as_excel(
    session: requests.Session,
    post_data: list[tuple[str, str]],
    headers: dict[str, str],
    payload_template: dict,
    save_path: Path,
    driver=None,
) -> Path | None:
    url = "https://www.fedstat.ru/indicator/data.do?format=sdmx"
    sdmx_headers = {
        key: value
        for key, value in headers.items()
        if key.lower() != "content-type"
    }
    sdmx_headers["Content-Type"] = "application/x-www-form-urlencoded"
    sdmx_headers["Accept"] = "text/xml,application/xml,*/*"
    sdmx_post_data = _post_data_with_format(post_data, "sdmx")

    try:
        response = session.post(url, data=sdmx_post_data, headers=sdmx_headers, timeout=120)
        response.raise_for_status()
        content_type = (response.headers.get("Content-Type") or "").lower()
        content = response.content
        if "html" in content_type or content[:200].lstrip().lower().startswith(b"<html"):
            preview = _compact_html_preview(response.text)
            print(f"  ⚠️  SDMX тоже вернул HTML: {preview}")
            raise ValueError("SDMX returned HTML")
        if not _looks_like_xml_bytes(content):
            print(f"  ⚠️  SDMX ответ не похож на XML ({content_type}, {len(content)} байт)")
            raise ValueError("SDMX response is not XML")
        out = _sdmx_34118_to_excel(content, payload_template, save_path)
        if out is not None:
            print(f"  ✅ Сохранён через SDMX fallback: {out}")
            return out
        raise ValueError("SDMX XML did not contain 34118 rows")
    except (requests.RequestException, OSError, ValueError, ET.ParseError) as exc:
        print(f"  ⚠️  SDMX fallback не сработал: {exc}")

    if driver is None or "fedstat.ru" not in (driver.current_url or ""):
        return None
    try:
        driver.set_script_timeout(BROWSER_FETCH_TIMEOUT)
    except Exception:
        pass
    try:
        entries = [[str(k), str(v)] for k, v in sdmx_post_data]
        result = driver.execute_async_script(
            """
            const done = arguments[arguments.length - 1];
            const [action, entries] = arguments;
            const body = new URLSearchParams();
            for (const [name, value] of entries) body.append(name, value);
            fetch(action, {
                method: 'POST',
                credentials: 'include',
                headers: {
                    'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8',
                    'Accept': 'text/xml,application/xml,*/*'
                },
                body
            }).then(async response => {
                const contentType = response.headers.get('content-type') || '';
                const buffer = await response.arrayBuffer();
                const bytes = new Uint8Array(buffer);
                let binary = '';
                const chunk = 0x8000;
                for (let i = 0; i < bytes.length; i += chunk) {
                    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
                }
                let text = '';
                if (contentType.toLowerCase().includes('html') || contentType.toLowerCase().startsWith('text/')) {
                    text = new TextDecoder('utf-8').decode(bytes.slice(0, 1000));
                }
                done({
                    ok: response.ok,
                    status: response.status,
                    contentType,
                    bodyBase64: btoa(binary),
                    text
                });
            }).catch(error => done({error: String(error)}));
            """,
            url,
            entries,
        )
        if not result or not result.get("bodyBase64"):
            if result and result.get("error"):
                print(f"  ⚠️  Browser SDMX fetch не сработал: {result.get('error')}")
            return None
        content = base64.b64decode(result["bodyBase64"])
        content_type = str(result.get("contentType") or "").lower()
        if "html" in content_type or content[:200].lstrip().lower().startswith(b"<html"):
            preview = _compact_html_preview(result.get("text") or content[:1000].decode("utf-8", "replace"))
            print(f"  ⚠️  Browser SDMX fetch вернул HTML ({result.get('status')}): {preview}")
            return None
        if not _looks_like_xml_bytes(content):
            print(f"  ⚠️  Browser SDMX ответ не похож на XML ({content_type}, {len(content)} байт)")
            return None
        out = _sdmx_34118_to_excel(content, payload_template, save_path)
        if out is not None:
            print(f"  ✅ Сохранён через Browser SDMX fallback: {out}")
        return out
    except Exception as exc:
        print(f"  ⚠️  Browser SDMX fallback не сработал: {exc}")
        return None


def _download_excel_via_browser(driver, url: str, post_data: list[tuple[str, str]],
                                save_dir: Path, save_path: Path) -> Path | None:
    """Submit Fedstat Excel POST through Chrome when direct requests are blocked."""
    if driver is None:
        return None
    save_dir.mkdir(parents=True, exist_ok=True)
    _set_driver_download_dir(driver, save_dir)
    before = {p.resolve() for p in save_dir.glob("*") if p.is_file()}
    form_path = None
    try:
        entries = [[str(k), str(v)] for k, v in post_data]
        if "fedstat.ru" not in (driver.current_url or ""):
            real_id = next(
                (value.split("_", 1)[1] for key, value in post_data
                 if key == "selectedFilterIds" and str(value).startswith("0_")),
                "",
            )
            if real_id:
                driver.get(f"https://www.fedstat.ru/indicator/{real_id}")
                time.sleep(2)
                close_popup(driver)

        if "fedstat.ru" in (driver.current_url or ""):
            try:
                driver.set_script_timeout(BROWSER_FETCH_TIMEOUT)
            except Exception:
                pass
            try:
                result = driver.execute_async_script(
                    """
                    const done = arguments[arguments.length - 1];
                    const [action, entries] = arguments;
                    const body = new URLSearchParams();
                    for (const [name, value] of entries) body.append(name, value);
                    fetch(action, {
                        method: 'POST',
                        credentials: 'include',
                        headers: {
                            'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8',
                            'Accept': 'application/vnd.ms-excel,application/octet-stream,*/*'
                        },
                        body
                    }).then(async response => {
                        const contentType = response.headers.get('content-type') || '';
                        const buffer = await response.arrayBuffer();
                        const bytes = new Uint8Array(buffer);
                        let binary = '';
                        const chunk = 0x8000;
                        for (let i = 0; i < bytes.length; i += chunk) {
                            binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
                        }
                        let text = '';
                        if (contentType.toLowerCase().includes('html') || contentType.toLowerCase().startsWith('text/')) {
                            text = new TextDecoder('utf-8').decode(bytes.slice(0, 1000));
                        }
                        done({
                            ok: response.ok,
                            status: response.status,
                            contentType,
                            length: bytes.length,
                            bodyBase64: btoa(binary),
                            text
                        });
                    }).catch(error => done({error: String(error)}));
                    """,
                    url,
                    entries,
                )
                if result and result.get("bodyBase64"):
                    content_type = str(result.get("contentType") or "").lower()
                    status = result.get("status")
                    body = base64.b64decode(result["bodyBase64"])
                    if _looks_like_excel_bytes(body):
                        write_bytes_atomic(save_path, body, validate=validate_excel_file)
                        return save_path
                    if "html" in content_type or content_type.startswith("text/"):
                        preview = _compact_html_preview(result.get("text") or body[:1000].decode("utf-8", "replace"))
                        print(f"  ⚠️  Browser fetch вернул HTML ({status}): {preview}")
                    else:
                        write_bytes_atomic(save_path, body, validate=validate_excel_file)
                        return save_path
                elif result and result.get("error"):
                    print(f"  ⚠️  Browser fetch не сработал: {result.get('error')}")
            except Exception as exc:
                print(f"  ⚠️  Browser fetch не сработал: {exc}")

        # Submit from fedstat.ru itself. A local file:// form can lose SameSite
        # cookies on cross-site POST and Fedstat responds with 403/no download.
        if "fedstat.ru" in (driver.current_url or ""):
            driver.execute_script(
                """
                const [action, entries] = arguments;
                const old = document.getElementById('codex-fedstat-download');
                if (old) old.remove();
                const form = document.createElement('form');
                form.id = 'codex-fedstat-download';
                form.method = 'post';
                form.action = action;
                form.style.display = 'none';
                for (const [name, value] of entries) {
                    const input = document.createElement('input');
                    input.type = 'hidden';
                    input.name = name;
                    input.value = value;
                    form.appendChild(input);
                }
                document.body.appendChild(form);
                form.submit();
                """,
                url,
                entries,
            )
        else:
            inputs = "\n".join(
                f'<input type="hidden" name="{html.escape(str(k), quote=True)}" '
                f'value="{html.escape(str(v), quote=True)}">'
                for k, v in post_data
            )
            form_html = (
                "<!doctype html><meta charset=\"utf-8\">"
                f"<form id=\"fedstat\" method=\"post\" action=\"{html.escape(url, quote=True)}\">"
                f"{inputs}</form>"
                "<script>document.getElementById('fedstat').submit();</script>"
            )
            with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False,
                                             encoding="utf-8") as fh:
                fh.write(form_html)
                form_path = Path(fh.name)
            driver.get(form_path.as_uri())

        downloaded = wait_for_download(save_dir, before_snapshot=before, timeout=BROWSER_POST_TIMEOUT)
        if downloaded is None:
            try:
                page_text = driver.find_element(By.TAG_NAME, "body").text
            except Exception:
                page_text = ""
            if page_text:
                text = " ".join(page_text.split())
                markers = (
                    "слишком большой объем выборки",
                    "результат обработки запроса",
                    "ошибка",
                    "forbidden",
                )
                if any(marker in text.lower() for marker in markers):
                    print(f"  ⚠️  Fedstat ответил страницей: {text[:240]}")
            print(f"  ⚠️  Browser POST не вернул Excel за {BROWSER_POST_TIMEOUT}с")
            return None
        validate_excel_file(downloaded)
        if downloaded.resolve() != save_path.resolve():
            if save_path.exists():
                save_path.unlink()
            shutil.move(str(downloaded), str(save_path))
        return save_path
    except Exception as exc:
        print(f"  ❌ Browser POST fallback не сработал: {exc}")
        return None
    finally:
        if form_path is not None:
            try:
                form_path.unlink()
            except OSError:
                pass


def _should_direct_fallback(indicator_id: str) -> bool:
    real_id = indicator_id.split("_")[0]
    return "*" in DIRECT_FALLBACK_IDS or indicator_id in DIRECT_FALLBACK_IDS or real_id in DIRECT_FALLBACK_IDS


def _payload_34118_part(indicator_id: str) -> dict:
    """Compact EMISS 34118 export in the same layout as emiss_34118_base.xls.

    The full site layout is too large and returns an HTML warning instead of xls.
    Keep 0/30611 as hidden filters so the first two columns remain region/category,
    which is what the dashboard parser expects.
    """
    is_part1 = indicator_id.endswith("часть1")
    years = range(2015, 2023) if is_part1 else range(2023, 2027)
    suffix = "часть1_2015_2022" if is_part1 else "часть2_2023_2026"
    period_ids = [
        "33560_1540222", "33560_1540224", "33560_1540226", "33560_1540227",
        "33560_1540228", "33560_1540229", "33560_1540230", "33560_1540233",
        "33560_1540234", "33560_1540235", "33560_1540236", "33560_1540272",
        "33560_1540273", "33560_1540276", "33560_1540282", "33560_1540283",
        "33560_1540284", "33560_1540285", "33560_1540286", "33560_1540287",
        "33560_1540288", "33560_1540289", "33560_1540290", "33560_1540291",
        "33560_1540292", "33560_1540293", "33560_1540294",
    ]
    region_ids = [
        "57831_1688487",  # Российская Федерация
        "57831_1688506",  # Москва
        "57831_1849012",  # РФ без новых субъектов
    ]
    category_ids = [
        "58389_1754554",  # Жилые дома, построенные населением / ИЖС
        "58389_1754555",  # Жилые дома
        "58389_1754556",  # Жилые здания многоквартирные
    ]
    return {
        "title": "Введено в действие общей площади жилых домов (оперативные данные)",
        "filename_title": (
            f"34118_{suffix}_Введено в действие общей площади жилых домов "
            "(оперативные данные)"
        ),
        "id": "34118",
        "lineObjectIds": ["57831", "58389"],
        "columnObjectIds": ["3", "33560"],
        "selectedFilterIds": (
            ["0_34118"]
            + [f"3_{year}" for year in years]
            + ["30611_950292"]
            + period_ids
            + region_ids
            + category_ids
        ),
        "filterObjectIds": ["0", "30611"],
    }


def _chunked(values: list[str], size: int) -> list[list[str]]:
    return [values[i:i + size] for i in range(0, len(values), size)]


def _is_34118_year_filter(value: str) -> bool:
    return bool(re.fullmatch(r"3_20\d{2}", str(value)))


def _is_34118_period_filter(value: str) -> bool:
    return str(value).startswith("33560_")


def _subset_34118_filters(selected: list[str], years: list[str], periods: list[str]) -> list[str]:
    year_set = set(years)
    period_set = set(periods)
    out: list[str] = []
    for item in selected:
        if _is_34118_year_filter(item):
            if item in year_set:
                out.append(item)
        elif _is_34118_period_filter(item):
            if item in period_set:
                out.append(item)
        else:
            out.append(item)
    return out


def _parse_only_ids(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    out: list[str] = []
    for item in re.split(r"[,;\s]+", raw):
        item = item.strip()
        if not item:
            continue
        if item == "34118":
            out.extend(["34118_часть1", "34118_часть2"])
        else:
            out.append(item)
    return out or None


def _read_fedstat_data_sheet(path: Path) -> pd.DataFrame:
    try:
        return pd.read_excel(path, sheet_name="Данные", header=None)
    except Exception:
        return pd.read_excel(path, sheet_name=0, header=None)


def _merge_fedstat_excel_chunks(paths: list[Path], save_path: Path) -> Path | None:
    frames = []
    for path in paths:
        try:
            df = _read_fedstat_data_sheet(path)
        except Exception as exc:
            print(f"  ⚠️  Не смог прочитать chunk {path.name}: {exc}")
            return None
        if df.shape[1] < 3:
            print(f"  ⚠️  В chunk {path.name} слишком мало колонок")
            return None
        frames.append(df)
    if not frames:
        return None

    base = frames[0].iloc[:, :2].copy()
    out = pd.concat([base] + [df.iloc[:, 2:].reset_index(drop=True) for df in frames], axis=1)
    final_path = save_path.with_suffix(".xlsx")
    final_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = final_path.with_name(f"{final_path.stem}.tmp{final_path.suffix}")
    try:
        with pd.ExcelWriter(tmp_path, engine="openpyxl") as writer:
            out.to_excel(writer, sheet_name="Данные", header=False, index=False)
        validate_excel_file(tmp_path)
        tmp_path.replace(final_path)
        return final_path
    finally:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass


def _download_34118_period_chunks(
    indicator_id: str,
    payload_template: dict,
    save_dir: Path,
    save_path: Path,
    *,
    remote_date: str | None,
    driver=None,
    period_chunk_size: int | None = None,
    year_chunk_size: int | None = None,
) -> Path | None:
    selected = list(payload_template.get("selectedFilterIds", []))
    year_ids = [x for x in selected if _is_34118_year_filter(x)]
    period_ids = [x for x in selected if _is_34118_period_filter(x)]
    period_chunk_size = period_chunk_size or FEDSTAT_34118_CHUNK_SIZE
    year_chunk_size = year_chunk_size or FEDSTAT_34118_YEAR_CHUNK_SIZE
    if len(period_ids) <= period_chunk_size and len(year_ids) <= year_chunk_size:
        return None

    attempts = [(period_chunk_size, year_chunk_size)]
    if (period_chunk_size, year_chunk_size) != (1, 1):
        attempts.append((1, 1))

    for attempt_index, (period_size, year_size) in enumerate(attempts, start=1):
        period_chunks = _chunked(period_ids, period_size)
        year_chunks = _chunked(year_ids, year_size)
        jobs = [(years, periods) for years in year_chunks for periods in period_chunks]
        print(
            f"  -> Делю 34118 на {len(jobs)} маленьких запросов "
            f"(лет до {year_size}, периодов до {period_size})"
        )

        with tempfile.TemporaryDirectory(prefix="fedstat-34118-chunks-") as tmp:
            tmp_dir = Path(tmp)
            chunk_paths: list[Path] = []
            failed = False
            for idx, (year_chunk, period_chunk) in enumerate(jobs, start=1):
                chunk_payload = {
                    key: (list(value) if isinstance(value, list) else value)
                    for key, value in payload_template.items()
                }
                chunk_payload["selectedFilterIds"] = _subset_34118_filters(
                    selected, year_chunk, period_chunk
                )
                chunk_payload["filename_title"] = (
                    f"{payload_template.get('filename_title', payload_template['title'])}_chunk{idx:03d}"
                )
                chunk_path = tmp_dir / f"34118_{indicator_id}_chunk{idx:03d}.xls"
                downloaded = None
                for retry in range(1, FEDSTAT_34118_CHUNK_RETRIES + 1):
                    if retry > 1:
                        print(
                            f"  -> Повторяю chunk {idx}/{len(jobs)} "
                            f"(попытка {retry}/{FEDSTAT_34118_CHUNK_RETRIES})"
                        )
                        if FEDSTAT_34118_CHUNK_RETRY_SLEEP:
                            time.sleep(FEDSTAT_34118_CHUNK_RETRY_SLEEP)
                    downloaded = download_excel(
                        indicator_id,
                        tmp_dir,
                        remote_date=remote_date,
                        driver=driver,
                        payload_template_override=chunk_payload,
                        save_path_override=chunk_path,
                        allow_34118_chunks=False,
                    )
                    if downloaded is not None:
                        break
                if downloaded is None:
                    print(f"  ⚠️  Chunk {idx}/{len(jobs)} не скачался")
                    failed = True
                    break
                chunk_paths.append(downloaded)

            if failed:
                if attempt_index < len(attempts):
                    print("  -> Пробую 34118 максимально мелко: один год и один период")
                    continue
                return None

            merged = _merge_fedstat_excel_chunks(chunk_paths, save_path)
            if merged is not None:
                print(f"  ✅ Собрал 34118 из chunks: {merged}")
                return merged
    return None


def download_excel(indicator_id, save_dir, *, remote_date: str | None = None,
                   driver=None, payload_template_override: dict | None = None,
                   save_path_override: Path | None = None,
                   allow_34118_chunks: bool = True):
    PAYLOADS = {
        # Введено в действие общей площади жилых домов (оперативные данные).
        # Параметры — из data/raw/realty/vvod/34118_filter.txt (экспорт ЕМИСС).
        # Индикатор (0) и категория (58389) вынесены в строки (lineObjectIds) →
        # выгрузка получается «регион × категория» по годам, как в
        # emiss_34118_base.xls. filterObjectIds в фильтре отсутствует —
        # все измерения распределены по строкам/столбцам.
        "34118": {
            "title": "Введено в действие общей площади жилых домов (оперативные данные)",
            "id": "34118",
            "lineObjectIds": ["0", "30611", "57831", "58389"],
            "columnObjectIds": ["3", "33560"],
            "selectedFilterIds": [
                "0_34118", "3_2024", "3_2025", "3_2026", "30611_950292", "33560_1540222",
                "33560_1540224", "33560_1540226", "33560_1540227", "33560_1540228", "33560_1540229", "33560_1540230",
                "33560_1540233", "33560_1540234", "33560_1540235", "33560_1540236", "33560_1540272", "33560_1540273",
                "33560_1540276", "33560_1540282", "33560_1540283", "33560_1540284", "33560_1540285", "33560_1540286",
                "33560_1540287", "33560_1540288", "33560_1540289", "33560_1540290", "33560_1540291", "33560_1540292",
                "33560_1540293", "33560_1540294", "57831_1688487", "57831_1688506", "57831_1849012", "58389_1754554",
                "58389_1754555", "58389_1754556", "58389_1754557", "58389_1754558", "58389_1754559", "58389_1754560",
                "58389_1754561", "58389_1754562", "58389_1754563", "58389_1754564", "58389_1754565", "58389_1754566",
                "58389_1754567", "58389_1754568", "58389_1754569", "58389_1836598", "58389_1836599"],
        },
        "33648": {
            "title": "Индекс предпринимательской уверенности в строительстве (процент)",
            "id": "33648",
            "lineObjectIds": "57831",
            "columnObjectIds": ["3", "33560"],
            "selectedFilterIds": ["0_33648", "3_2005", "3_2006", "3_2007", "3_2008", "3_2009", "3_2010", "3_2011",
                "3_2012", "3_2013", "3_2014", "3_2015", "3_2016", "3_2017", "3_2018", "3_2019",
                "3_2020", "3_2021", "3_2022", "3_2023", "3_2024", "3_2025", "3_2026", "30611_950473",
                "33560_1540222", "33560_1540224", "33560_1540226", "33560_1540227", "57831_1688487", "57831_1688488", "57831_1688489", "57831_1688490",
                "57831_1688491", "57831_1688492", "57831_1688493", "57831_1688494", "57831_1688495", "57831_1688496", "57831_1688497", "57831_1688498",
                "57831_1688499", "57831_1688500", "57831_1688501", "57831_1688502", "57831_1688503", "57831_1688504", "57831_1688505", "57831_1688506",
                "57831_1688507", "57831_1688508", "57831_1688509", "57831_1688510", "57831_1688511", "57831_1688512", "57831_1688513", "57831_1688514",
                "57831_1688515", "57831_1688516", "57831_1688517", "57831_1688518", "57831_1688519", "57831_1688521", "57831_1688522", "57831_1688523",
                "57831_1688524", "57831_1688525", "57831_1688526", "57831_1688527", "57831_1688528", "57831_1688529", "57831_1688530", "57831_1688531",
                "57831_1688532", "57831_1688533", "57831_1688534", "57831_1688535", "57831_1688536", "57831_1688537", "57831_1688538", "57831_1688539",
                "57831_1688540", "57831_1688541", "57831_1688542", "57831_1688543", "57831_1688545", "57831_1688546", "57831_1688547", "57831_1688548",
                "57831_1688549", "57831_1688550", "57831_1688551", "57831_1688552", "57831_1688553", "57831_1688554", "57831_1688555", "57831_1688556",
                "57831_1688557", "57831_1688558", "57831_1688559", "57831_1688560", "57831_1688561", "57831_1688562", "57831_1688563", "57831_1688564",
                "57831_1688565", "57831_1688566", "57831_1688568", "57831_1688571", "57831_1688573", "57831_1688574", "57831_1688575", "57831_1688576",
                "57831_1688577", "57831_1688578", "57831_1688579", "57831_1688581", "57831_1688582", "57831_1688583", "57831_1688584", "57831_1688585",
                "57831_1688586", "57831_1688587", "57831_1692937", "57831_1692938", "57831_1692939", "57831_1692940", "57831_1695534", "57831_1697988",
                "57831_1795276", "57831_1795277", "57831_1849012"],
            "filterObjectIds": ["0", "30611"],
        },
        "61497": {
            "title": "Валовой региональный продукт в основных ценах (ОКВЭД 2) (тысяча рублей, значение показателя за год)",
            "id": "61497",
            "lineObjectIds": "57940",
            "columnObjectIds": ["3", "58358"],
            "groupObjectIds": "57831",
            "selectedFilterIds": ["0_61497", "3_2016", "3_2017", "3_2018", "3_2019", "3_2020", "3_2021", "3_2022",
                "3_2023", "3_2024", "30611_950352", "33560_1558883", "57831_1688487", "57831_1688488", "57831_1688489", "57831_1688490",
                "57831_1688491", "57831_1688492", "57831_1688493", "57831_1688494", "57831_1688495", "57831_1688496", "57831_1688497", "57831_1688498",
                "57831_1688499", "57831_1688500", "57831_1688501", "57831_1688502", "57831_1688503", "57831_1688504", "57831_1688505", "57831_1688506",
                "57831_1688507", "57831_1688508", "57831_1688509", "57831_1688510", "57831_1688511", "57831_1688513", "57831_1688514", "57831_1688515",
                "57831_1688516", "57831_1688517", "57831_1688518", "57831_1688519", "57831_1688522", "57831_1688523", "57831_1688524", "57831_1688525",
                "57831_1688526", "57831_1688527", "57831_1688528", "57831_1688529", "57831_1688530", "57831_1688531", "57831_1688532", "57831_1688533",
                "57831_1688534", "57831_1688535", "57831_1688536", "57831_1688537", "57831_1688538", "57831_1688539", "57831_1688540", "57831_1688541",
                "57831_1688542", "57831_1688543", "57831_1688545", "57831_1688546", "57831_1688547", "57831_1688548", "57831_1688549", "57831_1688550",
                "57831_1688551", "57831_1688552", "57831_1688553", "57831_1688554", "57831_1688555", "57831_1688556", "57831_1688557", "57831_1688559",
                "57831_1688561", "57831_1688562", "57831_1688563", "57831_1688564", "57831_1688565", "57831_1688566", "57831_1688568", "57831_1688571",
                "57831_1688573", "57831_1688574", "57831_1688575", "57831_1688576", "57831_1688578", "57831_1688579", "57831_1688581", "57831_1688582",
                "57831_1688583", "57831_1688584", "57831_1688585", "57831_1688586", "57831_1688587", "57831_1692937", "57831_1692938", "57831_1692939",
                "57831_1692940", "57831_1695534", "57831_1707677", "57831_1795276", "57831_1795277", "57831_1849012", "57940_1692320", "57940_1692321",
                "57940_1692322", "57940_1692323", "57940_1692324", "57940_1692325", "57940_1692326", "57940_1692327", "57940_1692328", "57940_1692329",
                "57940_1692330", "57940_1692331", "57940_1692332", "57940_1692748", "57940_1692749", "57940_1692750", "57940_1692751", "57940_1692933",
                "57940_1695397", "57940_1695572", "57940_1696414", "58358_1710057", "58358_1830965"],
            "filterObjectIds": ["0", "30611", "33560"],
        },
        "59449": {
            "title": "Индексы физического объема валового регионального продукта (ОКВЭД 2) (процент, значение показателя за год)",
            "id": "59449",
            "lineObjectIds": "57940",
            "columnObjectIds": "3",
            "groupObjectIds": "57831",
            "selectedFilterIds": ["0_59449", "3_2017", "3_2018", "3_2019", "3_2020", "3_2021", "3_2022", "3_2023",
                "3_2024", "30611_950473", "33560_1558883", "57831_1688487", "57831_1688488", "57831_1688489", "57831_1688490", "57831_1688491",
                "57831_1688492", "57831_1688493", "57831_1688494", "57831_1688495", "57831_1688496", "57831_1688497", "57831_1688498", "57831_1688499",
                "57831_1688500", "57831_1688501", "57831_1688502", "57831_1688503", "57831_1688504", "57831_1688505", "57831_1688506", "57831_1688507",
                "57831_1688508", "57831_1688509", "57831_1688510", "57831_1688511", "57831_1688513", "57831_1688514", "57831_1688515", "57831_1688516",
                "57831_1688517", "57831_1688518", "57831_1688519", "57831_1688522", "57831_1688523", "57831_1688524", "57831_1688525", "57831_1688526",
                "57831_1688527", "57831_1688528", "57831_1688529", "57831_1688530", "57831_1688531", "57831_1688532", "57831_1688533", "57831_1688534",
                "57831_1688535", "57831_1688536", "57831_1688537", "57831_1688538", "57831_1688539", "57831_1688540", "57831_1688541", "57831_1688542",
                "57831_1688543", "57831_1688545", "57831_1688546", "57831_1688547", "57831_1688548", "57831_1688549", "57831_1688550", "57831_1688551",
                "57831_1688552", "57831_1688553", "57831_1688554", "57831_1688555", "57831_1688556", "57831_1688557", "57831_1688559", "57831_1688561",
                "57831_1688562", "57831_1688563", "57831_1688564", "57831_1688565", "57831_1688566", "57831_1688568", "57831_1688571", "57831_1688573",
                "57831_1688574", "57831_1688575", "57831_1688576", "57831_1688578", "57831_1688579", "57831_1688581", "57831_1688582", "57831_1688583",
                "57831_1688584", "57831_1688585", "57831_1688586", "57831_1688587", "57831_1692937", "57831_1692938", "57831_1692939", "57831_1692940",
                "57831_1695534", "57831_1795276", "57831_1795277", "57831_1849012", "57940_1692320", "57940_1692321", "57940_1692322", "57940_1692323",
                "57940_1692324", "57940_1692325", "57940_1692326", "57940_1692327", "57940_1692328", "57940_1692329", "57940_1692330", "57940_1692331",
                "57940_1692332", "57940_1692748", "57940_1692749", "57940_1692750", "57940_1692751", "57940_1692933", "57940_1695397", "57940_1695572",
                "57940_1696414"],
            "filterObjectIds": ["0", "30611", "33560"],
        },
        "61483": {
            "title": "Валовой региональный продукт на душу населения (ОКВЭД 2)",
            "id": "61483",
            "lineObjectIds": ["57940", "57831", "30611"],
            "columnObjectIds": ["3", "33560"],
            "selectedFilterIds": ["0_61483", "3_2016", "3_2017", "3_2018", "3_2019", "3_2020", "3_2021", "3_2022",
                "3_2023", "3_2024", "30611_950351", "33560_1558883", "57831_1688487", "57831_1688488", "57831_1688489", "57831_1688490",
                "57831_1688491", "57831_1688492", "57831_1688493", "57831_1688494", "57831_1688495", "57831_1688496", "57831_1688497", "57831_1688498",
                "57831_1688499", "57831_1688500", "57831_1688501", "57831_1688502", "57831_1688503", "57831_1688504", "57831_1688505", "57831_1688506",
                "57831_1688507", "57831_1688508", "57831_1688509", "57831_1688510", "57831_1688511", "57831_1688513", "57831_1688514", "57831_1688515",
                "57831_1688516", "57831_1688517", "57831_1688518", "57831_1688519", "57831_1688522", "57831_1688523", "57831_1688524", "57831_1688525",
                "57831_1688526", "57831_1688527", "57831_1688528", "57831_1688529", "57831_1688530", "57831_1688531", "57831_1688532", "57831_1688533",
                "57831_1688534", "57831_1688535", "57831_1688536", "57831_1688537", "57831_1688538", "57831_1688539", "57831_1688540", "57831_1688541",
                "57831_1688542", "57831_1688543", "57831_1688545", "57831_1688546", "57831_1688547", "57831_1688548", "57831_1688549", "57831_1688550",
                "57831_1688551", "57831_1688552", "57831_1688553", "57831_1688554", "57831_1688555", "57831_1688556", "57831_1688557", "57831_1688559",
                "57831_1688561", "57831_1688562", "57831_1688563", "57831_1688564", "57831_1688565", "57831_1688566", "57831_1688568", "57831_1688571",
                "57831_1688573", "57831_1688574", "57831_1688575", "57831_1688576", "57831_1688578", "57831_1688579", "57831_1688581", "57831_1688582",
                "57831_1688583", "57831_1688584", "57831_1688585", "57831_1688586", "57831_1688587", "57831_1692937", "57831_1692938", "57831_1692939",
                "57831_1692940", "57831_1695534", "57831_1795276", "57831_1795277", "57831_1849012", "57940_1692933"],
            "filterObjectIds": "0",
        },
        "45341": {
            "title": "Индекс физического объема валового регионального продукта на душу населения (процент, значение показателя за год)",
            "id": "45341",
            "lineObjectIds": "57831",
            "columnObjectIds": "3",
            "selectedFilterIds": ["0_45341", "3_2005", "3_2006", "3_2007", "3_2008", "3_2009", "3_2010", "3_2011",
                "3_2012", "3_2013", "3_2014", "3_2015", "3_2016", "30611_950473", "33560_1558883", "57831_1688487",
                "57831_1688488", "57831_1688489", "57831_1688490", "57831_1688491", "57831_1688492", "57831_1688493", "57831_1688494", "57831_1688495",
                "57831_1688496", "57831_1688497", "57831_1688498", "57831_1688499", "57831_1688500", "57831_1688501", "57831_1688502", "57831_1688503",
                "57831_1688504", "57831_1688505", "57831_1688506", "57831_1688507", "57831_1688508", "57831_1688509", "57831_1688510", "57831_1688511",
                "57831_1688513", "57831_1688514", "57831_1688515", "57831_1688516", "57831_1688517", "57831_1688518", "57831_1688519", "57831_1688522",
                "57831_1688523", "57831_1688524", "57831_1688525", "57831_1688526", "57831_1688527", "57831_1688528", "57831_1688529", "57831_1688530",
                "57831_1688531", "57831_1688532", "57831_1688533", "57831_1688534", "57831_1688535", "57831_1688536", "57831_1688537", "57831_1688538",
                "57831_1688539", "57831_1688540", "57831_1688541", "57831_1688542", "57831_1688543", "57831_1688545", "57831_1688546", "57831_1688547",
                "57831_1688548", "57831_1688549", "57831_1688550", "57831_1688551", "57831_1688552", "57831_1688553", "57831_1688554", "57831_1688555",
                "57831_1688556", "57831_1688557", "57831_1688559", "57831_1688561", "57831_1688562", "57831_1688563", "57831_1688564", "57831_1688565",
                "57831_1688566", "57831_1688568", "57831_1688571", "57831_1688573", "57831_1688574", "57831_1688575", "57831_1688576", "57831_1688578",
                "57831_1688579", "57831_1688581", "57831_1688582", "57831_1688583", "57831_1688584", "57831_1688585", "57831_1688586", "57831_1688587",
                "57831_1692937", "57831_1692938", "57831_1692939", "57831_1692940", "57831_1695534", "57831_1795276", "57831_1795277"],
            "filterObjectIds": ["0", "30611", "33560"],
        },
        "59450": {
            "title": "Структура валового регионального продукта (ОКВЭД 2) (процент, значение показателя за год)",
            "id": "59450",
            "lineObjectIds": "57831",
            "columnObjectIds": "3",
            "groupObjectIds": "57940",
            "selectedFilterIds": ["0_59450", "3_2016", "3_2017", "3_2018", "3_2019", "3_2020", "3_2021", "3_2022",
                "3_2023", "3_2024", "30611_950473", "33560_1558883", "57831_1688487", "57831_1688488", "57831_1688489", "57831_1688490",
                "57831_1688491", "57831_1688492", "57831_1688493", "57831_1688494", "57831_1688495", "57831_1688496", "57831_1688497", "57831_1688498",
                "57831_1688499", "57831_1688500", "57831_1688501", "57831_1688502", "57831_1688503", "57831_1688504", "57831_1688505", "57831_1688506",
                "57831_1688507", "57831_1688508", "57831_1688509", "57831_1688510", "57831_1688511", "57831_1688513", "57831_1688514", "57831_1688515",
                "57831_1688516", "57831_1688517", "57831_1688518", "57831_1688519", "57831_1688522", "57831_1688523", "57831_1688524", "57831_1688525",
                "57831_1688526", "57831_1688527", "57831_1688528", "57831_1688529", "57831_1688530", "57831_1688531", "57831_1688532", "57831_1688533",
                "57831_1688534", "57831_1688535", "57831_1688536", "57831_1688537", "57831_1688538", "57831_1688539", "57831_1688540", "57831_1688541",
                "57831_1688542", "57831_1688543", "57831_1688545", "57831_1688546", "57831_1688547", "57831_1688548", "57831_1688549", "57831_1688550",
                "57831_1688551", "57831_1688552", "57831_1688553", "57831_1688554", "57831_1688555", "57831_1688556", "57831_1688557", "57831_1688559",
                "57831_1688561", "57831_1688562", "57831_1688563", "57831_1688564", "57831_1688565", "57831_1688566", "57831_1688568", "57831_1688571",
                "57831_1688573", "57831_1688574", "57831_1688575", "57831_1688576", "57831_1688578", "57831_1688579", "57831_1688581", "57831_1688582",
                "57831_1688583", "57831_1688584", "57831_1688585", "57831_1688586", "57831_1688587", "57831_1692937", "57831_1692938", "57831_1692939",
                "57831_1692940", "57831_1695534", "57831_1795276", "57831_1795277", "57831_1849012", "57940_1692320", "57940_1692321", "57940_1692322",
                "57940_1692323", "57940_1692324", "57940_1692325", "57940_1692326", "57940_1692327", "57940_1692328", "57940_1692329", "57940_1692330",
                "57940_1692331", "57940_1692332", "57940_1692748", "57940_1692749", "57940_1692750", "57940_1692751", "57940_1692933", "57940_1695397",
                "57940_1695572", "57940_1696414"],
            "filterObjectIds": ["0", "30611", "33560"],
        },
        "55370": {
            "title": "Доля продукции высокотехнологичных и наукоемких отраслей в валовом региональном продукте относительно уровня 2011 года (процент, значение показателя за год, К базисному периоду)",
            "id": "55370",
            "lineObjectIds": "57831",
            "columnObjectIds": "3",
            "selectedFilterIds": ["0_55370", "3_2012", "3_2013", "3_2014", "3_2015", "3_2016", "3_2017", "3_2018",
                "3_2019", "3_2020", "3_2021", "3_2022", "3_2023", "3_2024", "30611_950473", "33560_1558883",
                "57831_1688487", "57831_1688488", "57831_1688489", "57831_1688490", "57831_1688491", "57831_1688492", "57831_1688493", "57831_1688494",
                "57831_1688495", "57831_1688496", "57831_1688497", "57831_1688498", "57831_1688499", "57831_1688500", "57831_1688501", "57831_1688502",
                "57831_1688503", "57831_1688504", "57831_1688505", "57831_1688506", "57831_1688507", "57831_1688508", "57831_1688509", "57831_1688510",
                "57831_1688511", "57831_1688512", "57831_1688513", "57831_1688514", "57831_1688515", "57831_1688516", "57831_1688517", "57831_1688518",
                "57831_1688519", "57831_1688521", "57831_1688522", "57831_1688523", "57831_1688524", "57831_1688525", "57831_1688526", "57831_1688527",
                "57831_1688528", "57831_1688529", "57831_1688530", "57831_1688531", "57831_1688532", "57831_1688533", "57831_1688534", "57831_1688535",
                "57831_1688536", "57831_1688537", "57831_1688538", "57831_1688539", "57831_1688540", "57831_1688541", "57831_1688542", "57831_1688543",
                "57831_1688545", "57831_1688546", "57831_1688547", "57831_1688548", "57831_1688549", "57831_1688550", "57831_1688551", "57831_1688552",
                "57831_1688553", "57831_1688554", "57831_1688555", "57831_1688556", "57831_1688557", "57831_1688558", "57831_1688559", "57831_1688560",
                "57831_1688561", "57831_1688562", "57831_1688563", "57831_1688564", "57831_1688565", "57831_1688566", "57831_1688568", "57831_1688571",
                "57831_1688573", "57831_1688574", "57831_1688575", "57831_1688576", "57831_1688577", "57831_1688578", "57831_1688579", "57831_1688581",
                "57831_1688582", "57831_1688583", "57831_1688584", "57831_1688585", "57831_1688586", "57831_1688587", "57831_1692937", "57831_1692938",
                "57831_1692939", "57831_1692940", "57831_1695534", "57831_1795276", "57831_1795277", "57831_1849012", "57937_1707669"],
            "filterObjectIds": ["0", "30611", "33560", "57937"],
        },
        "55373": {
            "title": "Индекс производительности труда относительно уровня 2011 года (процент, значение показателя за год, К базисному периоду)",
            "id": "55373",
            "lineObjectIds": "57831",
            "columnObjectIds": "3",
            "selectedFilterIds": ["0_55373", "3_2012", "3_2013", "3_2014", "3_2015", "3_2016", "3_2017", "3_2018",
                "3_2019", "3_2020", "3_2021", "3_2022", "3_2023", "3_2024", "30611_950473", "33560_1558883",
                "57831_1688487", "57831_1688488", "57831_1688489", "57831_1688490", "57831_1688491", "57831_1688492", "57831_1688493", "57831_1688494",
                "57831_1688495", "57831_1688496", "57831_1688497", "57831_1688498", "57831_1688499", "57831_1688500", "57831_1688501", "57831_1688502",
                "57831_1688503", "57831_1688504", "57831_1688505", "57831_1688506", "57831_1688507", "57831_1688508", "57831_1688509", "57831_1688510",
                "57831_1688511", "57831_1688512", "57831_1688513", "57831_1688514", "57831_1688515", "57831_1688516", "57831_1688517", "57831_1688518",
                "57831_1688519", "57831_1688522", "57831_1688523", "57831_1688524", "57831_1688525", "57831_1688526", "57831_1688527", "57831_1688528",
                "57831_1688529", "57831_1688530", "57831_1688531", "57831_1688532", "57831_1688533", "57831_1688534", "57831_1688535", "57831_1688536",
                "57831_1688537", "57831_1688538", "57831_1688539", "57831_1688540", "57831_1688541", "57831_1688542", "57831_1688543", "57831_1688545",
                "57831_1688546", "57831_1688547", "57831_1688548", "57831_1688549", "57831_1688550", "57831_1688551", "57831_1688552", "57831_1688553",
                "57831_1688554", "57831_1688555", "57831_1688556", "57831_1688557", "57831_1688558", "57831_1688559", "57831_1688561", "57831_1688562",
                "57831_1688563", "57831_1688564", "57831_1688565", "57831_1688566", "57831_1688568", "57831_1688571", "57831_1688573", "57831_1688574",
                "57831_1688575", "57831_1688576", "57831_1688578", "57831_1688579", "57831_1688581", "57831_1688582", "57831_1688583", "57831_1688584",
                "57831_1688585", "57831_1688586", "57831_1688587", "57831_1692937", "57831_1692938", "57831_1692939", "57831_1692940", "57831_1695534",
                "57831_1849012", "57937_1707669"],
            "filterObjectIds": ["0", "30611", "33560", "57937"],
        },
        "31557": {
            "title": "Численность постоянного населения на 1 января (Раздел 1. Муниципальные образования субъектов Российской Федерации)",
            "id": "31557",
            "lineObjectIds": ["57831", "30611", "58274"],
            "columnObjectIds": ["3", "33560"],
            "selectedFilterIds": ["0_31557", "3_1990", "3_1991", "3_1992", "3_1993", "3_1994", "3_1995", "3_1996",
                "3_1997", "3_1998", "3_1999", "3_2000", "3_2001", "3_2002", "3_2003", "3_2004",
                "3_2005", "3_2006", "3_2007", "3_2008", "3_2009", "3_2010", "3_2011", "3_2012",
                "3_2013", "3_2014", "3_2015", "3_2016", "3_2017", "3_2018", "3_2019", "3_2020",
                "3_2021", "3_2022", "3_2023", "3_2024", "3_2025", "30611_950458", "33560_1540248", "57831_1688487",
                "57831_1688488", "57831_1688489", "57831_1688490", "57831_1688491", "57831_1688492", "57831_1688493", "57831_1688494", "57831_1688495",
                "57831_1688496", "57831_1688497", "57831_1688498", "57831_1688499", "57831_1688500", "57831_1688501", "57831_1688502", "57831_1688503",
                "57831_1688504", "57831_1688505", "57831_1688506", "57831_1688507", "57831_1688508", "57831_1688509", "57831_1688510", "57831_1688511",
                "57831_1688512", "57831_1688513", "57831_1688514", "57831_1688515", "57831_1688516", "57831_1688517", "57831_1688518", "57831_1688519",
                "57831_1688520", "57831_1688521", "57831_1688522", "57831_1688523", "57831_1688524", "57831_1688525", "57831_1688526", "57831_1688527",
                "57831_1688528", "57831_1688529", "57831_1688530", "57831_1688531", "57831_1688532", "57831_1688533", "57831_1688534", "57831_1688535",
                "57831_1688536", "57831_1688537", "57831_1688538", "57831_1688539", "57831_1688540", "57831_1688541", "57831_1688542", "57831_1688543",
                "57831_1688544", "57831_1688545", "57831_1688546", "57831_1688547", "57831_1688548", "57831_1688549", "57831_1688550", "57831_1688551",
                "57831_1688552", "57831_1688553", "57831_1688554", "57831_1688555", "57831_1688556", "57831_1688557", "57831_1688558", "57831_1688559",
                "57831_1688560", "57831_1688561", "57831_1688562", "57831_1688563", "57831_1688564", "57831_1688565", "57831_1688566", "57831_1688567",
                "57831_1688568", "57831_1688569", "57831_1688570", "57831_1688571", "57831_1688572", "57831_1688573", "57831_1688574", "57831_1688575",
                "57831_1688576", "57831_1688577", "57831_1688578", "57831_1688579", "57831_1688580", "57831_1688581", "57831_1688582", "57831_1688583",
                "57831_1688584", "57831_1688585", "57831_1688586", "57831_1688587", "57831_1692937", "57831_1692938", "57831_1692939", "57831_1692940",
                "57831_1695534", "57831_1697988", "57831_1795276", "57831_1795277", "57831_1849012", "58246_1784669", "58274_1744150", "58274_1750788",
                "58274_1750789"],
            "filterObjectIds": ["0", "58246"],
        },
        "31556": {
            "title": "Численность постоянного населения в среднем за год (человек, значение показателя за год)",
            "id": "31556",
            "lineObjectIds": ["57831", "58274"],
            "columnObjectIds": "3",
            "selectedFilterIds": ["0_31556", "3_1990", "3_1991", "3_1992", "3_1993", "3_1994", "3_1995", "3_1996",
                "3_1997", "3_1998", "3_1999", "3_2000", "3_2001", "3_2002", "3_2003", "3_2004",
                "3_2005", "3_2006", "3_2007", "3_2008", "3_2009", "3_2010", "3_2011", "3_2012",
                "3_2013", "3_2014", "3_2015", "3_2016", "3_2017", "3_2018", "3_2019", "3_2020",
                "3_2021", "3_2022", "3_2023", "3_2024", "30611_950458", "33560_1558883", "57831_1688487", "57831_1688488",
                "57831_1688489", "57831_1688490", "57831_1688491", "57831_1688492", "57831_1688493", "57831_1688494", "57831_1688495", "57831_1688496",
                "57831_1688497", "57831_1688498", "57831_1688499", "57831_1688500", "57831_1688501", "57831_1688502", "57831_1688503", "57831_1688504",
                "57831_1688505", "57831_1688506", "57831_1688507", "57831_1688508", "57831_1688509", "57831_1688510", "57831_1688511", "57831_1688512",
                "57831_1688513", "57831_1688514", "57831_1688515", "57831_1688516", "57831_1688517", "57831_1688518", "57831_1688519", "57831_1688520",
                "57831_1688521", "57831_1688522", "57831_1688523", "57831_1688524", "57831_1688525", "57831_1688526", "57831_1688527", "57831_1688528",
                "57831_1688529", "57831_1688530", "57831_1688531", "57831_1688532", "57831_1688533", "57831_1688534", "57831_1688535", "57831_1688536",
                "57831_1688537", "57831_1688538", "57831_1688539", "57831_1688540", "57831_1688541", "57831_1688542", "57831_1688543", "57831_1688544",
                "57831_1688545", "57831_1688546", "57831_1688547", "57831_1688548", "57831_1688549", "57831_1688550", "57831_1688551", "57831_1688552",
                "57831_1688553", "57831_1688554", "57831_1688555", "57831_1688556", "57831_1688557", "57831_1688558", "57831_1688559", "57831_1688560",
                "57831_1688561", "57831_1688562", "57831_1688563", "57831_1688564", "57831_1688565", "57831_1688566", "57831_1688567", "57831_1688568",
                "57831_1688569", "57831_1688570", "57831_1688571", "57831_1688572", "57831_1688573", "57831_1688574", "57831_1688575", "57831_1688576",
                "57831_1688577", "57831_1688578", "57831_1688579", "57831_1688580", "57831_1688581", "57831_1688582", "57831_1688583", "57831_1688584",
                "57831_1688585", "57831_1688586", "57831_1688587", "57831_1692937", "57831_1692938", "57831_1692939", "57831_1692940", "57831_1695534",
                "57831_1697988", "57831_1709529", "57831_1709530", "57831_1709531", "57831_1709532", "57831_1709533", "57831_1709534", "57831_1709535",
                "57831_1709536", "57831_1709537", "57831_1709538", "57831_1709539", "57831_1743321", "57831_1795276", "57831_1795277", "57831_1849012",
                "58274_1744150", "58274_1750788", "58274_1750789"],
            "filterObjectIds": ["0", "30611", "33560"],
        },
        "36730": {
            "title": "Среднегодовая численность трудовых ресурсов (значение показателя за год)",
            "id": "36730",
            "lineObjectIds": ["57831", "30611", "58328"],
            "columnObjectIds": "3",
            "selectedFilterIds": ["0_36730", "3_2011", "3_2012", "3_2013", "3_2014", "3_2015", "3_2016", "3_2017",
                "3_2018", "3_2019", "3_2020", "3_2021", "3_2022", "3_2023", "3_2024", "30611_950458",
                "33560_1558883", "57831_1688487", "57831_1688488", "57831_1688489", "57831_1688490", "57831_1688491", "57831_1688492", "57831_1688493",
                "57831_1688494", "57831_1688495", "57831_1688496", "57831_1688497", "57831_1688498", "57831_1688499", "57831_1688500", "57831_1688501",
                "57831_1688502", "57831_1688503", "57831_1688504", "57831_1688505", "57831_1688506", "57831_1688507", "57831_1688508", "57831_1688509",
                "57831_1688510", "57831_1688511", "57831_1688513", "57831_1688514", "57831_1688515", "57831_1688516", "57831_1688517", "57831_1688518",
                "57831_1688519", "57831_1688521", "57831_1688522", "57831_1688523", "57831_1688524", "57831_1688525", "57831_1688526", "57831_1688527",
                "57831_1688528", "57831_1688529", "57831_1688530", "57831_1688531", "57831_1688532", "57831_1688533", "57831_1688534", "57831_1688535",
                "57831_1688536", "57831_1688537", "57831_1688538", "57831_1688539", "57831_1688540", "57831_1688541", "57831_1688542", "57831_1688543",
                "57831_1688545", "57831_1688546", "57831_1688547", "57831_1688548", "57831_1688549", "57831_1688550", "57831_1688551", "57831_1688552",
                "57831_1688553", "57831_1688554", "57831_1688555", "57831_1688556", "57831_1688557", "57831_1688559", "57831_1688560", "57831_1688561",
                "57831_1688562", "57831_1688563", "57831_1688564", "57831_1688565", "57831_1688566", "57831_1688568", "57831_1688571", "57831_1688573",
                "57831_1688574", "57831_1688575", "57831_1688576", "57831_1688577", "57831_1688578", "57831_1688579", "57831_1688581", "57831_1688582",
                "57831_1688583", "57831_1688584", "57831_1688585", "57831_1688586", "57831_1688587", "57831_1692937", "57831_1692938", "57831_1692939",
                "57831_1692940", "57831_1695534", "57831_1795276", "57831_1795277", "57831_1849012", "58328_1709473", "58328_1750840", "58328_1794179",
                "58328_1794181", "58328_1795521", "58328_1830593"],
            "filterObjectIds": ["0", "33560"],
        },
        "58994": {
            "title": "Среднегодовая численность занятых в экономике (расчеты на основе интеграции данных) с 2017 г. (человек, значение показателя за год)",
            "id": "58994",
            "lineObjectIds": ["57831", "57940"],
            "columnObjectIds": "3",
            "selectedFilterIds": ["0_58994", "3_2017", "3_2018", "3_2019", "3_2020", "3_2021", "3_2022", "3_2023",
                "3_2024", "30611_950458", "33560_1558883", "57831_1688487", "57831_1688488", "57831_1688489", "57831_1688490", "57831_1688491",
                "57831_1688492", "57831_1688493", "57831_1688494", "57831_1688495", "57831_1688496", "57831_1688497", "57831_1688498", "57831_1688499",
                "57831_1688500", "57831_1688501", "57831_1688502", "57831_1688503", "57831_1688504", "57831_1688505", "57831_1688506", "57831_1688507",
                "57831_1688508", "57831_1688509", "57831_1688510", "57831_1688511", "57831_1688513", "57831_1688514", "57831_1688515", "57831_1688516",
                "57831_1688517", "57831_1688518", "57831_1688519", "57831_1688522", "57831_1688523", "57831_1688524", "57831_1688525", "57831_1688526",
                "57831_1688527", "57831_1688528", "57831_1688529", "57831_1688530", "57831_1688531", "57831_1688532", "57831_1688533", "57831_1688534",
                "57831_1688535", "57831_1688536", "57831_1688537", "57831_1688538", "57831_1688539", "57831_1688540", "57831_1688541", "57831_1688542",
                "57831_1688543", "57831_1688545", "57831_1688546", "57831_1688547", "57831_1688548", "57831_1688549", "57831_1688550", "57831_1688551",
                "57831_1688552", "57831_1688553", "57831_1688554", "57831_1688555", "57831_1688556", "57831_1688557", "57831_1688559", "57831_1688561",
                "57831_1688562", "57831_1688563", "57831_1688564", "57831_1688565", "57831_1688566", "57831_1688568", "57831_1688571", "57831_1688573",
                "57831_1688574", "57831_1688575", "57831_1688576", "57831_1688578", "57831_1688579", "57831_1688581", "57831_1688582", "57831_1688583",
                "57831_1688584", "57831_1688585", "57831_1688586", "57831_1688587", "57831_1692937", "57831_1692938", "57831_1692939", "57831_1692940",
                "57831_1695534", "57831_1795276", "57831_1795277", "57831_1849012", "57940_1692309", "57940_1692320", "57940_1692321", "57940_1692322",
                "57940_1692323", "57940_1692324", "57940_1692325", "57940_1692326", "57940_1692327", "57940_1692328", "57940_1692329", "57940_1692330",
                "57940_1692331", "57940_1692332", "57940_1692333", "57940_1692334", "57940_1692340", "57940_1692341", "57940_1692342", "57940_1692343",
                "57940_1692344", "57940_1692345", "57940_1692346", "57940_1692347", "57940_1692348", "57940_1692349", "57940_1692350", "57940_1692351",
                "57940_1692352", "57940_1692353", "57940_1692354", "57940_1692355", "57940_1692356", "57940_1692357", "57940_1692362", "57940_1692363",
                "57940_1692364", "57940_1692369", "57940_1692748", "57940_1692749", "57940_1692750", "57940_1692751", "57940_1692796", "57940_1692800",
                "57940_1692818", "57940_1692819", "57940_1692835", "57940_1692868", "57940_1692933", "57940_1694996", "57940_1694997", "57940_1695397",
                "57940_1695572", "57940_1696129", "57940_1696414"],
            "filterObjectIds": ["0", "30611", "33560"],
        },
        "43211": {
            "title": "Среднегодовая численность занятых в экономике (расчеты на основе интеграции данных) по 2016 г. (человек, значение показателя за год)",
            "id": "43211",
            "lineObjectIds": ["57833", "57831"],
            "columnObjectIds": ["3", "0"],
            "selectedFilterIds": ["0_43211", "3_2012", "3_2013", "3_2014", "3_2015", "3_2016", "30611_950458", "33560_1558883",
                "57831_1688487", "57831_1688488", "57831_1688489", "57831_1688490", "57831_1688491", "57831_1688492", "57831_1688493", "57831_1688494",
                "57831_1688495", "57831_1688496", "57831_1688497", "57831_1688498", "57831_1688499", "57831_1688500", "57831_1688501", "57831_1688502",
                "57831_1688503", "57831_1688504", "57831_1688505", "57831_1688506", "57831_1688507", "57831_1688508", "57831_1688509", "57831_1688510",
                "57831_1688511", "57831_1688513", "57831_1688514", "57831_1688515", "57831_1688516", "57831_1688517", "57831_1688518", "57831_1688519",
                "57831_1688521", "57831_1688522", "57831_1688523", "57831_1688524", "57831_1688525", "57831_1688526", "57831_1688527", "57831_1688528",
                "57831_1688529", "57831_1688530", "57831_1688531", "57831_1688532", "57831_1688533", "57831_1688534", "57831_1688535", "57831_1688536",
                "57831_1688537", "57831_1688538", "57831_1688539", "57831_1688540", "57831_1688541", "57831_1688542", "57831_1688543", "57831_1688545",
                "57831_1688546", "57831_1688547", "57831_1688548", "57831_1688549", "57831_1688550", "57831_1688551", "57831_1688552", "57831_1688553",
                "57831_1688554", "57831_1688555", "57831_1688556", "57831_1688557", "57831_1688559", "57831_1688560", "57831_1688561", "57831_1688562",
                "57831_1688563", "57831_1688564", "57831_1688565", "57831_1688566", "57831_1688568", "57831_1688571", "57831_1688573", "57831_1688574",
                "57831_1688575", "57831_1688576", "57831_1688577", "57831_1688578", "57831_1688579", "57831_1688581", "57831_1688582", "57831_1688583",
                "57831_1688584", "57831_1688585", "57831_1688586", "57831_1688587", "57831_1692937", "57831_1692938", "57831_1692939", "57831_1692940",
                "57831_1695534", "57831_1697988", "57833_1688470", "57833_1688471", "57833_1688472", "57833_1688473", "57833_1688474", "57833_1688475",
                "57833_1688476", "57833_1688477", "57833_1688478", "57833_1688479", "57833_1688480", "57833_1688481", "57833_1688482", "57833_1688483",
                "57833_1688484", "57833_1688485", "57833_1688588", "57833_1688596", "57833_1688600", "57833_1688601", "57833_1702103", "57833_1702144",
                "57833_1703599"],
            "filterObjectIds": ["30611", "33560"],
        },
        "57824": {
            "title": "Среднемесячная номинальная начисленная заработная плата работающих в экономике с 2017 г.  (рубль)",
            "id": "57824",
            "lineObjectIds": "33560",
            "columnObjectIds": ["57831", "3"],
            "groupObjectIds": "57940",
            "selectedFilterIds": ["0_57824", "3_2017", "3_2018", "3_2019", "3_2020", "3_2021", "3_2022", "3_2023",
                "3_2024", "3_2025", "3_2026", "30611_950351", "33560_1540228", "33560_1540229", "33560_1540230", "33560_1540233",
                "33560_1540234", "33560_1540235", "33560_1540236", "33560_1540272", "33560_1540273", "33560_1540276", "33560_1540282", "33560_1540283",
                "33560_1540284", "33560_1540285", "33560_1540286", "33560_1540287", "33560_1540288", "33560_1540289", "33560_1540290", "33560_1540291",
                "33560_1540292", "33560_1540293", "33560_1540294", "57831_1688487", "57831_1688498", "57831_1688506", "57831_1849012", "57940_1692306",
                "57940_1692308", "57940_1692309", "57940_1692320", "57940_1692321", "57940_1692322", "57940_1692323", "57940_1692324", "57940_1692325",
                "57940_1692326", "57940_1692327", "57940_1692328", "57940_1692329", "57940_1692330", "57940_1692331", "57940_1692332", "57940_1692333",
                "57940_1692334", "57940_1692335", "57940_1692336", "57940_1692337", "57940_1692338", "57940_1692339", "57940_1692340", "57940_1692341",
                "57940_1692342", "57940_1692343", "57940_1692344", "57940_1692345", "57940_1692346", "57940_1692347", "57940_1692348", "57940_1692349",
                "57940_1692350", "57940_1692351", "57940_1692352", "57940_1692353", "57940_1692354", "57940_1692355", "57940_1692356", "57940_1692357",
                "57940_1692358", "57940_1692359", "57940_1692360", "57940_1692361", "57940_1692362", "57940_1692363", "57940_1692364", "57940_1692365",
                "57940_1692366", "57940_1692367", "57940_1692368", "57940_1692369", "57940_1692370", "57940_1692371", "57940_1692372", "57940_1692373",
                "57940_1692374", "57940_1692375", "57940_1692376", "57940_1692377", "57940_1692378", "57940_1692379", "57940_1692380", "57940_1692381",
                "57940_1692382", "57940_1692383", "57940_1692384", "57940_1692385", "57940_1692386", "57940_1692387", "57940_1692388", "57940_1692389",
                "57940_1692390", "57940_1692391", "57940_1692392", "57940_1692393", "57940_1692394", "57940_1692395", "57940_1692396", "57940_1692397",
                "57940_1692398", "57940_1692399", "57940_1692400", "57940_1692401", "57940_1692402", "57940_1692403", "57940_1692404", "57940_1692405",
                "57940_1692406", "57940_1692407", "57940_1692408", "57940_1692409", "57940_1692410", "57940_1692411", "57940_1692412", "57940_1692413",
                "57940_1692414", "57940_1692415", "57940_1692416", "57940_1692417", "57940_1692418", "57940_1692419", "57940_1692420", "57940_1692421",
                "57940_1692422", "57940_1692423", "57940_1692424", "57940_1692425", "57940_1692426", "57940_1692427", "57940_1692428", "57940_1692429",
                "57940_1692430", "57940_1692431", "57940_1692432", "57940_1692433", "57940_1692434", "57940_1692435", "57940_1692436", "57940_1692437",
                "57940_1692438", "57940_1692439", "57940_1692440", "57940_1692441", "57940_1692442", "57940_1692443", "57940_1692444", "57940_1692445",
                "57940_1692446", "57940_1692447", "57940_1692448", "57940_1692449", "57940_1692450", "57940_1692451", "57940_1692452", "57940_1692453",
                "57940_1692454", "57940_1692455", "57940_1692456", "57940_1692457", "57940_1692458", "57940_1692459", "57940_1692460", "57940_1692461",
                "57940_1692462", "57940_1692463", "57940_1692464", "57940_1692465", "57940_1692466", "57940_1692467", "57940_1692468", "57940_1692469",
                "57940_1692470", "57940_1692748", "57940_1692749", "57940_1692750", "57940_1692751", "57940_1692757", "57940_1692758", "57940_1692759",
                "57940_1692762", "57940_1692763", "57940_1692764", "57940_1692765", "57940_1692766", "57940_1692768", "57940_1692788", "57940_1692789",
                "57940_1692791", "57940_1692792", "57940_1692793", "57940_1692794", "57940_1692795", "57940_1692796", "57940_1692798", "57940_1692800",
                "57940_1692813", "57940_1692814", "57940_1692816", "57940_1692817", "57940_1692818", "57940_1692819", "57940_1692821", "57940_1692834",
                "57940_1692835", "57940_1692839", "57940_1692842", "57940_1692843", "57940_1692849", "57940_1692850", "57940_1692854", "57940_1692855",
                "57940_1692868", "57940_1692869", "57940_1692870", "57940_1692871", "57940_1692872", "57940_1692875", "57940_1692878", "57940_1692880",
                "57940_1692881", "57940_1692883", "57940_1692884", "57940_1692889", "57940_1692891", "57940_1692892", "57940_1692893", "57940_1692901",
                "57940_1692903", "57940_1692906", "57940_1692932", "57940_1692933", "57940_1692934", "57940_1694995", "57940_1694996", "57940_1694997",
                "57940_1694998", "57940_1694999", "57940_1695000", "57940_1695001", "57940_1695002", "57940_1695003", "57940_1695004", "57940_1695005",
                "57940_1695006", "57940_1695007", "57940_1695008", "57940_1695009", "57940_1695010", "57940_1695011", "57940_1695012", "57940_1695013",
                "57940_1695014", "57940_1695015", "57940_1695016", "57940_1695017", "57940_1695018", "57940_1695019", "57940_1695020", "57940_1695021",
                "57940_1695022", "57940_1695023", "57940_1695024", "57940_1695025", "57940_1695026", "57940_1695027", "57940_1695028", "57940_1695029",
                "57940_1695030", "57940_1695031", "57940_1695032", "57940_1695033", "57940_1695034", "57940_1695035", "57940_1695036", "57940_1695037",
                "57940_1695038", "57940_1695039", "57940_1695040", "57940_1695041", "57940_1695042", "57940_1695043", "57940_1695044", "57940_1695045",
                "57940_1695046", "57940_1695047", "57940_1695048", "57940_1695049", "57940_1695050", "57940_1695051", "57940_1695052", "57940_1695053",
                "57940_1695054", "57940_1695055", "57940_1695056", "57940_1695057", "57940_1695058", "57940_1695059", "57940_1695060", "57940_1695061",
                "57940_1695062", "57940_1695063", "57940_1695397", "57940_1695408", "57940_1695410", "57940_1695411", "57940_1695422", "57940_1695424",
                "57940_1695428", "57940_1695429", "57940_1695430", "57940_1695470", "57940_1695471", "57940_1695477", "57940_1695480", "57940_1695483",
                "57940_1695486", "57940_1695487", "57940_1695488", "57940_1695489", "57940_1695491", "57940_1695498", "57940_1695507", "57940_1695508",
                "57940_1695510", "57940_1695512", "57940_1695572", "57940_1695573", "57940_1695574", "57940_1695575", "57940_1695576", "57940_1695577",
                "57940_1695578", "57940_1695580", "57940_1695581", "57940_1695582", "57940_1695583", "57940_1695584", "57940_1695585", "57940_1695586",
                "57940_1695587", "57940_1695588", "57940_1695589", "57940_1695590", "57940_1695591", "57940_1695592", "57940_1695593", "57940_1695594",
                "57940_1695595", "57940_1695596", "57940_1695597", "57940_1695598", "57940_1695599", "57940_1695600", "57940_1695601", "57940_1695602",
                "57940_1695603", "57940_1695604", "57940_1695605", "57940_1695606", "57940_1695607", "57940_1695608", "57940_1695609", "57940_1695610",
                "57940_1695611", "57940_1695612", "57940_1695613", "57940_1695614", "57940_1695615", "57940_1695616", "57940_1695617", "57940_1695618",
                "57940_1695619", "57940_1695620", "57940_1696004", "57940_1696010", "57940_1696011", "57940_1696012", "57940_1696013", "57940_1696015",
                "57940_1696040", "57940_1696041", "57940_1696049", "57940_1696075", "57940_1696085", "57940_1696087", "57940_1696091", "57940_1696129",
                "57940_1696130", "57940_1696133", "57940_1696365", "57940_1696366", "57940_1696381", "57940_1696382", "57940_1696414", "57940_1696415",
                "57940_1696416", "57940_1696417", "57940_1696715", "57940_1751132", "57940_1795411"],
            "filterObjectIds": ["0", "30611"],
        },
        "43246": {
            "title": "Среднемесячная номинальная начисленная заработная плата работающих в экономике по 2016 г. (рубль)",
            "id": "43246",
            "lineObjectIds": ["57833", "57831"],
            "columnObjectIds": ["3", "33560"],
            "selectedFilterIds": ["0_43246", "3_2011", "3_2012", "3_2013", "3_2014", "3_2015", "3_2016", "30611_950351", "33560_1540228", "33560_1540229",
                "33560_1540230", "33560_1540233", "33560_1540234", "33560_1540235", "33560_1540236", "33560_1540272", "33560_1540273", "33560_1540276",
                "33560_1540282", "33560_1540283", "33560_1540284", "33560_1540285", "33560_1540286", "33560_1540287", "33560_1540288", "33560_1540289",
                "33560_1540290", "33560_1540291", "33560_1540292", "33560_1540293", "33560_1540294", "57831_1688487", "57831_1688488", "57831_1688498",
                "57831_1688506", "57833_1688470", "57833_1688471", "57833_1688472", "57833_1688473", "57833_1688474", "57833_1688475", "57833_1688476",
                "57833_1688477", "57833_1688478", "57833_1688479", "57833_1688480", "57833_1688481", "57833_1688482", "57833_1688483", "57833_1688484",
                "57833_1688588", "57833_1688589", "57833_1688590", "57833_1688591", "57833_1688592", "57833_1688593", "57833_1688594", "57833_1688595",
                "57833_1688596", "57833_1688597", "57833_1688598", "57833_1688599", "57833_1688600", "57833_1688601", "57833_1688602", "57833_1688603",
                "57833_1688604", "57833_1688605", "57833_1688606", "57833_1688607", "57833_1688608", "57833_1688609", "57833_1688610", "57833_1688611",
                "57833_1688612", "57833_1688613", "57833_1688614", "57833_1688615", "57833_1688616", "57833_1688617", "57833_1688618", "57833_1688619",
                "57833_1688620", "57833_1688621", "57833_1688622", "57833_1688623", "57833_1688624", "57833_1688625", "57833_1688626", "57833_1688627",
                "57833_1688628", "57833_1688629", "57833_1688630", "57833_1688631", "57833_1688632", "57833_1688633", "57833_1688634", "57833_1688635",
                "57833_1688636", "57833_1688637", "57833_1688638", "57833_1688639", "57833_1688640", "57833_1688641", "57833_1688642", "57833_1688643",
                "57833_1688657", "57833_1688658", "57833_1688670", "57833_1688745", "57833_1702103", "57833_1702104", "57833_1702105", "57833_1702106",
                "57833_1702107", "57833_1702108", "57833_1702109", "57833_1702110", "57833_1702111", "57833_1702112", "57833_1702113", "57833_1702114",
                "57833_1702115", "57833_1702116", "57833_1702117", "57833_1702118", "57833_1702119", "57833_1702120", "57833_1702121", "57833_1702122",
                "57833_1702123", "57833_1702124", "57833_1702125", "57833_1702126", "57833_1702127", "57833_1702128", "57833_1702129", "57833_1702130",
                "57833_1702131", "57833_1702132", "57833_1702133", "57833_1702134", "57833_1702135", "57833_1702136", "57833_1702137", "57833_1702138",
                "57833_1702139", "57833_1702140", "57833_1702141", "57833_1702142", "57833_1702143", "57833_1702144", "57833_1702145", "57833_1702146",
                "57833_1702147", "57833_1702148", "57833_1702149", "57833_1702150", "57833_1702151", "57833_1702152", "57833_1702153", "57833_1702154",
                "57833_1702155", "57833_1702157", "57833_1702158", "57833_1702159", "57833_1702160", "57833_1702161", "57833_1702162", "57833_1702163",
                "57833_1702164", "57833_1702165", "57833_1702166", "57833_1702167", "57833_1702168", "57833_1702169", "57833_1702170", "57833_1702171",
                "57833_1702172", "57833_1702173", "57833_1702174", "57833_1702175", "57833_1702176", "57833_1702177", "57833_1702178", "57833_1702179",
                "57833_1702180", "57833_1702181", "57833_1702182", "57833_1702183", "57833_1702184", "57833_1702185", "57833_1702186", "57833_1702187",
                "57833_1702188", "57833_1702189", "57833_1702190", "57833_1702191", "57833_1702192", "57833_1702193", "57833_1702194", "57833_1702195",
                "57833_1702196", "57833_1702197", "57833_1702198", "57833_1702199", "57833_1702200", "57833_1702201", "57833_1702202", "57833_1702203",
                "57833_1702204", "57833_1702205", "57833_1702206", "57833_1702207", "57833_1702208", "57833_1702209", "57833_1702210", "57833_1702211",
                "57833_1702212", "57833_1702213", "57833_1702214", "57833_1702215", "57833_1702216", "57833_1702217", "57833_1702218", "57833_1702219",
                "57833_1702220", "57833_1702221", "57833_1702222", "57833_1702223", "57833_1702224", "57833_1702225", "57833_1702226", "57833_1702227",
                "57833_1702228", "57833_1702229", "57833_1702230", "57833_1702231", "57833_1702232", "57833_1702233", "57833_1702234", "57833_1702235",
                "57833_1702236", "57833_1702237", "57833_1702238", "57833_1702239", "57833_1702240", "57833_1702241", "57833_1702242", "57833_1702243",
                "57833_1702244", "57833_1702245", "57833_1702246", "57833_1702247", "57833_1702248", "57833_1702249", "57833_1702250", "57833_1702251",
                "57833_1702252", "57833_1702253", "57833_1702254", "57833_1702255", "57833_1702256", "57833_1702257", "57833_1702258", "57833_1702259",
                "57833_1702260", "57833_1702261", "57833_1702262", "57833_1702263", "57833_1702264", "57833_1702265", "57833_1702266", "57833_1702267",
                "57833_1702268", "57833_1702269", "57833_1702270", "57833_1702271", "57833_1702272", "57833_1702273", "57833_1702275", "57833_1702276",
                "57833_1702277", "57833_1702278", "57833_1702279", "57833_1702280", "57833_1702281", "57833_1702282", "57833_1702283", "57833_1702284",
                "57833_1702285", "57833_1702286", "57833_1702287", "57833_1702288", "57833_1702289", "57833_1702290", "57833_1702291", "57833_1702292",
                "57833_1702293", "57833_1702294", "57833_1702295", "57833_1702296", "57833_1702297", "57833_1702298", "57833_1702299", "57833_1702300",
                "57833_1702301", "57833_1702302", "57833_1702303", "57833_1702304", "57833_1702305", "57833_1702306", "57833_1702307", "57833_1702308",
                "57833_1702309", "57833_1702310", "57833_1702311", "57833_1702312", "57833_1702313", "57833_1702314", "57833_1702315", "57833_1702316",
                "57833_1702317", "57833_1702318", "57833_1702319", "57833_1702320", "57833_1702321", "57833_1702322", "57833_1702323", "57833_1702324",
                "57833_1702325", "57833_1702326", "57833_1702327", "57833_1702328", "57833_1702329", "57833_1702330", "57833_1702331", "57833_1702332",
                "57833_1702333", "57833_1702334", "57833_1702335", "57833_1702336", "57833_1702337", "57833_1702338", "57833_1702339", "57833_1702340",
                "57833_1702341", "57833_1702343", "57833_1702566", "57833_1702673", "57833_1703596", "57833_1703599", "57833_1703607", "57833_1703692",
                "57833_1703694", "57833_1703695", "57833_1703696", "57833_1703697", "57833_1703698", "57833_1703699", "57833_1703700", "57833_1703701",
                "57833_1703889", "57833_1703891", "57833_1703893", "57833_1704630", "57833_1736335", "57833_1830581"],
            "filterObjectIds": ["0", "30611"],
        },
        "34475": {
            "title": "Инвестиции в основной капитал по полному кругу организаций по 2016 г. (миллион рублей, значение показателя за год)",
            "id": "34475",
            "lineObjectIds": ["57831", "58121"],
            "columnObjectIds": "3",
            "selectedFilterIds": ["0_34475", "3_1998", "3_1999", "3_2000", "3_2001", "3_2002", "3_2003", "3_2004",
                "3_2005", "3_2006", "3_2007", "3_2008", "3_2009", "3_2010", "3_2011", "3_2012",
                "3_2013", "3_2014", "3_2015", "3_2016", "30611_950353", "33560_1558883", "57831_1688487", "57831_1688488",
                "57831_1688489", "57831_1688490", "57831_1688491", "57831_1688492", "57831_1688493", "57831_1688494", "57831_1688495", "57831_1688496",
                "57831_1688497", "57831_1688498", "57831_1688499", "57831_1688500", "57831_1688501", "57831_1688502", "57831_1688503", "57831_1688504",
                "57831_1688505", "57831_1688506", "57831_1688507", "57831_1688508", "57831_1688509", "57831_1688510", "57831_1688511", "57831_1688513",
                "57831_1688514", "57831_1688515", "57831_1688516", "57831_1688517", "57831_1688518", "57831_1688519", "57831_1688520", "57831_1688521",
                "57831_1688522", "57831_1688523", "57831_1688524", "57831_1688525", "57831_1688526", "57831_1688527", "57831_1688528", "57831_1688529",
                "57831_1688530", "57831_1688531", "57831_1688532", "57831_1688533", "57831_1688534", "57831_1688535", "57831_1688536", "57831_1688537",
                "57831_1688538", "57831_1688539", "57831_1688540", "57831_1688541", "57831_1688542", "57831_1688543", "57831_1688544", "57831_1688545",
                "57831_1688546", "57831_1688547", "57831_1688548", "57831_1688549", "57831_1688550", "57831_1688551", "57831_1688552", "57831_1688553",
                "57831_1688554", "57831_1688555", "57831_1688556", "57831_1688557", "57831_1688559", "57831_1688560", "57831_1688561", "57831_1688562",
                "57831_1688563", "57831_1688564", "57831_1688565", "57831_1688566", "57831_1688567", "57831_1688568", "57831_1688569", "57831_1688570",
                "57831_1688571", "57831_1688572", "57831_1688573", "57831_1688574", "57831_1688575", "57831_1688576", "57831_1688577", "57831_1688578",
                "57831_1688579", "57831_1688580", "57831_1688581", "57831_1688582", "57831_1688583", "57831_1688584", "57831_1688585", "57831_1688586",
                "57831_1688587", "57831_1692937", "57831_1692938", "57831_1692939", "57831_1692940", "57831_1695534", "57831_1697988", "58121_1704560",
                "58121_1704586", "58121_1704611", "58121_1834545", "58121_1853784"],
            "filterObjectIds": ["0", "30611", "33560"],
        },
        "59047": {
            "title": "Инвестиции в основной капитал по полному кругу хозяйствующих субъектов с 2017 г. (тысяча рублей, значение показателя за год)",
            "id": "59047",
            "lineObjectIds": "58121",
            "columnObjectIds": "3",
            "groupObjectIds": "57831",
            "selectedFilterIds": ["0_59047", "3_2017", "3_2018", "3_2019", "3_2020", "3_2021", "3_2022", "3_2023",
                "3_2024", "30611_950352", "33560_1558883", "57831_1688487", "57831_1688488", "57831_1688489", "57831_1688490", "57831_1688491",
                "57831_1688492", "57831_1688493", "57831_1688494", "57831_1688495", "57831_1688496", "57831_1688497", "57831_1688498", "57831_1688499",
                "57831_1688500", "57831_1688501", "57831_1688502", "57831_1688503", "57831_1688504", "57831_1688505", "57831_1688506", "57831_1688507",
                "57831_1688508", "57831_1688509", "57831_1688510", "57831_1688511", "57831_1688513", "57831_1688514", "57831_1688515", "57831_1688516",
                "57831_1688517", "57831_1688518", "57831_1688519", "57831_1688522", "57831_1688523", "57831_1688524", "57831_1688525", "57831_1688526",
                "57831_1688527", "57831_1688528", "57831_1688529", "57831_1688530", "57831_1688531", "57831_1688532", "57831_1688533", "57831_1688534",
                "57831_1688535", "57831_1688536", "57831_1688537", "57831_1688538", "57831_1688539", "57831_1688540", "57831_1688541", "57831_1688542",
                "57831_1688543", "57831_1688545", "57831_1688546", "57831_1688547", "57831_1688548", "57831_1688549", "57831_1688550", "57831_1688551",
                "57831_1688552", "57831_1688553", "57831_1688554", "57831_1688555", "57831_1688556", "57831_1688557", "57831_1688559", "57831_1688560",
                "57831_1688561", "57831_1688562", "57831_1688563", "57831_1688564", "57831_1688565", "57831_1688566", "57831_1688568", "57831_1688571",
                "57831_1688573", "57831_1688574", "57831_1688575", "57831_1688576", "57831_1688577", "57831_1688578", "57831_1688579", "57831_1688581",
                "57831_1688582", "57831_1688583", "57831_1688584", "57831_1688585", "57831_1688586", "57831_1688587", "57831_1692937", "57831_1692938",
                "57831_1692939", "57831_1692940", "57831_1695534", "57831_1795276", "57831_1795277", "57831_1849012", "58121_1704560", "58121_1704562",
                "58121_1704563", "58121_1704570", "58121_1704586", "58121_1704611", "58121_1853783", "58121_1853784"],
            "filterObjectIds": ["0", "30611", "33560"],
        },
        "36078": {
            "title": "Индекс физического объема инвестиций в основной капитал по полному кругу организаций по 2016 г. (процент)",
            "id": "36078",
            "lineObjectIds": ["57833", "57831"],
            "columnObjectIds": ["33560", "3"],
            "selectedFilterIds": ["0_36078", "3_2005", "3_2006", "3_2007", "3_2008", "3_2009", "3_2010", "3_2011",
                "3_2012", "3_2013", "3_2014", "3_2015", "3_2016", "30611_950473", "33560_1558883", "57831_1688487",
                "57833_1688470", "57833_1688471", "57833_1688472", "57833_1688473", "57833_1688474", "57833_1688475", "57833_1688476", "57833_1688477",
                "57833_1688478", "57833_1688479", "57833_1688480", "57833_1688481", "57833_1688482", "57833_1688483", "57833_1688484", "57833_1688588",
                "57833_1688589", "57833_1688590", "57833_1688591", "57833_1688592", "57833_1688593", "57833_1688594", "57833_1688595", "57833_1688596",
                "57833_1688597", "57833_1688598", "57833_1688599", "57833_1688600", "57833_1688601", "57833_1688602", "57833_1688603", "57833_1688604",
                "57833_1688605", "57833_1688606", "57833_1688607", "57833_1688608", "57833_1688609", "57833_1688610", "57833_1688611", "57833_1688612",
                "57833_1688613", "57833_1688614", "57833_1702103", "57833_1702104", "57833_1702105", "57833_1702106", "57833_1702107", "57833_1702108",
                "57833_1702109", "57833_1702110", "57833_1702111", "57833_1702112", "57833_1702113", "57833_1702114", "57833_1702115", "57833_1702116",
                "57833_1702117", "57833_1702118", "57833_1702119", "57833_1702120", "57833_1702121", "57833_1702122", "57833_1702123", "57833_1702124",
                "57833_1702125", "57833_1702126", "57833_1702127", "57833_1702128", "57833_1702129", "57833_1702130", "57833_1702131", "57833_1702132",
                "57833_1702133", "57833_1702134", "57833_1702135", "57833_1702136", "57833_1702137", "57833_1702138", "57833_1702139", "57833_1702140",
                "57833_1702141", "57833_1702142", "57833_1702143", "57833_1702144", "57833_1702145", "57833_1702146", "57833_1702147", "57833_1702148",
                "57833_1702149", "57833_1702150", "57833_1702151", "57833_1702152", "57833_1702153", "57833_1702154", "57833_1702155", "57833_1702157",
                "57833_1702258", "57833_1702259", "57833_1702260", "57833_1702284", "57833_1702285", "57833_1702286", "57833_1703596", "57833_1703599",
                "57833_1703607", "57833_1703659", "57833_1703694", "57833_1703695", "57833_1703696", "57833_1703697", "57833_1703891", "57833_1750740"],
            "filterObjectIds": ["0", "30611"],
        },
        "34129": {
            "title": "Индекс физического объема инвестиций в основной капитал (процент, значение показателя за год)",
            "id": "34129",
            "lineObjectIds": "57831",
            "columnObjectIds": "3",
            "selectedFilterIds": ["0_34129", "3_2000", "3_2001", "3_2002", "3_2003", "3_2004", "3_2005", "3_2006",
                "3_2007", "3_2008", "3_2009", "3_2010", "3_2011", "3_2012", "3_2013", "3_2014",
                "3_2015", "3_2016", "3_2017", "3_2018", "3_2019", "3_2020", "3_2021", "3_2022",
                "3_2023", "3_2024", "30611_950473", "33560_1558883", "57831_1688487", "57831_1688488", "57831_1688489", "57831_1688490",
                "57831_1688491", "57831_1688492", "57831_1688493", "57831_1688494", "57831_1688495", "57831_1688496", "57831_1688497", "57831_1688498",
                "57831_1688499", "57831_1688500", "57831_1688501", "57831_1688502", "57831_1688503", "57831_1688504", "57831_1688505", "57831_1688506",
                "57831_1688507", "57831_1688508", "57831_1688509", "57831_1688510", "57831_1688511", "57831_1688513", "57831_1688514", "57831_1688515",
                "57831_1688516", "57831_1688517", "57831_1688518", "57831_1688519", "57831_1688520", "57831_1688521", "57831_1688522", "57831_1688523",
                "57831_1688524", "57831_1688525", "57831_1688526", "57831_1688527", "57831_1688528", "57831_1688529", "57831_1688530", "57831_1688531",
                "57831_1688532", "57831_1688533", "57831_1688534", "57831_1688535", "57831_1688536", "57831_1688537", "57831_1688538", "57831_1688539",
                "57831_1688540", "57831_1688541", "57831_1688542", "57831_1688543", "57831_1688544", "57831_1688545", "57831_1688546", "57831_1688547",
                "57831_1688548", "57831_1688549", "57831_1688550", "57831_1688551", "57831_1688552", "57831_1688553", "57831_1688554", "57831_1688555",
                "57831_1688556", "57831_1688557", "57831_1688559", "57831_1688560", "57831_1688561", "57831_1688562", "57831_1688563", "57831_1688564",
                "57831_1688565", "57831_1688566", "57831_1688567", "57831_1688568", "57831_1688569", "57831_1688570", "57831_1688571", "57831_1688572",
                "57831_1688573", "57831_1688574", "57831_1688575", "57831_1688576", "57831_1688577", "57831_1688578", "57831_1688579", "57831_1688580",
                "57831_1688581", "57831_1688582", "57831_1688583", "57831_1688584", "57831_1688585", "57831_1688586", "57831_1688587", "57831_1692937",
                "57831_1692938", "57831_1692939", "57831_1692940", "57831_1695534", "57831_1697988", "57831_1795276", "57831_1795277", "57831_1849012"],
            "filterObjectIds": ["0", "30611", "33560"],
        },
        "58089": {
            "title": "Индекс физического объема инвестиций в основной капитал с 2017 г. (оперативные данные) (процент)",
            "id": "58089",
            "lineObjectIds": "57831",
            "columnObjectIds": ["3", "33560"],
            "groupObjectIds": ["57936", "57940"],
            "selectedFilterIds": ["0_58089", "3_2017", "3_2018", "3_2019", "3_2020", "3_2021", "3_2022", "3_2023",
                "3_2024", "3_2025", "30611_950473", "33560_1540222", "33560_1540224", "33560_1540226", "33560_1540227", "33560_1540286",
                "33560_1540288", "33560_1540290", "33560_1540293", "57831_1688487", "57831_1688488", "57831_1688489", "57831_1688490", "57831_1688491",
                "57831_1688492", "57831_1688493", "57831_1688494", "57831_1688495", "57831_1688496", "57831_1688497", "57831_1688498", "57831_1688499",
                "57831_1688500", "57831_1688501", "57831_1688502", "57831_1688503", "57831_1688504", "57831_1688505", "57831_1688506", "57831_1688507",
                "57831_1688508", "57831_1688509", "57831_1688510", "57831_1688511", "57831_1688513", "57831_1688514", "57831_1688515", "57831_1688516",
                "57831_1688517", "57831_1688518", "57831_1688519", "57831_1688522", "57831_1688523", "57831_1688524", "57831_1688525", "57831_1688526",
                "57831_1688527", "57831_1688528", "57831_1688529", "57831_1688530", "57831_1688531", "57831_1688532", "57831_1688533", "57831_1688534",
                "57831_1688535", "57831_1688536", "57831_1688537", "57831_1688538", "57831_1688539", "57831_1688540", "57831_1688541", "57831_1688542",
                "57831_1688543", "57831_1688545", "57831_1688546", "57831_1688547", "57831_1688548", "57831_1688549", "57831_1688550", "57831_1688551",
                "57831_1688552", "57831_1688553", "57831_1688554", "57831_1688555", "57831_1688556", "57831_1688557", "57831_1688559", "57831_1688560",
                "57831_1688561", "57831_1688562", "57831_1688563", "57831_1688564", "57831_1688565", "57831_1688566", "57831_1688568", "57831_1688571",
                "57831_1688573", "57831_1688574", "57831_1688575", "57831_1688576", "57831_1688577", "57831_1688578", "57831_1688579", "57831_1688581",
                "57831_1688582", "57831_1688583", "57831_1688584", "57831_1688585", "57831_1688586", "57831_1688587", "57831_1692937", "57831_1692938",
                "57831_1692939", "57831_1692940", "57831_1695534", "57831_1795276", "57831_1795277", "57831_1849012", "57936_1704497", "57936_1704498",
                "57940_1692322", "57940_1692358", "57940_1692933"],
            "filterObjectIds": ["0", "30611"],
        },
        "34118": {
            "title": "Введено в действие общей площади жилых домов (оперативные данные) (тысяча квадратных метров общей площади)",
            "id": "34118",
            "lineObjectIds": ["57831", "58389"],
            "columnObjectIds": ["3", "33560"],
            "selectedFilterIds": ["0_34118", "3_2012", "3_2013", "3_2014", "3_2015", "3_2016", "3_2017", "3_2018",
                "3_2019", "3_2020", "3_2021", "3_2022", "3_2023", "3_2024", "3_2025", "3_2026",
                "30611_950292", "33560_1540222", "33560_1540224", "33560_1540226", "33560_1540227", "33560_1540228", "33560_1540229", "33560_1540230",
                "33560_1540233", "33560_1540234", "33560_1540235", "33560_1540236", "33560_1540272", "33560_1540273", "33560_1540276", "33560_1540282",
                "33560_1540283", "33560_1540284", "33560_1540285", "33560_1540286", "33560_1540287", "33560_1540288", "33560_1540289", "33560_1540290",
                "33560_1540291", "33560_1540292", "33560_1540293", "33560_1540294", "57831_1688487", "57831_1688488", "57831_1688489", "57831_1688490",
                "57831_1688491", "57831_1688492", "57831_1688493", "57831_1688494", "57831_1688495", "57831_1688496", "57831_1688497", "57831_1688498",
                "57831_1688499", "57831_1688500", "57831_1688501", "57831_1688502", "57831_1688503", "57831_1688504", "57831_1688505", "57831_1688506",
                "57831_1688507", "57831_1688508", "57831_1688509", "57831_1688510", "57831_1688511", "57831_1688513", "57831_1688514", "57831_1688515",
                "57831_1688516", "57831_1688517", "57831_1688518", "57831_1688519", "57831_1688522", "57831_1688523", "57831_1688524", "57831_1688525",
                "57831_1688526", "57831_1688527", "57831_1688528", "57831_1688529", "57831_1688530", "57831_1688531", "57831_1688532", "57831_1688533",
                "57831_1688534", "57831_1688535", "57831_1688536", "57831_1688537", "57831_1688538", "57831_1688539", "57831_1688540", "57831_1688541",
                "57831_1688542", "57831_1688543", "57831_1688545", "57831_1688546", "57831_1688547", "57831_1688548", "57831_1688549", "57831_1688550",
                "57831_1688551", "57831_1688552", "57831_1688553", "57831_1688554", "57831_1688555", "57831_1688556", "57831_1688557", "57831_1688559",
                "57831_1688560", "57831_1688561", "57831_1688562", "57831_1688563", "57831_1688564", "57831_1688565", "57831_1688566", "57831_1688568",
                "57831_1688571", "57831_1688573", "57831_1688574", "57831_1688575", "57831_1688576", "57831_1688577", "57831_1688578", "57831_1688579",
                "57831_1688581", "57831_1688582", "57831_1688583", "57831_1688584", "57831_1688585", "57831_1688586", "57831_1688587", "57831_1692937",
                "57831_1692938", "57831_1692939", "57831_1692940", "57831_1695534", "57831_1795276", "57831_1795277", "58389_1754554", "58389_1754555",
                "58389_1754556", "58389_1754557", "58389_1754558", "58389_1754559", "58389_1754560", "58389_1754561", "58389_1754562", "58389_1754563",
                "58389_1754564", "58389_1754565", "58389_1754566", "58389_1754567", "58389_1754568", "58389_1754569", "58389_1754570", "58389_1754571",
                "58389_1754572", "58389_1836598", "58389_1836599"],
            "filterObjectIds": ["0", "30611"],
        },
        "33575": {
            "title": "Введено в действие общей площади жилых домов, построенных населением (оперативные данные) (тысяча квадратных метров общей площади)",
            "id": "33575",
            "lineObjectIds": "57831",
            "columnObjectIds": ["3", "33560"],
            "selectedFilterIds": ["0_33575", "3_2017", "3_2018", "3_2019", "3_2020", "3_2021", "3_2022", "3_2023",
                "3_2024", "3_2025", "3_2026", "30611_950292", "33560_1540222", "33560_1540224", "33560_1540226", "33560_1540227",
                "33560_1540228", "33560_1540229", "33560_1540230", "33560_1540233", "33560_1540234", "33560_1540235", "33560_1540236", "33560_1540272",
                "33560_1540273", "33560_1540276", "33560_1540282", "33560_1540283", "33560_1540284", "33560_1540285", "33560_1540286", "33560_1540287",
                "33560_1540288", "33560_1540289", "33560_1540290", "33560_1540291", "33560_1540292", "33560_1540293", "33560_1540294", "57831_1688487",
                "57831_1688488", "57831_1688489", "57831_1688490", "57831_1688491", "57831_1688492", "57831_1688493", "57831_1688494", "57831_1688495",
                "57831_1688496", "57831_1688497", "57831_1688498", "57831_1688499", "57831_1688500", "57831_1688501", "57831_1688502", "57831_1688503",
                "57831_1688504", "57831_1688505", "57831_1688506", "57831_1688507", "57831_1688508", "57831_1688509", "57831_1688510", "57831_1688511",
                "57831_1688513", "57831_1688514", "57831_1688515", "57831_1688516", "57831_1688517", "57831_1688518", "57831_1688519", "57831_1688522",
                "57831_1688523", "57831_1688524", "57831_1688525", "57831_1688526", "57831_1688527", "57831_1688528", "57831_1688529", "57831_1688530",
                "57831_1688531", "57831_1688532", "57831_1688533", "57831_1688534", "57831_1688535", "57831_1688536", "57831_1688537", "57831_1688538",
                "57831_1688539", "57831_1688540", "57831_1688541", "57831_1688542", "57831_1688543", "57831_1688545", "57831_1688546", "57831_1688547",
                "57831_1688548", "57831_1688549", "57831_1688550", "57831_1688551", "57831_1688552", "57831_1688553", "57831_1688554", "57831_1688555",
                "57831_1688556", "57831_1688557", "57831_1688559", "57831_1688560", "57831_1688561", "57831_1688562", "57831_1688563", "57831_1688564",
                "57831_1688565", "57831_1688566", "57831_1688568", "57831_1688571", "57831_1688573", "57831_1688574", "57831_1688575", "57831_1688576",
                "57831_1688577", "57831_1688578", "57831_1688579", "57831_1688581", "57831_1688582", "57831_1688583", "57831_1688584", "57831_1688585",
                "57831_1688586", "57831_1688587", "57831_1692937", "57831_1692938", "57831_1692939", "57831_1692940", "57831_1695534", "57831_1795276",
                "57831_1795277", "57831_1849012"],
            "filterObjectIds": ["0", "30611"],
        },
        "33574": {
            "title": "Введено в действие общей площади жилых домов на 1000 человек населения (квадратный метр, значение показателя за год)",
            "id": "33574",
            "lineObjectIds": "57831",
            "columnObjectIds": "3",
            "selectedFilterIds": ["0_33574", "3_2000", "3_2001", "3_2002", "3_2003", "3_2004", "3_2005", "3_2006",
                "3_2007", "3_2008", "3_2009", "3_2010", "3_2011", "3_2012", "3_2013", "3_2014",
                "3_2015", "3_2016", "3_2017", "3_2018", "3_2019", "3_2020", "3_2021", "3_2022",
                "3_2023", "3_2024", "30611_950134", "33560_1558883", "57831_1688487", "57831_1688488", "57831_1688489", "57831_1688490",
                "57831_1688491", "57831_1688492", "57831_1688493", "57831_1688494", "57831_1688495", "57831_1688496", "57831_1688497", "57831_1688498",
                "57831_1688499", "57831_1688500", "57831_1688501", "57831_1688502", "57831_1688503", "57831_1688504", "57831_1688505", "57831_1688506",
                "57831_1688507", "57831_1688508", "57831_1688509", "57831_1688510", "57831_1688511", "57831_1688513", "57831_1688514", "57831_1688515",
                "57831_1688516", "57831_1688517", "57831_1688518", "57831_1688519", "57831_1688520", "57831_1688521", "57831_1688522", "57831_1688523",
                "57831_1688524", "57831_1688525", "57831_1688526", "57831_1688527", "57831_1688528", "57831_1688529", "57831_1688530", "57831_1688531",
                "57831_1688532", "57831_1688533", "57831_1688534", "57831_1688535", "57831_1688536", "57831_1688537", "57831_1688538", "57831_1688539",
                "57831_1688540", "57831_1688541", "57831_1688542", "57831_1688543", "57831_1688545", "57831_1688546", "57831_1688547", "57831_1688548",
                "57831_1688549", "57831_1688550", "57831_1688551", "57831_1688552", "57831_1688553", "57831_1688554", "57831_1688555", "57831_1688556",
                "57831_1688557", "57831_1688559", "57831_1688560", "57831_1688561", "57831_1688562", "57831_1688563", "57831_1688564", "57831_1688565",
                "57831_1688566", "57831_1688568", "57831_1688571", "57831_1688573", "57831_1688574", "57831_1688575", "57831_1688576", "57831_1688577",
                "57831_1688578", "57831_1688579", "57831_1688581", "57831_1688582", "57831_1688583", "57831_1688584", "57831_1688585", "57831_1688586",
                "57831_1688587", "57831_1692937", "57831_1692938", "57831_1692939", "57831_1692940", "57831_1695534", "57831_1697988", "57831_1795276",
                "57831_1795277", "57831_1849012"],
            "filterObjectIds": ["0", "30611", "33560"],
        },
        "33738": {
            "title": "Количество построенных квартир (значение показателя за год)",
            "id": "33738",
            "lineObjectIds": ["57831", "30611"],
            "columnObjectIds": "3",
            "selectedFilterIds": ["0_33738", "3_2000", "3_2001", "3_2002", "3_2003", "3_2004", "3_2005", "3_2006",
                "3_2007", "3_2008", "3_2009", "3_2010", "3_2011", "3_2012", "3_2013", "3_2014",
                "3_2015", "3_2016", "3_2017", "3_2018", "3_2019", "3_2020", "3_2021", "3_2022",
                "3_2023", "3_2024", "3_2025", "30611_950475", "33560_1558883", "57831_1688487", "57831_1688488", "57831_1688489",
                "57831_1688490", "57831_1688491", "57831_1688492", "57831_1688493", "57831_1688494", "57831_1688495", "57831_1688496", "57831_1688497",
                "57831_1688498", "57831_1688499", "57831_1688500", "57831_1688501", "57831_1688502", "57831_1688503", "57831_1688504", "57831_1688505",
                "57831_1688506", "57831_1688507", "57831_1688508", "57831_1688509", "57831_1688510", "57831_1688511", "57831_1688513", "57831_1688514",
                "57831_1688515", "57831_1688516", "57831_1688517", "57831_1688518", "57831_1688519", "57831_1688520", "57831_1688521", "57831_1688522",
                "57831_1688523", "57831_1688524", "57831_1688525", "57831_1688526", "57831_1688527", "57831_1688528", "57831_1688529", "57831_1688530",
                "57831_1688531", "57831_1688532", "57831_1688533", "57831_1688534", "57831_1688535", "57831_1688536", "57831_1688537", "57831_1688538",
                "57831_1688539", "57831_1688540", "57831_1688541", "57831_1688542", "57831_1688543", "57831_1688545", "57831_1688546", "57831_1688547",
                "57831_1688548", "57831_1688549", "57831_1688550", "57831_1688551", "57831_1688552", "57831_1688553", "57831_1688554", "57831_1688555",
                "57831_1688556", "57831_1688557", "57831_1688559", "57831_1688560", "57831_1688561", "57831_1688562", "57831_1688563", "57831_1688564",
                "57831_1688565", "57831_1688566", "57831_1688568", "57831_1688571", "57831_1688573", "57831_1688574", "57831_1688575", "57831_1688576",
                "57831_1688577", "57831_1688578", "57831_1688579", "57831_1688581", "57831_1688582", "57831_1688583", "57831_1688584", "57831_1688585",
                "57831_1688586", "57831_1688587", "57831_1692937", "57831_1692938", "57831_1692939", "57831_1692940", "57831_1695534", "57831_1697988",
                "57831_1697989", "57831_1704670", "57831_1707677", "57831_1710977", "57831_1736322", "57831_1743269", "57831_1743270", "57831_1743271",
                "57831_1743272", "57831_1743273", "57831_1743274", "57831_1743275", "57831_1743320", "57831_1750754", "57831_1751201", "57831_1758277",
                "57831_1795276", "57831_1795277", "57831_1830322", "57831_1830323", "57831_1830324", "57831_1830325", "57831_1830326", "57831_1830327",
                "57831_1830328", "57831_1830329", "57831_1830330", "57831_1830331", "57831_1830332", "57831_1830333", "57831_1830334", "57831_1830335",
                "57831_1830336", "57831_1830337", "57831_1830338", "57831_1830339", "57831_1830340", "57831_1831341", "57831_1831342", "57831_1831343",
                "57831_1831372", "57831_1834539", "57831_1836810", "57831_1837699", "57831_1837700", "57831_1837701", "57831_1837702", "57831_1837703",
                "57831_1837704", "57831_1837740", "57831_1837741", "57831_1837988", "57831_1846706", "57831_1846850", "57831_1846851", "57831_1846852",
                "57831_1846853", "57831_1846854", "57831_1846855", "57831_1846856", "57831_1846857", "57831_1846858", "57831_1846859", "57831_1846860",
                "57831_1846861", "57831_1846862", "57831_1846863", "57831_1846864", "57831_1846865", "57831_1846866", "57831_1846867", "57831_1846868",
                "57831_1846869", "57831_1846870", "57831_1846871", "57831_1848077", "57831_1848143", "57831_1849012", "57831_1855638", "57831_1855639",
                "57831_1855640", "57831_1855641", "57831_1856895"],
            "filterObjectIds": ["0", "33560"],
        },
        "40466": {
            "title": "Общая площадь жилых помещений, приходящаяся в среднем на одного жителя (квадратный метр, значение показателя за год)",
            "id": "40466",
            "lineObjectIds": ["57831", "58274"],
            "columnObjectIds": "3",
            "selectedFilterIds": ["0_40466", "3_2008", "3_2009", "3_2010", "3_2011", "3_2012", "3_2013", "3_2014",
                "3_2015", "3_2016", "3_2017", "3_2018", "3_2019", "3_2020", "3_2021", "3_2022",
                "3_2023", "3_2024", "30611_950134", "33560_1558883", "57831_1688487", "57831_1688488", "57831_1688489", "57831_1688490",
                "57831_1688491", "57831_1688492", "57831_1688493", "57831_1688494", "57831_1688495", "57831_1688496", "57831_1688497", "57831_1688498",
                "57831_1688499", "57831_1688500", "57831_1688501", "57831_1688502", "57831_1688503", "57831_1688504", "57831_1688505", "57831_1688506",
                "57831_1688507", "57831_1688508", "57831_1688509", "57831_1688510", "57831_1688511", "57831_1688512", "57831_1688513", "57831_1688514",
                "57831_1688515", "57831_1688516", "57831_1688517", "57831_1688518", "57831_1688519", "57831_1688520", "57831_1688521", "57831_1688522",
                "57831_1688523", "57831_1688524", "57831_1688525", "57831_1688526", "57831_1688527", "57831_1688528", "57831_1688529", "57831_1688530",
                "57831_1688531", "57831_1688532", "57831_1688533", "57831_1688534", "57831_1688535", "57831_1688536", "57831_1688537", "57831_1688538",
                "57831_1688539", "57831_1688540", "57831_1688541", "57831_1688542", "57831_1688543", "57831_1688545", "57831_1688546", "57831_1688547",
                "57831_1688548", "57831_1688549", "57831_1688550", "57831_1688551", "57831_1688552", "57831_1688553", "57831_1688554", "57831_1688555",
                "57831_1688556", "57831_1688557", "57831_1688558", "57831_1688559", "57831_1688560", "57831_1688561", "57831_1688562", "57831_1688563",
                "57831_1688564", "57831_1688565", "57831_1688566", "57831_1688568", "57831_1688571", "57831_1688573", "57831_1688574", "57831_1688575",
                "57831_1688576", "57831_1688577", "57831_1688578", "57831_1688579", "57831_1688581", "57831_1688582", "57831_1688583", "57831_1688584",
                "57831_1688585", "57831_1688586", "57831_1688587", "57831_1692937", "57831_1692938", "57831_1692939", "57831_1692940", "57831_1695534",
                "57831_1697988", "57831_1707677", "57831_1710514", "57831_1710522", "57831_1710562", "57831_1710977", "57831_1711537", "57831_1744509",
                "57831_1744510", "57831_1744511", "57831_1744512", "57831_1744513", "57831_1744514", "57831_1744515", "57831_1744516", "57831_1744517",
                "57831_1744518", "57831_1744519", "57831_1744520", "57831_1744521", "57831_1744522", "57831_1744523", "57831_1744524", "57831_1744525",
                "57831_1744526", "57831_1744527", "57831_1744528", "57831_1744529", "57831_1744530", "57831_1744531", "57831_1744532", "57831_1744533",
                "57831_1744534", "57831_1744535", "57831_1744536", "57831_1744537", "57831_1744538", "57831_1744539", "57831_1744540", "57831_1744541",
                "57831_1744543", "57831_1744544", "57831_1744545", "57831_1744546", "57831_1744547", "57831_1744548", "57831_1744549", "57831_1744550",
                "57831_1744551", "57831_1744552", "57831_1744553", "57831_1744554", "57831_1744555", "57831_1744556", "57831_1744557", "57831_1744558",
                "57831_1744559", "57831_1744560", "57831_1744561", "57831_1744562", "57831_1744563", "57831_1744564", "57831_1744565", "57831_1744566",
                "57831_1744567", "57831_1744568", "57831_1744569", "57831_1744570", "57831_1744571", "57831_1744572", "57831_1744573", "57831_1744574",
                "57831_1744575", "57831_1744576", "57831_1744577", "57831_1744578", "57831_1744579", "57831_1744580", "57831_1744581", "57831_1744582",
                "57831_1744583", "57831_1744584", "57831_1744585", "57831_1744586", "57831_1744587", "57831_1751229", "57831_1795276", "57831_1795277",
                "58274_1707676", "58274_1710521", "58274_1759500"],
            "filterObjectIds": ["0", "30611", "33560"],
        },
        # Разделён на две части потому что в .xls предел 256 колонок: 16 лет × 12 месяцев × 2 типа
        # индекса не помещаются в одну выгрузку. Все остальные фильтры в обеих частях одинаковые.
        "31074_часть1": {
            "title": "Индексы потребительских цен на товары и услуги часть1 (процент)",
            "id": "31074",
            "lineObjectIds": ["0", "30611", "57831", "58273"],
            "columnObjectIds": ["3", "33560", "57937"],
            "selectedFilterIds": ["0_31074",
                "3_2011", "3_2012", "3_2013", "3_2014", "3_2015", "3_2016", "3_2017", "3_2018",
                "30611_950473",
                "33560_1540228", "33560_1540229", "33560_1540230", "33560_1540233", "33560_1540234",
                "33560_1540235", "33560_1540236", "33560_1540272", "33560_1540273", "33560_1540276",
                "33560_1540282", "33560_1540283",
                "57831_1688487", "57831_1688506", "57831_1849012",
                "57937_1704142", "57937_1704143",
                "58273_1707675"],
            "filterObjectIds": ["0", "30611"],
        },
        "31074_часть2": {
            "title": "Индексы потребительских цен на товары и услуги часть2 (процент)",
            "id": "31074",
            "lineObjectIds": ["0", "30611", "57831", "58273"],
            "columnObjectIds": ["3", "33560", "57937"],
            "selectedFilterIds": ["0_31074",
                "3_2019", "3_2020", "3_2021", "3_2022", "3_2023", "3_2024", "3_2025", "3_2026",
                "30611_950473",
                "33560_1540228", "33560_1540229", "33560_1540230", "33560_1540233", "33560_1540234",
                "33560_1540235", "33560_1540236", "33560_1540272", "33560_1540273", "33560_1540276",
                "33560_1540282", "33560_1540283",
                "57831_1688487", "57831_1688506", "57831_1849012",
                "57937_1704142", "57937_1704143",
                "58273_1707675"],
            "filterObjectIds": ["0", "30611"],
        },
        "31452": {
            "title": "Средняя цена 1 кв. м  общей площади квартир на рынке жилья  (рубль)",
            "id": "31452",
            "lineObjectIds": ["57831", "58849", "63148"],
            "columnObjectIds": ["3", "33560"],
            "selectedFilterIds": ["0_31452", "3_2000", "3_2001", "3_2002", "3_2003", "3_2004", "3_2005", "3_2006",
                "3_2007", "3_2008", "3_2009", "3_2010", "3_2011", "3_2012", "3_2013", "3_2014",
                "3_2015", "3_2016", "3_2017", "3_2018", "3_2019", "3_2020", "3_2021", "3_2022",
                "3_2023", "3_2024", "3_2025", "30611_950351", "33560_1540222", "33560_1540224", "33560_1540226", "33560_1540227",
                "57831_1688487", "57831_1688488", "57831_1688489", "57831_1688490", "57831_1688491", "57831_1688492", "57831_1688493", "57831_1688494",
                "57831_1688495", "57831_1688496", "57831_1688497", "57831_1688498", "57831_1688499", "57831_1688500", "57831_1688501", "57831_1688502",
                "57831_1688503", "57831_1688504", "57831_1688505", "57831_1688506", "57831_1688507", "57831_1688508", "57831_1688509", "57831_1688510",
                "57831_1688511", "57831_1688513", "57831_1688514", "57831_1688515", "57831_1688516", "57831_1688517", "57831_1688518", "57831_1688519",
                "57831_1688520", "57831_1688521", "57831_1688522", "57831_1688523", "57831_1688524", "57831_1688525", "57831_1688526", "57831_1688527",
                "57831_1688528", "57831_1688529", "57831_1688530", "57831_1688531", "57831_1688532", "57831_1688533", "57831_1688534", "57831_1688535",
                "57831_1688536", "57831_1688537", "57831_1688538", "57831_1688539", "57831_1688540", "57831_1688541", "57831_1688542", "57831_1688543",
                "57831_1688545", "57831_1688546", "57831_1688547", "57831_1688548", "57831_1688549", "57831_1688550", "57831_1688551", "57831_1688552",
                "57831_1688553", "57831_1688554", "57831_1688555", "57831_1688556", "57831_1688557", "57831_1688559", "57831_1688560", "57831_1688561",
                "57831_1688562", "57831_1688563", "57831_1688564", "57831_1688565", "57831_1688566", "57831_1688568", "57831_1688569", "57831_1688571",
                "57831_1688573", "57831_1688574", "57831_1688575", "57831_1688576", "57831_1688577", "57831_1688578", "57831_1688579", "57831_1688581",
                "57831_1688582", "57831_1688583", "57831_1688584", "57831_1688585", "57831_1688586", "57831_1688587", "57831_1692937", "57831_1692938",
                "57831_1692939", "57831_1692940", "57831_1695534", "57831_1697988", "57831_1709529", "57831_1709530", "57831_1709531", "57831_1709532",
                "57831_1709533", "57831_1709534", "57831_1709535", "57831_1709536", "57831_1709537", "57831_1709538", "57831_1709539", "57831_1795276",
                "57831_1795277", "57831_1849012", "58849_1752260", "58849_1752261", "58849_1752262", "58849_1752263", "58849_1752264", "63148_1855614",
                "63148_1855615"],
            "filterObjectIds": ["0", "30611"],
        },
        "31448": {
            "title": "Средние потребительские цены (тарифы) на товары и услуги (рубль)",
            "id": "31448",
            "lineObjectIds": ["57831", "58273"],
            "columnObjectIds": ["3", "33560"],
            "selectedFilterIds": ["0_31448", "3_2000", "3_2001", "3_2002", "3_2003", "3_2004", "3_2005", "3_2006",
                "3_2007", "3_2008", "3_2009", "3_2010", "3_2011", "3_2012", "3_2013", "3_2014",
                "3_2015", "3_2016", "3_2017", "3_2018", "3_2019", "3_2020", "3_2021", "3_2022",
                "3_2023", "3_2024", "3_2025", "3_2026", "30611_950351", "33560_1540228", "33560_1540229", "33560_1540230",
                "33560_1540233", "33560_1540234", "33560_1540235", "33560_1540236", "33560_1540272", "33560_1540273", "33560_1540276", "33560_1540282",
                "33560_1540283", "57831_1688487", "57831_1688488", "57831_1688498", "57831_1688506", "57831_1849012", "58273_1755035", "58273_1755157"],
            "filterObjectIds": ["0", "30611"],
        },
    }

    payload_template = payload_template_override or (
        _payload_34118_part(indicator_id)
        if indicator_id in {"34118_часть1", "34118_часть2"}
        else PAYLOADS.get(indicator_id)
    )
    if payload_template is None:
        print(f"  ⚠️  Нет payload для индикатора {indicator_id}")
        return None

    post_data = [
        ("format", "excel"),
        ("id", payload_template["id"]),
        ("indicator_title", payload_template["title"]),
    ]
    for key, value in payload_template.items():
        if key in {"filename_title", "id", "title"}:
            continue
        post_key = key
        if isinstance(value, list):
            for v in value:
                post_data.append((post_key, v))
        else:
            post_data.append((post_key, value))

    url = "https://www.fedstat.ru/indicator/data.do?format=excel"
    real_id = indicator_id.split("_")[0]
    user_agent = None
    if driver is not None:
        try:
            user_agent = driver.execute_script("return navigator.userAgent")
        except Exception:
            user_agent = None
    if not user_agent:
        user_agent = FEDSTAT_USER_AGENT
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer": f"https://www.fedstat.ru/indicator/{real_id}",
        "User-Agent": user_agent,
    }
    file_title = payload_template.get("filename_title", payload_template["title"])
    safe_title = re.sub(r'[\\/*?:"<>|]', "", file_title)
    safe_title = safe_title[:80]
    # Дата в имени = дата ОБНОВЛЕНИЯ ДАННЫХ с сайта fedstat
    # (а не сегодняшняя). Так файл сразу говорит когда контент
    # реально обновлён.
    date_in_name = _parse_remote_date_to_yyyymmdd(remote_date)
    filename = f"{date_in_name}_{safe_title}.xls"
    save_path = save_path_override or (save_dir / filename)
    can_chunk_34118 = (
        allow_34118_chunks
        and payload_template_override is None
        and indicator_id in {"34118_часть1", "34118_часть2"}
    )
    tried_34118_chunks = False

    if can_chunk_34118:
        tried_34118_chunks = True
        chunk_path = _download_34118_period_chunks(
            indicator_id,
            payload_template,
            save_dir,
            save_path,
            remote_date=remote_date,
            driver=driver,
        )
        if chunk_path is not None:
            return chunk_path
        print("  -> 34118 chunks не собрались, пробую полный экспорт")

    session: requests.Session | None = None
    try:
        print(f"  ⬇️  Скачиваю Excel...")
        session = requests.Session()
        if driver is not None:
            try:
                for cookie in driver.get_cookies():
                    session.cookies.set(
                        cookie.get("name"),
                        cookie.get("value"),
                        domain=cookie.get("domain"),
                        path=cookie.get("path", "/"),
                    )
            except Exception:
                pass
        last_error: Exception | None = None

        try:
            response = session.post(url, data=post_data, headers=headers, timeout=120, stream=True)
            response.raise_for_status()
            _raise_if_html_response(response)
            stream_response_atomic(response, save_path, validate=validate_excel_file)
        except (requests.RequestException, OSError, ValueError) as exc:
            last_error = exc
            try:
                response.close()  # type: ignore[name-defined]
            except Exception:
                pass
            print(f"  ⚠️  urlencoded POST не дал Excel: {exc}")
            print("  -> Пробую multipart POST...")
            multipart_headers = {k: v for k, v in headers.items() if k.lower() != "content-type"}
            try:
                multipart = [(key, (None, str(value))) for key, value in post_data]
                response = session.post(
                    url,
                    files=multipart,
                    headers=multipart_headers,
                    timeout=120,
                )
                response.raise_for_status()
                _raise_if_html_response(response)
                write_bytes_atomic(save_path, response.content, validate=validate_excel_file)
            except (requests.RequestException, OSError, ValueError) as exc2:
                last_error = exc2
                raise last_error

        print(f"  ✅ Сохранён: {save_path}")
        return save_path

    except (requests.RequestException, OSError, ValueError) as e:
        print(f"  ❌ Ошибка при скачивании: {e}")
        if driver is not None:
            print("  -> Пробую скачать через browser POST...")
            browser_path = _download_excel_via_browser(
                driver, url, post_data, save_dir, save_path
            )
            if browser_path is not None:
                print(f"  ✅ Сохранён через browser POST: {browser_path}")
                return browser_path
        if indicator_id in {"34118_часть1", "34118_часть2"} and session is not None:
            print("  -> Пробую SDMX fallback для 34118...")
            sdmx_path = _download_34118_sdmx_as_excel(
                session,
                post_data,
                headers,
                payload_template,
                save_path,
                driver=driver,
            )
            if sdmx_path is not None:
                return sdmx_path
        if (
            allow_34118_chunks
            and payload_template_override is None
            and indicator_id in {"34118_часть1", "34118_часть2"}
            and not tried_34118_chunks
        ):
            chunk_path = _download_34118_period_chunks(
                indicator_id,
                payload_template,
                save_dir,
                save_path,
                remote_date=remote_date,
                driver=driver,
            )
            if chunk_path is not None:
                return chunk_path
        return None


def run(force: bool = False, only_ids: list[str] | None = None):
    """force=True — игнорируем state, перекачиваем все индикаторы."""
    DOWNLOAD_DIR.mkdir(exist_ok=True)
    state = load_state() if not force else {}
    indicators_to_run = INDICATORS
    if only_ids:
        indicators_to_run = {
            indicator_id: INDICATORS[indicator_id]
            for indicator_id in only_ids
            if indicator_id in INDICATORS
        }
        missing = [indicator_id for indicator_id in only_ids if indicator_id not in INDICATORS]
        if missing:
            print(f"⚠️  Неизвестные indicator id в --only/FEDSTAT_ONLY_IDS: {', '.join(missing)}")
    downloaded_files = []
    checked_ok = 0
    downloaded_without_date = []
    skipped_indicators = []
    failed_downloads = []

    print(f"\n{'='*60}")
    print(f"Запуск: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Индикаторов: {len(indicators_to_run)}/{len(INDICATORS)}")
    if only_ids:
        print(f"Фильтр: {', '.join(indicators_to_run)}")
    print(f"{'='*60}\n")

    driver = create_driver(download_dir=DOWNLOAD_DIR)

    try:
        for indicator_id, name in indicators_to_run.items():
            print(f"📊 [{indicator_id}] {name[:55]}")

            remote_date = get_last_update_date(driver, indicator_id)

            if remote_date is None:
                print("  ⚠️  Не удалось получить дату со страницы")
                if DIRECT_DOWNLOAD_ON_DATE_FAILURE and _should_direct_fallback(indicator_id):
                    print("  -> Пробую скачать Excel напрямую без даты паспорта...")
                    saved_path = download_excel(indicator_id, DOWNLOAD_DIR,
                                                remote_date=None,
                                                driver=driver)
                    if saved_path:
                        downloaded_files.append(saved_path)
                        downloaded_without_date.append(indicator_id)
                        checked_ok += 1
                        print("  ✅ Скачано напрямую; state по дате не обновляю\n")
                        continue
                elif DIRECT_DOWNLOAD_ON_DATE_FAILURE:
                    print("  ℹ️  Direct fallback выключен для этого индикатора")
                skipped_indicators.append(indicator_id)
                print("  ⚠️  Пропускаю — не удалось получить дату и скачать напрямую\n")
                continue

            checked_ok += 1
            saved_date = state.get(indicator_id)

            if saved_date is None:
                print(f"  ℹ️  Первая загрузка. Дата: {remote_date}")
            elif remote_date != saved_date:
                print(f"  🔄 Обновился! Было: {saved_date} → Стало: {remote_date}")
            else:
                print(f"  ✔️  Без изменений ({remote_date})\n")
                continue

            saved_path = download_excel(indicator_id, DOWNLOAD_DIR,
                                        remote_date=remote_date,
                                        driver=driver)
            if saved_path:
                downloaded_files.append(saved_path)
                state[indicator_id] = remote_date
            else:
                failed_downloads.append(indicator_id)
            print()

    finally:
        driver.quit()

    save_state(state)

    print(f"\n{'='*60}")
    print(f"Итог: скачано файлов — {len(downloaded_files)}")
    print(f"Проверено индикаторов — {checked_ok}/{len(indicators_to_run)}")
    if downloaded_without_date:
        print(f"Скачано напрямую без даты — {len(downloaded_without_date)}: {', '.join(downloaded_without_date)}")
    if failed_downloads:
        print(f"Обновились, но не скачались — {len(failed_downloads)}: {', '.join(failed_downloads)}")
    if skipped_indicators:
        print(f"Пропущено без даты — {len(skipped_indicators)}: {', '.join(skipped_indicators)}")
    for f in downloaded_files:
        print(f"  • {f}")
    print(f"{'='*60}\n")

    ok = checked_ok > 0
    return downloaded_files, ok


if __name__ == "__main__":
    import sys
    force = "--force" in sys.argv
    only_arg = next((arg.split("=", 1)[1] for arg in sys.argv if arg.startswith("--only=")), None)
    only_ids = _parse_only_ids(only_arg or os.environ.get("FEDSTAT_ONLY_IDS"))
    files, ok = run(force=force, only_ids=only_ids)
    # exit 2 только если fedstat не удалось проверить вообще. Если все даты
    # прочитаны и новых файлов нет, это штатное "без изменений".
    sys.exit(0 if ok else 2)
