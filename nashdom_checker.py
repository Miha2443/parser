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
    """repYear/repMonth — пред-предыдущий месяц.

    Логика: данные за месяц публикуются с задержкой. У пользователя
    «текущий» запрос — апрель 2026 (хотя сегодня июнь). Поэтому берём
    -2 месяца от сегодня.
    """
    today = datetime.now()
    month = today.month - 2
    year = today.year
    if month < 1:
        month += 12
        year -= 1
    path = quote(RASPRODANNOST_PATH)
    return (
        f"{NASHDOM_BASE}/{path}"
        f"?repYear={year}&repMonth={month}&foCd=all&regionCd=all"
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
    {"key": "msk", "label": "г.Москва",             "search": "Москва", "click_label": "г.Москва"},
]


def _color_to_room(bg: str) -> str:
    """Маппит rgb/rgba цвет столбика гистограммы на тип комнат.

    Цветовая схема наш.дом.рф (verified 04.06.2026):
    - rgba(139, 197, 64) = #8BC540 green     → 1 комн
    - rgba(78, 195, 224) = #4EC3E0 blue       → 2 комн
    - rgba(7, 40, 51)    = #072833 dark navy → 3 комн
    - rgb(228, 231, 232) = light gray         → 4+ комн
    """
    if not bg:
        return ""
    if "139, 197, 64" in bg:
        return "1комн"
    if "78, 195, 224" in bg:
        return "2комн"
    if "7, 40, 51" in bg:
        return "3комн"
    if "228, 231, 232" in bg:
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
        new_in_step = 0
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
                new_in_step += 1
        step_num += 1
        if step_num % 20 == 0:
            print(f"         · шаг {step_num}, pos={pos}/{height}, собрано {len(seen)}/{expected}")
        if new_in_step == 0:
            no_progress += 1
            if no_progress >= 3:
                break
        else:
            no_progress = 0
        pos += step_px

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
    """Возвращает текущее значение фильтра «Регион» (текст в span.css-wjcl1w)."""
    return driver.execute_script(
        """
        const p = [...document.querySelectorAll('p')].find(e =>
            e.offsetParent !== null && /федеральный\\s*округ/i.test(e.innerText || ''));
        if (!p) return '';
        let scope = p.parentElement;
        for (let i = 0; i < 4 && scope; i++) {
            const span = scope.querySelector('span.css-wjcl1w');
            if (span) return span.innerText.trim();
            scope = scope.parentElement;
        }
        return '';
        """
    ) or ""


def _switch_region_filter(driver, target_label: str, search_query: str = "") -> bool:
    """Переключает фильтр «Федеральный округ / Регион» на target_label.

    Стратегия:
    1. Запоминаем текущее значение фильтра.
    2. Клик на span с этим значением (нативно через Selenium).
    3. Ждём появления нового input (попап с поиском).
    4. Selenium .send_keys() в input (нативный набор, триггерит React).
    5. WebDriverWait до появления элемента с target_label.
    6. Клик на target_label (нативно).
    7. WebDriverWait пока текущий label фильтра не сменится на target.
    """
    current = _get_current_region_label(driver)
    print(f"       · текущий регион: «{current}»")
    if not current:
        print(f"       ⚠️  не нашёл текущее значение фильтра")
        return False
    if current == target_label:
        return True

    # 1) Запоминаем существующие inputs до клика — чтобы найти новый
    existing_inputs = driver.execute_script(
        "return [...document.querySelectorAll('input')].length;"
    ) or 0

    # 2) Клик на span с текущим регионом (Selenium native click)
    try:
        # Прямой XPath на span с нужным текстом, чтобы взять именно его
        spans = driver.find_elements(
            By.XPATH,
            f"//span[contains(@class,'css-wjcl1w') and normalize-space(text())='{current}']",
        )
        clicked = False
        for span in spans:
            if span.is_displayed():
                # Поднимаемся до tabindex=0 родителя для надёжного клика
                trigger = driver.execute_script(
                    "let el = arguments[0];"
                    "for (let i = 0; i < 4; i++) {"
                    "  if (!el) break;"
                    "  if (el.getAttribute && el.getAttribute('tabindex') === '0') return el;"
                    "  el = el.parentElement;"
                    "}"
                    "return arguments[0];",
                    span,
                )
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", trigger)
                time.sleep(0.5)
                trigger.click()
                clicked = True
                print(f"       · клик на trigger ({trigger.tag_name})")
                break
        if not clicked:
            print(f"       ⚠️  не нашёл span «{current}» для клика")
            return False
    except WebDriverException as exc:
        print(f"       ⚠️  ошибка клика по триггеру: {exc}")
        return False

    # 3) Ждём появления нового input (попап рендерится после клика)
    try:
        WebDriverWait(driver, 8).until(
            lambda d: d.execute_script(
                "return [...document.querySelectorAll('input')].length;"
            ) > existing_inputs
        )
    except TimeoutException:
        print(f"       ⚠️  попап с input не появился")
        _save_debug_snapshot(driver, "region_switch_no_popup")
        return False

    # 4) Найти НОВЫЙ видимый input и ввести запрос
    search_input = None
    candidates = driver.find_elements(By.CSS_SELECTOR, 'input[type="text"], input[type="search"], input:not([type])')
    for inp in reversed(candidates):  # с конца — самые свежие
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
        time.sleep(2)  # фильтр в попапе обновляется
    except WebDriverException as exc:
        print(f"       ⚠️  не смог ввести в поиск: {exc}")
        return False

    # 5) Ждём появления target_label в видимых элементах попапа
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
        print(f"       ⚠️  пункт «{target_label}» не появился в попапе")
        _save_debug_snapshot(driver, f"region_switch_no_option_{target_label}")
        return False

    # 6) Клик на target_label через Selenium (без JS)
    clicked = False
    candidates = driver.find_elements(
        By.XPATH,
        f"//*[normalize-space(text())='{target_label}']",
    )
    for el in candidates:
        try:
            if el.is_displayed():
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                time.sleep(0.3)
                el.click()
                clicked = True
                print(f"       · клик на «{target_label}» ({el.tag_name})")
                break
        except WebDriverException:
            continue
    if not clicked:
        print(f"       ⚠️  не смог кликнуть «{target_label}»")
        return False

    # 7) Ждём пока фильтр действительно сменится на target_label
    try:
        WebDriverWait(driver, 15).until(
            lambda d: _get_current_region_label(d) == target_label
        )
        print(f"       ✅ регион сменился на «{target_label}»")
        time.sleep(3)  # даём данным дорендериться
        return True
    except TimeoutException:
        actual = _get_current_region_label(driver)
        print(f"       ⚠️  фильтр не сменился (текущий: «{actual}»)")
        _save_debug_snapshot(driver, "region_switch_no_change")
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

    Структура такая же, как у квартирографии — div-сетка. Для тех месяцев,
    где данных нет, в HTML стоит «Нет данных» во всех блоках. Парсер
    распознаёт это и возвращает empty=True.
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return {}
    soup = BeautifulSoup(html, "lxml")

    out: dict = {
        "report_date": "",
        "region": "",
        "url": url,
        "empty": False,
    }
    page_text = soup.get_text(" ", strip=True)
    out["report_date"] = _parse_russian_date(page_text)
    if "Российская Федерация" in page_text:
        out["region"] = "Российская Федерация"

    # Эвристика «нет данных»: страница многократно содержит «Нет данных»
    no_data_count = page_text.count("Нет данных")
    if no_data_count >= 4:
        out["empty"] = True
        out["reason"] = f"страница показывает «Нет данных» × {no_data_count}"
        return out

    # TODO: когда период с данными — расширить. Пока best-effort через
    # тот же CSS-селектор кнопок что в kvartirografia.
    out["buttons_with_data"] = []
    for b in soup.find_all("button", class_="css-5nggi1"):
        divs = b.find_all("div", recursive=False)
        texts = [d.get_text(strip=True) for d in divs]
        prev_h2 = b.find_previous("h2")
        out["buttons_with_data"].append({
            "section": prev_h2.get_text(strip=True) if prev_h2 else "",
            "cells": texts,
        })

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


def fetch_rasprodannost(state: dict) -> list[Path]:
    return _scrape_table_source("rasprodannost", _build_rasprodannost_url(), state)




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
