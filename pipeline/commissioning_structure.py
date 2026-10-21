"""Monthly RV commissioning structure with independently measured comparisons."""
import math

import pandas as pd

from app.vvod_operational import MONTHS_RU


STRUCTURE_LABELS = (
    ("total", "Общий ввод недвижимости"), ("housing_objects", "Жильё"),
    ("mkd_total", "МКД"), ("mkd_residential", "Квартиры"), ("mop", "МОП"),
    ("nonres_in_housing", "Нежильё в жилье"), ("izhs", "ИЖС"),
    ("residential_area", "Жилая площадь"), ("nonres_objects", "Нежильё"),
    ("offices", "Офисы"), ("social", "Социальные объекты"), ("warehouses", "Склады"),
    ("industrial", "Промышленность"), ("hotels", "Гостиницы и апарт-отели"),
    ("parking", "Паркинги и автообслуживание"), ("trade", "Торговля и услуги"),
    ("transport", "Транспорт"), ("other", "Прочее"), ("nonres_total", "Всё нежильё"),
)


def _sum(frame, column, measured=(), *, known=False):
    if column not in frame:
        return None
    if frame.empty:
        return 0.0
    values = pd.to_numeric(frame[column], errors="coerce")
    valid = values.notna() & values.map(lambda value: math.isfinite(value) if pd.notna(value) else False)
    for field in measured:
        if field + "_measured" in frame:
            valid &= frame[field + "_measured"]
    if not valid.all() and not known or not valid.any():
        return None
    return float(values[valid].sum()) / 1_000_000


def _add(*values, known=False):
    if known:
        return sum(value for value in values if value is not None) if any(value is not None for value in values) else None
    return sum(values) if all(value is not None for value in values) else None


def _values(selected, *, known=False):
    if selected.empty:
        return {key: None for key, _ in STRUCTURE_LABELS}
    industries = selected["Отрасли"].fillna("").astype(str).str.strip().str.casefold()
    residential = industries.eq("жилые объекты")
    subtype = selected.get("Подтип объекта", pd.Series("", index=selected.index)).fillna("").astype(str).str.strip()
    grouping = selected.get("Группировка", pd.Series("Жилье", index=selected.index)).fillna("").astype(str).str.strip().str.casefold().str.replace("ё", "е")
    living_rows = selected[residential & grouping.eq("жилье")]
    embedded_rows = selected[residential & grouping.eq("нежилье")]
    standalone = selected[~residential]
    area = lambda frame, column, measured=(): _sum(frame, column, measured, known=known)
    living = area(living_rows, "category_жилое", ("Жилая площадь",))
    mop = area(living_rows, "category_моп", ("Общая площадь", "Жилая площадь"))
    if not living_rows.empty and {"Общая площадь", "Жилая площадь"} <= set(living_rows):
        if (living_rows["Общая площадь"] < living_rows["Жилая площадь"]).any():
            mop = area(living_rows[living_rows["Общая площадь"].ge(living_rows["Жилая площадь"])], "category_моп", ("Общая площадь", "Жилая площадь")) if known else None
    embedded = area(embedded_rows, "category_нежилое_в_жилом", ("Общая площадь",))
    apartments = area(living_rows[subtype.reindex(living_rows.index).eq("МКД")], "category_жилое", ("Жилая площадь",))
    result = {"total": area(selected, "Общая площадь", ("Общая площадь",)),
              "housing_objects": area(selected[residential], "Общая площадь", ("Общая площадь",)),
              "mkd_residential": apartments, "mop": mop, "nonres_in_housing": embedded,
              "mkd_total": _add(apartments, mop, embedded, known=known), "residential_area": living,
              "izhs": area(living_rows[subtype.reindex(living_rows.index).eq("ИЖС")], "category_жилое", ("Жилая площадь",)),
              "nonres_objects": area(standalone, "category_нежилое_отдельное", ("Общая площадь",))}
    result["nonres_total"] = _add(result["nonres_objects"], embedded, known=known)
    assigned = pd.Series(False, index=selected.index)
    for key, pattern in (("offices", "административно|делов"),
                         ("social", "доу|школ|образоват|лечеб|социально|спортив|культур|культов"),
                         ("warehouses", "склад"), ("industrial", "промышлен|производ"),
                         ("hotels", "гостиниц|апарт"), ("parking", "гараж|паркинг|автомой|автосервис"),
                         ("trade", "торгов"), ("transport", "транспорт|метро|дорог")):
        mask = ~residential & ~assigned & industries.str.contains(pattern, regex=True, na=False)
        result[key] = area(selected[mask], "category_нежилое_отдельное", ("Общая площадь",))
        assigned |= mask
    result["other"] = area(selected[~residential & ~assigned], "category_нежилое_отдельное", ("Общая площадь",))
    return result


def monthly_structure(rv, year, requested_month, source_date=None):
    prepared = rv.copy()
    # These rows describe linear road commissioning, not unknown building area.
    industries = prepared["Отрасли"].fillna("").astype(str).str.strip().str.casefold()
    prepared = prepared.loc[~industries.eq("улицы и дороги")].copy()
    measured_schema = {"Общая площадь_measured", "Жилая площадь_measured"} <= set(prepared)
    years = pd.to_numeric(prepared["Год ввода по Мосстату"], errors="coerce")
    months = prepared["Месяц ввода по Мосстату"].astype(str).str.strip().str.casefold().map(MONTHS_RU)
    available = months[years.eq(year)].dropna()
    snapshot = pd.to_datetime(source_date, errors="coerce", dayfirst=True)
    cap = int(snapshot.month) if pd.notna(snapshot) and snapshot.year == year else 12
    actual_month = min(requested_month, cap, int(available.max())) if not available.empty else None
    chosen_mask = years.eq(year) & months.le(actual_month) if actual_month else years.eq(-1)
    full_year = actual_month == 12 and pd.notna(snapshot) and year < snapshot.year
    if full_year:
        chosen_mask |= years.eq(year) & months.isna()
    chosen = prepared[chosen_mask]
    def period_values(selected, metric_year, *, known=False, renovation=False, annual=False):
        unknown = prepared[years.eq(metric_year) & months.isna()]
        if renovation:
            if metric_year < 2022 or "Инвестор для Реновации" not in selected or selected.empty:
                return None
            is_renovation = lambda frame: frame["Инвестор для Реновации"].fillna("").astype(str).str.strip().str.casefold().eq("да")
            selected = selected[is_renovation(selected)]
            unknown = unknown[is_renovation(unknown)]
            result = _values(selected, known=known) if not selected.empty else {key: 0.0 for key, _ in STRUCTURE_LABELS}
        else:
            result = _values(selected, known=known)
        if not known and not annual and not unknown.empty:
            unassigned = _values(unknown)
            for key in result:
                if unassigned[key] is None or unassigned[key] != 0:
                    result[key] = None
        return result
    current = period_values(chosen, year, annual=full_year)
    known = _values(chosen, known=True)
    monthly_frame = prepared[years.eq(year) & months.eq(actual_month)] if actual_month else prepared.iloc[:0]
    previous_frame = prepared[years.eq(year - 1) & months.le(actual_month)] if actual_month else prepared.iloc[:0]
    if full_year:
        previous_frame = prepared[years.eq(year - 1)]
    monthly = period_values(monthly_frame, year) if actual_month else None
    analogous = period_values(previous_frame, year - 1, annual=full_year) if actual_month else None
    renovation = period_values(chosen, year, renovation=True, annual=full_year)
    renovation_known = period_values(chosen, year, renovation=True, known=True)
    renovation_month = period_values(monthly_frame, year, renovation=True) if actual_month else None
    renovation_previous = period_values(previous_frame, year - 1, renovation=True, annual=full_year) if actual_month else None
    rows = []
    for key, label in STRUCTURE_LABELS:
        value, same_period = current[key], analogous.get(key) if analogous else None
        reno, reno_previous = renovation.get(key) if renovation else None, renovation_previous.get(key) if renovation_previous else None
        rows.append({"id": key, "label": label, "value": value,
                     "knownSum": known[key], "monthChange": monthly.get(key) if monthly else None,
                     "yearChangePct": (value / same_period - 1) * 100 if value is not None and same_period not in (None, 0) else None,
                     "renovation": reno, "renovationKnownSum": renovation_known.get(key) if renovation_known else None,
                     "renovationMonthChange": renovation_month.get(key) if renovation_month else None,
                     "renovationYearChangePct": (reno / reno_previous - 1) * 100 if reno is not None and reno_previous not in (None, 0) else None})
    issues = []
    if not measured_schema:
        issues.append("В реестре отсутствуют признаки измерения площадей; точные значения месячной структуры недоступны.")
        for row in rows:
            for key in ("value", "knownSum", "monthChange", "yearChangePct", "renovation", "renovationKnownSum", "renovationMonthChange", "renovationYearChangePct"):
                row[key] = None
    if months[years.isin([year, year - 1])].isna().any():
        issues.append("Есть строки без месяца ввода; затронутые периодные суммы и сравнения не подтверждены.")
    return {"year": year, "requestedMonth": requested_month, "actualMonth": actual_month,
            "sourceDate": str(source_date) if source_date else None, "rows": rows, "issues": issues}
