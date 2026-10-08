"""Current construction using the original operational and monthly KPI selections."""
from __future__ import annotations

import pandas as pd

from pipeline.report_values import records
from pipeline.operational_calculations import MONTH_LABELS


def latest_rasprod_row(data, region, metric_name):
    kpi = data.get("kpi", pd.DataFrame())
    periods = data.get("periods_by_region", {}).get(region, [])
    if kpi.empty or not periods:
        return None
    year, month = periods[-1]
    normalized = kpi["название"].astype(str).str.strip().str.casefold()
    rows = kpi[kpi["region_key"].eq(region) & kpi["year"].eq(year) & kpi["month"].eq(month)
               & normalized.eq(metric_name.strip().casefold())]
    return rows.iloc[0] if not rows.empty else None


def catalog(data):
    operational = data["load_construction_operational"]
    permits = operational.get("permits", pd.DataFrame())
    months = {}
    for kind in ("total", "housing", "nonresidential"):
        frame = permits if kind == "total" else permits[permits["kind"].eq(kind)] if not permits.empty else permits
        if not frame.empty:
            latest = int(frame["year"].max())
            values = sorted(int(v) for v in frame.loc[frame["year"].eq(latest), "month"].dropna().unique())
        else:
            values = []
        months[kind] = values or list(range(1, 13))
    return {"regions": [{"id": "msk", "label": "Город Москва"}, {"id": "rf", "label": "Российская Федерация"}],
            "permitKinds": [{"id": "total", "label": "Всего"}, {"id": "housing", "label": "Жильё"},
                            {"id": "nonresidential", "label": "Нежильё"}], "monthsByKind": months}


def report(data, options, region="msk", permit_kind="total", month=None):
    if region not in ("msk", "rf") or permit_kind not in options["monthsByKind"]:
        raise LookupError("Construction filter is unavailable")
    months = options["monthsByKind"][permit_kind]
    month = months[-1] if month is None else month
    if month not in months:
        raise LookupError("Permit month is unavailable")
    operational, rasprod = data["load_construction_operational"], data["load_rasprodannost"]
    construction = operational.get("construction", pd.DataFrame())
    current = construction[construction["region_key"].eq(region)] if not construction.empty else pd.DataFrame()
    period = current["report_period"].dropna().iloc[0] if not current.empty and current["report_period"].notna().any() else ""
    living = current.loc[current["area_kind"].eq("living"), "value_thousand_m2"] if not current.empty else pd.Series(dtype=float)
    total = current.loc[current["area_kind"].eq("total"), "value_thousand_m2"] if not current.empty else pd.Series(dtype=float)
    fallback = latest_rasprod_row(rasprod, region, "Объем жилищного строительства") if living.empty else None
    living_value = fallback.get("значение_num") if fallback is not None else living.iloc[0] if not living.empty else None
    total_value = total.iloc[0] if not total.empty else None
    values = [("living", "Жилая площадь", None if pd.isna(living_value) else float(living_value) / 1000, "млн м²", 2),
              ("total", "Общая площадь", None if pd.isna(total_value) else float(total_value) / 1000, "млн м²", 2)]
    for key, name in [("sold", "Распроданность"), ("ready", "Стройготовность"),
                      ("ratio", "Отношение распроданности к стройготовности")]:
        row = latest_rasprod_row(rasprod, region, name)
        value = row.get("значение_num") if row is not None else None
        values.append((key, name, value, "%", 0))
    metrics = records(pd.DataFrame(values, columns=["id", "label", "value", "unit", "digits"]))
    sales = operational.get("sales", pd.DataFrame())
    sales = sales[sales["region_key"].eq(region)] if not sales.empty else sales
    sales_row = records(sales.head(1))[0] if not sales.empty else None
    permits = operational.get("permits", pd.DataFrame())
    filtered = permits.copy() if permit_kind == "total" else permits[permits["kind"].eq(permit_kind)].copy() if not permits.empty else permits
    table = pd.DataFrame()
    chart = None
    if not filtered.empty:
        monthly_full = filtered.groupby("year")["value_thousand_m2"].sum()
        annual = operational.get("permit_annual", pd.DataFrame())
        if not annual.empty and permit_kind != "total":
            annual = annual[annual["kind"].eq(permit_kind)]
        full = annual.groupby("year")["value_thousand_m2"].sum() if not annual.empty else pd.Series(dtype=float)
        full = full.combine_first(monthly_full)
        ytd = filtered[filtered["month"].le(month)].groupby("year")["value_thousand_m2"].sum()
        table = pd.DataFrame({"Год": sorted(set(full.index) | set(ytd.index))})
        table["За год, тыс. м²"] = table["Год"].map(full)
        table["С начала года, тыс. м²"] = table["Год"].map(ytd)
        table = table[table["Год"].ge(2011)].reset_index(drop=True)
        table["Изменение, %"] = table["С начала года, тыс. м²"].pct_change() * 100
        period_values = table["С начала года, тыс. м²"].fillna(0)
        remainder = (table["За год, тыс. м²"] - period_values).clip(lower=0).fillna(0)
        chart = {"x": [str(int(y)) for y in table["Год"]], "period": records(pd.DataFrame({"value": period_values})),
                 "remainder": records(pd.DataFrame({"value": remainder})),
                 "totals": records(pd.DataFrame({"value": table["За год, тыс. м²"]})),
                 "growth": records(pd.DataFrame({"value": table["Изменение, %"]}))}
    periods = rasprod.get("periods_by_region", {}).get(region, [])
    return {"selection": {"region": region, "permitKind": permit_kind, "month": month},
            "constructionPeriod": period, "salesReadinessPeriod": {"year": periods[-1][0], "month": periods[-1][1]} if periods else None,
            "metrics": metrics, "sales": sales_row, "permits": {"region": "Москва", "unit": "тыс. м²",
               "periodLabel": MONTH_LABELS[month - 1], "columns": [{"id": col, "label": col} for col in table.columns],
               "rows": records(table), "chart": chart},
            "sourceDetails": {"file": operational.get("source_file"), "date": operational.get("source_date")},
            "notes": ["Разрешения на строительство всегда относятся к Москве, независимо от выбранного региона.",
                      "Объём жилой площади при отсутствии оперативного значения берётся из последнего KPI распроданности."]}
