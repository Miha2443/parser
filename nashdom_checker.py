"""
nashdom_checker.py
------------------
Скачивает данные из 3 источников «недвижимость»:

1. **monitoring_2_0** — Google Sheets, скачиваем целиком как xlsx через
   export?format=xlsx (не Selenium, обычный requests).
   https://docs.google.com/spreadsheets/d/<ID>/export?format=xlsx

2. **rasprodannost** — DOM-скрейп
   https://наш.дом.рф/аналитика/распроданность-стройготовность
   с query-параметрами ?repYear=YYYY&repMonth=N&foCd=all&regionCd=all

3. **kvartirografia** — DOM-скрейп
   https://наш.дом.рф/аналитика/квартирография

State хранится в `state/nashdom_state.json`. Запуск:
    py nashdom_checker.py                # все 3 источника
    py nashdom_checker.py monitoring_2_0 # один ключ
"""
from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Iterable
from urllib.parse import quote

import requests
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from pipeline.selenium_utils import create_chrome


# ─────────────────────────────────────────────
# КОНФИГ
# ─────────────────────────────────────────────

NASHDOM_BASE = "https://xn--80az8a.xn--d1aqf.xn--p1ai"

# Google Sheets «Мониторинг 2.0» — публичная (или с доступом по ссылке).
# export?format=xlsx без gid экспортирует все листы.
GSHEETS_MONITORING_2_0_ID = "19Z8y6EtoCNbygA9OIjZ7lHaPfeA9fV3Yl6Ned53gNcY"
GSHEETS_EXPORT_URL = (
    f"https://docs.google.com/spreadsheets/d/{GSHEETS_MONITORING_2_0_ID}"
    "/export?format=xlsx"
)

# наш.дом.рф URLs (с человекочитаемым путём — Chrome/Selenium сам
# percent-encodes кириллицу при необходимости, и `quote()` гарантирует это
# для requests).
RASPRODANNOST_PATH = "аналитика/распроданность-стройготовность"
KVARTIROGRAFIA_PATH = "аналитика/квартирография"

DOWNLOAD_DIR = Path("data/raw/realty/nashdom")
STATE_FILE = Path("state/nashdom_state.json")
PAGE_TIMEOUT = 60
HEADLESS = False  # TODO: переключить в True после первой удачной отладки.

REPORT_DATE_RE = re.compile(r"(\d{2}\.\d{2}\.\d{4})")


def _build_rasprodannost_url() -> str:
    # repYear/repMonth = предыдущий месяц от сегодня (свежие данные).
    today = datetime.now()
    if today.month == 1:
        rep_year, rep_month = today.year - 1, 12
    else:
        rep_year, rep_month = today.year, today.month - 1
    path = quote(RASPRODANNOST_PATH)
    return (
        f"{NASHDOM_BASE}/{path}"
        f"?repYear={rep_year}&repMonth={rep_month}&foCd=all&regionCd=all"
    )


def _build_kvartirografia_url() -> str:
    return f"{NASHDOM_BASE}/{quote(KVARTIROGRAFIA_PATH)}"


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


def _save_debug_snapshot(driver, tag: str) -> None:
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


def _read_report_date(driver) -> str | None:
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


# ─────────────────────────────────────────────
# 1. Monitoring 2.0 — Google Sheets export
# ─────────────────────────────────────────────


def fetch_monitoring_2_0(state: dict) -> list[Path]:
    """Скачивает Google Sheet целиком через export?format=xlsx."""
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    url = GSHEETS_EXPORT_URL
    print(f"  🌐 monitoring_2_0 (Google Sheets export):")
    print(f"     {url}")

    try:
        # allow_redirects=True — Google делает 302 на сам файл; если же редирект
        # идёт на accounts.google.com — это значит таблица закрыта и нужен логин.
        r = requests.get(url, timeout=120, allow_redirects=True)
    except requests.RequestException as exc:
        print(f"  ❌ Не удалось скачать: {exc}")
        return []

    final_url = r.url
    ctype = (r.headers.get("content-type") or "").lower()
    if "accounts.google.com" in final_url or "text/html" in ctype:
        print(f"  ⚠️  Google вернул HTML (вероятно нужен логин или таблица закрыта).")
        print(f"     final_url = {final_url}")
        print(f"     content-type = {ctype}")
        print(f"     Открой ссылку в браузере и в настройках доступа выбери")
        print(f"     «Доступ всем у кого есть ссылка → Читатель».")
        return []
    if r.status_code != 200:
        print(f"  ⚠️  HTTP {r.status_code}")
        return []

    date_str = datetime.now().strftime("%Y%m%d")
    target = DOWNLOAD_DIR / f"monitoring_2_0_{date_str}.xlsx"
    target.write_bytes(r.content)
    size_kb = len(r.content) / 1024
    print(f"  ✅ {target.name} ({size_kb:,.0f} KB)")

    state["monitoring_2_0"] = {
        "downloaded_at": datetime.now().isoformat(timespec="seconds"),
        "filename": target.name,
        "size_bytes": len(r.content),
        "source_url": url,
    }
    return [target]


# ─────────────────────────────────────────────
# 2 + 3. DOM-скрейп таблиц (rasprodannost / kvartirografia)
# ─────────────────────────────────────────────


def _extract_table(driver) -> list[dict]:
    """Снимает основную таблицу страницы в виде list[dict]."""
    return driver.execute_script(
        """
        function pickTable() {
            const tables = [...document.querySelectorAll('table')]
                .filter(t => t.offsetParent !== null && t.rows.length > 1);
            if (tables.length) return tables[0];
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
        // role="table" вариант
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


def _scrape_table_source(source_key: str, url: str, state: dict) -> list[Path]:
    """Открывает URL, ждёт таблицу, сохраняет JSON в data/raw/realty/nashdom/."""
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    print(f"  🌐 {source_key}: {url}")

    driver = create_chrome(download_dir=DOWNLOAD_DIR, headless=HEADLESS)
    new_files: list[Path] = []
    try:
        driver.set_page_load_timeout(PAGE_TIMEOUT)
        driver.get(url)
        time.sleep(5)

        try:
            WebDriverWait(driver, 45).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, 'table, [role="table"]')
                )
            )
        except TimeoutException:
            print(f"  ⚠️  {source_key}: таблица не появилась за 45 сек")
            _save_debug_snapshot(driver, f"{source_key}_no_table")
            return []

        time.sleep(3)  # дать виджету дорендериться
        report_date = _read_report_date(driver) or ""
        if report_date:
            print(f"     · отчёт на: {report_date}")

        rows = _extract_table(driver)
        if not rows:
            print(f"  ⚠️  {source_key}: таблица пустая")
            _save_debug_snapshot(driver, f"{source_key}_empty_table")
            return []

        date_str = datetime.now().strftime("%Y%m%d")
        target = DOWNLOAD_DIR / f"{source_key}_{date_str}.json"
        payload = {
            "source": source_key,
            "report_date": report_date,
            "scraped_at": datetime.now().isoformat(timespec="seconds"),
            "url": url,
            "rows": rows,
        }
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"  ✅ {target.name} ({len(rows)} строк)")
        new_files.append(target)
        state[source_key] = {
            "report_date": report_date,
            "filename": target.name,
            "scraped_at": datetime.now().isoformat(timespec="seconds"),
            "row_count": len(rows),
        }
    except WebDriverException as exc:
        print(f"  ❌ {source_key}: {exc}")
    finally:
        driver.quit()
    return new_files


def fetch_rasprodannost(state: dict) -> list[Path]:
    return _scrape_table_source("rasprodannost", _build_rasprodannost_url(), state)


def fetch_kvartirografia(state: dict) -> list[Path]:
    return _scrape_table_source("kvartirografia", _build_kvartirografia_url(), state)


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
    print(f"наш.дом.рф / GSheets | Запуск: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
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
            save_state(state)
        except Exception as exc:  # noqa: BLE001
            print(f"  ❌ {key}: {exc}")

    print(f"\n{'='*60}")
    print(f"Итог: новых/обновлённых файлов — {len(all_new)}")
    for f in all_new:
        print(f"  • {f}")
    print(f"{'='*60}\n")
    return all_new


if __name__ == "__main__":
    args = sys.argv[1:]
    run(only=args if args else None)
