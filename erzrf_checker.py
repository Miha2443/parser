"""
erzrf_checker.py
----------------
Скачивает данные с erzrf.ru (2 источника):

1. **erzrf_top** — `https://erzrf.ru/top-zastroyshchikov/`. Для каждой
   комбинации (5 сортировок × 2 региона) кликает кнопку «Скачать TOP в
   Excel». Получено по факту (Шаг 0 fixtures, 03.06.2026):
   - «По объёму текущего строительства»     → 15 колонок
   - «По объёму ввода МКД»                  → 15 колонок
   - «По накопленному вводу МКД с 2016 года»→ 16 колонок (+ «Ушёл с рынка»)
   - «По потребительским качествам»          → 14 совсем других колонок
   - «По скорости строительства»             → 10 совсем других колонок
   Все 5 — разные наборы, поэтому качаем все.

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

import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Iterable

from selenium.common.exceptions import (
    NoSuchElementException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from pipeline.selenium_utils import create_chrome, wait_for_download


# ─────────────────────────────────────────────
# КОНФИГ
# ─────────────────────────────────────────────

BASE = "https://erzrf.ru"
TOP_URL = f"{BASE}/top-zastroyshchikov"

# topType — URL-параметр erzrf для выбора сортировки (увидено в URL
# 03.06.2026: `?regionKey=0&topType=0&date=260601`).
TOP_TYPES = {
    "obyem_stroitelstva":  0,   # По объёму текущего строительства (default)
    "obyem_vvoda":         1,   # По объёму ввода
    "nakopl_vvod":         2,   # По накопленному вводу
    "potreb_kachestva":    3,   # По потребительским качествам
    "skorost":             4,   # По скорости строительства
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


def _build_top_url(region: dict, sorting_key: str) -> str:
    top_type = TOP_TYPES.get(sorting_key, 0)
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
HEADLESS = False  # TODO: переключить в True после первой удачной отладки.

SLUG_RE = re.compile(r"/zastroyschiki/([^?#]+?)/?(?:[?#]|$)")


# ─────────────────────────────────────────────
# State / config
# ─────────────────────────────────────────────


def load_state() -> dict:
    if STATE_FILE.exists():
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


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
        # Шаг 1: открыть главную (там есть иконка «Войти» в шапке)
        driver.get(BASE)
        time.sleep(3)

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
        if not opened:
            print("  ⚠️  Не нашёл кнопку открытия модалки логина")
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
        time.sleep(1)

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
        time.sleep(0.5)

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
    time.sleep(1)

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
        time.sleep(4)
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
        time.sleep(pause)
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


def _switch_year_filter(driver, year: int) -> bool:
    """Переключает фильтр года ввода через скрытый <select> (Select2 wrapper).

    HTML (предоставлен пользователем):
        <select id="select6" class="styleClass select2-hidden-accessible">
          <option value="2026">2026</option>
          <option value="2025">2025</option>
          ...
        </select>

    Select2 рендерит видимый span, но слушает события change на скрытом
    select. Меняем value и шлём 'change' с bubbles — Select2 и Angular
    оба подхватывают.

    Возвращает True если переключение прошло (текущий year == year).
    """
    target = str(year)
    ok = driver.execute_script(
        """
        const target = String(arguments[0]).trim();
        // Ищем select с опциями годов (id='select6' по примеру или
        // любой select где есть option с нужным value).
        let sel = document.getElementById('select6');
        if (!sel) {
            sel = [...document.querySelectorAll('select')].find(s =>
                [...s.options].some(o => (o.value || '').trim() === target));
        }
        if (!sel) return false;
        if (sel.value === target) return true;
        sel.value = target;
        sel.dispatchEvent(new Event('input', {bubbles: true}));
        sel.dispatchEvent(new Event('change', {bubbles: true}));
        // jQuery trigger если есть (Select2 слушает именно jq-event)
        if (window.jQuery) {
            try { window.jQuery(sel).val(target).trigger('change'); }
            catch(e) {}
        }
        return sel.value === target;
        """,
        target,
    )
    if not ok:
        return False
    # Ждём пока контент перерисуется (можно по смене таблицы)
    time.sleep(4)
    return True


def fetch_top(state: dict) -> list[Path]:
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    new_files: list[Path] = []

    # Одна сессия Chrome на весь обход — кука авторизации сохраняется.
    driver = create_chrome(download_dir=DOWNLOAD_DIR, headless=HEADLESS)
    try:
        driver.set_page_load_timeout(PAGE_TIMEOUT)

        if not _ensure_logged_in(driver):
            print("  ❌ Без авторизации скачивание xlsx невозможно. Пропускаем TOP.")
            return []

        date_str = datetime.now().strftime("%Y%m%d")

        def _download_current(filename_base: str) -> Path | None:
            """Кликает «Весь список», ждёт xlsx, переименовывает в target."""
            before = set(DOWNLOAD_DIR.glob("*"))
            click_info = _click_download_excel(driver, target_label="Весь список")
            if not click_info.get("clicked"):
                print(f"       ⚠️  кнопка «Весь список» не найдена")
                _save_debug_snapshot(driver, f"{filename_base}_no_button")
                return None
            print(
                f"       клик: [{click_info.get('matched')}] "
                f"{click_info.get('tag')} «{(click_info.get('text') or '')[:60]}»"
            )
            new_file = wait_for_download(
                DOWNLOAD_DIR, before_snapshot=before, timeout=180
            )
            if new_file is None:
                print(f"       ⚠️  xlsx не появился в папке за 180 сек")
                _save_debug_snapshot(driver, f"{filename_base}_after_click")
                return None
            target = DOWNLOAD_DIR / f"{filename_base}{new_file.suffix}"
            if target.exists():
                target.unlink()
            new_file.rename(target)
            print(f"       ✅ {target.name}")
            return target

        for region in REGIONS:
            print(f"  🌐 регион: {region['key']} ({region['label']})")

            # Шаг A: качаем «Весь список» по всем 5 сортировкам
            for sorting in SORTINGS:
                url = _build_top_url(region, sorting["key"])
                print(f"     ▸ сортировка: {sorting['key']} (topType={TOP_TYPES[sorting['key']]})")
                print(f"       URL: {url}")
                driver.get(url)
                time.sleep(4)

                if not _wait_for_top_content(driver):
                    print(f"       ⚠️  контент не появился")
                    _save_debug_snapshot(driver, f"top_{region['key']}_{sorting['key']}_timeout")
                    continue

                # 1) Скачиваем «текущее» состояние (без явного выбора года —
                # это последний год по умолчанию)
                base_name = f"top_{sorting['key']}_{region['key']}_{date_str}"
                target = _download_current(base_name)
                if target:
                    new_files.append(target)

                # 2) Для obyem_vvoda — циклим по годам 2022-2026 и для
                # каждого скачиваем отдельный xlsx с суффиксом года.
                if sorting["key"] in PER_YEAR_SORTINGS:
                    print(f"       ── обход по годам: {PER_YEAR_RANGE}")
                    for year in PER_YEAR_RANGE:
                        ok = _switch_year_filter(driver, year)
                        if not ok:
                            print(f"          ⚠️  год {year} — не удалось переключить")
                            continue
                        # Подождём ещё немного для гарантированного ререндера
                        if not _wait_for_top_content(driver):
                            print(f"          ⚠️  год {year} — контент не появился")
                            continue
                        year_name = f"top_{sorting['key']}_{region['key']}_{year}_{date_str}"
                        ytarget = _download_current(year_name)
                        if ytarget:
                            new_files.append(ytarget)

            # Шаг B: собираем ТОП-100 застройщиков пагинацией (5 страниц × 20)
            # — это для fetch_cards (карточки нужны только по ТОП-100).
            print(f"     ── Сбор ТОП-{TOP_N_DEVELOPERS} застройщиков для карточек:")
            developers = _collect_top_n_developers(
                driver, region, n=TOP_N_DEVELOPERS
            )
            if developers:
                dev_file = DOWNLOAD_DIR / f"top_developers_{region['key']}_{date_str}.json"
                dev_file.write_text(
                    json.dumps(
                        {
                            "region": region["key"],
                            "scraped_at": datetime.now().isoformat(timespec="seconds"),
                            "developers": developers,
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                print(f"     ✅ {dev_file.name} ({len(developers)} застройщиков)")
                new_files.append(dev_file)
            else:
                print(f"     ⚠️  не удалось собрать ТОП-{TOP_N_DEVELOPERS}")
    except WebDriverException as exc:
        print(f"  ❌ {exc}")
    finally:
        driver.quit()

    if new_files:
        state["erzrf_top"] = {
            "last_run": datetime.now().isoformat(timespec="seconds"),
            "files": [p.name for p in new_files],
        }
    return new_files


# ─────────────────────────────────────────────
# CARDS — DOM-скрейп карточек ТОП-100
# ─────────────────────────────────────────────


def _developer_slug(card_url: str) -> str | None:
    m = SLUG_RE.search(card_url or "")
    return m.group(1) if m else None


def _load_top_developers() -> list[dict]:
    candidates = sorted(DOWNLOAD_DIR.glob("top_developers_rf_*.json"))
    if not candidates:
        return []
    payload = json.loads(candidates[-1].read_text(encoding="utf-8"))
    return list(payload.get("developers", []))


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


def _parse_construction_table_df(df) -> dict:
    """Устаревший fallback, оставлен на случай нестандартной разметки."""
    return {}


def fetch_cards(state: dict) -> list[Path]:
    """Обходит карточки ТОП-застройщиков и собирает один xlsx.

    Селекторы и формат вывода верифицированы локально на 20 реальных HTML
    (см. parse_card_html). Перед обращением к карточке делает sleep 10с —
    Angular SPA нужно время на отрисовку (без WebDriverWait, который раньше
    давал ложные таймауты).
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
                # 10 сек на отрисовку Angular SPA. WebDriverWait здесь
                # давал ложные таймауты при том что данные были в page_source.
                time.sleep(10)

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

        target = CARDS_DIR / f"cards_{date_str}.xlsx"
        with pd.ExcelWriter(target, engine="openpyxl") as writer:
            if wide_rows:
                pd.DataFrame(wide_rows).to_excel(writer, sheet_name="cards", index=False)
            if failed:
                pd.DataFrame(failed).to_excel(writer, sheet_name="failed", index=False)
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


def run(only: Iterable[str] | None = None) -> list[Path]:
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    state = load_state()
    keys = set(only) if only else {"top", "cards"}

    print(f"\n{'='*60}")
    print(f"erzrf.ru | Запуск: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Источники: {', '.join(sorted(keys))}")
    print(f"{'='*60}\n")

    all_new: list[Path] = []
    if "top" in keys:
        try:
            all_new.extend(fetch_top(state))
            save_state(state)
        except Exception as exc:  # noqa: BLE001
            print(f"  ❌ top: {exc}")
    if "cards" in keys:
        try:
            all_new.extend(fetch_cards(state))
            save_state(state)
        except Exception as exc:  # noqa: BLE001
            print(f"  ❌ cards: {exc}")

    print(f"\n{'='*60}")
    print(f"erzrf.ru | Итог: новых/обновлённых файлов — {len(all_new)}")
    print(f"{'='*60}\n")
    return all_new


if __name__ == "__main__":
    args = sys.argv[1:]
    run(only=args if args else None)
