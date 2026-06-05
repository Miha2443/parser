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

    with pd.ExcelWriter(target_xlsx, engine="openpyxl") as writer:
        if apartments_rows:
            pd.DataFrame(apartments_rows).to_excel(writer, sheet_name="apartments", index=False)
        if distribution_rows:
            pd.DataFrame(distribution_rows).to_excel(writer, sheet_name="distribution", index=False)
        if developers_rows:
            pd.DataFrame(developers_rows).to_excel(writer, sheet_name="developers", index=False)
        if regions_rows:
            pd.DataFrame(regions_rows).to_excel(writer, sheet_name="regions", index=False)


def fetch_kvartirografia(state: dict) -> list[Path]:
    """Квартирография по 2 регионам (РФ + Москва) с inner-scroll.

    xlsx сохраняется ПОСЛЕ КАЖДОГО региона — даже если процесс
    прерван на половине, у пользователя есть актуальный файл.
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
        """Сохраняем текущее накопленное состояние в xlsx + json."""
        if not all_data:
            return
        try:
            _build_kvart_xlsx(all_data, target_xlsx)
            target_json.write_text(
                json.dumps(all_data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            sizes = {
                "apartments": sum(len(d.get("apartments", [])) for d in all_data),
                "distribution": sum(len(d.get("distribution", [])) for d in all_data),
                "developers": sum(len(d.get("developers", [])) for d in all_data),
                "regions": sum(len(d.get("regions", [])) for d in all_data),
            }
            print(f"     💾 {target_xlsx.name}: {sizes}")
            if target_xlsx not in new_files:
                new_files.append(target_xlsx)
            if target_json not in new_files:
                new_files.append(target_json)
        except Exception as exc:  # noqa: BLE001
            print(f"     ⚠️  ошибка при записи xlsx: {exc}")

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

    DOM на rasprodannost ДРУГАЯ (styles__Container-...). id='regionSelect'
    у контейнера. Верификация — по СМЕНЕ KPI «Объем жилищного строительства».
    """
    baseline = _get_rasprod_kpi_value(driver, "Объем жилищного строительства")
    print(f"       · baseline объём: {baseline}")
    if not baseline:
        print(f"       ⚠️  не нашёл baseline KPI")
        return False

    # 1) Клик на триггер #regionSelect [tabindex=0]
    try:
        trigger = driver.find_element(
            By.CSS_SELECTOR, '#regionSelect [tabindex="0"]'
        )
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", trigger)
        time.sleep(0.5)
        trigger.click()
        print(f"       · клик на trigger #regionSelect")
    except WebDriverException as exc:
        print(f"       ⚠️  не нашёл триггер региона: {exc}")
        return False

    # 2) Ждём появления нового ВИДИМОГО input (попап с поиском).
    existing_visible = driver.execute_script(
        "return [...document.querySelectorAll('input')].filter(i => i.offsetParent !== null).length;"
    ) or 0
    try:
        WebDriverWait(driver, 10).until(
            lambda d: d.execute_script(
                "return [...document.querySelectorAll('input')].filter(i => i.offsetParent !== null).length;"
            ) > existing_visible
        )
    except TimeoutException:
        print(f"       ⚠️  попап не появился")
        _save_debug_snapshot(driver, "rasprod_region_no_popup")
        return False

    # 3) Ввести запрос
    inputs = driver.find_elements(
        By.CSS_SELECTOR, 'input[type="text"], input[type="search"], input:not([type])'
    )
    search_input = None
    for inp in reversed(inputs):
        try:
            if inp.is_displayed() and inp.is_enabled():
                search_input = inp
                break
        except WebDriverException:
            continue
    if not search_input:
        print(f"       ⚠️  не нашёл input для поиска")
        return False
    try:
        search_input.click()
        search_input.clear()
        if search_query:
            search_input.send_keys(search_query)
            print(f"       · ввёл в поиск: '{search_query}'")
        time.sleep(2)
    except WebDriverException as exc:
        print(f"       ⚠️  не смог ввести в поиск: {exc}")
        return False

    # 4) Клик по target_label
    try:
        WebDriverWait(driver, 8).until(
            lambda d: d.execute_script(
                """
                const target = arguments[0];
                return [...document.querySelectorAll('div,span,li,button,a')]
                    .some(e => e.offsetParent !== null &&
                        (e.innerText||'').trim() === target);
                """,
                target_label,
            )
        )
    except TimeoutException:
        print(f"       ⚠️  пункт «{target_label}» не появился")
        _save_debug_snapshot(driver, f"rasprod_region_no_option_{target_label}")
        return False

    candidates = driver.find_elements(
        By.XPATH, f"//*[normalize-space(text())='{target_label}']"
    )
    clicked = False
    for el in candidates:
        try:
            if el.is_displayed():
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                time.sleep(0.3)
                el.click()
                clicked = True
                print(f"       · клик на «{target_label}»")
                break
        except WebDriverException:
            continue
    if not clicked:
        print(f"       ⚠️  не смог кликнуть «{target_label}»")
        return False

    try:
        driver.execute_script("document.body.click();")
    except WebDriverException:
        pass

    # 5) Верификация: KPI должен смениться
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
            # KPI лист: широкий формат с region_key и колонками-годами
            kpi_rows = []
            for d in all_data:
                rk = d.get("region_key", "")
                for k in d.get("kpi", []):
                    row = {
                        "region_key": rk,
                        "report_period": d.get("report_period", ""),
                        "название": k["название"],
                        "значение": k["значение"],
                        "единица": k["единица"],
                    }
                    # Колонки по годам: год → значение
                    for y, v in (k.get("по_годам") or {}).items():
                        row[y] = v
                    kpi_rows.append(row)
            if kpi_rows:
                pd.DataFrame(kpi_rows).to_excel(writer, sheet_name="kpi", index=False)

            # Объединённые таблицы (по region_key)
            tables_combined: dict[str, list[dict]] = {}
            for d in all_data:
                rk = d.get("region_key", "")
                for section, rows in (d.get("tables") or {}).items():
                    for r in rows:
                        rr = {
                            "region_key": rk,
                            "section": section,
                            "report_period": d.get("report_period", ""),
                            **r,
                        }
                        tables_combined.setdefault(section, []).append(rr)
            for section, rows in tables_combined.items():
                sname = sheet_name_map.get(section, section[:30])
                pd.DataFrame(rows).to_excel(writer, sheet_name=sname, index=False)
        if target_xlsx not in new_files:
            new_files.append(target_xlsx)
        if target_json not in new_files:
            new_files.append(target_json)
        print(f"     💾 {target_xlsx.name} (regions={len(all_data)})")

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

                # снэпшот текущего региона
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                (debug_dir / f"nashdom_rasprodannost_{region['key']}_{ts}.html").write_text(
                    driver.page_source, encoding="utf-8"
                )

                data = _parse_rasprodannost(driver.page_source, driver.current_url)
                data["region_key"] = region["key"]
                data["scraped_at"] = datetime.now().isoformat(timespec="seconds")
                data["source"] = "rasprodannost"
                print(
                    f"       · KPI={len(data.get('kpi') or [])}, "
                    f"tables={sum(len(v) for v in (data.get('tables') or {}).values())} строк"
                )
                all_data.append(data)
                flush()
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
