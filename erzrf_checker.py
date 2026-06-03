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

# Все 5 сортировок (заголовок дропдауна на странице).
# `label` — текст пункта в дропдауне «По объёму текущего строительства ▼».
SORTINGS = [
    {"key": "obyem_stroitelstva",  "label": "По объёму текущего строительства"},
    {"key": "obyem_vvoda",         "label": "По объёму ввода"},
    {"key": "nakopl_vvod",         "label": "По накопленному вводу"},
    {"key": "potreb_kachestva",    "label": "По потребительским качествам"},
    {"key": "skorost",             "label": "По скорости строительства"},
]

# Регионы. `dropdown_value` — что вводится в поиск дропдауна региона,
# `option_text` — текст пункта который кликаем (точное совпадение).
REGIONS = [
    {"key": "rf",  "dropdown_value": "",        "option_text": "РФ"},
    {"key": "msk", "dropdown_value": "Москва",  "option_text": "г.Москва"},
]

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
    """Логинится через форму на erzrf.ru. Возвращает True при успехе.

    Креды читает из config/erzrf.json. Если файла нет — печатает warning
    и возвращает False (вызывающий код сам решит, продолжать ли).
    """
    creds = _load_credentials()
    if not creds:
        print("  ⚠️  Нет config/erzrf.json — скачивание xlsx требует авторизации.")
        print("     Скопируй config/erzrf.example.json → config/erzrf.json")
        print("     и впиши email/password от erzrf.ru.")
        return False

    login_url = creds.get("login_url") or f"{BASE}/login"
    email = creds.get("email") or ""
    password = creds.get("password") or ""
    if not email or not password:
        print("  ⚠️  В config/erzrf.json не заполнены email/password.")
        return False

    print(f"  🔐 Логин на {login_url} как {email[:3]}***")
    try:
        driver.get(login_url)
        time.sleep(3)

        # TODO(verify): селекторы формы могут отличаться. Ищем максимально
        # широко: input[type=email] или name содержит email/login/user;
        # input[type=password]; submit-кнопку рядом.
        email_input = None
        for css in [
            'input[type="email"]',
            'input[name*="email" i]',
            'input[name*="login" i]',
            'input[name*="user" i]',
            'input[id*="email" i]',
            'input[id*="login" i]',
        ]:
            try:
                el = driver.find_element(By.CSS_SELECTOR, css)
                if el.is_displayed():
                    email_input = el
                    break
            except NoSuchElementException:
                continue

        password_input = None
        try:
            password_input = driver.find_element(By.CSS_SELECTOR, 'input[type="password"]')
        except NoSuchElementException:
            pass

        if not email_input or not password_input:
            print("  ⚠️  Поля логина/пароля не найдены — проверь login_url")
            _save_debug_snapshot(driver, "login")
            return False

        email_input.clear()
        email_input.send_keys(email)
        password_input.clear()
        password_input.send_keys(password)

        # Сабмит: или нажать Enter в пароле, или найти кнопку
        try:
            password_input.send_keys(Keys.RETURN)
        except WebDriverException:
            pass
        time.sleep(2)

        # Проверка: на странице исчезла форма логина, либо появилось имя
        # пользователя. Считаем успех, если после редиректа URL изменился
        # ИЛИ форма логина пропала.
        try:
            driver.find_element(By.CSS_SELECTOR, 'input[type="password"]')
            # Форма ещё на месте — возможно ошибка кредов.
            print("  ⚠️  После сабмита форма логина ещё видна — возможно неверные креды.")
            _save_debug_snapshot(driver, "login_failed")
            return False
        except NoSuchElementException:
            print("  ✅ Авторизация прошла")
            return True

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
    """Кликает кнопку «Скачать TOP в Excel» (или эквивалент)."""
    return bool(
        driver.execute_script(
            """
            const visible = e => e.offsetParent !== null;
            const all = [...document.querySelectorAll('button, a, div[class*="button"], div[class*="btn"], span')];
            // 1) ищем кнопку с обоими словами «TOP» и «Excel»/«xlsx»
            let btn = all.find(b => visible(b) && b.innerText &&
                /top/i.test(b.innerText) && /excel|xlsx/i.test(b.innerText));
            // 2) fallback: «Скачать» + «excel»
            if (!btn) btn = all.find(b => visible(b) && b.innerText &&
                /скачать/i.test(b.innerText) && /excel|xlsx/i.test(b.innerText));
            // 3) fallback: просто «Скачать»
            if (!btn) btn = all.find(b => visible(b) && b.innerText &&
                /^скачать/i.test(b.innerText.trim()));
            if (btn) { btn.click(); return true; }
            return false;
            """
        )
    )


def _scrape_developers_from_table(driver) -> list[dict]:
    return driver.execute_script(
        """
        const tables = [...document.querySelectorAll('table')]
            .filter(t => t.offsetParent !== null && t.rows.length > 1);
        const t = tables[0];
        if (!t) return [];
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
        """
    )


# ─────────────────────────────────────────────
# TOP — скачивание xlsx + ссылок на карточки
# ─────────────────────────────────────────────


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

        for region in REGIONS:
            print(f"  🌐 регион: {region['key']} ({region['option_text']})")
            driver.get(TOP_URL)
            time.sleep(5)

            try:
                WebDriverWait(driver, 30).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "table"))
                )
            except TimeoutException:
                print(f"     ⚠️  таблица не появилась")
                _save_debug_snapshot(driver, f"top_{region['key']}_table_timeout")
                continue

            # Применяем регион один раз для всего блока сортировок
            if not _set_region(driver, region):
                _save_debug_snapshot(driver, f"top_{region['key']}_region")
                continue

            date_str = datetime.now().strftime("%Y%m%d")

            # Для региона снимаем таблицу с дефолтной сортировкой
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

            # По всем 5 сортировкам качаем xlsx
            for sorting in SORTINGS:
                print(f"     ▸ сортировка: {sorting['key']}")
                if not _set_sorting(driver, sorting):
                    _save_debug_snapshot(driver, f"top_{region['key']}_{sorting['key']}_sort")
                    continue
                time.sleep(2)

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
