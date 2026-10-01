"""
erzrf_checker.py
----------------
Скачивает данные с erzrf.ru (2 источника):

1. **erzrf_top** — `https://erzrf.ru/top-zastroyshchikov/`. Для каждой
   комбинации (5 сортировок × 2 региона) кликает кнопку «Скачать TOP в
   Excel». Ожидаемые схемы (исторические имена части файлов были ошибочны;
   исправленное соответствие см. docs/audit/erzrf_collector.md):
   - «По объёму текущего строительства»      → площадь строительства
   - «По объёму ввода жилья»                 → введённая площадь за год
   - «По накопленному вводу жилья с 2016 г.» → накопленная введённая площадь
   - «По потребительским качествам ЖК»        → оценки качества
   - «По скорости строительства»             → дни на дом
   Годовой и накопленный ввод имеют общую схему Excel; их различают
   подтверждённые URL, сортировка и год вокруг конкретного скачивания.

2. **erzrf_cards** — для ТОП-100 застройщиков из последнего
   `top_developers_rf_*.json` DOM-скрейпим карточки на
   `erzrf.ru/zastroyschiki/<slug>`.

**Авторизация.** Для скачивания xlsx сайт требует логин. Креды в
`config/erzrf.json` (gitignored), пример — `config/erzrf.example.json`.
Логин делается один раз в начале сессии, кука держит до конца. Если
файла кредов нет — fetch выводит warning и завершается без скачивания.

State в `state/erzrf_state.json`. Запуск:
    py erzrf_checker.py            # обе подпрограммы
    py erzrf_checker.py top        # только top
    py erzrf_checker.py cards      # только карточки
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import sys
import tempfile
import uuid
import warnings
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qs, urlparse
from zipfile import BadZipFile

from selenium.common.exceptions import (
    NoSuchElementException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from pipeline.file_utils import validate_excel_file
from pipeline.selenium_utils import create_chrome, selenium_sleep, wait_for_download
from pipeline.state_utils import load_json_state, write_json_atomic


# ─────────────────────────────────────────────
# КОНФИГ
# ─────────────────────────────────────────────

BASE = "https://erzrf.ru"
TOP_URL = f"{BASE}/top-zastroyshchikov"

# Values have public-source evidence (28.09.2026).
# The complete mapping is checked against the site's sorting select every run;
# historical values 2/3/4 were wrong. See docs/audit/erzrf_collector.md.
TOP_TYPES = {
    "obyem_stroitelstva":  0,   # По объёму текущего строительства (default)
    "obyem_vvoda":         1,   # По объёму ввода
    "nakopl_vvod":         4,   # По накопленному вводу с 2016 года
    "potreb_kachestva":    2,   # По потребительским качествам
    "skorost":             3,   # По скорости строительства
}

# Все 5 сортировок (по факту дают разные наборы колонок).
SORTINGS = [
    {"key": "obyem_stroitelstva",  "label": "По объёму текущего строительства"},
    {"key": "obyem_vvoda",         "label": "По объёму ввода"},
    {"key": "nakopl_vvod",         "label": "По накопленному вводу"},
    {"key": "potreb_kachestva",    "label": "По потребительским качествам"},
    {"key": "skorost",             "label": "По скорости строительства"},
]

# URL-параметры регионов получены опытным путём пользователем 03.06.2026.
# РФ:    /top-zastroyshchikov/rf?regionKey=0&...
# Москва: /top-zastroyshchikov/moskva?regionKey=143443001&region=moskva&...
REGIONS = [
    {
        "key": "rf",
        "label": "РФ",
        "path": "rf",
        "extra_query": "regionKey=0",
    },
    {
        "key": "msk",
        "label": "г.Москва",
        "path": "moskva",
        "extra_query": "regionKey=143443001&region=moskva",
    },
]


def _build_top_url(region: dict, sorting_key: str, top_types: dict | None = None) -> str:
    known_sortings = {item["key"] for item in SORTINGS}
    if sorting_key not in known_sortings:
        raise ValueError(f"unknown TOP sorting: {sorting_key}")
    if region not in REGIONS:
        raise ValueError(f"unknown TOP region: {region.get('key')}")
    mapping = TOP_TYPES if top_types is None else top_types
    if sorting_key not in mapping or type(mapping[sorting_key]) is not int:
        raise ValueError(f"unverified topType for sorting: {sorting_key}")
    top_type = mapping[sorting_key]
    if top_type < 0:
        raise ValueError("topType must be nonnegative")
    return (
        f"{BASE}/top-zastroyshchikov/{region['path']}"
        f"?topType={top_type}&{region['extra_query']}"
    )


TOP_N_DEVELOPERS = 100
DOWNLOAD_DIR = Path("data/raw/realty/erzrf")
CARDS_DIR = DOWNLOAD_DIR / "cards"
STATE_FILE = Path("state/erzrf_state.json")
CONFIG_FILE = Path("config/erzrf.json")
PAGE_TIMEOUT = 60
HEADLESS = os.environ.get("HEADLESS", "1") != "0"  # default headless

SLUG_RE = re.compile(r"/zastroyschiki/([^?#]+?)/?(?:[?#]|$)")


# ─────────────────────────────────────────────
# State / config
# ─────────────────────────────────────────────


def load_state() -> dict:
    return load_json_state(STATE_FILE, label="erzrf")


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _write_json_atomic(STATE_FILE, state)


def _write_json_atomic(path: Path, payload) -> None:
    write_json_atomic(path, payload)


def _write_excel_atomic(path: Path, write_func) -> None:
    tmp = path.with_name(f"{path.stem}.tmp{path.suffix}")
    try:
        write_func(tmp)
        import pandas as pd

        with pd.ExcelFile(tmp, engine="openpyxl") as workbook:
            if not workbook.sheet_names:
                raise RuntimeError("temporary workbook has no sheets")
        tmp.replace(path)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def _normalise_label(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold().replace("ё", "е")


TOP_LABEL_ALIASES = {
    "obyem_stroitelstva": {"По объёму текущего строительства"},
    "obyem_vvoda": {"По объёму ввода", "По объёму ввода МКД", "По объёму ввода жилья"},
    "nakopl_vvod": {
        "По накопленному вводу", "По накопленному вводу МКД",
        "По накопленному вводу МКД с 2016 года", "По накопленному вводу жилья",
        "По накопленному вводу с 2016 года",
        "По накопленному вводу жилья (с 2016 г.)",
    },
    "potreb_kachestva": {
        "По потребительским качествам", "По потребительским качествам жилья",
        "По потребительским качествам ЖК",
    },
    "skorost": {"По скорости строительства", "По скорости строительства жилья"},
}


def _sorting_from_label(label: str) -> str | None:
    normalised = _normalise_label(label)
    matches = [key for key, labels in TOP_LABEL_ALIASES.items()
               if normalised in {_normalise_label(text) for text in labels}]
    return matches[0] if len(matches) == 1 else None


def _excel_contract(path: Path, sorting_key: str) -> dict:
    """Validate actual XLSX cells, not the browser name or ZIP signature."""
    from openpyxl import load_workbook

    metric_names = {
        "obyem_stroitelstva": "Строится, м²",
        "obyem_vvoda": "Введено, м²",
        "nakopl_vvod": "Введено, м²",
        "potreb_kachestva": "Средняя оценка",
        "skorost": "Скорость строительства, дней/дом",
    }
    if sorting_key not in metric_names:
        raise ValueError(f"unknown TOP sorting: {sorting_key}")
    if path.suffix.lower() != ".xlsx":
        raise ValueError("TOP export must be XLSX")
    validate_excel_file(path)
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="Workbook contains no default style")
        workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        if len(workbook.worksheets) != 1:
            raise ValueError("expected exactly one TOP worksheet")
        rows = workbook.worksheets[0].iter_rows(values_only=True)
        headers = [_normalise_label(value) for value in next(rows, ())]
        if not headers or "" in headers or len(set(headers)) != len(headers):
            raise ValueError("missing, blank or duplicate TOP headers")
        name_headers = {"наименование", "наименование, регион"}
        name_columns = [i for i, header in enumerate(headers) if header in name_headers]
        if len(name_columns) != 1 or "место" not in headers:
            raise ValueError("TOP name/rank columns missing or ambiguous")
        metric = _normalise_label(metric_names[sorting_key])
        if metric not in headers:
            raise ValueError(f"{sorting_key}: missing metric {metric_names[sorting_key]}")
        # ERZ includes the two deadline columns in construction, annual input,
        # and accumulated input exports. They describe developer delays and are
        # not, by themselves, evidence of the accumulated-input rating. Annual
        # and accumulated input therefore share one workbook schema; their exact
        # identity is established by the verified URL and selected sorting before
        # and after the download.
        transfer_marker = any(
            header.startswith("с переносом срока") for header in headers
        )
        clarification_marker = any(
            header.startswith("уточнение срока") for header in headers
        )
        markers = {
            "quality": "средняя оценка" in headers,
            "speed": "скорость строительства, дней/дом" in headers,
            "deadline_columns": transfer_marker and clarification_marker,
            "commissioned": "введено, м²" in headers,
            "construction": "строится, м²" in headers,
        }
        expected = {
            "obyem_stroitelstva": (False, False, True, False, True),
            "obyem_vvoda": (False, False, True, True, False),
            "nakopl_vvod": (False, False, True, True, False),
            "potreb_kachestva": (True, False, False, False, True),
            "skorost": (False, True, False, False, False),
        }[sorting_key]
        if tuple(markers.values()) != expected:
            raise ValueError(f"{sorting_key}: conflicting export schema {markers}")
        if sorting_key == "potreb_kachestva" and "жк/пт, всего в расчете" not in headers:
            raise ValueError("quality export has no ЖК/ПТ calculation column")
        if sorting_key == "skorost" and "введено мкд по дду за 3 года" not in headers:
            raise ValueError("speed export has no three-year ДДУ column")
        metric_col, rank_col = headers.index(metric), headers.index("место")
        count = 0
        ranks: list[int] = []
        row_preview: list[dict] = []
        for excel_row, row in enumerate(rows, 2):
            if all(value is None for value in row):
                continue
            name = row[name_columns[0]]
            if not isinstance(name, str) or not name.strip():
                raise ValueError(f"row {excel_row}: missing developer name")
            for column, positive, integer in ((metric_col, sorting_key == "skorost", False),
                                             (rank_col, True, True)):
                value = row[column]
                if isinstance(value, bool) or value is None:
                    raise ValueError(f"row {excel_row}: missing/invalid {headers[column]}")
                try:
                    number = float(re.sub(r"\s+", "", str(value)).replace(",", "."))
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"row {excel_row}: invalid {headers[column]}") from exc
                if not math.isfinite(number) or number < 0 or (positive and number <= 0):
                    raise ValueError(f"row {excel_row}: invalid range {headers[column]}")
                if integer and not number.is_integer():
                    raise ValueError(f"row {excel_row}: invalid rank")
                if integer:
                    ranks.append(int(number))
            if len(row_preview) < 25:
                row_preview.append({
                    "rank": ranks[-1],
                    "name": re.sub(r"\s+", " ", name).strip(),
                })
            count += 1
        if not count:
            raise ValueError("TOP export contains no developer rows")
        # A complete ERZ "full list" is intrinsically numbered 1..N.  This is
        # stronger evidence than the pagination counter on the HTML page: ERZ
        # can expose a smaller UI count than the authenticated Excel export.
        # Reject duplicate, missing and out-of-order ranks before publication.
        if ranks != list(range(1, count + 1)):
            raise ValueError("TOP ranks must be a unique continuous sequence 1..N")
        schema_family = (
            "commissioned_with_deadlines"
            if sorting_key in {"obyem_vvoda", "nakopl_vvod"}
            else sorting_key
        )
        return {"rows": count, "columns": headers, "metric": metric_names[sorting_key],
                "rank_first": ranks[0], "rank_last": ranks[-1],
                "rank_sequence_complete": True,
                "row_preview": row_preview,
                "schema_family": schema_family}
    finally:
        workbook.close()


def _validate_downloaded_excel(path: Path, sorting_key: str) -> bool:
    try:
        _excel_contract(path, sorting_key)
        return True
    except (OSError, ValueError, BadZipFile, KeyError, IndexError) as exc:
        print(f"       ERROR: invalid downloaded Excel {path.name}: {exc}")
        return False


def _finalize_downloaded_excel(source: Path, target: Path, sorting_key: str) -> bool:
    if not _validate_downloaded_excel(source, sorting_key):
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        source.replace(target)
        return True
    except OSError as exc:
        print(f"       ERROR: failed to finalize {target.name}: {exc}")
        return False


def _load_credentials() -> dict | None:
    if not CONFIG_FILE.exists():
        return None
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


# ─────────────────────────────────────────────
# Login
# ─────────────────────────────────────────────


def _ensure_logged_in(driver) -> bool:
    """Логинится через модалку «Вход в аккаунт ЕРЗ» на erzrf.ru.

    Креды читает из config/erzrf.json (поля email, password). Формат логина:
    модальное окно открывается кликом по иконке/кнопке «Войти» в правом
    верхнем углу шапки сайта (не отдельная /login страница).
    """
    creds = _load_credentials()
    if not creds:
        print("  ⚠️  Нет config/erzrf.json — скачивание xlsx требует авторизации.")
        print("     Скопируй config/erzrf.example.json → config/erzrf.json")
        print("     и впиши email/password от erzrf.ru.")
        return False

    email = creds.get("email") or ""
    password = creds.get("password") or ""
    if not email or not password:
        print("  ⚠️  В config/erzrf.json не заполнены email/password.")
        return False

    print(f"  🔐 Логин как {email[:3]}***")
    try:
        # Шаг 1: открыть главную с retry — erzrf периодически отдаёт
        # «The page you are looking for is temporarily unavailable»
        # или просто долго грузится. До 5 попыток с refresh + пауза.
        MAX_PAGE_ATTEMPTS = 5
        opened = False
        for attempt in range(1, MAX_PAGE_ATTEMPTS + 1):
            try:
                driver.get(BASE)
                selenium_sleep(4)
                # Проверяем что страница нормальная (не «temporarily unavailable»)
                page_text = (driver.execute_script(
                    "return document.body && document.body.innerText || ''"
                ) or "").lower()
                if "temporarily unavailable" in page_text or "the page you are looking for" in page_text:
                    print(f"  ⏳ Попытка {attempt}/{MAX_PAGE_ATTEMPTS}: страница "
                          f"temporarily unavailable, ждём 10 сек...")
                    selenium_sleep(10)
                    continue
                # Шаг 2: кликнуть кнопку открытия модалки
                opened = driver.execute_script(
                    """
                    const visible = e => e.offsetParent !== null;
                    const re = /^\\s*(вход|войти)\\s*$/i;

                    // 1) точный title (как на скрине пользователя — tooltip «Вход»)
                    let btn = [...document.querySelectorAll('[title]')]
                        .find(e => visible(e) && re.test(e.getAttribute('title') || ''));

                    // 2) точный aria-label
                    if (!btn) {
                        btn = [...document.querySelectorAll('[aria-label]')]
                            .find(e => visible(e) && re.test(e.getAttribute('aria-label') || ''));
                    }

                    // 3) элемент в шапке с текстом «Войти»/«Вход» — НЕ ищем по всему
                    //    документу, иначе словим случайные кнопки в контенте.
                    if (!btn) {
                        const header = document.querySelector(
                            'header, [class*="header" i], [class*="Header"], [id*="header" i]'
                        );
                        if (header) {
                            btn = [...header.querySelectorAll('button, a, div, span')]
                                .find(e => visible(e) && e.innerText && re.test(e.innerText));
                        }
                    }

                    if (btn) { btn.click(); return true; }
                    return false;
                    """
                )
                if opened:
                    break
                # Не нашёл кнопку — пробуем refresh ещё раз
                print(f"  ⏳ Попытка {attempt}/{MAX_PAGE_ATTEMPTS}: кнопка логина "
                      f"не появилась, обновляю страницу...")
                selenium_sleep(5)
            except Exception as e:  # noqa: BLE001
                print(f"  ⏳ Попытка {attempt}/{MAX_PAGE_ATTEMPTS}: {e}, повторяю...")
                selenium_sleep(5)
        if not opened:
            print(f"  ⚠️  Не нашёл кнопку открытия модалки логина "
                  f"за {MAX_PAGE_ATTEMPTS} попыток")
            _save_debug_snapshot(driver, "login_open_button")
            return False

        # Шаг 3: дождаться появления модалки (по placeholder «Логин» или
        # input с типом password, отсутствующим до клика).
        try:
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, 'input[type="password"]')
                )
            )
        except TimeoutException:
            print("  ⚠️  Модалка логина не появилась")
            _save_debug_snapshot(driver, "login_modal_timeout")
            return False
        selenium_sleep(1)

        # Шаг 4: найти поле логина. По скрину placeholder = «Логин или
        # адрес электронной почты», иногда тип input может быть text/email.
        email_input = None
        for css in [
            'input[placeholder*="огин" i]',  # «Логин»
            'input[placeholder*="лектронной" i]',  # «электронной»
            'input[placeholder*="mail" i]',
            'input[type="email"]',
            'input[name*="login" i]',
            'input[name*="email" i]',
        ]:
            try:
                els = driver.find_elements(By.CSS_SELECTOR, css)
                visible = [e for e in els if e.is_displayed()]
                if visible:
                    email_input = visible[0]
                    break
            except WebDriverException:
                continue

        # Если не нашли по селектору — берём первый видимый text-input
        # рядом с password-input (родитель/форма).
        if email_input is None:
            try:
                pw = driver.find_element(By.CSS_SELECTOR, 'input[type="password"]')
                form = pw
                for _ in range(5):
                    form = form.find_element(By.XPATH, "..")
                    inputs = form.find_elements(
                        By.CSS_SELECTOR, 'input[type="text"], input[type="email"], input:not([type])'
                    )
                    visible = [i for i in inputs if i.is_displayed()]
                    if visible:
                        email_input = visible[0]
                        break
            except WebDriverException:
                pass

        password_input = None
        try:
            password_input = driver.find_element(By.CSS_SELECTOR, 'input[type="password"]')
        except NoSuchElementException:
            pass

        if not email_input or not password_input:
            print("  ⚠️  Поля логина/пароля не найдены в модалке")
            _save_debug_snapshot(driver, "login_fields_not_found")
            return False

        email_input.clear()
        email_input.send_keys(email)
        password_input.clear()
        password_input.send_keys(password)
        selenium_sleep(0.5)

        # Шаг 5: кликнуть оранжевую кнопку «Войти» В МОДАЛКЕ.
        clicked = driver.execute_script(
            """
            const visible = e => e.offsetParent !== null;
            const pw = document.querySelector('input[type="password"]');
            if (!pw) return false;
            // поднимаемся к контейнеру модалки и ищем там кнопку «Войти»
            let cont = pw;
            for (let i = 0; i < 8; i++) {
                cont = cont.parentElement;
                if (!cont) break;
                const btns = [...cont.querySelectorAll('button, a, div[class*="button"]')];
                const btn = btns.find(b => visible(b) && b.innerText &&
                    b.innerText.trim() === 'Войти');
                if (btn) { btn.click(); return true; }
            }
            return false;
            """
        )
        if not clicked:
            # fallback — Enter в password
            try:
                password_input.send_keys(Keys.RETURN)
            except WebDriverException:
                pass

        # Шаг 6: проверка успеха — модалка закрылась, password-input
        # больше не виден.
        try:
            WebDriverWait(driver, 10).until_not(
                EC.visibility_of_element_located(
                    (By.CSS_SELECTOR, 'input[type="password"]')
                )
            )
            print("  ✅ Авторизация прошла")
            return True
        except TimeoutException:
            print("  ⚠️  Модалка не закрылась после сабмита — возможно неверные креды.")
            _save_debug_snapshot(driver, "login_failed")
            return False

    except WebDriverException as exc:
        print(f"  ❌ Ошибка авторизации: {exc}")
        return False


# ─────────────────────────────────────────────
# UI helpers (фильтры на TOP-странице)
# ─────────────────────────────────────────────


def _save_debug_snapshot(driver, tag: str) -> None:
    debug_dir = DOWNLOAD_DIR.parent / "_debug"
    debug_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    (debug_dir / f"erzrf_{tag}_{ts}.html").write_text(
        driver.page_source, encoding="utf-8"
    )
    try:
        driver.save_screenshot(str(debug_dir / f"erzrf_{tag}_{ts}.png"))
    except WebDriverException:
        pass


def _click_download_excel(driver, target_label: str = "Весь список") -> dict:
    """Кликает кнопку скачивания xlsx (по умолчанию «Весь список»).

    HTML 03.06.2026:
        <span class="flex links" title="нажмите для формирования и скачивания файла">
            <span class="pointer">ТОП-20</span>
            <span class="pointer">ТОП-50</span>
            <span class="pointer">ТОП-100</span>
            <span class="pointer">Весь список</span>
        </span>
    Передавай target_label="ТОП-100" если нужен ТОП-100, по умолчанию —
    «Весь список» (вся выгрузка ~2900 застройщиков).
    """
    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
    selenium_sleep(1)

    return driver.execute_script(
        """
        const TARGET_LABEL = arguments[0];
        const visible = e => e.offsetParent !== null;

        // 1) основной путь: родитель с title содержит «формирования|скачивания»,
        // ребёнок — span/кнопка с точным текстом target
        const parents = [...document.querySelectorAll('[title]')]
            .filter(e => visible(e) &&
                /формирования|скачивания|скачать/i.test(e.getAttribute('title') || ''));
        for (const p of parents) {
            const target = [...p.querySelectorAll('span, button, a, div')]
                .find(c => visible(c) && (c.innerText || '').trim() === TARGET_LABEL);
            if (target) {
                target.scrollIntoView({block: 'center'});
                target.click();
                return {
                    clicked: true,
                    matched: 'title-parent>' + TARGET_LABEL,
                    tag: target.tagName,
                    text: (target.innerText || '').substring(0, 100),
                    html: target.outerHTML.substring(0, 300),
                    parent_title: p.getAttribute('title') || '',
                };
            }
        }

        // 2) fallback: любой видимый .pointer/span с этим текстом
        const fallback = [...document.querySelectorAll('.pointer, span, a, button')]
            .find(s => visible(s) && (s.innerText || '').trim() === TARGET_LABEL);
        if (fallback) {
            fallback.scrollIntoView({block: 'center'});
            fallback.click();
            return {
                clicked: true,
                matched: 'fallback:' + TARGET_LABEL,
                tag: fallback.tagName,
                text: (fallback.innerText || '').substring(0, 100),
                html: fallback.outerHTML.substring(0, 300),
            };
        }

        return {clicked: false, matched: 'none'};
        """,
        target_label,
    )


def _scrape_developers_from_table(driver) -> list[dict]:
    """Снимает таблицу ТОП в формате list[dict].

    Устойчив к разным разметкам: настоящая <table>, [role="table"],
    или div-сетка. Финальный fallback — собрать все ссылки
    /zastroyschiki/<slug> на странице.
    """
    return driver.execute_script(
        """
        const visible = e => e.offsetParent !== null;

        // 1) настоящая <table>
        const tables = [...document.querySelectorAll('table')]
            .filter(t => visible(t) && t.rows.length > 1);
        if (tables.length) {
            const t = tables[0];
            const out = [];
            for (let i = 1; i < t.rows.length; i++) {
                const row = t.rows[i];
                const cells = [...row.cells].map(c => c.innerText.trim());
                const link = row.querySelector('a[href*="/zastroyschiki/"]');
                out.push({
                    place: cells[0] || '',
                    cells: cells,
                    card_url: link ? link.href : null,
                    name: link ? link.innerText.trim() : (cells[2] || cells[1] || ''),
                });
            }
            return out;
        }

        // 2) fallback — собираем уникальные ссылки /zastroyschiki/<slug>
        const seen = new Set();
        const out = [];
        const links = [...document.querySelectorAll('a[href*="/zastroyschiki/"]')]
            .filter(visible);
        for (const a of links) {
            const href = a.href;
            const m = href.match(/\\/zastroyschiki\\/([^/?#]+)/);
            if (!m) continue;
            if (seen.has(href)) continue;
            seen.add(href);
            // ищем родительскую "строку" — div с несколькими детьми с текстом
            let row = a.closest('tr, [role="row"], li, [class*="row" i]');
            const cells = row
                ? [...row.children].map(c => (c.innerText || '').trim())
                : [];
            out.push({
                place: cells[0] || String(seen.size),
                cells: cells,
                card_url: href,
                name: a.innerText.trim(),
            });
        }
        return out;
        """
    )


# ─────────────────────────────────────────────
# TOP — скачивание xlsx + ссылок на карточки
# ─────────────────────────────────────────────


def _wait_for_top_content(driver, timeout: int = 30) -> bool:
    """Ждёт появления данных в таблице ТОП.

    erzrf может рендерить таблицу как настоящий <table>, как [role="table"]
    или как набор div'ов. Поэтому ждём по контенту — строку «Застройщик»
    или «Место в».
    """
    try:
        WebDriverWait(driver, timeout).until(
            lambda d: (
                "Застройщик" in d.page_source
                or "Место в" in d.page_source
                or "ГК Самолет" in d.page_source
                or "ПИК" in d.page_source
            )
        )
        return True
    except TimeoutException:
        return False




def _collect_top_n_developers(driver, region: dict, n: int) -> list[dict]:
    """Обходит страницы пагинации (по 20 на странице) и собирает первых N застройщиков.

    На странице erzrf.ru/top-zastroyshchikov/<region>?topType=0&page=K
    показывается ровно 20 строк. Чтобы взять ТОП-100, идём по страницам 1-5.
    """
    devs: list[dict] = []
    pages_needed = (n + 19) // 20
    for page_num in range(1, pages_needed + 1):
        url = _build_top_url(region, "obyem_stroitelstva") + f"&page={page_num}"
        print(f"     · страница {page_num}/{pages_needed}: {url}")
        driver.get(url)
        selenium_sleep(4)
        if not _wait_for_top_content(driver):
            print(f"       ⚠️  страница {page_num} не загрузилась")
            _save_debug_snapshot(driver, f"top_{region['key']}_page{page_num}_timeout")
            break
        page_devs = _scrape_developers_from_table(driver)
        if not page_devs:
            print(f"       ⚠️  страница {page_num} пустая")
            break
        # На второй+ странице JS-скрейпер может вернуть тех же 20 что
        # уже взяты. Дедуплицируем по card_url.
        seen_urls = {d.get("card_url") for d in devs}
        new = [d for d in page_devs if d.get("card_url") and d["card_url"] not in seen_urls]
        devs.extend(new)
        print(f"       + {len(new)} новых (всего {len(devs)})")
        if len(devs) >= n or not new:
            break
    return devs[:n]


def _scroll_to_load_all(driver, *, max_scrolls: int = 30, pause: float = 1.0) -> int:
    """Скроллит страницу вниз пока её высота продолжает расти.

    Возвращает количество найденных ссылок /zastroyschiki/ после скролла —
    полезный индикатор «сколько строк подгрузилось».
    """
    last_height = 0
    for _ in range(max_scrolls):
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        selenium_sleep(pause)
        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height:
            break
        last_height = new_height
    count = driver.execute_script(
        "return document.querySelectorAll('a[href*=\"/zastroyschiki/\"]').length"
    )
    return int(count or 0)


# Сортировки и регионы где имеет смысл скачивать данные ПО ГОДАМ
# (т.е. где на странице есть фильтр «Год ввода»):
#   obyem_vvoda — там реально разные данные за разные годы
# Прочие сортировки (obyem_stroitelstva / nakopl_vvod / potreb_kachestva /
# skorost) показывают НЕ годовой срез — год для них не применим.
PER_YEAR_SORTINGS = {"obyem_vvoda"}
PER_YEAR_RANGE = list(range(2022, 2027))  # 2022..2026 включительно


@dataclass(frozen=True)
class TopExport:
    region_key: str
    sorting_key: str
    year: int | None = None

    @property
    def region(self) -> dict:
        return next(region for region in REGIONS if region["key"] == self.region_key)

    def filename(self, date_str: str) -> str:
        year = f"_{self.year}" if self.year is not None else ""
        return f"top_{self.sorting_key}_{self.region_key}{year}_{date_str}.xlsx"


@dataclass
class TopFetchResult:
    required_files: set[str]
    files: list[Path] = field(default_factory=list)
    error: str = ""

    @property
    def missing_files(self) -> list[str]:
        return sorted(self.required_files - {path.name for path in self.files})

    @property
    def complete(self) -> bool:
        return bool(self.required_files) and not self.error and not self.missing_files


class TopExportError(ValueError):
    """An uncertain export ends the browser session before another download."""


def _top_export_plan() -> list[TopExport]:
    return [TopExport(region["key"], sorting["key"], year)
            for region in REGIONS for sorting in SORTINGS
            for year in [None, *(PER_YEAR_RANGE if sorting["key"] in PER_YEAR_SORTINGS else [])]]


def _read_top_selection(driver) -> dict:
    return driver.execute_script(r"""
        const totals = [...(document.body.innerText || '').matchAll(
            /из\s+([\d\s\u00a0\u202f]+)\s+застройщик(?:ов|а)?/gi
        )].map(m => Number(m[1].replace(/\s/g, '')));
        return {url: location.href, developer_totals: totals,
            selects: [...document.querySelectorAll('select')].map(s => ({
            id: s.id, value: s.value,
            selected_text: s.selectedOptions.length === 1 ? s.selectedOptions[0].textContent.trim() : '',
            options: [...s.options].map(o => ({value: o.value, text: o.textContent.trim()}))
        }))};
    """)


def _sorting_control(selection: dict) -> tuple[dict, dict]:
    candidates = []
    observed = []
    for control in selection.get("selects", []):
        mapping = {}
        for option in control.get("options", []):
            key = _sorting_from_label(option.get("text", ""))
            if key is None:
                continue
            value = str(option.get("value", ""))
            if key in mapping or not re.fullmatch(r"\d+", value):
                raise TopExportError("ambiguous/non-numeric sorting option")
            mapping[key] = int(value)
        if mapping:
            observed.append({"id": control.get("id", ""), "mapping": mapping})
        if set(mapping) == {sorting["key"] for sorting in SORTINGS}:
            if len(set(mapping.values())) != len(mapping):
                raise TopExportError("duplicate topType values")
            candidates.append((control, mapping))
    if len(candidates) != 1:
        raise TopExportError(
            "cannot establish one complete sorting selector; "
            f"candidates={len(candidates)}, observed={observed}"
        )
    control, mapping = candidates[0]
    if mapping != TOP_TYPES:
        raise TopExportError("source sorting mapping changed; source evidence needs review")
    return control, mapping


def _year_control(selection: dict) -> dict:
    controls = [item for item in selection.get("selects", [])
                if len([option for option in item.get("options", [])
                        if re.fullmatch(r"20\d{2}", str(option.get("value", "")))]) >= 2]
    if (len(controls) != 1
            or not re.fullmatch(r"20\d{2}", str(controls[0].get("value", "")))):
        raise TopExportError("selected annual period is missing or ambiguous")
    return controls[0]


def _top_table_snapshot(driver) -> tuple[list[dict], str]:
    rows = _scrape_developers_from_table(driver)
    if not rows or not all(row.get("name") and row.get("card_url") and row.get("cells") for row in rows):
        raise TopExportError("cannot establish nonempty developer table contents")
    digest = hashlib.sha256(
        json.dumps(rows, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return rows, digest


def _top_table_digest(driver) -> str:
    return _top_table_snapshot(driver)[1]


def _assert_visible_prefix_matches_workbook(
        visible_rows: list[dict], contract: dict, required_rows: int) -> None:
    """Bind the downloaded workbook to the ranking visible before its click."""
    preview = contract.get("row_preview") or []
    if required_rows <= 0 or len(visible_rows) < required_rows:
        raise TopExportError("visible table has too few rows for request correlation")
    if len(preview) < required_rows:
        raise TopExportError("workbook has no sufficient row preview for request correlation")
    for index, visible in enumerate(visible_rows[:required_rows]):
        place_match = re.match(r"\s*(\d+)", str(visible.get("place", "")))
        if not place_match:
            raise TopExportError("visible table rank cannot be parsed")
        workbook = preview[index]
        if int(place_match.group(1)) != workbook.get("rank"):
            raise TopExportError("workbook ranking differs from the visible table")
        visible_name = _normalise_label(visible.get("name", ""))
        workbook_name = _normalise_label(workbook.get("name", ""))
        if not visible_name or not (
            workbook_name == visible_name
            or workbook_name.startswith(visible_name + ",")
        ):
            raise TopExportError("workbook developers differ from the visible table")


def _top_request_evidence(driver, request: TopExport, top_types: dict) -> dict:
    selection = _read_top_selection(driver)
    control, observed_mapping = _sorting_control(selection)
    if observed_mapping != top_types:
        raise TopExportError("sorting mapping changed during collection")
    if (_sorting_from_label(control.get("selected_text", "")) != request.sorting_key
            or str(control.get("value")) != str(top_types[request.sorting_key])):
        raise TopExportError("selected sorting differs from requested sorting")
    wanted_url = _build_top_url(request.region, request.sorting_key, top_types)
    actual, wanted = urlparse(selection.get("url", "")), urlparse(wanted_url)
    query, wanted_query = parse_qs(actual.query), parse_qs(wanted.query)
    if ((actual.scheme, actual.netloc, actual.path.rstrip("/"))
            != (wanted.scheme, wanted.netloc, wanted.path.rstrip("/"))
            or any(query.get(key) != value for key, value in wanted_query.items())):
        raise TopExportError("current URL differs from requested region/sorting")
    region_controls = [item for item in selection.get("selects", [])
                       if {"0", "143443001"}.issubset(
                           {str(option.get("value")) for option in item.get("options", [])})]
    wanted_region = wanted_query["regionKey"][0]
    if (not region_controls
            or any(str(item.get("value")) != wanted_region for item in region_controls)):
        raise TopExportError("selected region is missing, ambiguous or incorrect")
    selected_year = None
    if request.sorting_key in PER_YEAR_SORTINGS:
        selected_year = int(_year_control(selection)["value"])
        if request.year is not None and selected_year != request.year:
            raise TopExportError("selected year differs from requested year")
    elif request.year is not None:
        raise TopExportError("year cannot be requested for this sorting")
    totals = selection.get("developer_totals", [])
    if (not totals or any(type(total) is not int or total <= 0 for total in totals)
            or len(set(totals)) != 1):
        raise TopExportError("full-list developer count is missing or ambiguous")
    return {"requested_url": wanted_url, "observed_url": selection["url"],
            "sorting": request.sorting_key, "top_type": top_types[request.sorting_key],
            "selected_sorting": control["selected_text"], "region": request.region_key,
            "selected_region": region_controls[0].get("selected_text", ""),
            "region_control_ids": [item.get("id", "") for item in region_controls],
            "requested_year": request.year, "selected_year": selected_year,
            "expected_rows": totals[0]}


def _download_top_export(driver, request: TopExport, date_str: str,
                         staging: Path, top_types: dict,
                         output_dir: Path | None = None) -> Path:
    """A private attempt directory plus session abort prevents late-file reuse."""
    evidence = _top_request_evidence(driver, request, top_types)
    year_evidence = getattr(driver, "_erzrf_year_evidence", None)
    if request.year is not None and (not year_evidence or year_evidence.get("year") != request.year):
        raise TopExportError("explicit year has no observed selection/render acknowledgement")
    visible_rows, table_digest = _top_table_snapshot(driver)
    attempt = staging / request.filename(date_str).removesuffix(".xlsx")
    attempt.mkdir()  # Never reuse a previous attempt directory.
    driver.execute_cdp_cmd("Browser.setDownloadBehavior", {
        "behavior": "allow", "downloadPath": str(attempt.resolve()),
    })
    click_info = _click_download_excel(driver, target_label="Весь список")
    if not click_info.get("clicked"):
        raise TopExportError("export button was not found")
    source = wait_for_download(attempt, before_snapshot=set(), timeout=180)
    if source is None:
        raise TopExportError("export timed out; browser session must be closed")
    candidates = [path for path in attempt.iterdir() if path.is_file()]
    if (source.resolve().parent != attempt.resolve() or len(candidates) != 1
            or candidates[0].resolve() != source.resolve()):
        raise TopExportError("ambiguous or unrelated export download")
    if _top_request_evidence(driver, request, top_types) != evidence:
        raise TopExportError("selected request changed while export was downloading")
    if _top_table_digest(driver) != table_digest:
        raise TopExportError("table changed while export was downloading")
    # Run the semantic contract before publication; keep failures private until
    # the session closes and the staging directory is cleaned up.
    contract = _excel_contract(source, request.sorting_key)
    ui_rows = evidence.pop("expected_rows")
    _assert_visible_prefix_matches_workbook(
        visible_rows, contract, required_rows=min(20, ui_rows)
    )
    if contract["rows"] < ui_rows:
        raise TopExportError(
            f"full-list export has fewer rows than the page: {contract['rows']} / {ui_rows}"
        )
    evidence.update({
        "ui_developer_count": ui_rows,
        "workbook_developer_count": contract["rows"],
        "row_count_discrepancy": contract["rows"] - ui_rows,
        "completeness_evidence": "continuous unique workbook ranks 1..N; workbook rows >= UI count",
    })
    evidence.update({"schema_version": 2, "received_at": datetime.now().isoformat(timespec="seconds"),
                     "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                     "workbook": contract,
                     "visible_table_sha256": table_digest,
                     "year_selection_evidence": year_evidence,
                     "period_verified": False,
                     "period_evidence": "selected_year is a UI selection, not an independently verified response period"})
    target = (DOWNLOAD_DIR if output_dir is None else output_dir) / request.filename(date_str)
    target.parent.mkdir(parents=True, exist_ok=True)
    source.replace(target)
    _write_json_atomic(target.with_suffix(".xlsx.provenance.json"), evidence)
    print(f"       ✓ проверен и подготовлен {target.name} ({contract['rows']} rows)")
    return target


def _publish_top_batch(staged_files: list[Path], destination: Path, rollback_dir: Path) -> list[Path]:
    """Publish one complete TOP snapshot and restore prior files on an error.

    ``staged_files`` contains the 20 Excel files and two developer JSON files.
    Excel provenance sidecars live beside them and are committed in the same
    transaction.  Nothing becomes discoverable by ETL until collection of the
    entire required set has succeeded.
    """
    staged_names = {path.name for path in staged_files}
    if len(staged_names) != len(staged_files) or any(not path.is_file() for path in staged_files):
        raise TopExportError("staged TOP batch is missing or contains duplicate primary files")
    batch_dir = staged_files[0].parent if staged_files else None
    if batch_dir is None or any(path.parent != batch_dir for path in staged_files):
        raise TopExportError("staged TOP batch spans multiple directories")
    artifacts = sorted(path for path in batch_dir.iterdir() if path.is_file())
    sidecars = {path.name for path in artifacts if path.name.endswith(".xlsx.provenance.json")}
    expected_sidecars = {f"{name}.provenance.json" for name in staged_names if name.endswith(".xlsx")}
    if sidecars != expected_sidecars:
        raise TopExportError("staged TOP batch has incomplete provenance sidecars")
    allowed_artifacts = staged_names | expected_sidecars
    if {path.name for path in artifacts} != allowed_artifacts:
        raise TopExportError("staged TOP batch contains unexpected artifacts")

    destination.mkdir(parents=True, exist_ok=True)
    rollback_dir.mkdir(parents=True, exist_ok=False)
    try:
        for source in artifacts:
            target = destination / source.name
            if target.exists():
                backup = rollback_dir / source.name
                target.replace(backup)
            source.replace(target)
    except BaseException:
        rollback_errors = []
        # Reconstruct what happened from the filesystem instead of relying on
        # Python-side lists: an interrupt may arrive after os.replace succeeds
        # but before the next bytecode can update a journal.
        for source in reversed(artifacts):
            target = destination / source.name
            backup = rollback_dir / source.name
            try:
                if backup.exists():
                    target.unlink(missing_ok=True)
                    backup.replace(target)
                elif not source.exists() and target.exists():
                    # No prior target existed and this staged artifact moved.
                    target.unlink()
            except OSError as exc:
                rollback_errors.append(f"rollback {target.name}: {exc}")
        if rollback_errors:
            raise TopExportError(
                "TOP batch publish failed and rollback was incomplete; "
                f"recovery files preserved at {rollback_dir}: "
                + "; ".join(rollback_errors)
            )
        try:
            rollback_dir.rmdir()
        except OSError:
            pass
        raise
    try:
        shutil.rmtree(rollback_dir)
    except OSError as exc:
        print(f"  WARNING: old TOP backups remain at {rollback_dir}: {exc}")
    return [destination / path.name for path in staged_files]


def _switch_year_filter(driver, year: int) -> bool:
    """Require selected year and an observed table change after a year switch.

    A select value alone acknowledges our own assignment, not a site response.
    Identical/stale table contents therefore fail closed when the year changes.
    This is render acknowledgement, not independent evidence of the data period.
    """
    control = _year_control(_read_top_selection(driver))
    before = _top_table_digest(driver)
    target = str(year)
    driver._erzrf_year_evidence = None
    if str(control["value"]) == target:
        driver._erzrf_year_evidence = {"year": year, "already_selected": True,
                                      "table_changed": False}
        return True
    if not control.get("id"):
        return False
    ok = driver.execute_script("""
        const sel = document.getElementById(arguments[0]);
        const target = arguments[1];
        if (!sel || ![...sel.options].some(o => o.value === target)) return false;
        sel.value = target;
        sel.dispatchEvent(new Event('input', {bubbles: true}));
        sel.dispatchEvent(new Event('change', {bubbles: true}));
        if (window.jQuery) window.jQuery(sel).val(target).trigger('change');
        return sel.value === target;
    """, control["id"], target)
    if not ok:
        return False

    def changed_and_selected(browser):
        if str(_year_control(_read_top_selection(browser))["value"]) != target:
            return False
        try:
            return _top_table_digest(browser) != before
        except TopExportError:
            return False

    try:
        WebDriverWait(driver, 30).until(changed_and_selected)
    except TimeoutException:
        return False
    driver._erzrf_year_evidence = {"year": year, "already_selected": False,
                                  "table_changed": True}
    return True


def fetch_top(state: dict) -> TopFetchResult:
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y%m%d")
    plan = _top_export_plan()
    required = {request.filename(date_str) for request in plan}
    required.update(f"top_developers_{region['key']}_{date_str}.json" for region in REGIONS)
    result = TopFetchResult(required_files=required)
    driver = None
    # Staging is outside the discoverable raw/erzrf folder, on the same volume
    # as the final destination. Close Chrome before removing any attempt files.
    with tempfile.TemporaryDirectory(prefix=".erzrf-top-", dir=DOWNLOAD_DIR.parent) as tmp:
        staging = Path(tmp)
        batch = staging / "validated-batch"
        batch.mkdir()
        try:
            driver = create_chrome(download_dir=staging, headless=HEADLESS)
            driver.set_page_load_timeout(PAGE_TIMEOUT)
            if not _ensure_logged_in(driver):
                raise TopExportError("authorization unavailable; no TOP exports downloaded")
            driver.get(_build_top_url(REGIONS[0], "obyem_stroitelstva"))
            if not _wait_for_top_content(driver):
                raise TopExportError("initial TOP content did not load")
            _, top_types = _sorting_control(_read_top_selection(driver))
            for request in plan:
                url = _build_top_url(request.region, request.sorting_key, top_types)
                print(f"  TOP {request.region_key}/{request.sorting_key}/{request.year or 'current'}: {url}")
                driver._erzrf_year_evidence = None
                driver.get(url)
                if not _wait_for_top_content(driver):
                    raise TopExportError("TOP content did not load")
                if request.year is not None and not _switch_year_filter(driver, request.year):
                    raise TopExportError(f"year {request.year} could not be selected")
                _download_top_export(
                    driver, request, date_str, staging, top_types, output_dir=batch
                )
            for region in REGIONS:
                developers = _collect_top_n_developers(driver, region, n=TOP_N_DEVELOPERS)
                urls = {dev.get("card_url") for dev in developers if dev.get("card_url")}
                if len(developers) != TOP_N_DEVELOPERS or len(urls) != TOP_N_DEVELOPERS:
                    raise TopExportError(f"incomplete TOP-{TOP_N_DEVELOPERS} developers for {region['key']}")
                target = batch / f"top_developers_{region['key']}_{date_str}.json"
                _write_json_atomic(target, {"region": region["key"],
                                           "scraped_at": datetime.now().isoformat(timespec="seconds"),
                                           "developers": developers})
            # End the browser session before making the validated snapshot
            # visible. A late Chrome write can then only affect staging.
            try:
                driver.quit()
            except WebDriverException as exc:
                raise TopExportError(f"browser cleanup failed before publication: {exc}") from exc
            driver = None
            staged_files = [batch / name for name in sorted(required)]
            recovery_dir = (
                DOWNLOAD_DIR.parent / f".erzrf-top-recovery-{uuid.uuid4().hex}"
            )
            result.files = _publish_top_batch(staged_files, DOWNLOAD_DIR, recovery_dir)
            print(f"  ✅ Опубликован полный пакет TOP: {len(result.files)} основных файлов "
                  f"и {len(plan)} provenance-файлов")
        except Exception as exc:  # An uncertain download must end this session.
            result.error = f"{type(exc).__name__}: {exc}"
            print(f"  ERROR TOP: {result.error}")
        finally:
            if driver is not None:
                try:
                    driver.quit()
                except WebDriverException as exc:
                    result.error = result.error or f"browser cleanup failed: {exc}"
    attempted_at = datetime.now().isoformat(timespec="seconds")
    state["erzrf_top_attempt"] = {"attempted_at": attempted_at, "complete": result.complete,
                                  "required_files": sorted(required),
                                  "files": [path.name for path in result.files],
                                  "missing_files": result.missing_files, "error": result.error}
    if result.complete:
        state["erzrf_top"] = {"last_run": attempted_at, "files": [path.name for path in result.files]}
    return result


# ─────────────────────────────────────────────
# CARDS — DOM-скрейп карточек ТОП-100
# ─────────────────────────────────────────────


def _developer_slug(card_url: str) -> str | None:
    m = SLUG_RE.search(card_url or "")
    return m.group(1) if m else None


def _load_top_developers() -> list[dict]:
    candidates = sorted(DOWNLOAD_DIR.glob("top_developers_rf_*.json"))
    for path in reversed(candidates):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("payload is not an object")
            developers = payload.get("developers", [])
            if not isinstance(developers, list):
                raise ValueError("developers is not a list")
            if not developers:
                raise ValueError("developers list is empty")
            return list(developers)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            print(f"  ⚠️  Пропускаю битый top_developers JSON {path.name}: {exc}")
    return []


def _parse_card_html(html: str) -> dict:
    """Парсит HTML карточки erzrf.ru/zastroyschiki/brand/<slug>.

    Структура страницы (Angular SPA, verified 03.06.2026 на 20 файлах):
    - Имя: <app-org-table> > <h3>, fallback breadcrumb «Бренд X»
    - Регионы: <app-org-regions-of-presence> > <a class="accordion__head">
      содержит «N (По состоянию на DD.MM.YYYY)»
    - Таблица Сдано/Перенос/Уточнение: <app-org-table-deadline>, дети
      .clmn — по одной колонке на сущность («Строится» + по году с 2016+):
        ps[0] <b>Сдано в YYYY</b>
        ps[1] sdano (X м²)
        ps[2] перенос м² (X м²)
        ps[3] перенос % ((X%))
        .show_btn — уточнение (X месяцев)
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return {"name": "", "regions_count": "", "regions_as_of": "", "deliveries": {}}

    soup = BeautifulSoup(html, "lxml")
    out = {
        "name": "",
        "regions_count": "",
        "regions_as_of": "",
        "deliveries": {},
    }

    # 1. Имя
    org_table = soup.find("app-org-table")
    if org_table:
        h3 = org_table.find("h3")
        if h3:
            out["name"] = re.sub(r"\s+", " ", h3.get_text(strip=True))
    if not out["name"]:
        m = re.search(r">Бренд\s+([^<]+)<", html)
        if m:
            out["name"] = m.group(1).strip()

    # 2. Регионы строительства
    regions_block = soup.find("app-org-regions-of-presence")
    if regions_block:
        a = regions_block.find("a", class_=re.compile(r"accordion__head"))
        if a:
            text = a.get_text(strip=True)
            m = re.match(
                r"(\d+)\s*\(По\s*состоянию\s*на\s*(\d{2}\.\d{2}\.\d{4})", text
            )
            if m:
                out["regions_count"] = m.group(1)
                out["regions_as_of"] = m.group(2)

    # 3. Таблица Сдано / Перенос / Уточнение
    deadline_block = soup.find("app-org-table-deadline")
    if deadline_block:
        for col in deadline_block.find_all("div", class_="clmn"):
            ps = col.find_all("p")
            if not ps:
                continue
            label_b = ps[0].find("b")
            if not label_b:
                continue
            label_text = label_b.get_text(strip=True)
            ym = re.search(r"(20\d{2})", label_text)
            key = ym.group(1) if ym else ("Строится" if "Строит" in label_text else None)
            if not key:
                continue

            data = {"sdano": "", "perenos_m2": "", "perenos_pct": "", "utochn": ""}
            if len(ps) > 1:
                m = re.search(r"([\d\s]+)\s*м", ps[1].get_text())
                if m:
                    data["sdano"] = re.sub(r"\s+", " ", m.group(1)).strip()
            if len(ps) > 2:
                m = re.search(r"([\d\s]+)\s*м", ps[2].get_text())
                if m:
                    data["perenos_m2"] = re.sub(r"\s+", " ", m.group(1)).strip()
            if len(ps) > 3:
                m = re.search(r"\(([\d.,]+)\s*%\)", ps[3].get_text())
                if m:
                    data["perenos_pct"] = m.group(1)
            show_btn = col.find("div", class_="show_btn")
            if show_btn:
                m = re.search(r"([\d.,]+)\s*месяц", show_btn.get_text())
                if m:
                    data["utochn"] = m.group(1)

            out["deliveries"][key] = data

    return out


def _scrape_card(driver) -> dict:
    """Тонкая обёртка над `_parse_card_html` — берёт driver.page_source."""
    try:
        return _parse_card_html(driver.page_source)
    except WebDriverException:
        return {"name": "", "regions_count": "", "regions_as_of": "", "deliveries": {}}


def _card_html_has_content(html: str) -> bool:
    return any(
        token in html
        for token in (
            "app-org-table",
            "app-org-regions-of-presence",
            "app-org-table-deadline",
            "Регионы присутствия",
            "Сдано",
        )
    )


def _wait_for_card_content(driver, timeout: int = 8) -> bool:
    try:
        WebDriverWait(driver, timeout).until(lambda d: _card_html_has_content(d.page_source))
        return True
    except TimeoutException:
        return False


def _parse_construction_table_df(df) -> dict:
    """Устаревший fallback, оставлен на случай нестандартной разметки."""
    return {}


def fetch_cards(state: dict) -> list[Path]:
    """Обходит карточки ТОП-застройщиков и собирает один xlsx.

    Селекторы и формат вывода верифицированы локально на 20 реальных HTML
    (см. parse_card_html). После перехода ждём признаков Angular-разметки в
    page_source; если сайт не успел отрисоваться, добираем короткой паузой.
    """
    CARDS_DIR.mkdir(parents=True, exist_ok=True)
    developers = _load_top_developers()
    if not developers:
        print("  ⚠️  Нет файла top_developers_rf_*.json — сначала запусти fetch_top")
        return []

    date_str = datetime.now().strftime("%Y%m%d")
    rows: list[dict] = []
    failed: list[dict] = []
    debug_saved = False

    driver = create_chrome(download_dir=CARDS_DIR, headless=HEADLESS)
    try:
        driver.set_page_load_timeout(PAGE_TIMEOUT)
        if not _ensure_logged_in(driver):
            print("  ⚠️  Без авторизации — карточки могут не открыться")

        for dev in developers[:TOP_N_DEVELOPERS]:
            card_url = dev.get("card_url")
            if not card_url:
                continue
            slug = _developer_slug(card_url) or ""
            place_str = str(dev.get("place", "?")).replace("\n", " | ")[:20]
            name_str = str(dev.get("name", "")).replace("\n", " ")[:50]
            print(f"     · {place_str:>20}  {name_str[:40]}  →  {slug[:60]}")
            try:
                driver.get(card_url)
                if not _wait_for_card_content(driver):
                    selenium_sleep(2)

                # Сохраняем HTML первой карточки для аудита
                if not debug_saved:
                    _save_debug_snapshot(driver, f"card_first_{slug.replace('/', '_')[:40]}")
                    debug_saved = True

                data = _scrape_card(driver)
                if not data.get("deliveries") and not data.get("name"):
                    print(f"       ⚠️  пусто — не удалось распарсить")
                    failed.append({"slug": slug, "url": card_url, "reason": "empty parse"})
                    if len(failed) <= 3:
                        _save_debug_snapshot(driver, f"card_{slug.replace('/', '_')[:40]}_empty")
                    continue

                rows.append(
                    {
                        "place": place_str,
                        "name_table": dev.get("name", ""),
                        "name_card": data.get("name", ""),
                        "slug": slug,
                        "url": card_url,
                        "regions_count": data.get("regions_count", ""),
                        "regions_as_of": data.get("regions_as_of", ""),
                        "deliveries": data.get("deliveries", {}),
                        "scraped_at": datetime.now().isoformat(timespec="seconds"),
                    }
                )
            except WebDriverException as exc:
                print(f"       ❌ {exc}")
                failed.append({"slug": slug, "url": card_url, "reason": str(exc)[:200]})
    finally:
        driver.quit()

    new_files: list[Path] = []
    if rows or failed:
        try:
            import pandas as pd
        except ImportError:
            print("     ⚠️  pandas не установлен — записать xlsx не получится")
            return []

        # Все встреченные года + флаг наличия «Строится»
        all_years = sorted({
            y for r in rows for y in (r.get("deliveries") or {}).keys() if y.isdigit()
        })
        has_stroitsa = any("Строится" in (r.get("deliveries") or {}) for r in rows)

        # Wide-формат: одна строка на застройщика
        base_cols = ["place", "name_card", "name_table", "slug", "url",
                     "regions_count", "regions_as_of"]
        wide_rows = []
        for r in rows:
            row = {c: r.get(c, "") for c in base_cols}
            deliveries = r.get("deliveries") or {}
            if has_stroitsa:
                d = deliveries.get("Строится", {}) or {}
                row["Строится_м²"] = d.get("sdano", "")
                row["Строится_перенос_м²"] = d.get("perenos_m2", "")
                row["Строится_перенос_%"] = d.get("perenos_pct", "")
                row["Строится_уточн_мес"] = d.get("utochn", "")
            for y in all_years:
                d = deliveries.get(y, {}) or {}
                row[f"Сдано_{y}_м²"] = d.get("sdano", "")
                row[f"Перенос_{y}_м²"] = d.get("perenos_m2", "")
                row[f"Перенос_{y}_%"] = d.get("perenos_pct", "")
                row[f"Уточн_{y}_мес"] = d.get("utochn", "")
            row["scraped_at"] = r.get("scraped_at", "")
            wide_rows.append(row)

        def _write_cards_workbook(path: Path) -> None:
            with pd.ExcelWriter(path, engine="openpyxl") as writer:
                if wide_rows:
                    pd.DataFrame(wide_rows).to_excel(writer, sheet_name="cards", index=False)
                if failed:
                    pd.DataFrame(failed).to_excel(writer, sheet_name="failed", index=False)

        target = CARDS_DIR / f"cards_{date_str}.xlsx"
        _write_excel_atomic(target, _write_cards_workbook)
        print(
            f"     ✅ {target.name} "
            f"(карточек: {len(wide_rows)}, лет: {len(all_years)} {all_years}, "
            f"ошибок: {len(failed)})"
        )
        new_files.append(target)
        state["erzrf_cards"] = {
            "last_run": datetime.now().isoformat(timespec="seconds"),
            "scraped": len(wide_rows),
            "failed": len(failed),
            "file": target.name,
        }
    return new_files


# ─────────────────────────────────────────────
# Главный цикл
# ─────────────────────────────────────────────


def run(only: Iterable[str] | None = None) -> tuple[list[Path], bool]:
    """Возвращает (скачанные_файлы, успех).

    успех=False если хотя бы один из запрошенных источников упал
    с ошибкой (incl. провал авторизации). Используется в __main__
    для exit-кода — чтобы update_realty.py видел провал и сделал retry.
    """
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    state = load_state()
    keys = set(only) if only else {"top", "cards"}
    unknown = sorted(keys - {"top", "cards"})

    print(f"\n{'='*60}")
    print(f"erzrf.ru | Запуск: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Источники: {', '.join(sorted(keys))}")
    print("Контракт TOP: nakopl_vvod=4, potreb_kachestva=2, skorost=3; "
          "каждый Excel проверяется до публикации")
    print(f"{'='*60}\n")

    all_new: list[Path] = []
    failed = bool(unknown)
    for key in unknown:
        print(f"  ⚠️  Неизвестный ключ: {key}")
    if "top" in keys:
        try:
            result = fetch_top(state)
            all_new.extend(result.files)
            save_state(state)
            if not result.complete:
                failed = True
        except Exception as exc:  # noqa: BLE001
            print(f"  ❌ top: {exc}")
            failed = True
    if "cards" in keys:
        try:
            files = fetch_cards(state)
            all_new.extend(files)
            save_state(state)
            if not files:
                failed = True
        except Exception as exc:  # noqa: BLE001
            print(f"  ❌ cards: {exc}")
            failed = True

    print(f"\n{'='*60}")
    print(f"erzrf.ru | Итог: новых/обновлённых файлов — {len(all_new)}")
    if failed:
        print(f"erzrf.ru | ⚠️  Один или несколько источников завершились неполно")
    print(f"{'='*60}\n")
    return all_new, not failed


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "sources",
        nargs="*",
        choices=["top", "cards"],
        help="Источники для запуска",
    )
    args = parser.parse_args()
    _files, ok = run(only=args.sources if args.sources else None)
    sys.exit(0 if ok else 2)
