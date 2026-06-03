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

# Регион — slug в пути URL. По факту 03.06.2026 РФ = `/rf`, regionKey=0.
# Москва — нужно подтвердить (TODO: на первом запуске msk/Москвы посмотри URL,
# и если он другой — поправь REGION_PATHS).
REGION_PATHS = {
    "rf":  "rf",
    "msk": "msk",
}

REGIONS = [
    {"key": "rf",  "label": "РФ"},
    {"key": "msk", "label": "г.Москва"},
]


def _build_top_url(region_key: str, sorting_key: str) -> str:
    region_path = REGION_PATHS.get(region_key, "rf")
    top_type = TOP_TYPES.get(sorting_key, 0)
    # regionKey=0 включаем только для РФ (по факту наблюдаемого URL);
    # для других регионов это число другое — полагаемся на путь и topType.
    extra = "&regionKey=0" if region_key == "rf" else ""
    return f"{BASE}/top-zastroyshchikov/{region_path}?topType={top_type}{extra}"


TOP_N_DEVELOPERS = 100
DOWNLOAD_DIR = Path("data/raw/realty/erzrf")
CARDS_DIR = DOWNLOAD_DIR / "cards"
STATE_FILE = Path("state/erzrf_state.json")
CONFIG_FILE = Path("config/erzrf.json")
PAGE_TIMEOUT = 60
HEADLESS = False  # TODO: переключить в True после первой удачной отладки.

SLUG_RE = re.compile(r"/zastroyschiki/([^/?#]+)")


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


def _click_by_text(driver, text: str, *, tag_filter: tuple = ()) -> bool:
    """Кликает первый видимый элемент с точно таким innerText."""
    script = """
        const target = arguments[0];
        const tags = arguments[1] || [];
        const all = [...document.querySelectorAll('*')];
        const visible = e => e.offsetParent !== null;
        const candidates = all.filter(e =>
            visible(e) &&
            e.innerText && e.innerText.trim() === target &&
            (tags.length === 0 || tags.includes(e.tagName))
        );
        // приоритет — самый «глубокий» (без вложенных совпадений)
        candidates.sort((a, b) =>
            (b.compareDocumentPosition(a) & Node.DOCUMENT_POSITION_CONTAINED_BY) ? -1 : 1
        );
        const el = candidates[0];
        if (el) { el.click(); return true; }
        return false;
    """
    return bool(driver.execute_script(script, text, list(tag_filter)))


def _click_by_text_contains(driver, substring: str) -> bool:
    script = """
        const sub = arguments[0].toLowerCase();
        const visible = e => e.offsetParent !== null;
        const all = [...document.querySelectorAll('button, a, div, span, li')];
        const cands = all.filter(e =>
            visible(e) && e.innerText &&
            e.innerText.toLowerCase().includes(sub) &&
            e.innerText.length < 200
        );
        // самый короткий (точное совпадение лучше)
        cands.sort((a, b) => a.innerText.length - b.innerText.length);
        const el = cands[0];
        if (el) { el.click(); return true; }
        return false;
    """
    return bool(driver.execute_script(script, substring))


def _set_region(driver, region: dict) -> bool:
    """Открывает дропдаун региона рядом с заголовком ТОП застройщиков и выбирает регион.

    По скрину 03.06.2026: рядом с «ТОП застройщиков РФ ▼» — клик открывает
    окно с поиском, где можно ввести «Москва» и выбрать «г.Москва».
    """
    if not region["dropdown_value"]:
        # «РФ» — это дефолт на странице, ничего не меняем.
        return True

    # Шаг 1: клик по «РФ» рядом с заголовком (или текущему региону).
    # Пытаемся последовательно несколько кандидатов.
    clicked = False
    for txt in ("РФ", "Россия", "г.Москва", "Москва"):
        if _click_by_text(driver, txt):
            clicked = True
            break
    if not clicked:
        # Fallback: ищем по стрелке/треугольнику возле «ТОП застройщиков»
        clicked = bool(
            driver.execute_script(
                """
                const h = [...document.querySelectorAll('h1,h2,h3')]
                    .find(e => /топ\\s*застройщик/i.test(e.innerText));
                if (!h) return false;
                // Кликаем сам заголовок (часто это и есть кликабельный dropdown)
                h.click();
                return true;
                """
            )
        )
    if not clicked:
        print(f"     ⚠️  не нашёл dropdown региона")
        return False
    time.sleep(1)

    # Шаг 2: ищем search-input в открывшемся попапе
    search_input = None
    for css in [
        'input[placeholder*="егион" i]',
        'input[placeholder*="айти" i]',
        'input[type="search"]',
        'input[type="text"]:not([readonly])',
    ]:
        try:
            inputs = driver.find_elements(By.CSS_SELECTOR, css)
            visible = [i for i in inputs if i.is_displayed()]
            if visible:
                search_input = visible[-1]  # последний (попап вероятно поверх)
                break
        except WebDriverException:
            continue

    if search_input:
        try:
            search_input.clear()
            search_input.send_keys(region["dropdown_value"])
            time.sleep(1.5)
        except WebDriverException:
            pass

    # Шаг 3: кликаем option_text
    if not _click_by_text(driver, region["option_text"]):
        # fallback: contains
        if not _click_by_text_contains(driver, region["option_text"]):
            print(f"     ⚠️  не нашёл пункт «{region['option_text']}»")
            return False
    time.sleep(2)
    return True


def _set_sorting(driver, sorting: dict) -> bool:
    """Открывает дропдаун сортировки и выбирает нужную."""
    # Дефолт на странице — «По объёму текущего строительства»
    if sorting["key"] == "obyem_stroitelstva":
        return True

    # Шаг 1: клик по текущей сортировке (любому из вариантов)
    clicked = False
    for txt in [s["label"] for s in SORTINGS]:
        if _click_by_text(driver, txt):
            clicked = True
            break
    if not clicked:
        # fallback: contains «по объёму»
        clicked = _click_by_text_contains(driver, "По объёму")
    if not clicked:
        print(f"     ⚠️  не нашёл dropdown сортировки")
        return False
    time.sleep(1)

    # Шаг 2: клик по нужному пункту
    if not _click_by_text(driver, sorting["label"]):
        if not _click_by_text_contains(driver, sorting["label"]):
            print(f"     ⚠️  не нашёл пункт «{sorting['label']}»")
            return False
    time.sleep(3)
    return True


def _click_download_excel(driver) -> bool:
    """Кликает кнопку «Скачать TOP в Excel» (или эквивалент).

    Кнопка может быть ниже таблицы — скроллим страницу до конца.
    Также пробуем найти в подменю «Настроить фильтр»/«Поделиться».
    """
    # Скроллим вниз чтобы все ленивые элементы появились
    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
    time.sleep(1)

    return bool(
        driver.execute_script(
            """
            const visible = e => e.offsetParent !== null;
            const all = [...document.querySelectorAll('button, a, div, span')];
            // 1) кнопка с «TOP» и «Excel»/«xlsx»
            let btn = all.find(b => visible(b) && b.innerText &&
                /top/i.test(b.innerText) && /excel|xlsx/i.test(b.innerText));
            // 2) «Скачать» + «excel/xlsx»
            if (!btn) btn = all.find(b => visible(b) && b.innerText &&
                /скачать/i.test(b.innerText) && /excel|xlsx/i.test(b.innerText));
            // 3) кнопка с иконкой и title/aria «Скачать»
            if (!btn) {
                btn = [...document.querySelectorAll('[title], [aria-label]')]
                    .find(e => visible(e) && (
                        /скачать.*excel|excel.*скачать|TOP.*Excel/i.test(
                            (e.getAttribute('title') || '') + ' ' + (e.getAttribute('aria-label') || '')
                        )
                    ));
            }
            // 4) ссылка a[href] на xlsx
            if (!btn) {
                btn = [...document.querySelectorAll('a[href]')]
                    .find(a => visible(a) && /\\.xlsx?(\\?|$)/i.test(a.href));
            }
            // 5) последний шанс: просто «Скачать»
            if (!btn) btn = all.find(b => visible(b) && b.innerText &&
                /^\\s*скачать\\s*$/i.test(b.innerText.trim()));
            if (btn) {
                btn.scrollIntoView({block: 'center'});
                btn.click();
                return true;
            }
            return false;
            """
        )
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

        for region in REGIONS:
            print(f"  🌐 регион: {region['key']} ({region['label']})")

            # ── Снимок таблицы по дефолтной сортировке: для cards-обхода
            url_default = _build_top_url(region["key"], "obyem_stroitelstva")
            print(f"     URL: {url_default}")
            driver.get(url_default)
            time.sleep(5)

            if not _wait_for_top_content(driver):
                print(f"     ⚠️  контент таблицы не появился за 30 сек")
                _save_debug_snapshot(driver, f"top_{region['key']}_content_timeout")
                continue

            developers = _scrape_developers_from_table(driver)
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
                print(f"     ✅ {dev_file.name} ({len(developers)} строк)")
                new_files.append(dev_file)
            else:
                # Снэпшот всегда, чтобы видеть структуру страницы
                _save_debug_snapshot(driver, f"top_{region['key']}_no_table")

            # ── По всем 5 сортировкам качаем xlsx (URL-навигация)
            for sorting in SORTINGS:
                print(f"     ▸ сортировка: {sorting['key']} (topType={TOP_TYPES[sorting['key']]})")
                url = _build_top_url(region["key"], sorting["key"])
                driver.get(url)
                time.sleep(4)

                if not _wait_for_top_content(driver):
                    print(f"       ⚠️  контент не появился")
                    _save_debug_snapshot(driver, f"top_{region['key']}_{sorting['key']}_timeout")
                    continue

                before = set(DOWNLOAD_DIR.glob("*"))
                if not _click_download_excel(driver):
                    print(f"       ⚠️  кнопка «Скачать TOP в Excel» не найдена")
                    _save_debug_snapshot(driver, f"top_{region['key']}_{sorting['key']}_button")
                    continue
                new_file = wait_for_download(
                    DOWNLOAD_DIR, before_snapshot=before, timeout=120
                )
                if new_file is None:
                    print(f"       ⚠️  xlsx не появился в папке за 120 сек")
                    continue
                target = (
                    DOWNLOAD_DIR
                    / f"top_{sorting['key']}_{region['key']}_{date_str}{new_file.suffix}"
                )
                if target.exists():
                    target.unlink()
                new_file.rename(target)
                print(f"       ✅ {target.name}")
                new_files.append(target)
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


def _scrape_card(driver) -> dict:
    return driver.execute_script(
        """
        let name = '';
        const h1 = document.querySelector('h1');
        if (h1) name = h1.innerText.trim();

        let regions = [];
        const headers = [...document.querySelectorAll('h2, h3, h4, div, span')];
        for (const h of headers) {
            if (h.innerText && /регион/i.test(h.innerText) && h.innerText.length < 80) {
                const sib = h.nextElementSibling || h.parentElement;
                if (sib) {
                    const items = [...sib.querySelectorAll('li, a, span')]
                        .map(e => e.innerText.trim())
                        .filter(s => s && s.length < 80);
                    if (items.length) { regions = items; break; }
                }
            }
        }

        let deliveries = {};
        const tables = [...document.querySelectorAll('table')];
        for (const t of tables) {
            const headerText = (t.rows[0] ? t.rows[0].innerText : '') + '';
            if (/сдано|перенос|уточн/i.test(headerText)) {
                const hdrs = [...t.rows[0].cells].map(c => c.innerText.trim());
                for (let i = 1; i < t.rows.length; i++) {
                    const cells = [...t.rows[i].cells].map(c => c.innerText.trim());
                    const yearMatch = cells[0].match(/(20\\d{2})/);
                    if (yearMatch) {
                        const yr = yearMatch[1];
                        const obj = {};
                        hdrs.forEach((h, j) => { if (j > 0) obj[h] = cells[j] ?? ''; });
                        deliveries[yr] = obj;
                    }
                }
                break;
            }
        }

        let ratings = {};
        const all = [...document.querySelectorAll('*')];
        for (const e of all) {
            const t = (e.innerText || '').trim();
            if (!t || t.length > 120) continue;
            if (/место.+потреб/i.test(t)) ratings.place_quality = t;
            if (/оценка\\s*ерз/i.test(t)) ratings.erz_score = t;
            if (/место.+скорост/i.test(t)) ratings.place_speed = t;
        }

        return {name, regions, deliveries, ratings};
        """
    )


def fetch_cards(state: dict) -> list[Path]:
    CARDS_DIR.mkdir(parents=True, exist_ok=True)
    developers = _load_top_developers()
    if not developers:
        print("  ⚠️  Нет файла top_developers_rf_*.json — сначала запусти fetch_top")
        return []

    today = datetime.now().strftime("%d.%m.%Y")
    cards_state = state.get("erzrf_cards", {}) if isinstance(state.get("erzrf_cards"), dict) else {}

    new_files: list[Path] = []
    driver = create_chrome(download_dir=CARDS_DIR, headless=HEADLESS)
    try:
        driver.set_page_load_timeout(PAGE_TIMEOUT)
        # Карточки публичные — авторизация не требуется (если потребуется,
        # вызвать _ensure_logged_in(driver) здесь).
        for dev in developers[:TOP_N_DEVELOPERS]:
            card_url = dev.get("card_url")
            if not card_url:
                continue
            slug = _developer_slug(card_url)
            if not slug:
                continue
            saved = cards_state.get(slug, {})
            if isinstance(saved, dict) and saved.get("scraped_at_date") == today:
                continue

            print(f"     · {slug}")
            try:
                driver.get(card_url)
                time.sleep(3)
                try:
                    WebDriverWait(driver, 20).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, "h1, table"))
                    )
                except TimeoutException:
                    print(f"       ⚠️  карточка не загрузилась")
                    continue
                data = _scrape_card(driver)
                data["url"] = card_url
                data["slug"] = slug
                data["scraped_at"] = datetime.now().isoformat(timespec="seconds")
                target = CARDS_DIR / f"{slug}.json"
                target.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                new_files.append(target)
                cards_state[slug] = {
                    "scraped_at_date": today,
                    "name": data.get("name", ""),
                }
            except WebDriverException as exc:
                print(f"       ❌ {exc}")
    finally:
        driver.quit()

    if new_files:
        state["erzrf_cards"] = cards_state
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
