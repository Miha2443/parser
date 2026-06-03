"""
nashdom_checker.py
------------------
Скачивает данные с наш.дом.рф (3 источника):

1. **monitoring_2_0** — единый xlsx «Мониторинг новостроек 2.0», скачивается
   через клик по кнопке «Скачать». Ежедневно — пользователь явно попросил
   качать целиком, без сравнения дат.

2. **rasprodannost** — DOM-скрейп страницы /аналитика/распроданность-новостроек.
   5 срезов по фильтру «Класс недвижимости» (Все / Типовой / Комфорт / Бизнес /
   Элитный). Глубина — все месяцы из пикера, но не раньше 2011-01.

3. **kvartirografia** — DOM-скрейп страницы /аналитика/квартирография-новостроек.
   То же: 5 классов, помесячно с 2011-01.

Для rasprodannost / kvartirografia инкрементальная логика: читаем плашку
«Отчёт по данным на ДД.ММ.ГГГГ» — если совпадает со state, ничего не качаем.
Иначе обходим все 5 классов и сохраняем в JSON.

State хранится в `state/nashdom_state.json`.

Запуск:
    py nashdom_checker.py                # все 3 источника
    py nashdom_checker.py monitoring_2_0 # только один ключ

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

from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from pipeline.selenium_utils import create_chrome, wait_for_download


# ─────────────────────────────────────────────
# КОНФИГ
# ─────────────────────────────────────────────

# Кириллический домен наш.дом.рф в punycode.
BASE = "https://xn--80az8a.xn--d1aqf.xn--p1ai"

# TODO(verify): URL-ы уточнить на первом запуске (могут быть редиректы).
SOURCE_URLS = {
    "monitoring_2_0": f"{BASE}/%D0%B0%D0%BD%D0%B0%D0%BB%D0%B8%D1%82%D0%B8%D0%BA%D0%B0/%D0%BC%D0%BE%D0%BD%D0%B8%D1%82%D0%BE%D1%80%D0%B8%D0%BD%D0%B3-%D0%BD%D0%BE%D0%B2%D0%BE%D1%81%D1%82%D1%80%D0%BE%D0%B5%D0%BA-2-0",
    "rasprodannost": f"{BASE}/%D0%B0%D0%BD%D0%B0%D0%BB%D0%B8%D1%82%D0%B8%D0%BA%D0%B0/%D1%80%D0%B0%D1%81%D0%BF%D1%80%D0%BE%D0%B4%D0%B0%D0%BD%D0%BD%D0%BE%D1%81%D1%82%D1%8C-%D0%BD%D0%BE%D0%B2%D0%BE%D1%81%D1%82%D1%80%D0%BE%D0%B5%D0%BA",
    "kvartirografia": f"{BASE}/%D0%B0%D0%BD%D0%B0%D0%BB%D0%B8%D1%82%D0%B8%D0%BA%D0%B0/%D0%BA%D0%B2%D0%B0%D1%80%D1%82%D0%B8%D1%80%D0%BE%D0%B3%D1%80%D0%B0%D1%84%D0%B8%D1%8F-%D0%BD%D0%BE%D0%B2%D0%BE%D1%81%D1%82%D1%80%D0%BE%D0%B5%D0%BA",
}

REALTY_CLASSES = ["Все", "Типовой", "Комфорт", "Бизнес", "Элитный"]

DOWNLOAD_DIR = Path("data/raw/realty/nashdom")
STATE_FILE = Path("state/nashdom_state.json")
PAGE_TIMEOUT = 60
HEADLESS = False  # TODO: переключить в True после первой удачной отладки.

REPORT_DATE_RE = re.compile(r"(\d{2}\.\d{2}\.\d{4})")


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
# Общее
# ─────────────────────────────────────────────


def _read_report_date(driver) -> str | None:
    """Ищет на странице плашку «Отчёт по данным на ДД.ММ.ГГГГ».

    Возвращает дату в формате DD.MM.YYYY или None если не нашёл.
    """
    text = driver.execute_script(
        """
        const all = [...document.querySelectorAll('*')];
        const el = all.find(e =>
            e.innerText &&
            e.innerText.includes('данным на') &&
            e.innerText.length < 300
        );
        return el ? el.innerText : null;
        """
    )
    if not text:
        return None
    m = REPORT_DATE_RE.search(text)
    if m:
        return m.group(1)
    return None


def _save_debug_snapshot(driver, tag: str) -> None:
    """Сохраняет HTML-страницу и скриншот в data/raw/realty/_debug/."""
    debug_dir = DOWNLOAD_DIR.parent / "_debug"
    debug_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    (debug_dir / f"nashdom_{tag}_{ts}.html").write_text(
        driver.page_source, encoding="utf-8"
    )
    try:
        driver.save_screenshot(str(debug_dir / f"nashdom_{tag}_{ts}.png"))
    except WebDriverException:
        pass


# ─────────────────────────────────────────────
# 1. Monitoring 2.0 — скачивание xlsx
# ─────────────────────────────────────────────


def fetch_monitoring_2_0(state: dict) -> list[Path]:
    """Скачивает «Мониторинг 2.0» целиком (ежедневно)."""
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    url = SOURCE_URLS["monitoring_2_0"]
    print(f"  🌐 monitoring_2_0: {url}")

    driver = create_chrome(download_dir=DOWNLOAD_DIR, headless=HEADLESS)
    new_files: list[Path] = []
    try:
        driver.set_page_load_timeout(PAGE_TIMEOUT)
        driver.get(url)
        time.sleep(5)

        report_date = _read_report_date(driver) or ""

        before = set(DOWNLOAD_DIR.glob("*"))

        # TODO(verify): селектор кнопки «Скачать» уточнить на первом запуске.
        clicked = driver.execute_script(
            """
            const buttons = [...document.querySelectorAll('button, a, div[class*="button"], div[class*="btn"]')];
            const btn = buttons.find(b => b.innerText && /скачать/i.test(b.innerText));
            if (btn) { btn.click(); return true; }
            return false;
            """
        )
        if not clicked:
            print("  ⚠️  Кнопка «Скачать» не найдена")
            _save_debug_snapshot(driver, "monitoring_2_0")
            return []

        new_file = wait_for_download(DOWNLOAD_DIR, before_snapshot=before, timeout=180)
        if new_file is None:
            print("  ⚠️  Файл не появился в папке за 180 сек")
            return []

        date_str = datetime.now().strftime("%Y%m%d")
        target = DOWNLOAD_DIR / f"monitoring_2_0_{date_str}{new_file.suffix}"
        if target.exists():
            target.unlink()
        new_file.rename(target)
        print(f"  ✅ Сохранён: {target}")
        new_files.append(target)
        state["monitoring_2_0"] = {
            "report_date": report_date,
            "filename": target.name,
            "downloaded_at": datetime.now().isoformat(timespec="seconds"),
        }
    except WebDriverException as exc:
        print(f"  ❌ monitoring_2_0: {exc}")
    finally:
        driver.quit()
    return new_files


# ─────────────────────────────────────────────
# 2 + 3. DOM-скрейп таблиц (rasprodannost / kvartirografia)
# ─────────────────────────────────────────────


def _switch_class_filter(driver, realty_class: str) -> bool:
    """Переключает фильтр «Класс недвижимости» на нужное значение.

    Возвращает True если нашли и кликнули. На разных страницах виджет
    может быть селектом, кнопками-чипами или дропдауном — JS-скрипт
    охватывает основные варианты, но при изменении вёрстки понадобится
    уточнить (см. TODO(verify) ниже).
    """
    # TODO(verify): селекторы фильтра уточнить на первом запуске.
    return bool(
        driver.execute_script(
            """
            const target = arguments[0];
            const all = [...document.querySelectorAll('*')];
            // 1. ищем <option> с нужным текстом
            const opt = all.find(e => e.tagName === 'OPTION' && e.innerText.trim() === target);
            if (opt) {
                opt.selected = true;
                opt.parentElement.dispatchEvent(new Event('change', {bubbles: true}));
                return true;
            }
            // 2. ищем кликабельный элемент (button / li / div / span) с этим текстом
            const btn = all.find(e =>
                ['BUTTON','LI','DIV','SPAN','A'].includes(e.tagName) &&
                e.innerText && e.innerText.trim() === target &&
                e.offsetParent !== null
            );
            if (btn) { btn.click(); return true; }
            return false;
            """,
            realty_class,
        )
    )


def _extract_table(driver) -> list[dict]:
    """Снимает основную таблицу страницы в виде list[dict].

    Ищет первую видимую table или контейнер role="table". Возвращает
    список строк, где ключи — текст ячеек шапки.
    """
    return driver.execute_script(
        """
        function pickTable() {
            const tables = [...document.querySelectorAll('table')]
                .filter(t => t.offsetParent !== null && t.rows.length > 1);
            if (tables.length) return tables[0];
            // fallback: контейнер с role=table (react-table)
            const rt = document.querySelector('[role="table"]');
            return rt;
        }
        const t = pickTable();
        if (!t) return [];
        if (t.tagName === 'TABLE') {
            const headers = [...t.rows[0].cells].map(c => c.innerText.trim());
            const rows = [];
            for (let i = 1; i < t.rows.length; i++) {
                const cells = [...t.rows[i].cells].map(c => c.innerText.trim());
                const obj = {};
                headers.forEach((h, j) => obj[h || ('col_'+j)] = cells[j] ?? '');
                rows.push(obj);
            }
            return rows;
        }
        // role="table" вариант: первая строка role=row с role=columnheader = шапка
        const rows = [...t.querySelectorAll('[role="row"]')];
        if (!rows.length) return [];
        const headerCells = [...rows[0].querySelectorAll('[role="columnheader"], [role="cell"]')];
        const headers = headerCells.map(c => c.innerText.trim());
        const out = [];
        for (let i = 1; i < rows.length; i++) {
            const cells = [...rows[i].querySelectorAll('[role="cell"], [role="gridcell"]')];
            const obj = {};
            headers.forEach((h, j) => obj[h || ('col_'+j)] = (cells[j]?.innerText || '').trim());
            out.push(obj);
        }
        return out;
        """
    )


def _scrape_table_source(
    source_key: str, state: dict
) -> list[Path]:
    """Общий обход 5 классов недвижимости с сохранением в JSON.

    Если плашка «Отчёт по данным на …» совпадает с прошлой — выходим без действий.
    """
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    url = SOURCE_URLS[source_key]
    print(f"  🌐 {source_key}: {url}")

    driver = create_chrome(download_dir=DOWNLOAD_DIR, headless=HEADLESS)
    new_files: list[Path] = []
    try:
        driver.set_page_load_timeout(PAGE_TIMEOUT)
        driver.get(url)
        time.sleep(5)

        # Ждём появления таблицы. Если за 30 сек не дождались — снимаем дамп.
        try:
            WebDriverWait(driver, 30).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, 'table, [role="table"]')
                )
            )
        except TimeoutException:
            print(f"  ⚠️  {source_key}: таблица не появилась за 30 сек")
            _save_debug_snapshot(driver, source_key)
            return []

        report_date = _read_report_date(driver) or ""
        saved = state.get(source_key, {})
        saved_date = saved.get("report_date") if isinstance(saved, dict) else None

        if report_date and saved_date == report_date:
            print(f"  ✔️  {source_key}: без изменений ({report_date})")
            return []

        date_str = datetime.now().strftime("%Y%m%d")
        files_for_state: list[str] = []

        for realty_class in REALTY_CLASSES:
            print(f"     · класс «{realty_class}»")
            if realty_class != "Все":
                ok = _switch_class_filter(driver, realty_class)
                if not ok:
                    print(f"       ⚠️  фильтр не переключился — пропускаем")
                    continue
                time.sleep(3)  # ждём ребилда таблицы после смены фильтра

            rows = _extract_table(driver)
            if not rows:
                print(f"       ⚠️  таблица пустая после фильтра")
                continue

            class_slug = {
                "Все": "all",
                "Типовой": "tipovoi",
                "Комфорт": "komfort",
                "Бизнес": "biznes",
                "Элитный": "elitnyi",
            }[realty_class]
            target = DOWNLOAD_DIR / f"{source_key}_{class_slug}_{date_str}.json"
            payload = {
                "source": source_key,
                "realty_class": realty_class,
                "report_date": report_date,
                "scraped_at": datetime.now().isoformat(timespec="seconds"),
                "url": url,
                "rows": rows,
            }
            target.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"       ✅ {target.name} ({len(rows)} строк)")
            new_files.append(target)
            files_for_state.append(target.name)

        if new_files:
            state[source_key] = {
                "report_date": report_date,
                "files": files_for_state,
                "scraped_at": datetime.now().isoformat(timespec="seconds"),
            }
    except WebDriverException as exc:
        print(f"  ❌ {source_key}: {exc}")
    finally:
        driver.quit()
    return new_files


def fetch_rasprodannost(state: dict) -> list[Path]:
    return _scrape_table_source("rasprodannost", state)


def fetch_kvartirografia(state: dict) -> list[Path]:
    return _scrape_table_source("kvartirografia", state)


# ─────────────────────────────────────────────
# Главный цикл
# ─────────────────────────────────────────────


SOURCE_FUNCS = {
    "monitoring_2_0": fetch_monitoring_2_0,
    "rasprodannost": fetch_rasprodannost,
    "kvartirografia": fetch_kvartirografia,
}


def run(only: Iterable[str] | None = None) -> list[Path]:
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    state = load_state()
    keys = list(only) if only else list(SOURCE_FUNCS.keys())

    print(f"\n{'='*60}")
    print(f"наш.дом.рф | Запуск: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Источники: {', '.join(keys)}")
    print(f"{'='*60}\n")

    all_new: list[Path] = []
    for key in keys:
        func = SOURCE_FUNCS.get(key)
        if func is None:
            print(f"  ⚠️  Неизвестный ключ: {key}")
            continue
        try:
            new_files = func(state)
            all_new.extend(new_files)
            save_state(state)  # сохраняем после каждого источника
        except Exception as exc:  # noqa: BLE001
            print(f"  ❌ {key}: {exc}")

    print(f"\n{'='*60}")
    print(f"наш.дом.рф | Итог: новых/обновлённых файлов — {len(all_new)}")
    for f in all_new:
        print(f"  • {f}")
    print(f"{'='*60}\n")
    return all_new


if __name__ == "__main__":
    args = sys.argv[1:]
    run(only=args if args else None)
