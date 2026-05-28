"""
domrf_mortgage_data.py
----------------------
Собирает данные с наш.дом.рф/аналитика/ипотечное_кредитование.

В Excel пишет числа в отдельных ячейках:
- название параметра + единица измерения в заголовке
- значение и темп прироста — отдельные столбцы

Зависимости:
    pip install selenium openpyxl
"""

import json
import re
import time
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, Alignment
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By


# ─────────────────────────────────────────────
# НАСТРОЙКИ
# ─────────────────────────────────────────────

PAGE_URL = "https://xn--80az8a.xn--d1aqf.xn--p1ai/аналитика/ипотечное_кредитование"

EXCEL_FILE = Path("ипотечное_кредитование.xlsx")
STATE_FILE = Path("domrf_mortgage_data_state.json")

FILTER_REGION_INDEX = 5
FILTER_MONTH_INDEX = 6
FILTER_YEAR_INDEX = 7

REGIONS = ["г. Москва", "Российская Федерация"]
YEARS = [str(y) for y in range(2009, 2027)]
MONTHS = [
    "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
]

RECHECK_LAST_N_MONTHS = 2

# Тестовый режим — несколько комбинаций для проверки
TEST_MODE = False
TEST_COMBINATIONS = [
    ("г. Москва", "2025", "май"),
    ("г. Москва", "2025", "июнь"),
    ("Российская Федерация", "2012", "май"),
]

PAGE_TIMEOUT = 60

# ─────────────────────────────────────────────


# Карта нормализации единиц измерения:
# (исходная единица) → (целевая единица, множитель)
UNIT_NORMALIZATION = {
    "млрд ₽": ("млн ₽", 1000),
    "тыс. ₽": ("млн ₽", 0.001),
    "тыс ₽": ("млн ₽", 0.001),
    "тыс. шт": ("шт", 1000),
    "тыс шт": ("шт", 1000),
    "тыс.": ("ед", 1000),
    # Без изменений
    "млн ₽": ("млн ₽", 1),
    "шт": ("шт", 1),
    "%": ("%", 1),
    "п.п": ("п.п", 1),
    "лет": ("лет", 1),
    "год": ("год", 1),
}

NO_DATA = "-"  # маркер отсутствующих данных


def normalize_unit(value, unit):
    """Приводит значение к стандартной единице. Возвращает (значение, новая_единица)."""
    if value is None:
        return value, unit
    u = (unit or "").strip().rstrip(".,")
    if u in UNIT_NORMALIZATION:
        target_unit, multiplier = UNIT_NORMALIZATION[u]
        return value * multiplier, target_unit
    return value, u


def is_dash(text):
    """Проверяет является ли значение прочерком (нет данных)."""
    if not text:
        return True
    t = text.strip().replace("\n", " ")
    # Убираем префиксы первичный/вторичный/за месяц/на дату
    t = re.sub(r"Первичный рынок\s*", "", t)
    t = re.sub(r"Вторичный рынок\s*", "", t)
    t = re.sub(r"за\s+\w+\s+\d{4}\s*года?\s*", "", t)
    t = re.sub(r"на\s+\d{2}\.\d{2}\.\d{4}\s*", "", t)
    t = t.strip(" |?")
    # Прочерк или прочерк с %
    return t in ("-", "—", "–", "- %", "— %", "- п.п.", "—п.п.")


def parse_value(text):
    """
    Разбирает текст и возвращает (значение, единица, темп, единица_темпа).
    Если значение — прочерк, возвращает (NO_DATA, "", NO_DATA, "")
    """
    if not text:
        return None, "", None, ""

    if is_dash(text):
        return NO_DATA, "", NO_DATA, ""

    t = re.sub(r"\s+", " ", text.strip()).replace("|", " ")
    t = re.sub(r"Первичный рынок\s*", "", t)
    t = re.sub(r"Вторичный рынок\s*", "", t)
    t = re.sub(r"за\s+\w+\s+\d{4}\s*года?\s*", "", t)
    t = re.sub(r"на\s+\d{2}\.\d{2}\.\d{4}\s*", "", t)
    t = t.strip()

    m = re.search(r"(-?[\d\s]*[\d](?:[,.]\d+)?)", t)
    if not m:
        return NO_DATA, "", NO_DATA, ""

    main_value_str = m.group(1).strip()
    try:
        main_value = float(main_value_str.replace(" ", "").replace(",", "."))
    except ValueError:
        return NO_DATA, "", NO_DATA, ""

    rest = t[m.end():].strip()

    # Извлекаем единицу — до знака +/- + цифра, или до отдельного прочерка
    unit_match = re.match(r"([^\d+\-—–]+?)(?=[+\-—–]\s*\d|\s+[-—–]\s*$|\s+[-—–]\s|$)", rest)
    if unit_match:
        main_unit = unit_match.group(1).strip().rstrip(".,")
        rest = rest[unit_match.end():].strip()
    else:
        main_unit = rest.strip()
        rest = ""

    delta_value = None
    delta_unit = ""
    delta_match = re.match(r"([+\-][\d,.\s]+)\s*(.*)$", rest)
    if delta_match:
        delta_str = delta_match.group(1).strip().replace(" ", "").replace(",", ".")
        try:
            delta_value = float(delta_str)
            delta_unit = delta_match.group(2).strip().rstrip(".,")
        except ValueError:
            pass

    # Если темп не нашёлся, но в rest есть прочерк — это NO_DATA для темпа
    if delta_value is None and rest and re.search(r"[-—–]", rest):
        delta_value = NO_DATA

    # Нормализуем единицы
    main_value, main_unit = normalize_unit(main_value, main_unit)
    delta_value, delta_unit = normalize_unit(delta_value, delta_unit) if delta_value not in (None, NO_DATA) else (delta_value, delta_unit)

    return main_value, main_unit, delta_value, delta_unit


def load_state():
    if STATE_FILE.exists():
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def create_driver():
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
    return webdriver.Chrome(options=options)


def select_filter(driver, button_index, value):
    buttons = driver.find_elements(By.CSS_SELECTOR, "button.custom-select")
    if button_index >= len(buttons):
        return False

    target_btn = buttons[button_index]
    if target_btn.text.strip() == value:
        return True

    driver.execute_script("arguments[0].click();", target_btn)
    time.sleep(0.7)

    try:
        span = driver.find_element(
            By.XPATH,
            f"//li[contains(@class,'opt-list__item')]//span[normalize-space(text())='{value}']"
        )
        driver.execute_script("arguments[0].click();", span)
        time.sleep(1.5)
        try:
            driver.execute_script("document.body.click();")
            time.sleep(0.3)
        except Exception:
            pass
        return True
    except Exception:
        return False


def collect_data(driver):
    return driver.execute_script("""
        const result = [];

        document.querySelectorAll('.main-card_item').forEach((card, idx) => {
            const title = card.querySelector('.title_name')?.innerText.trim() || '';
            if (!title) return;
            const mainValue = card.querySelector('.main-card_value_top')?.innerText.trim() || '';
            const primary = card.querySelector('.item_primary')?.innerText.trim() || '';
            const secondary = card.querySelector('.item_secondary')?.innerText.trim() || '';
            result.push({type: 'main', title, idx, mainValue, primary, secondary});
        });

        document.querySelectorAll('.card_item').forEach((card, idx) => {
            if (card.className.includes('main-card')) return;
            const title = card.querySelector('.title_name')?.innerText.trim() || '';
            if (!title) return;
            let fullText = card.innerText.trim().replace(title, '').trim();
            fullText = fullText.split('\\n').filter(l => l.trim() && l.trim() !== '?').join(' | ');
            result.push({type: 'extra', title, idx, mainValue: fullText, primary: '', secondary: ''});
        });

        return result;
    """)


def is_percent_unit(unit):
    return unit and ("%" in unit or "п.п" in unit)


def build_row(region, year, month, data, schema):
    """
    Превращает данные в плоский dict {заголовок: значение}.
    
    schema — глобальный словарь {ключ_показателя: (имя_столбца, единица, is_percent)}
    Заполняется при первой встрече показателя и потом используется для всех записей,
    чтобы имена столбцов были стабильными.
    
    Возвращает (row_dict, pct_cols).
    """
    row = {"Регион": region, "Год": year, "Месяц": month}
    pct_cols = set()

    def add_value(key_id, base_title, raw_text, schema):
        """Добавляет значение в row, фиксируя имя столбца в schema."""
        nonlocal pct_cols
        v, u, d, du = parse_value(raw_text)

        # ─── Основное значение ───
        val_key = key_id  # стабильный ID
        if val_key not in schema:
            # Первая встреча — формируем имя столбца
            if is_percent_unit(u):
                col_name = base_title
                schema[val_key] = (col_name, u, True)
            elif u and v != NO_DATA:
                col_name = f"{base_title} ({u})"
                schema[val_key] = (col_name, u, False)
            else:
                # Нет единицы (например прочерк) — записываем без неё пока
                col_name = base_title
                schema[val_key] = (col_name, "", False)
        else:
            # Уже встречали — берём фиксированное имя
            col_name, fixed_unit, is_pct = schema[val_key]
            # Если раньше единицы не было, а теперь появилась — обновляем заголовок
            if not fixed_unit and u and v != NO_DATA:
                if is_percent_unit(u):
                    schema[val_key] = (base_title, u, True)
                else:
                    schema[val_key] = (f"{base_title} ({u})", u, False)
                col_name = schema[val_key][0]

        col_name, _, is_pct = schema[val_key]
        row[col_name] = v if v is not None else NO_DATA
        if is_pct:
            pct_cols.add(col_name)

        # ─── Темп прироста ───
        delta_key = f"{key_id}__delta"
        # Определяем единицу темпа: если у основного значения была п.п. — темп тоже в п.п.
        is_pp = du and "п.п" in du
        if is_pp:
            delta_col_name = f"{base_title}, темп прироста к предыдущему году (п.п.)"
            delta_is_pct = False
        else:
            delta_col_name = f"{base_title}, темп прироста к предыдущему году"
            delta_is_pct = True

        if delta_key not in schema:
            schema[delta_key] = (delta_col_name, "п.п." if is_pp else "%", delta_is_pct)

        col_name, _, is_pct = schema[delta_key]
        row[col_name] = d if d is not None else NO_DATA
        if is_pct:
            pct_cols.add(col_name)

    # Основные карточки
    main_idx = 0
    for item in data:
        if item["type"] != "main":
            continue
        main_idx += 1
        title = item["title"]
        prefix = f"К{main_idx}. {title}"

        add_value(f"main_{main_idx}_total", prefix, item["mainValue"], schema)

        if item["primary"]:
            add_value(
                f"main_{main_idx}_primary",
                f"{prefix}, первичный рынок",
                item["primary"],
                schema,
            )

        if item["secondary"]:
            add_value(
                f"main_{main_idx}_secondary",
                f"{prefix}, вторичный рынок",
                item["secondary"],
                schema,
            )

    # Нижние карточки — по названию (они уникальные)
    for item in data:
        if item["type"] != "extra":
            continue
        title = item["title"]
        add_value(f"extra_{title}", title, item["mainValue"], schema)

    return row, pct_cols


def make_key(region, year, month):
    return f"{region}|{year}|{month}"


def load_excel():
    if EXCEL_FILE.exists():
        wb = load_workbook(EXCEL_FILE)
        ws = wb.active
        headers = [c.value for c in ws[1]] if ws.max_row >= 1 else []
        existing = {}
        for r in range(2, ws.max_row + 1):
            vals = [ws.cell(row=r, column=c).value for c in range(1, len(headers) + 1)]
            d = dict(zip(headers, vals))
            key = make_key(d.get("Регион"), d.get("Год"), d.get("Месяц"))
            if d.get("Регион"):
                existing[key] = d
        return wb, ws, headers, existing
    else:
        wb = Workbook()
        ws = wb.active
        ws.title = "Ипотека"
        return wb, ws, [], {}


def save_excel(wb, ws, headers, all_rows, pct_cols):
    ws.delete_rows(1, ws.max_row)

    for col_idx, h in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col_idx, value=h)
        cell.font = Font(bold=True)
        cell.alignment = Alignment(wrap_text=True, vertical="top")

    for row_idx, row_data in enumerate(all_rows, start=2):
        for col_idx, h in enumerate(headers, start=1):
            val = row_data.get(h)
            cell = ws.cell(row=row_idx, column=col_idx)

            if val is None or val == NO_DATA:
                # Прочерк или пусто
                cell.value = NO_DATA
            elif h in pct_cols and isinstance(val, (int, float)):
                # Процентный формат — Excel показывает "50%"
                cell.value = val / 100
                cell.number_format = "0.00%"
            else:
                cell.value = val

    ws.column_dimensions["A"].width = 25
    ws.column_dimensions["B"].width = 8
    ws.column_dimensions["C"].width = 12
    for col_idx in range(4, len(headers) + 1):
        col_letter = ws.cell(row=1, column=col_idx).column_letter
        ws.column_dimensions[col_letter].width = 18

    ws.row_dimensions[1].height = 60

    wb.save(EXCEL_FILE)


def get_combinations(state):
    if TEST_MODE:
        return TEST_COMBINATIONS
    years_desc = sorted(YEARS, reverse=True)
    months_desc = list(reversed(MONTHS))
    if not state:
        return [(r, y, m) for r in REGIONS for y in years_desc for m in months_desc]
    current_year = datetime.now().year
    years = [str(current_year), str(current_year - 1)]
    return [(r, y, m) for r in REGIONS for y in years for m in months_desc[:RECHECK_LAST_N_MONTHS]]


def run():
    state = load_state()

    print(f"\n{'='*60}")
    print(f"Ипотека (наш.дом.рф) | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}\n")

    wb, ws, headers, existing = load_excel()
    print(f"📊 В Excel сейчас: {len(existing)} строк, {len(headers)} столбцов")

    combinations = get_combinations(state)
    print(f"🔍 Будет проверено: {len(combinations)}")
    if TEST_MODE:
        print(f"⚠️  ТЕСТОВЫЙ РЕЖИМ\n")

    pct_cols_all = set()
    schema = {}  # стабильные имена столбцов

    driver = create_driver()
    try:
        driver.set_page_load_timeout(PAGE_TIMEOUT)
        driver.get(PAGE_URL)
        time.sleep(8)

        all_rows = list(existing.values())

        for i, (region, year, month) in enumerate(combinations, start=1):
            print(f"[{i}/{len(combinations)}] {region} | {year} | {month}")

            if not select_filter(driver, FILTER_REGION_INDEX, region):
                print("  ⚠️  Регион не выбрался")
                continue
            if not select_filter(driver, FILTER_YEAR_INDEX, year):
                print("  ⚠️  Год не выбрался")
                continue
            if not select_filter(driver, FILTER_MONTH_INDEX, month):
                print("  ⚠️  Месяц не выбрался (возможно недоступен)")
                continue

            time.sleep(2)
            data = collect_data(driver)
            if not data:
                print("  ⚠️  Данные не собрались")
                continue

            row, pct_cols = build_row(region, year, month, data, schema)
            pct_cols_all.update(pct_cols)
            print(f"  ✓ Собрано {len(data)} карточек → {len(row)-3} столбцов")

            for k in row.keys():
                if k not in headers:
                    headers.append(k)

            key = make_key(region, year, month)
            replaced = False
            for j, er in enumerate(all_rows):
                ek = make_key(er.get("Регион"), er.get("Год"), er.get("Месяц"))
                if ek == key:
                    all_rows[j] = row
                    replaced = True
                    break
            if not replaced:
                all_rows.append(row)

            state[key] = datetime.now().isoformat()

    finally:
        driver.quit()

    save_excel(wb, ws, headers, all_rows, pct_cols_all)
    save_state(state)

    print(f"\n{'='*60}")
    print(f"✅ Готово: {EXCEL_FILE}")
    print(f"   Строк: {len(all_rows)}, столбцов: {len(headers)}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    run()