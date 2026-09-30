"""Calculations for the operational Moscow commissioning dashboard."""
from __future__ import annotations

import math

import pandas as pd


MONTHS_RU = {
    "январь": 1, "февраль": 2, "март": 3, "апрель": 4,
    "май": 5, "июнь": 6, "июль": 7, "август": 8,
    "сентябрь": 9, "октябрь": 10, "ноябрь": 11, "декабрь": 12,
}

def _prepared_monitoring(rv: pd.DataFrame) -> pd.DataFrame:
    out = rv.copy()
    out["_year"] = pd.to_numeric(out.get("Год ввода по Мосстату"), errors="coerce")
    out["_month"] = (
        out.get("Месяц ввода по Мосстату", pd.Series(index=out.index, dtype=object))
        .astype(str).str.strip().str.casefold().map(MONTHS_RU)
    )
    quarter = out.get("Квартал ввода по Мосстату", pd.Series(index=out.index, dtype=object))
    out["_quarter"] = pd.to_numeric(
        quarter.astype(str).str.extract(r"(\d+)", expand=False), errors="coerce"
    )
    return out


def _sum(frame: pd.DataFrame, column: str) -> float:
    return float(pd.to_numeric(frame.get(column, 0), errors="coerce").fillna(0).sum()) / 1_000_000


def _with_growth(table: pd.DataFrame) -> pd.DataFrame:
    table = table.sort_values("Год").reset_index(drop=True)
    table["Изменение полного года, %"] = table["За год, млн м²"].pct_change(fill_method=None) * 100
    table["Изменение к аналогичному периоду, %"] = (
        table["За выбранный период, млн м²"].pct_change(fill_method=None) * 100
    )
    return table


def _history_ytd(monthly: pd.DataFrame, month: int) -> dict[int, float]:
    if monthly is None or monthly.empty:
        return {}
    selected = monthly[pd.to_numeric(monthly["month"], errors="coerce").le(month)].copy()
    selected["value_thousand_m2"] = pd.to_numeric(selected["value_thousand_m2"], errors="coerce")
    return (selected.groupby("year")["value_thousand_m2"].sum() / 1000).to_dict()


def housing_ytd_table(annual: pd.DataFrame, rv: pd.DataFrame, history: pd.DataFrame, month: int) -> pd.DataFrame:
    years = sorted(set(pd.to_numeric(annual["year"], errors="coerce").dropna().astype(int)))
    data = {int(k): float(v) for k, v in _history_ytd(history, month).items()}
    prepared = _prepared_monitoring(rv)
    current_year = int(prepared["_year"].max()) if prepared["_year"].notna().any() else None
    if current_year:
        current = prepared[(prepared["_year"] == current_year) & (prepared["_month"] <= month)]
        if not current.empty:
            data[current_year] = _sum(current, "category_жилое")
            years.append(current_year)
    annual_values = annual.set_index("year")["жильё"].to_dict()
    rows = [{
        "Год": year,
        "За год, млн м²": float(annual_values.get(year, math.nan)),
        "За выбранный период, млн м²": data.get(year, math.nan),
    } for year in sorted(set(years) | set(data))]
    return _with_growth(pd.DataFrame(rows))


def nonres_ytd_table(
    annual: pd.DataFrame,
    rv: pd.DataFrame,
    month: int,
    *,
    exclude_mkd: bool,
) -> pd.DataFrame:
    prepared = _prepared_monitoring(rv)
    rows = []
    annual_col = "нежильё" if exclude_mkd else "общая"
    annual_values = annual.set_index("year")[annual_col].to_dict()
    years = sorted(set(pd.to_numeric(annual["year"], errors="coerce").dropna().astype(int)))
    dynamic_years = sorted(prepared.loc[prepared["_month"].le(month), "_year"].dropna().astype(int).unique())
    for year in sorted(set(years) | set(dynamic_years)):
        selected = prepared[(prepared["_year"] == year) & (prepared["_month"] <= month)]
        value = math.nan
        if not selected.empty:
            value = _sum(selected, "category_нежилое_отдельное")
            if not exclude_mkd:
                value += _sum(selected, "category_нежилое_в_жилом")
        rows.append({
            "Год": year,
            "За год, млн м²": float(annual_values.get(year, math.nan)),
            "За выбранный период, млн м²": value,
        })
    return _with_growth(pd.DataFrame(rows))


def quarter_tree(rv: pd.DataFrame, year: int, quarter: int, *, cumulative: bool = False) -> dict[str, float]:
    prepared = _prepared_monitoring(rv)
    quarter_mask = prepared["_quarter"].le(quarter) if cumulative else prepared["_quarter"].eq(quarter)
    selected = prepared[(prepared["_year"] == year) & quarter_mask]
    housing_objects = selected[selected["Отрасли"].astype(str).str.strip().eq("Жилые объекты")]
    standalone = selected[~selected.index.isin(housing_objects.index)]
    industries = standalone["Отрасли"].astype(str).str.casefold()

    def industry_sum(pattern: str) -> float:
        return _sum(standalone[industries.str.contains(pattern, regex=True, na=False)], "category_нежилое_отдельное")

    offices = industry_sum(r"административно|делов")
    hotels = industry_sum(r"гостиниц|апарт")
    industrial = industry_sum(r"промышлен|производ")
    social = industry_sum(r"доу|школ|образоват|лечеб|социально|спортив|культур|культов")
    standalone_total = _sum(selected, "category_нежилое_отдельное")
    known = offices + hotels + industrial + social
    return {
        "total": _sum(selected, "Общая площадь"),
        "housing_objects": _sum(housing_objects, "Общая площадь"),
        "nonres_objects": standalone_total,
        "residential_area": _sum(selected, "category_жилое"),
        "mkd_residential": _sum(selected[selected["Подтип объекта"].astype(str).eq("МКД")], "category_жилое"),
        "mkd_total": (
            _sum(selected[selected["Подтип объекта"].astype(str).eq("МКД")], "category_жилое")
            + _sum(selected, "category_моп")
            + _sum(selected, "category_нежилое_в_жилом")
        ),
        "izhs": _sum(selected[selected["Подтип объекта"].astype(str).eq("ИЖС")], "category_жилое"),
        "mop": _sum(selected, "category_моп"),
        "nonres_in_housing": _sum(selected, "category_нежилое_в_жилом"),
        "offices": offices,
        "hotels": hotels,
        "industrial": industrial,
        "social": social,
        "other": max(standalone_total - known, 0.0),
        "nonres_total": standalone_total + _sum(selected, "category_нежилое_в_жилом"),
    }
