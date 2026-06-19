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
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Iterable
from urllib.parse import quote

import requests
from selenium.common.exceptions import TimeoutException, WebDriverException

from pipeline.dev_name_utils import normalize_developer_name
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

# Русские месяцы → номер (для строк типа «3 июня 2026 года»)
RUS_MONTHS = {
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4,
    "ма": 5, "май": 5, "июн": 6, "июл": 7, "август": 8,
    "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12,
}


def _parse_russian_date(text: str) -> str:
    """«3 июня 2026 года» → «03.06.2026». Возвращает '' если не распарсилось."""
    m = re.search(
        r"(\d{1,2})\s+(январ|феврал|март|апрел|май|июн|июл|август|сентябр|октябр|ноябр|декабр)\w*\s+(\d{4})",
        text.lower(),
    )
    if not m:
        return ""
    day = int(m.group(1))
    month_stem = m.group(2)
    year = int(m.group(3))
    month = RUS_MONTHS.get(month_stem, 0)
    if not month:
        return ""
    return f"{day:02d}.{month:02d}.{year:04d}"


def _build_rasprodannost_url() -> str:
    """Распроданность — URL без query-параметров. Страница сама подберёт
    свежий отчётный период (фиксированные repMonth/repYear ломали запрос:
    страница показывала «Нет данных» даже для давно опубликованных периодов).
    """
    path = quote(RASPRODANNOST_PATH)
    return f"{NASHDOM_BASE}/{path}"


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
            (e.innerText.includes('данным на') || e.innerText.includes('состоянию на')) &&
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
    # Русский формат «3 июня 2026 года»
    rus = _parse_russian_date(text)
    if rus:
        return rus
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


def _scroll_through_page(driver, *, steps: int = 6, pause: float = 1.5) -> None:
    """Постепенный скролл — заставляет лениво-рендеримые таблицы появиться."""
    for i in range(steps):
        driver.execute_script(
            f"window.scrollTo(0, document.body.scrollHeight * {(i + 1) / steps});"
        )
        time.sleep(pause)
    driver.execute_script("window.scrollTo(0, 0);")
    time.sleep(0.5)


KVART_REGIONS = [
    {"key": "rf",  "label": "Российская Федерация", "search": "",       "click_label": "Российская Федерация"},
    {"key": "msk", "label": "Город Москва",          "search": "Москва", "click_label": "Город Москва"},
]


def _color_to_room(bg: str) -> str:
    """Маппит rgb/rgba цвет столбика гистограммы на тип комнат.

    Цветовая схема наш.дом.рф (по button[data-rooms=*] color attr):
    - rgba(139, 197, 64) = #8BC540 green        → 1 комн
    - rgba(78, 195, 224) = #4EC3E0 blue          → 2 комн
    - rgb(228, 231, 232) = light gray (#E4E7E8) → 3 комн (как #7A8386 в data-rooms="THREE")
    - rgba(7, 40, 51)    = #072833 dark navy    → 4+ комн (color FOUR-button)
    """
    if not bg:
        return ""
    if "139, 197, 64" in bg:
        return "1комн"
    if "78, 195, 224" in bg:
        return "2комн"
    if "228, 231, 232" in bg:
        return "3комн"
    if "7, 40, 51" in bg:
        return "4+комн"
    return ""


def _scroll_collect_list(driver, list_index: int, *, step_px: int = 600, pause: float = 0.25) -> list[dict]:
    """Прокручивает виртуальный список div.list[list_index] и собирает все строки.

    Для каждой строки извлекает:
    - наименование (девелопер / регион)
    - квартиры_тыс_шт (val1)
    - площадь_тыс_м² (val2)
    - доли по комнатности (1/2/3/4+ — % из histogram bars в sibling div.css-1r48qb0)

    Скроллит ИНУТРИ div.list (контейнер с overflow:auto). После scrollTop
    диспатчит 'scroll' event для React-state.

    Останавливается когда 3 шага подряд не дают новых имён, либо
    scrollTop достигает scrollHeight.
    """
    height = driver.execute_script(
        f"const ls=document.querySelectorAll('div.list');"
        f"return ls[{list_index}] ? ls[{list_index}].scrollHeight : 0;"
    )
    if not height:
        print(f"       ⚠️  scrollHeight=0, нет списка")
        return []
    expected = max(1, height // 19)
    print(f"       inner scrollHeight={height}px (≈{expected} строк)")

    seen: dict[str, dict] = {}
    pos = 0
    no_progress = 0
    step_num = 0
    while pos < height + step_px:
        driver.execute_script(
            """
            const ls = document.querySelectorAll('div.list');
            const idx = arguments[0];
            const pos = arguments[1];
            if (ls[idx]) {
                ls[idx].scrollTop = pos;
                ls[idx].dispatchEvent(new Event('scroll', {bubbles: true}));
            }
            """,
            list_index,
            pos,
        )
        time.sleep(pause)
        rows = driver.execute_script(
            """
            const idx = arguments[0];
            const ls = document.querySelectorAll('div.list');
            if (!ls[idx]) return [];
            const FLEX_RE = /flex-basis:\\s*([\\d.]+)%/;
            const BG_RE = /background:\\s*(rgb[a]?\\([^)]+\\))/;
            return [...ls[idx].querySelectorAll('button.css-5nggi1')].map(b => {
                const divs = [...b.querySelectorAll(':scope > div')];
                const result = {
                    name: (divs[0] && divs[0].innerText || '').trim(),
                    val1: (divs[1] && divs[1].innerText || '').trim(),
                    val2: (divs[2] && divs[2].innerText || '').trim(),
                    bars: []
                };
                let sib = b.nextElementSibling;
                while (sib && !(sib.classList && sib.classList.contains('css-1r48qb0'))) {
                    sib = sib.nextElementSibling;
                }
                if (sib) {
                    for (const bar of sib.querySelectorAll('.css-1xdcykx')) {
                        const style = bar.getAttribute('style') || '';
                        const flexM = style.match(FLEX_RE);
                        const bgM = style.match(BG_RE);
                        if (flexM && bgM) {
                            result.bars.push({pct: flexM[1], color: bgM[1]});
                        }
                    }
                }
                return result;
            }).filter(r => r.name);
            """,
            list_index,
        )
        for r in rows:
            if r["name"] not in seen:
                # Маппим bars → 4 столбца долей по комнатности
                shares = {"доля_1комн_%": "", "доля_2комн_%": "", "доля_3комн_%": "", "доля_4+комн_%": ""}
                for bar in r.get("bars", []):
                    room = _color_to_room(bar.get("color", ""))
                    if room:
                        shares[f"доля_{room}_%"] = bar.get("pct", "")
                seen[r["name"]] = {
                    "наименование": r["name"],
                    "квартиры_тыс_шт": r["val1"].replace("\xa0", " "),
                    "площадь_тыс_м²": r["val2"].replace("\xa0", " "),
                    **shares,
                }
        step_num += 1
        if step_num % 20 == 0:
            print(f"         · шаг {step_num}, pos={pos}/{height}, собрано {len(seen)}/{expected}")
        # Идём ДО конца виртуального скролла (height + 2*step), не останавливаемся
        # на отсутствии прогресса — некоторые батчи могут не дать новых имён,
        # но дальше ещё есть. Уходим за пределы scrollHeight чтобы поймать
        # последние rows которые рендерятся при scrollTop=max.
        if pos > height + step_px * 2:
            break
        pos += step_px

    # Финальный «добив»: ставим scrollTop в самый конец чтобы поймать
    # хвост, который мог не отрендериться при больших шагах.
    driver.execute_script(
        f"const ls=document.querySelectorAll('div.list');"
        f"if(ls[{list_index}]) ls[{list_index}].scrollTop = ls[{list_index}].scrollHeight;"
    )
    time.sleep(pause * 2)
    final_rows = driver.execute_script(
        """
        const idx = arguments[0];
        const ls = document.querySelectorAll('div.list');
        if (!ls[idx]) return [];
        return [...ls[idx].querySelectorAll('button.css-5nggi1')].map(b => {
            const divs = [...b.querySelectorAll(':scope > div')];
            return {
                name: (divs[0] && divs[0].innerText || '').trim(),
                val1: (divs[1] && divs[1].innerText || '').trim(),
                val2: (divs[2] && divs[2].innerText || '').trim()
            };
        }).filter(r => r.name);
        """,
        list_index,
    )
    for r in final_rows:
        if r["name"] not in seen:
            seen[r["name"]] = {
                "наименование": r["name"],
                "квартиры_тыс_шт": r["val1"].replace("\xa0", " "),
                "площадь_тыс_м²": r["val2"].replace("\xa0", " "),
                "доля_1комн_%": "", "доля_2комн_%": "",
                "доля_3комн_%": "", "доля_4+комн_%": "",
            }

    print(f"       финал: {len(seen)} строк за {step_num} шагов")
    return list(seen.values())


def _build_kvart_xlsx(all_data: list[dict], target_xlsx: Path) -> None:
    """Собирает все накопленные данные в xlsx с 4 листами."""
    try:
        import pandas as pd
    except ImportError:
        print("  ⚠️  pandas не установлен — xlsx не пишем")
        return

    apartments_rows, distribution_rows = [], []
    developers_rows, regions_rows = [], []
    per_dev_rows = []  # точные числа на каждого девелопера

    for data in all_data:
        rk = data.get("region_key", "")
        rl = data.get("region", "")
        rd = data.get("report_date", "")
        for a in data.get("apartments", []):
            apartments_rows.append({"region_key": rk, "region": rl, "report_date": rd, **a})
        for d in data.get("distribution", []):
            distribution_rows.append({"region_key": rk, "region": rl, "report_date": rd, **d})
        for dev in data.get("developers", []):
            developers_rows.append({"region_key": rk, "region": rl, "report_date": rd, **dev})
        for reg in data.get("regions", []):
            regions_rows.append({"region_key": rk, "region": rl, "report_date": rd, **reg})
        # Per-dev exact apartments (НОВОЕ)
        for pd_row in data.get("apartments_per_dev", []):
            apt = pd_row.get("apartments", {}) or {}
            per_dev_rows.append({
                "region_key": rk,
                "region": rl,
                "report_date": rd,
                "наименование": pd_row.get("наименование", ""),
                "источник_имя_monitoring": pd_row.get("monitoring_name", ""),
                "Все_количество_шт": (apt.get("all") or {}).get("count", ""),
                "Все_площадь_тыс_м²": (apt.get("all") or {}).get("area", ""),
                "1комн_количество_шт": (apt.get("ONE") or {}).get("count", ""),
                "1комн_площадь_тыс_м²": (apt.get("ONE") or {}).get("area", ""),
                "2комн_количество_шт": (apt.get("TWO") or {}).get("count", ""),
                "2комн_площадь_тыс_м²": (apt.get("TWO") or {}).get("area", ""),
                "3комн_количество_шт": (apt.get("THREE") or {}).get("count", ""),
                "3комн_площадь_тыс_м²": (apt.get("THREE") or {}).get("area", ""),
                "4+комн_количество_шт": (apt.get("FOUR") or {}).get("count", ""),
                "4+комн_площадь_тыс_м²": (apt.get("FOUR") or {}).get("area", ""),
            })

    with pd.ExcelWriter(target_xlsx, engine="openpyxl") as writer:
        if apartments_rows:
            pd.DataFrame(apartments_rows).to_excel(writer, sheet_name="apartments", index=False)
        if distribution_rows:
            pd.DataFrame(distribution_rows).to_excel(writer, sheet_name="distribution", index=False)
        if developers_rows:
            pd.DataFrame(developers_rows).to_excel(writer, sheet_name="developers", index=False)
        if regions_rows:
            pd.DataFrame(regions_rows).to_excel(writer, sheet_name="regions", index=False)
        if per_dev_rows:
            pd.DataFrame(per_dev_rows).to_excel(
                writer, sheet_name="apartments_per_dev", index=False)


def fetch_kvartirografia(state: dict) -> list[Path]:
    """Квартирография по 2 регионам (РФ + Москва) с inner-scroll.

    xlsx сохраняется ПОСЛЕ КАЖДОГО региона — даже если процесс
    прерван на половине, у пользователя есть актуальный файл.

    Per-developer обход (НОВОЕ):
      После основного сбора для региона дополнительно проходит по
      списку ГК из monitoring_2_0_*.xlsx, переключает фильтр
      «Девелопер» на каждого и парсит точное количество квартир
      (1/2/3/4+ комн) — данные сохраняются в sheet apartments_per_dev.

      Управление:
        KVART_PER_DEV=0        — выключить (по умолчанию включено)
        KVART_PER_DEV_LIMIT=N  — обработать только первые N (для отладки)

      Длительность: ~5-7 сек на девелопера × ~400 девелоперов × 2 региона
      ≈ 70-90 минут. Каждые 10 девелоперов делается flush в xlsx.
    """
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    url = _build_kvartirografia_url()
    print(f"  🌐 kvartirografia: {url}")

    date_str = datetime.now().strftime("%Y%m%d")
    target_xlsx = DOWNLOAD_DIR / f"kvartirografia_{date_str}.xlsx"
    target_json = DOWNLOAD_DIR / f"kvartirografia_{date_str}.json"

    driver = create_chrome(download_dir=DOWNLOAD_DIR, headless=HEADLESS)
    all_data: list[dict] = []
    new_files: list[Path] = []

    def flush():
        """Сохраняем текущее накопленное состояние в xlsx + json.

        JSON сохраняется ВСЕГДА (он не может быть занят пользователем).
        xlsx — best-effort: если файл открыт в Excel/OneDrive, пишем
        предупреждение и продолжаем, чтобы не терять прогресс per-dev.
        """
        if not all_data:
            return
        # JSON — приоритет, всегда сохраняем
        try:
            target_json.write_text(
                json.dumps(all_data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            if target_json not in new_files:
                new_files.append(target_json)
        except Exception as exc:  # noqa: BLE001
            print(f"     ⚠️  ошибка при записи json: {exc}")
        # xlsx — отдельный try; если занят — не блокируем сбор данных
        try:
            _build_kvart_xlsx(all_data, target_xlsx)
            sizes = {
                "apartments": sum(len(d.get("apartments", [])) for d in all_data),
                "distribution": sum(len(d.get("distribution", [])) for d in all_data),
                "developers": sum(len(d.get("developers", [])) for d in all_data),
                "regions": sum(len(d.get("regions", [])) for d in all_data),
                "per_dev": sum(len(d.get("apartments_per_dev", [])) for d in all_data),
            }
            print(f"     💾 {target_xlsx.name}: {sizes}")
            if target_xlsx not in new_files:
                new_files.append(target_xlsx)
        except Exception as exc:  # noqa: BLE001
            print(f"     ⚠️  xlsx занят/недоступен ({exc}) — JSON всё равно сохранён")

    try:
        driver.set_page_load_timeout(PAGE_TIMEOUT)
        driver.get(url)
        time.sleep(6)
        try:
            WebDriverWait(driver, 45).until(
                lambda d: "данным на" in d.page_source or "data-rooms" in d.page_source
            )
        except TimeoutException:
            print(f"  ⚠️  контент не появился за 45 сек")
            _save_debug_snapshot(driver, "kvartirografia_no_content")
            return []
        time.sleep(3)

        for region in KVART_REGIONS:
            try:
                print(f"     ── регион: {region['key']} ({region['label']})")
                if region["search"]:
                    ok = _switch_region_filter(
                        driver,
                        target_label=region["click_label"],
                        search_query=region["search"],
                    )
                    if not ok:
                        print(f"       ⚠️  не удалось переключить на {region['label']}")
                        _save_debug_snapshot(driver, f"kvartirografia_{region['key']}_switch_fail")
                        continue
                    # Подождём чтобы данные перезагрузились
                    time.sleep(5)

                data = _parse_kvartirografia(driver.page_source, driver.current_url)
                data["region_key"] = region["key"]

                print(f"       devs (виртуальный список 0):")
                data["developers"] = _scroll_collect_list(driver, list_index=0)
                print(f"       regions (виртуальный список 1):")
                data["regions"] = _scroll_collect_list(driver, list_index=1)

                print(
                    f"       · apartments={len(data['apartments'])}, "
                    f"distribution={len(data['distribution'])}, "
                    f"devs={len(data['developers'])}, regs={len(data['regions'])}"
                )

                _save_debug_snapshot(driver, f"kvartirografia_{region['key']}_ok")
                all_data.append(data)
                # КРИТИЧНО: сохраняем xlsx ПОСЛЕ каждого региона
                flush()

                # === Per-developer обход ===
                # Управляется KVART_PER_DEV (default=1).
                # Источник имён — лист developers (реальный список с сайта
                # для текущего региона), а НЕ monitoring_2_0.xlsx. Это
                # ~200-300 застройщиков вместо 745. Для каждого имени
                # подбираем monitoring_name через _build_dev_mapping —
                # чтобы в дашборде матчить с другими источниками.
                if _per_dev_enabled():
                    site_devs_full = [
                        d.get("наименование", "")
                        for d in data.get("developers", [])
                        if d.get("наименование", "")
                    ]
                    if not site_devs_full:
                        print("       ⚠️  лист developers пуст — per-dev пропускаем")
                    else:
                        # Сборка lookup: site_key → ожидаемые числа из developers.
                        # Используется и для verification, и для mapping ниже.
                        expected: dict[str, dict] = {}
                        for d_row in data.get("developers", []):
                            nm = normalize_developer_name(d_row.get("наименование", ""))
                            if not nm:
                                continue
                            expected[nm] = {
                                "all_count": _parse_num_apartments(d_row.get("квартиры_тыс_шт", "")),
                                "all_area": _parse_num_apartments(d_row.get("площадь_тыс_м²", "")),
                            }

                        # Mapping: site_name → monitoring_name (с overrides).
                        # Строится для ВСЕГО site_devs_full (полный аудит для
                        # пользователя) до применения лимитов на обход.
                        # Сохраняем СРАЗУ в data, до per-dev обхода —
                        # пользователь может посмотреть mapping даже если
                        # обход прервётся.
                        monitoring_devs = _load_monitoring_devs()
                        overrides = _load_dev_overrides()
                        mapping = _build_dev_mapping(
                            site_devs_full, monitoring_devs, overrides)
                        data["dev_name_mapping"] = mapping
                        mapping_by_site = {m["site_name"]: m for m in mapping}
                        n_matched = sum(1 for m in mapping if m["monitoring_name"])
                        n_conflict = sum(
                            1 for m in mapping if m["match_type"] == "conflict_first")
                        n_none = sum(
                            1 for m in mapping if m["match_type"] == "none")
                        print(f"       ── mapping: {len(mapping)} сайтовых, "
                              f"{n_matched} matched, {n_conflict} conflicts, "
                              f"{n_none} unmatched")
                        flush()

                        # Per-region лимит per-dev обхода. Для РФ список
                        # ~1678, без лимита это ~3 часа. Москва обычно
                        # 200-300 — обходится за 20-30 мин.
                        site_devs = list(site_devs_full)
                        region_limit = _per_dev_limit_for_region(region["key"])
                        if region_limit:
                            site_devs = site_devs[:region_limit]
                        glob_limit = _per_dev_limit()
                        if glob_limit:
                            site_devs = site_devs[:glob_limit]

                        debug_first = os.environ.get(
                            "KVART_PER_DEV_DEBUG", "0").strip() == "1"
                        per_dev: list[dict] = []
                        # attempts — диагностический лог КАЖДОЙ попытки
                        attempts: list[dict] = []
                        print(f"       ── per-dev обход: {len(site_devs)}/{len(site_devs_full)} "
                              f"девелоперов (KVART_PER_DEV=0 чтобы выключить)")
                        ok_count = fail_count = rejected = 0
                        for i, site_name in enumerate(site_devs, 1):
                            m = mapping_by_site.get(site_name, {})
                            attempt = {
                                "i": i,
                                "site_name": site_name,
                                "monitoring_name": m.get("monitoring_name"),
                                "match_type": m.get("match_type", "none"),
                                "status": "",
                            }
                            dbg = debug_first and i <= 3
                            try:
                                ok = _switch_developer_filter(
                                    driver, site_name, wait_change=True,
                                    timeout=15, debug=dbg)
                            except Exception as e:  # noqa: BLE001
                                print(f"          ⚠️  {i}/{len(site_devs)} «{site_name}» — {e}")
                                attempt.update(status="switch_exception", error=str(e)[:200])
                                ok = False
                            if not ok:
                                fail_count += 1
                                if not attempt["status"]:
                                    attempt["status"] = "switch_fail"
                                attempts.append(attempt)
                                continue

                            apt = _parse_apartments_live(
                                driver, debug=(debug_first and i == 1))

                            # Verification: ratio с агрегатом developers по site_key.
                            filter_label = _get_developer_filter_label(driver) or site_name
                            attempt["filter_label"] = filter_label
                            site_key = normalize_developer_name(site_name)
                            exp = expected.get(site_key) or expected.get(
                                normalize_developer_name(filter_label))
                            if exp and apt and apt.get("all"):
                                got = _parse_num_apartments(apt["all"].get("count", ""))
                                want = exp["all_count"] * 1000
                                if want > 0:
                                    ratio = got / want
                                    if ratio < 0.9 or ratio > 1.1:
                                        time.sleep(2.0)
                                        apt = _parse_apartments_live(driver)
                                        got2 = _parse_num_apartments(
                                            (apt.get("all") or {}).get("count", "")) if apt else 0
                                        if got2 > 0:
                                            ratio = got2 / want
                                        if ratio < 0.9 or ratio > 1.1:
                                            rejected += 1
                                            attempt.update(
                                                status="rejected",
                                                got=int(got2 or got),
                                                want=int(want),
                                                ratio=round(ratio, 3),
                                            )
                                            attempts.append(attempt)
                                            if rejected <= 3:
                                                print(f"          🚫 {i}/{len(site_devs)} «{site_name[:30]}»: "
                                                      f"парсер дал {got2 or got:.0f} шт, "
                                                      f"ожидалось ~{want:.0f} (ratio={ratio:.2f}) — пропускаю")
                                            continue

                            if apt and apt.get("all"):
                                per_dev.append({
                                    "наименование": filter_label,
                                    "site_name": site_name,
                                    "monitoring_name": m.get("monitoring_name"),
                                    "match_type": m.get("match_type", "none"),
                                    "apartments": apt,
                                })
                                attempt["status"] = "ok"
                                ok_count += 1
                            else:
                                fail_count += 1
                                attempt["status"] = "empty_apartments"
                            attempts.append(attempt)

                            if i % 10 == 0:
                                print(f"          · {i}/{len(site_devs)}: "
                                      f"ok={ok_count}, miss={fail_count}, rejected={rejected}")
                                data["apartments_per_dev"] = per_dev
                                data["per_dev_attempts"] = attempts
                                flush()
                        # финальный сейв
                        data["apartments_per_dev"] = per_dev
                        data["per_dev_attempts"] = attempts
                        flush()
                        print(f"       ✅ per-dev: {ok_count} собрано, "
                              f"{fail_count} ошибок селектора, "
                              f"{rejected} отвергнуто (не совпадает с агрегатом)")
                        if ok_count == 0 and attempts:
                            from collections import Counter
                            status_counts = Counter(a["status"] for a in attempts)
                            print(f"       ℹ️  диагностика: {dict(status_counts)}")
                            print(f"       ℹ️  per_dev_attempts сохранены в JSON для разбора")
                        # Сброс фильтра на «Все девелоперы»
                        try:
                            _switch_developer_filter(
                                driver, "Все девелоперы", wait_change=False)
                        except Exception:  # noqa: BLE001
                            pass

            except Exception as exc:  # noqa: BLE001
                # Любая ошибка внутри региона — сохраняем что есть и идём дальше
                print(f"     ❌ ошибка при обработке {region['key']}: {exc}")
                flush()
    except KeyboardInterrupt:
        print("\n  ⚠️  прерывание — сохраняю что собрано…")
        flush()
        raise
    except WebDriverException as exc:
        print(f"  ❌ {exc}")
        flush()
    finally:
        try:
            driver.quit()
        except Exception:  # noqa: BLE001
            pass

    if all_data:
        state["kvartirografia"] = {
            "scraped_at": datetime.now().isoformat(timespec="seconds"),
            "regions": [r["key"] for r in KVART_REGIONS],
            "file": target_xlsx.name,
        }
    return new_files


def _get_current_region_label(driver) -> str:
    """Возвращает текущее значение фильтра «Регион» (для поиска span'а на котором кликать)."""
    return driver.execute_script(
        """
        const p = [...document.querySelectorAll('p')].find(e =>
            e.offsetParent !== null && /федеральный\\s*округ/i.test(e.innerText || ''));
        if (!p || !p.nextElementSibling) return '';
        const span = p.nextElementSibling.querySelector('span.css-wjcl1w');
        return span ? span.innerText.trim() : '';
        """
    ) or ""


def _get_all_apartments_count(driver) -> str:
    """Читает значение «Все квартиры» из шапки apartments-блока.

    Возвращает count (например «2 438 972») или '' если не нашёл.
    Используется как индикатор смены региона: после переключения данные
    обновляются и количество «Все квартиры» становится другим.
    """
    return driver.execute_script(
        """
        const buttons = [...document.querySelectorAll('button')];
        const btn = buttons.find(b =>
            b.offsetParent !== null &&
            b.innerText && b.innerText.includes('Все квартиры')
        );
        if (!btn) return '';
        const divs = btn.querySelectorAll('div');
        if (divs.length < 2) return '';
        return (divs[1].innerText || '').trim();
        """
    ) or ""


def _switch_region_filter(driver, target_label: str, search_query: str = "") -> bool:
    """Переключает фильтр «Федеральный округ / Регион» на target_label.

    Структура попапа (verified по HTML момента сбоя 05.06.2026):
        <input placeholder="Поиск по названию" class="css-wsxkpq">
        ...
        <div class="css-eo6exy">
          <div class="css-1ynvnsn">
            <div class="css-hol6xw"></div>              <!-- чекбокс -->
            <span class="css-1ogbkfc">Город Москва</span>
          </div>
        </div>

    ВАЖНО: попап в position:fixed/absolute оверлее, поэтому offsetParent===null
    для его элементов. Раньше код считал input «невидимым» и падал. Теперь
    ищем напрямую по placeholder и тексту, БЕЗ проверки offsetParent.
    """
    baseline = _get_all_apartments_count(driver)
    print(f"       · baseline «Все квартиры»: {baseline}")
    if not baseline:
        print(f"       ⚠️  не нашёл baseline «Все квартиры»")
        return False

    current = _get_current_region_label(driver) or "Российская Федерация"
    print(f"       · текущий регион: «{current}»")
    if current == target_label:
        return True

    # 1) Клик на триггер dropdown'а региона.
    # В закрытом состоянии: span.css-wjcl1w с текстом текущего региона
    # внутри div[tabindex=0] рядом с <p>Федеральный округ / Регион</p>.
    try:
        trigger = driver.execute_script(
            """
            const p = [...document.querySelectorAll('p')].find(e =>
                /федеральный\\s*округ/i.test(e.innerText || ''));
            if (!p || !p.nextElementSibling) return null;
            return p.nextElementSibling.querySelector('[tabindex="0"]');
            """
        )
        if not trigger:
            print(f"       ⚠️  не нашёл триггер dropdown'а")
            return False
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", trigger)
        time.sleep(0.5)
        driver.execute_script("arguments[0].click();", trigger)
        print(f"       · клик на trigger")
    except WebDriverException as exc:
        print(f"       ⚠️  ошибка клика триггера: {exc}")
        return False

    # 2) Дождаться search-input по placeholder (БЕЗ offsetParent —
    # попап в fixed-оверлее). presence достаточно.
    try:
        WebDriverWait(driver, 10).until(
            lambda d: d.execute_script(
                "return !!document.querySelector('input[placeholder=\"Поиск по названию\"]');"
            )
        )
    except TimeoutException:
        print(f"       ⚠️  search-input попапа не появился")
        _save_debug_snapshot(driver, "region_switch_no_popup")
        return False
    time.sleep(0.5)

    # 3) Ввести запрос в search-input через JS (надёжно для React)
    if search_query:
        ok = driver.execute_script(
            """
            const q = arguments[0];
            const inp = document.querySelector('input[placeholder="Поиск по названию"]');
            if (!inp) return false;
            const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
            setter.call(inp, q);
            inp.dispatchEvent(new Event('input', {bubbles: true}));
            inp.dispatchEvent(new Event('change', {bubbles: true}));
            return true;
            """,
            search_query,
        )
        print(f"       · ввёл в поиск: '{search_query}' (ok={ok})")
        time.sleep(2)

    # 4) Дождаться появления target_label и кликнуть по ВСЕЙ строке
    # (div-родитель span'а — там висит обработчик клика чекбокса).
    try:
        WebDriverWait(driver, 10).until(
            lambda d: d.execute_script(
                """
                const target = arguments[0];
                return [...document.querySelectorAll('span')]
                    .some(s => (s.innerText || '').trim() === target);
                """,
                target_label,
            )
        )
    except TimeoutException:
        print(f"       ⚠️  пункт «{target_label}» не появился в попапе")
        _save_debug_snapshot(driver, f"region_no_option_{target_label}")
        return False

    clicked = driver.execute_script(
        """
        const target = arguments[0];
        const span = [...document.querySelectorAll('span')]
            .find(s => (s.innerText || '').trim() === target);
        if (!span) return false;
        // Кликаем по строке-родителю (содержит чекбокс + span).
        // Поднимаемся на 1-2 уровня до кликабельного контейнера.
        let row = span.parentElement;  // div.css-1ynvnsn
        if (!row) row = span;
        row.scrollIntoView({block: 'center'});
        row.click();
        return true;
        """,
        target_label,
    )
    print(f"       · клик на «{target_label}» (ok={clicked})")
    if not clicked:
        return False

    # 5) Закрыть попап (клик по body) чтобы данные применились
    time.sleep(1)
    try:
        driver.execute_script("document.body.click();")
    except WebDriverException:
        pass

    # 6) ВЕРИФИКАЦИЯ: «Все квартиры» должно смениться
    try:
        WebDriverWait(driver, 25).until(
            lambda d: (
                _get_all_apartments_count(d) != baseline
                and _get_all_apartments_count(d) != ""
            )
        )
        new_count = _get_all_apartments_count(driver)
        print(f"       ✅ данные сменились: {baseline} → {new_count}")
        time.sleep(2)
        return True
    except TimeoutException:
        actual = _get_all_apartments_count(driver)
        print(f"       ⚠️  «Все квартиры» не сменилось (всё ещё «{actual}»)")
        _save_debug_snapshot(driver, "region_switch_no_data_change")
        return False


# ─────────────────────────────────────────────
# Per-developer обход (НОВОЕ)
# ─────────────────────────────────────────────

def _get_developer_filter_label(driver) -> str:
    """Возвращает текст лейбла pill фильтра «Девелопер».

    Структура pill (по HTML предоставленному пользователем):
      <div tabindex="0" class="css-5nk8c4">           ← pill
        <div class="css-11s03ds">
          <span class="css-203ko5">4 девелопера</span> ← лейбл (!)
          <div class="css-1thn7xl">
            <svg class="css-1k2i6yk" .../>             ← крестик
            <svg class="css-1fv53l9" .../>             ← стрелка
          </div>
        </div>
      </div>

    Раньше я искал span.css-wjcl1w (так у фильтра «Регион»), но
    у фильтра «Девелопер» класс другой — css-203ko5. Из-за этого
    функция возвращала "" и проверка «уже пусто» срабатывала
    ошибочно, сброс не выполнялся.
    """
    return driver.execute_script(
        """
        const p = [...document.querySelectorAll('p')].find(e =>
            e.offsetParent !== null &&
            /^девелопер$/i.test((e.innerText || '').trim()));
        if (!p || !p.nextElementSibling) return '';
        const pill = p.nextElementSibling;
        // Пробуем разные классы (могут отличаться у разных фильтров)
        let span = pill.querySelector('span.css-203ko5') ||
                   pill.querySelector('span.css-wjcl1w') ||
                   pill.querySelector('span.css-pqw2m5');
        if (span) return (span.innerText || '').trim();
        // Запасной поиск: ЛЮБОЙ span внутри pill (берём первый
        // непустой — обычно это и есть лейбл)
        const allSpans = [...pill.querySelectorAll('span')];
        for (const s of allSpans) {
            const t = (s.innerText || '').trim();
            if (t) return t;
        }
        return '';
        """
    ) or ""


def _clear_developer_filter(driver, debug: bool = False) -> bool:
    """Очищает фильтр «Девелопер» (multi-select).

    Алгоритм (с debug-выводом если debug=True):
      1) Logging: сколько крестиков на странице
      2) Способ A: открыть попап → клик «Все девелоперы» (надёжнее)
      3) Способ B (fallback): клик на крестик ✕ через mousedown+click
      4) Verifies: после очистки в фильтре «Все девелоперы» или пусто
    """
    current = _get_developer_filter_label(driver) or ""
    if debug:
        print(f"          🔍 clear: текущий фильтр = «{current}»")
    if not current or "все" in current.lower():
        if debug:
            print("          🔍 clear: уже пусто — пропускаем")
        return True

    # === СПОСОБ A: открыть попап + клик «Все девелоперы» ===
    # Это наиболее надёжный сброс multi-select.
    trigger = driver.execute_script(
        """
        const p = [...document.querySelectorAll('p')].find(e =>
            e.offsetParent !== null &&
            /^девелопер$/i.test((e.innerText || '').trim()));
        if (!p || !p.nextElementSibling) return null;
        return p.nextElementSibling.querySelector('[tabindex="0"]');
        """
    )
    if trigger:
        try:
            driver.execute_script("arguments[0].click();", trigger)
            if debug:
                print("          🔍 clear: открыли попап")
            WebDriverWait(driver, 5).until(
                lambda d: d.execute_script(
                    "return !!document.querySelector('input[placeholder=\"Поиск по названию\"]');"
                )
            )
            time.sleep(0.3)
            # Очищаем поле поиска чтобы «Все девелоперы» был виден
            driver.execute_script(
                """
                const inp = document.querySelector('input[placeholder="Поиск по названию"]');
                if (!inp) return;
                const setter = Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype, 'value').set;
                setter.call(inp, '');
                inp.dispatchEvent(new Event('input', {bubbles: true}));
                inp.dispatchEvent(new Event('change', {bubbles: true}));
                """
            )
            time.sleep(0.5)
            # Скроллим список наверх (там «Все девелоперы» обычно первым)
            driver.execute_script(
                """
                const lists = document.querySelectorAll('div.list');
                for (const l of lists) {
                    if (l.offsetParent !== null) l.scrollTop = 0;
                }
                """
            )
            time.sleep(0.3)
            # Клик «Все девелоперы»
            result_a = driver.execute_script(
                """
                const spans = [...document.querySelectorAll(
                    'span.css-1ogbkfc, span.css-pqw2m5')];
                const target = spans.find(s =>
                    /^все\\s+девелоперы/i.test((s.innerText || '').trim()));
                if (!target) return 'not-found';
                const row = target.parentElement || target;
                row.scrollIntoView({block: 'center'});
                row.click();
                return 'clicked';
                """
            )
            if debug:
                print(f"          🔍 clear: клик «Все девелоперы» → {result_a}")
            time.sleep(0.4)
            # Закрываем попап
            try:
                driver.execute_script("document.body.click();")
            except WebDriverException:
                pass
            time.sleep(0.8)
            new_label = _get_developer_filter_label(driver) or ""
            if debug:
                print(f"          🔍 clear: после A фильтр = «{new_label}»")
            if not new_label or "все" in new_label.lower():
                return True
        except (TimeoutException, WebDriverException) as exc:
            if debug:
                print(f"          🔍 clear: способ A error: {exc}")

    # === СПОСОБ B: клик по svg.css-1k2i6yk ===
    # ВНИМАНИЕ из HTML пользователя:
    #   pill > div.css-11s03ds > div.css-1thn7xl > svg.css-1k2i6yk (крестик)
    #                                            > svg.css-1fv53l9 (стрелка)
    # Крестик и стрелка в ОДНОМ родителе → клик по родителю откроет
    # dropdown (как стрелка). Поэтому событие отправляем ТОЛЬКО на
    # сам SVG крестика (или его иммедиатного span-обёртки если есть).
    for attempt in range(5):
        clicked_info = driver.execute_script(
            """
            const p = [...document.querySelectorAll('p')].find(e =>
                e.offsetParent !== null &&
                /^девелопер$/i.test((e.innerText || '').trim()));
            if (!p || !p.nextElementSibling) return {ok:false, reason:'no-pill'};
            const pill = p.nextElementSibling;
            let xSvg = pill.querySelector('svg.css-1k2i6yk');
            if (!xSvg) {
                xSvg = [...pill.querySelectorAll('svg')]
                    .find(s => (s.getAttribute('viewBox') || '') === '0 0 12 12');
            }
            if (!xSvg) return {ok:false, reason:'no-svg'};

            const rect = xSvg.getBoundingClientRect();
            const x = rect.left + rect.width/2;
            const y = rect.top + rect.height/2;
            const init = {bubbles:true, cancelable:true, view:window,
                          clientX:x, clientY:y, button:0,
                          composed: true, pointerType:'mouse'};

            // Полный цикл: pointer + mouse events. React ловит pointerdown
            // и onClick. Отправляем на svg И на родителя (но НЕ на
            // div.css-1thn7xl — это контейнер обоих SVG, клик откроет dropdown).
            const targets = [xSvg];
            // path внутри svg — реальный pixel-hit таргет
            const path = xSvg.querySelector('path');
            if (path) targets.unshift(path);

            for (const el of targets) {
                try {
                    el.dispatchEvent(new PointerEvent('pointerdown', init));
                    el.dispatchEvent(new MouseEvent('mousedown', init));
                    el.dispatchEvent(new PointerEvent('pointerup', init));
                    el.dispatchEvent(new MouseEvent('mouseup', init));
                    el.dispatchEvent(new MouseEvent('click', init));
                } catch(e) {}
            }
            return {ok:true, targets: targets.length,
                    rect: {x:rect.left, y:rect.top, w:rect.width, h:rect.height}};
            """
        )
        if debug:
            print(f"          🔍 clear B попытка {attempt+1}: {clicked_info}")
        if not clicked_info or not clicked_info.get("ok"):
            break
        time.sleep(0.7)
        new_label = _get_developer_filter_label(driver) or ""
        if debug:
            print(f"          🔍 clear: после B[{attempt+1}] фильтр = «{new_label}»")
        if not new_label or "все" in new_label.lower():
            return True

    # === СПОСОБ C: настоящий клик через ActionChains (физический) ===
    # Если события не сработали — кликаем мышью по координатам крестика.
    try:
        from selenium.webdriver.common.action_chains import ActionChains
        crosshair = driver.execute_script(
            """
            const p = [...document.querySelectorAll('p')].find(e =>
                e.offsetParent !== null &&
                /^девелопер$/i.test((e.innerText || '').trim()));
            if (!p || !p.nextElementSibling) return null;
            const pill = p.nextElementSibling;
            return pill.querySelector('svg.css-1k2i6yk') ||
                   [...pill.querySelectorAll('svg')]
                       .find(s => (s.getAttribute('viewBox') || '') === '0 0 12 12');
            """
        )
        if crosshair:
            if debug:
                print("          🔍 clear C: ActionChains физический клик")
            driver.execute_script(
                "arguments[0].scrollIntoView({block:'center'});", crosshair)
            time.sleep(0.3)
            ActionChains(driver).move_to_element(crosshair).pause(0.2).click().perform()
            time.sleep(0.8)
            new_label = _get_developer_filter_label(driver) or ""
            if debug:
                print(f"          🔍 clear: после C фильтр = «{new_label}»")
            if not new_label or "все" in new_label.lower():
                return True
    except Exception as exc:  # noqa: BLE001
        if debug:
            print(f"          🔍 clear C error: {exc}")

    return False


def _switch_developer_filter(driver, target_label: str, *,
                             wait_change: bool = True,
                             timeout: int = 15,
                             debug: bool = False) -> bool:
    """Переключает фильтр «Девелопер» на target_label.

    КРИТИЧНО (multi-select): перед выбором нового сбрасываем
    предыдущий через _clear_developer_filter — иначе шапка
    показывает сумму всех выбранных девелоперов.

    target_label ищется как:
      1) точное совпадение текста span
      2) если не нашли — первое совпадение по подстроке
    """
    # === КРИТИЧНО: сначала сброс фильтра, ВСЕГДА ===
    if target_label and "все" not in target_label.lower():
        cleared = _clear_developer_filter(driver, debug=debug)
        if not cleared:
            if debug:
                print(f"          ⚠️  не смог сбросить фильтр перед выбором «{target_label}»")
            # Не выбираем — иначе будет суммирование
            return False
    baseline = _get_all_apartments_count(driver) if wait_change else ""

    # 1) Клик на триггер
    trigger = driver.execute_script(
        """
        const p = [...document.querySelectorAll('p')].find(e =>
            e.offsetParent !== null &&
            /^девелопер$/i.test((e.innerText || '').trim()));
        if (!p || !p.nextElementSibling) return null;
        return p.nextElementSibling.querySelector('[tabindex="0"]');
        """
    )
    if not trigger:
        return False
    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", trigger)
        time.sleep(0.2)
        driver.execute_script("arguments[0].click();", trigger)
    except WebDriverException:
        return False

    # 2) Ждём search-input
    try:
        WebDriverWait(driver, 6).until(
            lambda d: d.execute_script(
                "return !!document.querySelector('input[placeholder=\"Поиск по названию\"]');"
            )
        )
    except TimeoutException:
        return False
    time.sleep(0.3)

    # 3) Вводим запрос (для уже длинного списка — без запроса попап тормозит)
    driver.execute_script(
        """
        const q = arguments[0];
        const inp = document.querySelector('input[placeholder="Поиск по названию"]');
        if (!inp) return false;
        const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
        setter.call(inp, q);
        inp.dispatchEvent(new Event('input', {bubbles: true}));
        inp.dispatchEvent(new Event('change', {bubbles: true}));
        return true;
        """,
        target_label,
    )
    time.sleep(0.8)

    # 4) Кликаем по совпадению (сначала точное, потом подстрока — case-insensitive)
    clicked = driver.execute_script(
        """
        const target = (arguments[0] || '').trim().toLowerCase();
        if (!target) return false;
        const spans = [...document.querySelectorAll(
            'span.css-1ogbkfc, span.css-pqw2m5')];
        let match = spans.find(s =>
            (s.innerText || '').trim().toLowerCase() === target);
        if (!match) {
            match = spans.find(s => {
                const t = (s.innerText || '').trim().toLowerCase();
                return t && t.includes(target);
            });
        }
        if (!match) return false;
        const row = match.parentElement || match;
        row.scrollIntoView({block: 'center'});
        row.click();
        return true;
        """,
        target_label,
    )
    if not clicked:
        try:
            driver.execute_script("document.body.click();")
        except WebDriverException:
            pass
        return False

    # 5) Закрыть попап
    time.sleep(0.4)
    try:
        driver.execute_script("document.body.click();")
    except WebDriverException:
        pass

    if not wait_change:
        return True

    # 6) Ждём 2 вещи параллельно:
    #    (а) фильтр «Девелопер» показал нужное имя
    #    (б) число «Все квартиры» сменилось с baseline
    target_lower = (target_label or "").lower()[:25]
    try:
        WebDriverWait(driver, timeout).until(
            lambda d: target_lower in (_get_developer_filter_label(d) or "").lower()
        )
    except TimeoutException:
        actual_lbl = _get_developer_filter_label(driver)
        print(f"       ⚠️  фильтр девелопера не сменился (видим «{actual_lbl}»)")
        return False
    try:
        WebDriverWait(driver, timeout).until(
            lambda d: (
                _get_all_apartments_count(d) != baseline
                and _get_all_apartments_count(d) != ""
            )
        )
    except TimeoutException:
        # Может быть редкий случай когда у двух девелоперов одинаковое
        # число «всех квартир» — не повод для ошибки. Просто ждём
        # дополнительную секунду для React-ререндера.
        pass
    # ВАЖНО: даже после смены DOM React может ещё мигнуть/перерисовать.
    # Дополнительная пауза позволяет данным «успокоиться».
    time.sleep(1.2)
    return True


def _parse_apartments_live(driver, *, debug: bool = False) -> dict:
    """Парсит шапку «Все квартиры / 1 / 2 / 3 / 4+» в основном блоке.

    КРИТИЧНО: на странице наш.дом.рф/квартирография может быть
    несколько групп кнопок `[data-rooms]` (в фильтре сверху и в
    основной шапке). Раньше код искал ПО ВСЕМУ document и брал не
    тот блок — отсюда числа в 4 раза больше реальных.

    Теперь:
    1) Находим контейнер: поднимаемся от первой `[data-rooms]` пока
       внутри не окажется 4+ таких кнопок И кнопка «Все квартиры».
    2) Парсим ТОЛЬКО кнопки внутри этого контейнера.

    debug=True печатает детали для отладки селекторов.
    """
    raw = driver.execute_script(
        """
        const dbg = arguments[0];
        const debugInfo = {};

        // Все кандидаты в основной блок — это группы где рядом
        // есть и кнопка «Все квартиры», и кнопки [data-rooms].
        const allRoomBtns = [...document.querySelectorAll('button[data-rooms]')];
        debugInfo.totalRoomButtons = allRoomBtns.length;
        if (allRoomBtns.length === 0) return {error: 'no data-rooms buttons', debug: debugInfo};

        // Группируем по общему предку: для каждой кнопки идём вверх
        // пока не найдём контейнер с 4+ кнопками И «Все квартиры».
        function findContainer(btn) {
            let el = btn.parentElement;
            for (let i = 0; i < 8 && el; i++) {
                const roomBtns = el.querySelectorAll('button[data-rooms]');
                const allBtn = [...el.querySelectorAll('button')]
                    .find(b => /Все квартиры/.test(b.innerText || ''));
                if (roomBtns.length >= 4 && allBtn) return el;
                el = el.parentElement;
            }
            return null;
        }

        // Берём контейнер для ПЕРВОЙ data-rooms кнопки (обычно это
        // основной блок — фильтры идут после в DOM, но мы ищем по
        // структуре, не по позиции).
        const containers = new Set();
        for (const b of allRoomBtns) {
            const c = findContainer(b);
            if (c) containers.add(c);
        }
        debugInfo.containersFound = containers.size;
        if (containers.size === 0) {
            return {error: 'no valid container', debug: debugInfo};
        }

        // Выбираем контейнер: тот в котором кнопки имеют наибольшие
        // КОНКРЕТНЫЕ числа в divs[1] (фильтр-блок обычно показывает
        // лейблы типа «1 комн» без чисел; основной блок — числа).
        let bestContainer = null;
        let bestScore = -1;
        for (const c of containers) {
            const roomBtns = c.querySelectorAll('button[data-rooms]');
            let score = 0;
            for (const b of roomBtns) {
                const divs = b.querySelectorAll(':scope > div');
                if (divs.length >= 2) {
                    const txt = (divs[1].innerText || '').trim();
                    // Считаем «цифровым» если есть цифра
                    if (/\\d/.test(txt)) score += 1;
                }
            }
            if (score > bestScore) {
                bestScore = score;
                bestContainer = c;
            }
        }
        debugInfo.bestScore = bestScore;
        if (!bestContainer) {
            return {error: 'no container with numbers', debug: debugInfo};
        }

        const result = {};
        // Все квартиры
        const allBtn = [...bestContainer.querySelectorAll('button')]
            .find(b => /Все квартиры/.test(b.innerText || ''));
        if (allBtn) {
            const divs = allBtn.querySelectorAll(':scope > div');
            if (divs.length >= 3) {
                result.all = {
                    count: (divs[1].innerText || '').trim(),
                    area: (divs[2].innerText || '').trim()
                };
            }
        }
        // По типам комнат — ТОЛЬКО внутри найденного контейнера
        for (const b of bestContainer.querySelectorAll('button[data-rooms]')) {
            const room = b.getAttribute('data-rooms');
            const divs = b.querySelectorAll(':scope > div');
            if (divs.length < 3) continue;
            result[room] = {
                count: (divs[1].innerText || '').trim(),
                area: (divs[2].innerText || '').trim()
            };
        }
        if (dbg) result._debug = debugInfo;
        return result;
        """,
        debug,
    ) or {}
    if debug and raw:
        print(f"          🔍 _parse_apartments_live debug: {raw.get('_debug')}")
    return raw


def _parse_num_apartments(text: str) -> float:
    """«42 100» → 42100; «42,1 тыс. шт» → 42100; «< 1» → 1."""
    if not text:
        return 0.0
    s = str(text).replace("\xa0", " ").strip()
    is_th = "тыс" in s.lower()
    # Берём первую числовую группу: «42,1», «1 914», «42 100», «<1»
    m = re.search(r"([<>]?\s*\d[\d\s]*(?:[.,]\d+)?)", s)
    if not m:
        return 0.0
    num_str = m.group(1).replace(" ", "").replace(",", ".").lstrip("<>").strip()
    if not num_str:
        return 0.0
    try:
        v = float(num_str)
        return v * 1000 if is_th else v
    except ValueError:
        return 0.0


def _normalize_dev_name_for_lookup(name: str) -> str:
    """Простая нормализация для сопоставления имён девелоперов между
    monitoring и developers-листом квартирографии (lowercase + убрать
    спец.символы и юр.префиксы). Сравнение нужно для верификации
    результата per-dev парсинга против ожидаемого агрегата."""
    if not name:
        return ""
    s = str(name).strip().lower()
    # Убираем регион после запятой («ПИК, г.Москва» → «пик»)
    if "," in s:
        s = s.split(",", 1)[0].strip()
    s = s.replace("«", "").replace("»", "").replace('"', "")
    for p in ["группа компаний ", "гк ", "ао ", "пао ", "ооо ",
              "холдинг ", "спецзастройщик ", "сз "]:
        if s.startswith(p):
            s = s[len(p):]
            break
    return " ".join(s.split())


def _load_monitoring_devs() -> list[str]:
    """Свежий список ГК из monitoring_2_0_*.xlsx (объединение Реестр РВ + ОКС).

    Используется как справочник для mapping (site_name → monitoring_name)
    в per-dev обходе kvartirografia. По обходу мы НЕ итерируем — нам
    нужно только подобрать matching для каждого имени с сайта.
    """
    files = sorted(DOWNLOAD_DIR.glob("monitoring_2_0_*.xlsx"))
    if not files:
        return []
    latest = max(files, key=lambda p: p.stat().st_mtime)
    devs: set[str] = set()
    try:
        import openpyxl
        wb = openpyxl.load_workbook(latest, read_only=True, data_only=True)
        for sheet_name in ["Реестр РВ", "Реестр ОКС"]:
            if sheet_name not in wb.sheetnames:
                continue
            ws = wb[sheet_name]
            header_row = next(ws.iter_rows(min_row=1, max_row=1, values_only=True), None)
            if not header_row:
                continue
            try:
                col_idx = list(header_row).index("Группа компаний")
            except ValueError:
                continue
            for row in ws.iter_rows(min_row=2, values_only=True):
                v = row[col_idx] if col_idx < len(row) else None
                if not isinstance(v, str):
                    continue
                s = v.strip().strip('"').strip("'")
                if not s or s.startswith("#") or s.startswith("="):
                    continue
                if s.isdigit() or len(s) < 2:
                    continue
                devs.add(s)
        wb.close()
    except Exception as exc:  # noqa: BLE001
        print(f"     ⚠️  не удалось прочитать monitoring devs: {exc}")
        return []
    return sorted(devs)


def _load_dev_overrides() -> dict[str, str]:
    """Читает ручные overrides для mapping site_name → monitoring_name.

    Формат файла data/raw/realty/nashdom/dev_name_overrides.json:
        {
          "<site_key>": "<точное имя из monitoring>",
          ...
        }
    site_key — это normalize_developer_name(site_name).

    Используется чтобы вручную перебить ошибки автоматического матчинга
    (например конфликты или неправильные совпадения по нормализованному
    ключу). Если файла нет — пустой dict.
    """
    path = DOWNLOAD_DIR / "dev_name_overrides.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        return {str(k): str(v) for k, v in data.items() if v}
    except Exception as exc:  # noqa: BLE001
        print(f"     ⚠️  не удалось прочитать dev_name_overrides.json: {exc}")
        return {}


def _build_dev_mapping(site_devs: list[str],
                       monitoring_devs: list[str],
                       overrides: dict[str, str]) -> list[dict]:
    """Сопоставляет имена с сайта (developers) с именами из monitoring.

    Возвращает список dict-записей по одному на каждое site_name.
    Каждая запись:
      {
        "site_name":       <строка с сайта>,
        "site_key":        <normalize_developer_name(site_name)>,
        "monitoring_name": <строка из monitoring> | None,
        "monitoring_key":  <normalize_developer_name(monitoring_name)> | None,
        "match_type":      "override" | "exact" | "conflict_first" | "none",
        "candidates":      [<все monitoring-имена с тем же ключом>],
      }
    """
    by_key: dict[str, list[str]] = {}
    for m in monitoring_devs:
        k = normalize_developer_name(m)
        if not k:
            continue
        by_key.setdefault(k, []).append(m)
    for k in by_key:
        by_key[k].sort()

    mapping: list[dict] = []
    for site_name in site_devs:
        site_key = normalize_developer_name(site_name)
        if site_key in overrides:
            mon = overrides[site_key]
            mapping.append({
                "site_name": site_name,
                "site_key": site_key,
                "monitoring_name": mon,
                "monitoring_key": normalize_developer_name(mon),
                "match_type": "override",
                "candidates": by_key.get(site_key, []),
            })
            continue
        candidates = by_key.get(site_key, [])
        if len(candidates) == 1:
            mon = candidates[0]
            mapping.append({
                "site_name": site_name,
                "site_key": site_key,
                "monitoring_name": mon,
                "monitoring_key": site_key,
                "match_type": "exact",
                "candidates": candidates,
            })
        elif len(candidates) > 1:
            mon = candidates[0]
            mapping.append({
                "site_name": site_name,
                "site_key": site_key,
                "monitoring_name": mon,
                "monitoring_key": site_key,
                "match_type": "conflict_first",
                "candidates": candidates,
            })
        else:
            mapping.append({
                "site_name": site_name,
                "site_key": site_key,
                "monitoring_name": None,
                "monitoring_key": None,
                "match_type": "none",
                "candidates": [],
            })
    return mapping


# Per-dev обход ВКЛЮЧЁН по умолчанию.
# Баг «числа в 4 раза больше правды» был исправлен серией коммитов
# 2606fea/3853732/8704ed2/108445f/6ad9fd5: добавлен надёжный сброс
# multi-select фильтра «Девелопер» через крестик svg.css-1k2i6yk.
# Без сброса при переключении девелопера в DOM оставались несколько
# выбранных одновременно → числа суммировались. Теперь
# _clear_developer_filter() вызывается перед каждым переключением.
# Управление: KVART_PER_DEV=0 — выключить. KVART_PER_DEV_LIMIT=N —
# обработать только первые N (для отладки).
def _per_dev_enabled() -> bool:
    raw = os.environ.get("KVART_PER_DEV", "1").strip().lower()
    return raw in ("1", "true", "yes")


def _per_dev_limit() -> int | None:
    raw = os.environ.get("KVART_PER_DEV_LIMIT", "").strip()
    if not raw:
        return None
    try:
        n = int(raw)
        return n if n > 0 else None
    except ValueError:
        return None


def _per_dev_limit_for_region(region_key: str) -> int | None:
    """Per-region лимит per-dev обхода.

    - Москва (msk): без лимита (список ~200-300, проходится за 20-30 мин).
    - РФ (rf): топ-200 по умолчанию (список 1678, без лимита — ~3 часа).
      Список отсортирован сайтом по убыванию объёма строительства,
      то есть «первые 200» = топ-200 крупнейших застройщиков РФ.

    Override через env: KVART_PER_DEV_LIMIT_RF=N (0 чтобы выключить).
    """
    if region_key == "rf":
        raw = os.environ.get("KVART_PER_DEV_LIMIT_RF", "200").strip()
        try:
            n = int(raw)
            return n if n > 0 else None
        except ValueError:
            return 200
    return None


def _parse_kvartirografia(html: str, url: str) -> dict:
    """Парсер квартирографии (наш.дом.рф/аналитика/квартирография).

    Извлекает:
    - report_date из «Отчёт по данным на 3 июня 2026 года»
    - region из фильтра «Федеральный округ / Регион»
    - apartments — кнопки data-rooms + кнопка «Все квартиры»
    - distribution — гистограмма «Распределение квартир по площади» (data-id)
    - developers — «Объём строительства по девелоперам» (button.css-5nggi1
      после h2 «по девелоперам»)
    - regions — «Объём строительства по регионам» (button.css-5nggi1
      после h2 «по регионам»)
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return {}
    soup = BeautifulSoup(html, "lxml")

    out: dict = {
        "report_date": "",
        "region": "",
        "apartments": [],
        "distribution": [],
        "developers": [],
        "regions": [],
        "url": url,
    }

    page_text = soup.get_text(" ", strip=True)
    out["report_date"] = _parse_russian_date(page_text)

    # Регион из активного фильтра
    region_p = soup.find("p", string=re.compile(r"Федеральный округ\s*/\s*Регион"))
    if region_p and region_p.parent:
        span = region_p.parent.find("span")
        if span:
            out["region"] = span.get_text(strip=True)
    if not out["region"] and "Российская Федерация" in page_text:
        out["region"] = "Российская Федерация"

    # apartments (типы квартир)
    type_btns = soup.find_all("button", attrs={"data-rooms": True})
    if type_btns:
        for b in type_btns[0].parent.find_all("button"):
            if "Все квартиры" in b.get_text():
                divs = b.find_all("div", recursive=False)
                texts = [d.get_text(strip=True) for d in divs]
                if len(texts) >= 3:
                    out["apartments"].append({
                        "тип": "Все квартиры",
                        "количество_шт": texts[1].replace("\xa0", " "),
                        "площадь_тыс_м²": texts[2].replace("\xa0", " "),
                    })
                break
    for b in type_btns:
        label = {
            "ONE": "1 комнатные",
            "TWO": "2 комнатные",
            "THREE": "3 комнатные",
            "FOUR": "4+ комнатные",
        }.get(b.get("data-rooms"), b.get("data-rooms"))
        divs = b.find_all("div", recursive=False)
        texts = [d.get_text(strip=True) for d in divs]
        if len(texts) >= 3:
            out["apartments"].append({
                "тип": label,
                "количество_шт": texts[1].replace("\xa0", " "),
                "площадь_тыс_м²": texts[2].replace("\xa0", " "),
            })

    # distribution (гистограмма)
    seen_ids = set()
    for b in soup.find_all("button", attrs={"data-id": True}):
        did = b.get("data-id", "")
        if did in seen_ids:
            continue
        mm = re.match(r"FROM_(\d+)_TO_(\d+)", did)
        if mm:
            label = f"{mm.group(1)}-{mm.group(2)} м²"
        else:
            mm = re.match(r"FROM_(\d+)$", did) or re.match(r"MORE_(\d+)", did)
            label = f"более {mm.group(1)} м²" if mm else did
        first_span = b.find("span")
        percent = first_span.get_text(strip=True) if first_span else ""
        if percent:
            out["distribution"].append({"диапазон": label, "доля": percent})
            seen_ids.add(did)

    # developers + regions — определяем секцию по ближайшему предыдущему h2
    for b in soup.find_all("button", class_="css-5nggi1"):
        divs = b.find_all("div", recursive=False)
        texts = [d.get_text(strip=True) for d in divs]
        if len(texts) < 3:
            continue
        prev_h2 = b.find_previous("h2")
        h2_text = prev_h2.get_text(strip=True) if prev_h2 else ""
        row = {
            "наименование": texts[0],
            "площадь_тыс_м²": texts[1].replace("\xa0", " "),
            "количество_шт": texts[2].replace("\xa0", " "),
        }
        if "по девелоперам" in h2_text:
            out["developers"].append(row)
        elif "по регионам" in h2_text:
            out["regions"].append(row)

    return out



def _parse_rasprodannost(html: str, url: str) -> dict:
    """Парсер распроданности (наш.дом.рф/аналитика/распроданность-стройготовность).

    На странице 6 настоящих <table>:
    1. Федеральные округа
    2. Регионы
    3. Девелоперы
    4. Объём строительства девелоперов (диапазоны: до 10 / 10-50 / 50-500 / 500-1000 / 1000+ тыс.кв.м)
    5. Населённые пункты по численности (диапазоны: до 50 / 50-100 / 100-500 / 500-1000 / 1000+ тыс.чел)
    6. Класс недвижимости (Типовой / Стандарт / Комфорт / Бизнес / Элитный)

    Каждая таблица: name | Объём жил.строительства | Распроданность% | Стройготовность% | Отношение%

    Топовые KPI (4 метрики): Объём жилищного строительства / Распроданность /
    Отношение Р-к-С / Стройготовность — значения берутся из h5+number.
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return {}
    soup = BeautifulSoup(html, "lxml")

    out: dict = {
        "report_date": "",
        "report_period": "",
        "region": "",
        "url": url,
        "kpi": [],
        "tables": {},   # section_name → list of rows
    }
    page_text = soup.get_text(" ", strip=True)
    out["report_date"] = _parse_russian_date(page_text)
    # «Отчетный период Апрель 2026»
    m_period = re.search(r"Отчетный период\s+([А-Яа-я]+ \d{4})", page_text)
    if m_period:
        out["report_period"] = m_period.group(1)
    if "Российская Федерация" in page_text:
        out["region"] = "Российская Федерация"

    # === Топовые 4 KPI: h5-заголовок + общее значение + разбивка по годам ===
    kpi_substrings = [
        "Объем жилищного строительства",
        "Распроданность",
        "Отношение",
        "Стройготовность",
    ]
    # Собираем h5 и их позиции для определения границ KPI-секции
    all_h5 = soup.find_all("h5")
    for i, h5 in enumerate(all_h5):
        title = h5.get_text(strip=True).replace("\xa0", " ")
        if not any(t in title for t in kpi_substrings):
            continue
        # Ищем родительский контейнер KPI и извлекаем общее значение
        wrapper = h5.find_parent()
        if not wrapper:
            continue
        # Поднимаемся до контейнера KPI-блока (содержит и Number и SVG)
        for _ in range(4):
            if wrapper and wrapper.find("svg"):
                break
            wrapper = wrapper.parent
        if not wrapper:
            continue

        num_p = wrapper.find("p", class_=re.compile(r"styles__Number"))
        unit_p = wrapper.find("p", class_=re.compile(r"styles__SquareMeters"))
        kpi_entry = {
            "название": title,
            "значение": num_p.get_text(strip=True).replace("\xa0", " ") if num_p else "",
            "единица": unit_p.get_text(strip=True) if unit_p else "",
            "по_годам": {},
        }

        # Разбивка по годам: <tspan> внутри SVG
        # Сначала идут годы (2026, 2027, ..., 2031+), потом значения
        tspans = [t.get_text(strip=True).replace("\xa0", " ") for t in wrapper.find_all("tspan")]
        years = [t for t in tspans if re.match(r"^20\d{2}\+?$", t)]
        # Уникальные годы в порядке появления
        seen_y = []
        for y in years:
            if y not in seen_y:
                seen_y.append(y)
        # Значения — tspan-ы не похожие на год
        values = [t for t in tspans if not re.match(r"^20\d{2}\+?$", t)]
        # Сопоставляем по позиции (первые N значений = первые N годам)
        for j, y in enumerate(seen_y):
            if j < len(values):
                kpi_entry["по_годам"][y] = values[j]

        out["kpi"].append(kpi_entry)

    # === Таблицы: 6 штук с одинаковыми колонками, секцию определяем по первой
    # строке (название первой строки уникально для каждой таблицы) ===
    section_by_first_cell = {
        "Центральный ФО": "Федеральные округа",
        "Северо-Западный ФО": "Федеральные округа",
        "Город Москва": "Регионы",
        "Краснодарский край": "Регионы",
        "Самолет": "Девелоперы",
        "ПИК": "Девелоперы",
        "до 10": "Объём строительства девелоперов",
        "до 50 тыс.": "Населённые пункты по численности",
        "Типовой": "Класс недвижимости",
        "Стандарт": "Класс недвижимости",
    }

    tables = soup.find_all("table")
    for t in tables:
        rows = t.find_all("tr")
        if len(rows) < 2:
            continue
        # Шапка — th
        headers = [th.get_text(" ", strip=True).replace("\xa0", " ") for th in rows[0].find_all("th")]
        # Данные
        data_rows = []
        first_cell = ""
        for tr in rows[1:]:
            tds = [td.get_text(strip=True).replace("\xa0", " ") for td in tr.find_all("td")]
            if not tds:
                continue
            if not first_cell:
                first_cell = tds[0]
            row = {"наименование": tds[0]}
            # headers[0] обычно пустой, остальные — метрики
            for i, v in enumerate(tds[1:], 1):
                col_name = headers[i] if i < len(headers) and headers[i] else f"col_{i}"
                row[col_name] = v
            data_rows.append(row)

        # Определить секцию
        section = None
        for prefix, sec in section_by_first_cell.items():
            if first_cell.startswith(prefix):
                section = sec
                break
        if not section:
            section = f"unknown ({first_cell[:30]})"
        out["tables"][section] = data_rows

    return out


PARSERS = {
    "rasprodannost": _parse_rasprodannost,
    "kvartirografia": _parse_kvartirografia,
}


def _scrape_table_source(source_key: str, url: str, state: dict) -> list[Path]:
    """Открывает URL, ждёт контент, парсит, сохраняет JSON."""
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    print(f"  🌐 {source_key}: {url}")

    driver = create_chrome(download_dir=DOWNLOAD_DIR, headless=HEADLESS)
    new_files: list[Path] = []
    try:
        driver.set_page_load_timeout(PAGE_TIMEOUT)
        driver.get(url)
        time.sleep(5)

        # Ждём контент: текст «данным на» или «состоянию на» или
        # появление data-rooms/data-id кнопок. <table> на этих страницах
        # отсутствует — это div-сетка.
        try:
            WebDriverWait(driver, 45).until(
                lambda d: (
                    "данным на" in d.page_source
                    or "состоянию на" in d.page_source
                    or "data-rooms" in d.page_source
                    or "data-id" in d.page_source
                )
            )
        except TimeoutException:
            print(f"  ⚠️  {source_key}: контент не появился за 45 сек")
            _save_debug_snapshot(driver, f"{source_key}_no_content")
            return []

        # Скроллим — для подгрузки ленивых таблиц (девелоперы, регионы)
        _scroll_through_page(driver)
        time.sleep(2)

        report_date = _read_report_date(driver) or ""
        if report_date:
            print(f"     · отчёт на: {report_date}")

        # Сохраняем дамп всегда (для аудита) — первый раз
        snap_tag = f"{source_key}_ok"
        debug_dir = DOWNLOAD_DIR.parent / "_debug"
        debug_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        (debug_dir / f"nashdom_{snap_tag}_{ts}.html").write_text(
            driver.page_source, encoding="utf-8"
        )

        parser = PARSERS.get(source_key)
        if parser is None:
            print(f"  ⚠️  для {source_key} парсер не определён")
            return []
        data = parser(driver.page_source, url)
        data["report_date"] = data.get("report_date") or report_date
        data["scraped_at"] = datetime.now().isoformat(timespec="seconds")
        data["source"] = source_key

        # Эвристика «успешности» — есть ли в data что-то кроме мета
        non_meta_keys = [k for k in data.keys()
                         if k not in {"report_date", "scraped_at", "source", "url", "region", "raw_text_sample"}]
        has_content = any(data.get(k) for k in non_meta_keys)

        date_str = datetime.now().strftime("%Y%m%d")
        target = DOWNLOAD_DIR / f"{source_key}_{date_str}.json"
        target.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        summary = ", ".join(
            f"{k}={len(v) if isinstance(v, list) else 'есть'}"
            for k, v in data.items()
            if k in non_meta_keys and v
        ) or "пусто"
        print(f"  {'✅' if has_content else '⚠️ '} {target.name} ({summary})")
        new_files.append(target)
        state[source_key] = {
            "report_date": data.get("report_date", ""),
            "filename": target.name,
            "scraped_at": data["scraped_at"],
            "has_content": has_content,
        }
    except WebDriverException as exc:
        print(f"  ❌ {source_key}: {exc}")
    finally:
        driver.quit()
    return new_files


def _get_rasprod_kpi_value(driver, kpi_substring: str) -> str:
    """Читает текущее значение KPI «Объем жилищного строительства» и т.п.
    Используется для верификации смены региона.
    """
    return driver.execute_script(
        """
        const sub = arguments[0];
        const h5s = [...document.querySelectorAll('h5')];
        const h5 = h5s.find(h => h.offsetParent !== null &&
            (h.innerText || '').replace(/\\u00a0/g, ' ').includes(sub));
        if (!h5) return '';
        let scope = h5.parentElement;
        for (let i = 0; i < 5 && scope; i++) {
            const num = scope.querySelector('p[class*="styles__Number"]');
            if (num) return num.innerText.trim();
            scope = scope.parentElement;
        }
        return '';
        """,
        kpi_substring,
    ) or ""


def _switch_region_rasprodannost(
    driver, target_label: str, search_query: str = ""
) -> bool:
    """Переключает фильтр региона на странице распроданности.

    Использует тот же подход что и для kvartirografia после фикса:
    попап рендерится в position:fixed оверлее, offsetParent === null,
    поэтому ждём НАПРЯМУЮ по input[placeholder="Поиск по названию"]
    и кликаем на родительскую div-строку для надёжного триггера React-handler.

    Верификация — по СМЕНЕ KPI «Объем жилищного строительства».
    """
    baseline = _get_rasprod_kpi_value(driver, "Объем жилищного строительства")
    print(f"       · baseline объём: {baseline}")
    if not baseline:
        print(f"       ⚠️  не нашёл baseline KPI")
        return False

    # 1) Клик на триггер #regionSelect [tabindex=0] — через JS (надёжнее для SPA)
    try:
        opened = driver.execute_script(
            """
            const tr = document.querySelector('#regionSelect [tabindex="0"]');
            if (!tr) return false;
            tr.scrollIntoView({block: 'center'});
            tr.click();
            return true;
            """
        )
        if not opened:
            print(f"       ⚠️  не нашёл триггер #regionSelect")
            return False
        print(f"       · клик на trigger #regionSelect")
    except WebDriverException as exc:
        print(f"       ⚠️  ошибка клика триггера: {exc}")
        return False

    # 2) Ждём search-input ПО PLACEHOLDER (не по offsetParent —
    # попап в fixed-оверлее)
    try:
        WebDriverWait(driver, 10).until(
            lambda d: d.execute_script(
                "return !!document.querySelector('input[placeholder=\"Поиск по названию\"]');"
            )
        )
    except TimeoutException:
        print(f"       ⚠️  search-input попапа не появился")
        _save_debug_snapshot(driver, "rasprod_region_no_popup")
        return False
    time.sleep(0.5)

    # 3) Ввести запрос через native setter + dispatchEvent (для React state)
    if search_query:
        ok = driver.execute_script(
            """
            const q = arguments[0];
            const inp = document.querySelector('input[placeholder="Поиск по названию"]');
            if (!inp) return false;
            const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
            setter.call(inp, q);
            inp.dispatchEvent(new Event('input', {bubbles: true}));
            inp.dispatchEvent(new Event('change', {bubbles: true}));
            return true;
            """,
            search_query,
        )
        print(f"       · ввёл в поиск: '{search_query}' (ok={ok})")
        time.sleep(2)

    # 4) Дождаться появления target_label и кликнуть по div-родителю span
    # (там висит React-handler чекбокса)
    try:
        WebDriverWait(driver, 10).until(
            lambda d: d.execute_script(
                """
                const target = arguments[0];
                return [...document.querySelectorAll('span')]
                    .some(s => (s.innerText || '').trim() === target);
                """,
                target_label,
            )
        )
    except TimeoutException:
        print(f"       ⚠️  пункт «{target_label}» не появился в попапе")
        _save_debug_snapshot(driver, f"rasprod_region_no_option_{target_label}")
        return False

    clicked = driver.execute_script(
        """
        const target = arguments[0];
        const span = [...document.querySelectorAll('span')]
            .find(s => (s.innerText || '').trim() === target);
        if (!span) return false;
        // Кликаем по родителю-row (содержит чекбокс + span).
        let row = span.parentElement;
        if (!row) row = span;
        row.scrollIntoView({block: 'center'});
        row.click();
        return true;
        """,
        target_label,
    )
    print(f"       · клик на «{target_label}» (ok={clicked})")
    if not clicked:
        return False

    # 5) Закрыть попап
    time.sleep(1)
    try:
        driver.execute_script("document.body.click();")
    except WebDriverException:
        pass

    # 6) Верификация: KPI должен смениться
    try:
        WebDriverWait(driver, 25).until(
            lambda d: (
                _get_rasprod_kpi_value(d, "Объем жилищного строительства") != baseline
                and _get_rasprod_kpi_value(d, "Объем жилищного строительства") != ""
            )
        )
        new_val = _get_rasprod_kpi_value(driver, "Объем жилищного строительства")
        print(f"       ✅ данные сменились: {baseline} → {new_val}")
        time.sleep(2)
        return True
    except TimeoutException:
        actual = _get_rasprod_kpi_value(driver, "Объем жилищного строительства")
        print(f"       ⚠️  KPI не сменился (всё ещё «{actual}»)")
        _save_debug_snapshot(driver, "rasprod_region_no_data_change")
        return False


RASPROD_REGIONS = [
    {"key": "rf",  "label": "Все",        "search": "",       "click_label": "Все"},
    {"key": "msk", "label": "Город Москва","search": "Москва", "click_label": "Город Москва"},
]


# ─────────────────────────────────────────────
# Дата-пикер распроданности
# ─────────────────────────────────────────────

RUS_MONTHS_LIST = ["Янв","Фев","Мар","Апр","Май","Июн","Июл","Авг","Сен","Окт","Ноя","Дек"]
RUS_MONTHS_FULL = {
    "Январь":1, "Февраль":2, "Март":3, "Апрель":4, "Май":5, "Июнь":6,
    "Июль":7, "Август":8, "Сентябрь":9, "Октябрь":10, "Ноябрь":11, "Декабрь":12,
}


def _get_rasprod_current_period(driver) -> str:
    """Текущий период из триггера дата-пикера (например «Апрель 2026»)."""
    return driver.execute_script(
        """
        const el = document.querySelector(
            '.styles__SelectWrapper-sc-9f1fzk-2 .styles__Value-sc-9f1fzk-0');
        return el ? el.innerText.trim() : '';
        """
    ) or ""


def _open_date_picker(driver) -> bool:
    """Кликает на триггер дата-пикера. Возвращает True если YearPicker появился."""
    try:
        ok = driver.execute_script(
            """
            const tr = document.querySelector('.styles__SelectWrapper-sc-9f1fzk-2');
            if (!tr) return false;
            tr.click();
            return true;
            """
        )
        if not ok:
            return False
        WebDriverWait(driver, 5).until(
            lambda d: d.execute_script(
                "return !!document.querySelector('.styles__YearPicker-sc-1cm0se8-3');"
            )
        )
        return True
    except (TimeoutException, WebDriverException):
        return False


def _get_calendar_year(driver) -> int:
    """Год в открытом календарном попапе. 0 если попап закрыт."""
    return int(driver.execute_script(
        """
        const el = document.querySelector(
            '.styles__YearPicker-sc-1cm0se8-3 .styles__Value-sc-1cm0se8-2');
        return el ? parseInt(el.innerText) || 0 : 0;
        """
    ) or 0)


def _click_prev_year(driver) -> bool:
    """Клик на ◄ (1-я img в YearPicker)."""
    return bool(driver.execute_script(
        """
        const yp = document.querySelector('.styles__YearPicker-sc-1cm0se8-3');
        if (!yp) return false;
        const imgs = yp.querySelectorAll('img');
        if (!imgs.length) return false;
        imgs[0].click();
        return true;
        """
    ))


def _click_next_year(driver) -> bool:
    """Клик на ► (2-я img в YearPicker). Если справа disabled — клик ничего не сделает."""
    return bool(driver.execute_script(
        """
        const yp = document.querySelector('.styles__YearPicker-sc-1cm0se8-3');
        if (!yp) return false;
        const imgs = yp.querySelectorAll('img');
        if (imgs.length < 2) return false;
        imgs[1].click();
        return true;
        """
    ))


def _navigate_to_latest_year(driver, max_clicks: int = 15) -> int:
    """Кликает ► пока год не перестанет расти. Возвращает год."""
    for _ in range(max_clicks):
        current = _get_calendar_year(driver)
        if current == 0:
            return 0
        if not _click_next_year(driver):
            return current
        time.sleep(0.4)
        new_year = _get_calendar_year(driver)
        if new_year == current:
            return current
    return _get_calendar_year(driver)


def _navigate_calendar_to_year(driver, target_year: int, max_clicks: int = 15) -> bool:
    """Навигирует к target_year обеими стрелками ◄/► по необходимости."""
    for _ in range(max_clicks):
        current = _get_calendar_year(driver)
        if current == 0:
            return False
        if current == target_year:
            return True
        if current < target_year:
            if not _click_next_year(driver):
                return False
        else:  # current > target_year
            if not _click_prev_year(driver):
                return False
        time.sleep(0.4)
    return _get_calendar_year(driver) == target_year


def _get_available_month_indices(driver) -> list[int]:
    """Список индексов 0..11 — доступных (не disabled) кнопок месяцев."""
    return driver.execute_script(
        """
        const btns = document.querySelectorAll(
            '.styles__MonthsWrapper-sc-1cm0se8-5 button');
        const out = [];
        btns.forEach((b, i) => { if (!b.disabled) out.push(i); });
        return out;
        """
    ) or []


def _click_month_by_index(driver, idx: int) -> bool:
    """Клик на кнопку месяца по индексу 0..11."""
    return bool(driver.execute_script(
        """
        const idx = arguments[0];
        const btns = document.querySelectorAll(
            '.styles__MonthsWrapper-sc-1cm0se8-5 button');
        if (idx < 0 || idx >= btns.length) return false;
        if (btns[idx].disabled) return false;
        btns[idx].click();
        return true;
        """,
        idx,
    ))


def _switch_period(driver, year: int, month_idx: int) -> bool:
    """Открывает календарь, навигирует к (year, month_idx), кликает месяц,
    ждёт смены периода. Возвращает True если переключение прошло.
    """
    baseline = _get_rasprod_current_period(driver)
    expected_period = f"{['Январь','Февраль','Март','Апрель','Май','Июнь','Июль','Август','Сентябрь','Октябрь','Ноябрь','Декабрь'][month_idx]} {year}"

    if baseline == expected_period:
        return True

    if not _open_date_picker(driver):
        print(f"       ⚠️  не открыл календарь")
        return False
    time.sleep(0.5)

    if not _navigate_calendar_to_year(driver, year):
        print(f"       ⚠️  не довёл год до {year}")
        return False

    avail = _get_available_month_indices(driver)
    if month_idx not in avail:
        print(f"       ⚠️  месяц {month_idx+1}/{year} disabled")
        return False

    if not _click_month_by_index(driver, month_idx):
        print(f"       ⚠️  не кликнул месяц idx={month_idx}")
        return False

    # Ждём смены текста в триггере на ожидаемый период
    try:
        WebDriverWait(driver, 20).until(
            lambda d: _get_rasprod_current_period(d) == expected_period
        )
        time.sleep(2)  # данные дорендериться
        return True
    except TimeoutException:
        actual = _get_rasprod_current_period(driver)
        print(f"       ⚠️  период не сменился: ждали «{expected_period}», текущий «{actual}»")
        return False


def _list_all_periods(driver, year_from: int, year_to: int) -> list[tuple[int, int]]:
    """Обходит года от year_to до year_from, собирает (year, month_idx)
    для активных месяцев. Открывает календарь один раз, перемещается
    стрелкой ◄.
    """
    if not _open_date_picker(driver):
        print(f"     ⚠️  не открыл календарь для перечисления периодов")
        return []
    time.sleep(0.5)

    # КРИТИЧНО: сначала идём в самый свежий доступный год (►), так как
    # календарь может быть «застрял» на старом году после прошлой
    # итерации (например после обхода РФ закончили на 2020).
    latest_year = _navigate_to_latest_year(driver)
    print(f"     · перешли к самому свежему году: {latest_year}")

    periods: list[tuple[int, int]] = []
    cur_year = _get_calendar_year(driver)
    if cur_year == 0:
        return []

    # Идём от текущего года вниз до year_from
    while cur_year >= year_from:
        avail = _get_available_month_indices(driver)
        for m_idx in sorted(avail, reverse=True):  # последние месяцы первыми
            periods.append((cur_year, m_idx))
        if cur_year <= year_from:
            break
        if not _click_prev_year(driver):
            break
        time.sleep(0.4)
        new_year = _get_calendar_year(driver)
        if new_year == cur_year:
            break
        cur_year = new_year

    # Закрыть календарь (клик по body)
    try:
        driver.execute_script("document.body.click();")
    except WebDriverException:
        pass
    time.sleep(0.5)
    return periods


def fetch_rasprodannost(state: dict) -> list[Path]:
    """Распроданность — настоящая <table>-структура. Пишем xlsx с листами:
    kpi (4 метрики верха) + по одному листу на каждую из 6 таблиц.

    Обходит 2 региона (РФ + Москва).
    """
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    url = _build_rasprodannost_url()
    print(f"  🌐 rasprodannost: {url}")
    date_str = datetime.now().strftime("%Y%m%d")
    target_xlsx = DOWNLOAD_DIR / f"rasprodannost_{date_str}.xlsx"
    target_json = DOWNLOAD_DIR / f"rasprodannost_{date_str}.json"

    driver = create_chrome(download_dir=DOWNLOAD_DIR, headless=HEADLESS)
    all_data: list[dict] = []
    new_files: list[Path] = []

    def flush():
        if not all_data:
            return
        target_json.write_text(
            json.dumps(all_data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        try:
            import pandas as pd
        except ImportError:
            return
        sheet_name_map = {
            "Федеральные округа": "fed_okruga",
            "Регионы": "regions",
            "Девелоперы": "developers",
            "Объём строительства девелоперов": "by_dev_volume",
            "Населённые пункты по численности": "by_population",
            "Класс недвижимости": "by_class",
        }
        with pd.ExcelWriter(target_xlsx, engine="openpyxl") as writer:
            # KPI лист: каждая строка = (region × период × KPI) + колонки по годам прогноза
            kpi_rows = []
            for d in all_data:
                meta = {
                    "region_key": d.get("region_key", ""),
                    "year": d.get("year", ""),
                    "month": d.get("month_num", ""),
                    "month_name": d.get("month_name", ""),
                    "report_period": d.get("report_period", ""),
                }
                for k in d.get("kpi", []):
                    row = {**meta,
                           "название": k["название"],
                           "значение": k["значение"],
                           "единица": k["единица"]}
                    for y, v in (k.get("по_годам") or {}).items():
                        row[f"прогноз_{y}"] = v
                    kpi_rows.append(row)
            if kpi_rows:
                pd.DataFrame(kpi_rows).to_excel(writer, sheet_name="kpi", index=False)

            # Объединённые таблицы (region × period × section × row)
            tables_combined: dict[str, list[dict]] = {}
            for d in all_data:
                meta = {
                    "region_key": d.get("region_key", ""),
                    "year": d.get("year", ""),
                    "month": d.get("month_num", ""),
                    "month_name": d.get("month_name", ""),
                    "report_period": d.get("report_period", ""),
                }
                for section, rows in (d.get("tables") or {}).items():
                    for r in rows:
                        tables_combined.setdefault(section, []).append(
                            {**meta, "section": section, **r}
                        )
            for section, rows in tables_combined.items():
                sname = sheet_name_map.get(section, section[:30])
                pd.DataFrame(rows).to_excel(writer, sheet_name=sname, index=False)
        if target_xlsx not in new_files:
            new_files.append(target_xlsx)
        if target_json not in new_files:
            new_files.append(target_json)
        print(f"     💾 {target_xlsx.name} (записей={len(all_data)})")

    try:
        driver.set_page_load_timeout(PAGE_TIMEOUT)
        driver.get(url)
        time.sleep(6)
        try:
            WebDriverWait(driver, 45).until(
                lambda d: "данным на" in d.page_source or "<table" in d.page_source
            )
        except TimeoutException:
            print(f"  ⚠️  rasprodannost: контент не появился за 45 сек")
            _save_debug_snapshot(driver, "rasprodannost_no_content")
            return []
        _scroll_through_page(driver)
        time.sleep(3)

        debug_dir = DOWNLOAD_DIR.parent / "_debug"
        debug_dir.mkdir(parents=True, exist_ok=True)

        YEAR_FROM = 2020  # самый ранний доступный год по данным пользователя

        for region in RASPROD_REGIONS:
            try:
                print(f"     ── регион: {region['key']} ({region['label']})")
                if region["search"]:
                    ok = _switch_region_rasprodannost(
                        driver,
                        target_label=region["click_label"],
                        search_query=region["search"],
                    )
                    if not ok:
                        print(f"       ⚠️  не удалось переключить, пропускаю")
                        continue
                    _scroll_through_page(driver)
                    time.sleep(2)

                # Перечислим все доступные периоды (year, month_idx)
                periods = _list_all_periods(driver, year_from=YEAR_FROM, year_to=2030)
                print(f"       · доступных периодов: {len(periods)}")

                for period_i, (year, m_idx) in enumerate(periods, 1):
                    month_name = ["Январь","Февраль","Март","Апрель","Май","Июнь",
                                  "Июль","Август","Сентябрь","Октябрь","Ноябрь","Декабрь"][m_idx]
                    print(f"       ▸ {period_i}/{len(periods)}: {month_name} {year}")
                    ok = _switch_period(driver, year, m_idx)
                    if not ok:
                        continue
                    _scroll_through_page(driver)
                    time.sleep(1.5)

                    data = _parse_rasprodannost(driver.page_source, driver.current_url)
                    data["region_key"] = region["key"]
                    data["year"] = year
                    data["month_num"] = m_idx + 1
                    data["month_name"] = month_name
                    data["scraped_at"] = datetime.now().isoformat(timespec="seconds")
                    data["source"] = "rasprodannost"
                    print(
                        f"          KPI={len(data.get('kpi') or [])}, "
                        f"tables={sum(len(v) for v in (data.get('tables') or {}).values())} строк"
                    )
                    all_data.append(data)
                    flush()  # incremental save после каждого периода
            except Exception as exc:  # noqa: BLE001
                print(f"     ❌ ошибка {region['key']}: {exc}")
                flush()

        state["rasprodannost"] = {
            "report_period": (all_data[0] if all_data else {}).get("report_period", ""),
            "filename": target_xlsx.name,
            "regions": [r["key"] for r in RASPROD_REGIONS],
            "has_content": bool(all_data),
        }
    except KeyboardInterrupt:
        print("\n  ⚠️  прерывание — сохраняю собранное")
        flush()
        raise
    except WebDriverException as exc:
        print(f"  ❌ rasprodannost: {exc}")
        flush()
    finally:
        try:
            driver.quit()
        except Exception:  # noqa: BLE001
            pass
    return new_files




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
