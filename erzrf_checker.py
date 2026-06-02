"""
erzrf_checker.py
----------------
Скачивает данные с erzrf.ru (2 источника):

1. **erzrf_top** — `https://erzrf.ru/top-zastroyshchikov/`. Кнопка
   «Скачать TOP в Excel» отдаёт `TOP_EXCEL_*.xlsx`. По умолчанию качаем
   1 сортировку («по объёму строительства») × 2 региона (Россия / Москва) =
   2 файла. Список сортировок легко расширяется (см. `SORTINGS` ниже) —
   когда выяснится, что разные сортировки дают разный набор колонок.
   Параллельно DOM-скрейпим таблицу для получения ссылок на карточки
   застройщиков → пишем `top_developers_<region>_<YYYYMMDD>.json`.

2. **erzrf_cards** — для каждого из ТОП-100 застройщиков (берём из
   последнего `top_developers_rf_*.json`) открываем
   `erzrf.ru/zastroyschiki/<slug>`, DOM-скрейпим карточку: полное имя,
   регионы строительства, таблицу «Сдано в N год / С переносом сроков /
   Уточнение сроков» по годам, рейтинги. Сохраняем как
   `cards/<slug>.json`.

State в `state/erzrf_state.json`.

Запуск:
    py erzrf_checker.py            # обе подпрограммы
    py erzrf_checker.py top        # только top
    py erzrf_checker.py cards      # только карточки

Замечания по селекторам: URL и CSS-селекторы помечены `# TODO(verify):` —
их следует уточнить на первом запуске в headed-режиме (`HEADLESS=False`),
после чего обновить в файле.
"""
from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Iterable

import pandas as pd
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from pipeline.selenium_utils import create_chrome, wait_for_download


# ─────────────────────────────────────────────
# КОНФИГ
# ─────────────────────────────────────────────

BASE = "https://erzrf.ru"
TOP_URL = f"{BASE}/top-zastroyshchikov"

# Сортировки. Расширяй после Шага 0 верификации (если разные сортировки
# реально дают разный набор колонок в xlsx).
SORTINGS = [
    {"key": "po_obyemu_stroitelstva", "label": "По объёму строительства"},
    # {"key": "po_obyemu_vvoda", "label": "По объёму ввода"},
    # {"key": "po_nakoplennomu_vvodu", "label": "По накопленному вводу с 2016"},
    # {"key": "po_potreb_kachestvam", "label": "По потребительским качествам"},
    # {"key": "po_skorosti", "label": "По скорости строительства"},
]

REGIONS = [
    {"key": "rf", "label": ""},          # без фильтра = вся Россия
    {"key": "msk", "label": "Москва"},
]

TOP_N_DEVELOPERS = 100
DOWNLOAD_DIR = Path("data/raw/realty/erzrf")
CARDS_DIR = DOWNLOAD_DIR / "cards"
STATE_FILE = Path("state/erzrf_state.json")
PAGE_TIMEOUT = 60
HEADLESS = True

SLUG_RE = re.compile(r"/zastroyschiki/([^/?#]+)")


# ─────────────────────────────────────────────
# State
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


# ─────────────────────────────────────────────
# TOP — скачивание xlsx + ссылок на карточки
# ─────────────────────────────────────────────


def _set_region_filter(driver, region_label: str) -> bool:
    """Применяет фильтр региона (для msk = «Москва»; для rf = ничего)."""
    if not region_label:
        return True
    # TODO(verify): уточнить, где именно живёт селектор региона на странице.
    return bool(
        driver.execute_script(
            """
            const label = arguments[0];
            // 1) пробуем dropdown с input-поиском по тексту
            const inputs = [...document.querySelectorAll('input[type="text"], input[type="search"]')];
            const inp = inputs.find(i =>
                /регион/i.test((i.placeholder || '') + (i.getAttribute('aria-label') || ''))
            );
            if (inp) {
                const native = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                native.call(inp, label);
                inp.dispatchEvent(new Event('input', {bubbles: true}));
                return true;
            }
            // 2) кликабельный chip/li с текстом
            const all = [...document.querySelectorAll('button, li, span, div, a')];
            const btn = all.find(e =>
                e.innerText && e.innerText.trim() === label && e.offsetParent !== null
            );
            if (btn) { btn.click(); return true; }
            return false;
            """,
            region_label,
        )
    )


def _click_download_excel(driver) -> bool:
    """Кликает кнопку «Скачать TOP в Excel» (или эквивалент)."""
    # TODO(verify): уточнить точный текст/класс кнопки на первом запуске.
    return bool(
        driver.execute_script(
            """
            const all = [...document.querySelectorAll('button, a, div[class*="button"], div[class*="btn"]')];
            const btn = all.find(b =>
                b.innerText &&
                /excel|xlsx|скачать/i.test(b.innerText) &&
                /top|топ|excel/i.test(b.innerText)
            ) || all.find(b => b.innerText && /скачать.*top/i.test(b.innerText));
            if (btn) { btn.click(); return true; }
            return false;
            """
        )
    )


def _scrape_developers_from_table(driver) -> list[dict]:
    """Снимает таблицу ТОП-застройщиков: name + card_url + place."""
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


def fetch_top(state: dict) -> list[Path]:
    """Качает TOP_EXCEL для каждой комбинации (сортировка × регион)."""
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    new_files: list[Path] = []

    for sorting in SORTINGS:
        for region in REGIONS:
            tag = f"{sorting['key']}_{region['key']}"
            print(f"  🌐 erzrf_top: {tag}")

            driver = create_chrome(download_dir=DOWNLOAD_DIR, headless=HEADLESS)
            try:
                driver.set_page_load_timeout(PAGE_TIMEOUT)
                driver.get(TOP_URL)
                time.sleep(5)

                try:
                    WebDriverWait(driver, 30).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, "table"))
                    )
                except TimeoutException:
                    print(f"     ⚠️  таблица не появилась")
                    continue

                # Фильтр региона
                if not _set_region_filter(driver, region["label"]):
                    print(f"     ⚠️  не удалось применить регион «{region['label']}»")
                # TODO(verify): применение сортировки — пока что страница
                # отдаёт «по объёму строительства» по умолчанию. Если будет
                # включена другая сортировка, добавить здесь _set_sorting().
                time.sleep(3)

                # Снимок DOM-таблицы (для cards-обхода)
                date_str = datetime.now().strftime("%Y%m%d")
                developers = _scrape_developers_from_table(driver)
                if developers:
                    dev_file = DOWNLOAD_DIR / f"top_developers_{region['key']}_{date_str}.json"
                    dev_file.write_text(
                        json.dumps(
                            {
                                "region": region["key"],
                                "sorting": sorting["key"],
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

                # Скачивание xlsx
                before = set(DOWNLOAD_DIR.glob("*"))
                if not _click_download_excel(driver):
                    print(f"     ⚠️  кнопка «Скачать TOP в Excel» не найдена")
                    continue
                new_file = wait_for_download(
                    DOWNLOAD_DIR, before_snapshot=before, timeout=120
                )
                if new_file is None:
                    print(f"     ⚠️  xlsx не появился в папке за 120 сек")
                    continue
                target = (
                    DOWNLOAD_DIR
                    / f"top_{sorting['key']}_{region['key']}_{date_str}{new_file.suffix}"
                )
                if target.exists():
                    target.unlink()
                new_file.rename(target)
                print(f"     ✅ {target.name}")
                new_files.append(target)

            except WebDriverException as exc:
                print(f"     ❌ {exc}")
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
    """Берёт список застройщиков из последнего top_developers_rf_*.json."""
    candidates = sorted(DOWNLOAD_DIR.glob("top_developers_rf_*.json"))
    if not candidates:
        return []
    payload = json.loads(candidates[-1].read_text(encoding="utf-8"))
    return list(payload.get("developers", []))


def _scrape_card(driver) -> dict:
    """Парсит карточку застройщика в dict."""
    return driver.execute_script(
        """
        function txt(sel) {
            const el = document.querySelector(sel);
            return el ? el.innerText.trim() : '';
        }
        // Заголовок: первый h1 или h2 с названием
        let name = '';
        const h1 = document.querySelector('h1');
        if (h1) name = h1.innerText.trim();

        // Регионы строительства — ищем блок с заголовком «Регионы»
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

        // Таблица «Сдано в N год / С переносом / Уточнение» — ищем таблицу с такими шапками
        let deliveries = {};
        const tables = [...document.querySelectorAll('table')];
        for (const t of tables) {
            const headerText = (t.rows[0] ? t.rows[0].innerText : '') + '';
            if (/сдано|перенос|уточн/i.test(headerText)) {
                const headers = [...t.rows[0].cells].map(c => c.innerText.trim());
                for (let i = 1; i < t.rows.length; i++) {
                    const cells = [...t.rows[i].cells].map(c => c.innerText.trim());
                    const yearMatch = cells[0].match(/(20\\d{2})/);
                    if (yearMatch) {
                        const yr = yearMatch[1];
                        const obj = {};
                        headers.forEach((h, j) => { if (j > 0) obj[h] = cells[j] ?? ''; });
                        deliveries[yr] = obj;
                    }
                }
                break;
            }
        }

        // Рейтинги: ищем элементы с «Место», «Оценка ЕРЗ» рядом
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
    """Обходит карточки ТОП-N застройщиков."""
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
        for dev in developers[:TOP_N_DEVELOPERS]:
            card_url = dev.get("card_url")
            if not card_url:
                continue
            slug = _developer_slug(card_url)
            if not slug:
                continue
            saved = cards_state.get(slug, {})
            if isinstance(saved, dict) and saved.get("scraped_at_date") == today:
                continue  # уже обходили сегодня

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
