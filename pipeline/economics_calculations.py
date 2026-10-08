"""Economics page calculations, preserving legacy ordering, pivots and units."""
from __future__ import annotations

import pandas as pd

from pipeline.data_access import month_label, quarter_label
from pipeline.profile_calculations import clean, records

MSK = "Москва"
RF = "Российская Федерация"
VIEWS = ["Строительство", "Всего"]
INDEX_BASES = {"month_to_month": "К предыдущему месяцу", "ytd_to_yago": "С начала года к АППГ"}
ACCOUNT_BLOCKS = [
    ("vrp_total", "gdp_total", 1e-6, 1e-3, "трлн руб", "ВРП Москвы", "ВВП России", "bar",
     "ВРП Москвы и ВВП России"),
    ("vrp_per_capita", "gdp_pc_total", 1e-6, 1e-6, "млн руб/чел",
     "Москва (ВРП на душу)", "Россия (ВВП на душу)", "bar", "На душу населения"),
    ("vrp_index", "gdp_index", 1.0, 1.0, "%", "ВРП Москвы", "ВВП России", "line",
     "Индекс физического объема ВРП"),
    ("vrp_per_capita_index", "gdp_pc_index", 1.0, 1.0, "%",
     "Москва (на душу)", "Россия (на душу)", "line", "Индекс физического объема на душу населения"),
]


def aggregate_year(df):
    annual = pd.concat([df[(df["period_type"] == "ytd") & (df["month"] == 12)],
                        df[df["period_type"] == "year"]], ignore_index=True)
    return annual[["year", "view", "region", "value"]]


def aggregate_quarter(df, ytd):
    if ytd:
        out = df[df["period_type"] == "ytd"].copy()
        out["__q_last_month"] = out["quarter"].map({1: 3, 2: 6, 3: 9, 4: 12})
        out = out[out["month"] == out["__q_last_month"]]
        return out[["year", "quarter", "view", "region", "value"]]
    monthly = df[df["period_type"] == "month"].copy()
    return monthly.groupby(["year", "quarter", "view", "region"], as_index=False)["value"].mean().round(2)


def aggregate_month(df, ytd):
    return df[df["period_type"] == ("ytd" if ytd else "month")][["year", "month", "view", "region", "value"]].copy()


def table(identifier, title, frame):
    return {"id": identifier, "title": title,
            "columns": [{"id": str(col), "label": str(col)} for col in frame.columns],
            "rows": records(frame)}


def pivot_table(identifier, frame, index, column, value, *, order=None):
    pivot = frame.pivot_table(index=index, columns=column, values=value, aggfunc="first")
    if order is not None:
        pivot = pivot.reindex(order)
    if column == "year":
        pivot.columns = [str(int(col)) for col in pivot.columns]
    return table(identifier + "_pivot", "Данные графика", pivot.reset_index())


def chart(identifier, title, kind, unit, plot, x, color, value, exported, *, stack=False):
    result = table(identifier, title, exported)
    result.update({"kind": kind, "unit": unit, "stack": stack,
                   "series": [{"id": str(name), "name": str(name), "unit": unit,
                               "points": [{"x": clean(row[x]), "y": clean(row[value])}
                                          for row in records(group)]}
                              for name, group in plot.groupby(color, sort=False, observed=True)]})
    return result


def salary_report(df, *, period, region, views, ytd, months, quarters):
    sub = (aggregate_year(df) if period == "year" else
           aggregate_quarter(df, ytd) if period == "quarter" else aggregate_month(df, ytd))
    sub = sub[(sub["region"] == region) & sub["view"].isin(views)].copy()
    identifier = "salary_" + period
    if sub.empty:
        return {"charts": [], "tables": []}
    if period == "year":
        sub = sub.sort_values(["year", "view"])
        title = f"Среднемесячная заработная плата (год, январь–декабрь). {region}"
        pivot = pivot_table(identifier, sub, "view", "year", "value", order=views)
        exported = sub.rename(columns={"value": "значение_руб"})
        result = chart(identifier, title, "bar", "руб.", sub, "year", "view", "value", exported)
    else:
        dimension = "quarter" if period == "quarter" else "month"
        sub = sub[sub[dimension].isin(quarters if dimension == "quarter" else months)].copy()
        if sub.empty:
            return {"charts": [], "tables": []}
        label = quarter_label if dimension == "quarter" else month_label
        sub["period"] = sub.apply(lambda row: label(row["year"], row[dimension]), axis=1)
        sub["_period_sort"] = sub["year"] * (10 if dimension == "quarter" else 100) + sub[dimension]
        suffix = "с начала года" if ytd else "среднее за квартал" if dimension == "quarter" else "за месяц"
        title = f"Заработная плата по {'кварталам' if dimension == 'quarter' else 'месяцам'} ({suffix}). {region}"
        pivot = pivot_table(identifier, sub, "period", "view", "value",
                            order=sub.sort_values("_period_sort")["period"].unique())
        exported = sub[["year", dimension, "period", "view", "region", "value"]].rename(
            columns={"value": "значение_руб", "period": "период"})
        result = chart(identifier, title, "line", "руб.", sub.sort_values("_period_sort"),
                       "period", "view", "value", exported)
    return {"charts": [result], "tables": [pivot]}


def ipc_report(df, *, period, regions, index_base, months, quarters):
    base = "ytd_to_yago" if period == "year" else index_base
    sub = df[df["period_type"] == base].copy()
    if period == "year":
        sub = sub[sub["month"] == 12]
    elif period == "quarter":
        sub["_q_last_m"] = sub["quarter"].map({1: 3, 2: 6, 3: 9, 4: 12})
        sub = sub[sub["month"] == sub["_q_last_m"]]
    sub = sub[sub["region"].isin(regions)].copy()
    identifier = "ipc_" + period
    if sub.empty:
        return {"charts": [], "tables": []}
    if period == "year":
        sub = sub.sort_values(["year", "region"])
        title = "ИПЦ — декабрь к декабрю предыдущего года"
        pivot = pivot_table(identifier, sub, "region", "year", "value")
        exported = sub[["year", "region", "value"]].rename(columns={"value": "ипц_%"})
        result = chart(identifier, title, "bar", "%", sub, "year", "region", "value", exported)
    else:
        dimension = "quarter" if period == "quarter" else "month"
        sub = sub[sub[dimension].isin(quarters if dimension == "quarter" else months)].copy()
        if sub.empty:
            return {"charts": [], "tables": []}
        label = quarter_label if dimension == "quarter" else month_label
        sub["period"] = sub.apply(lambda row: label(row["year"], row[dimension]), axis=1)
        sub["_period_sort"] = sub["year"] * (10 if dimension == "quarter" else 100) + sub[dimension]
        title = f"ИПЦ по {'кварталам' if dimension == 'quarter' else 'месяцам'} — {INDEX_BASES[base]}"
        pivot = pivot_table(identifier, sub, "period", "region", "value",
                            order=sub.sort_values("_period_sort")["period"].unique())
        exported = sub[["year", dimension, "period", "region", "value"]].rename(
            columns={"value": "ипц_%", "period": "период"})
        result = chart(identifier, title, "line", "%", sub.sort_values("_period_sort"),
                       "period", "region", "value", exported)
    return {"charts": [result], "tables": [pivot]}


def industry_options(df, region, metric):
    sub = df[(df["year"] >= 2011) & (df["region"] == region) & (df["metric"] == metric) & (df["view"] != "Всего")]
    if metric != "vds_index":
        modern = sub.loc[sub["year"] >= 2016, "view"]
        if not modern.empty:
            return sorted(modern.unique().tolist())
    return sorted(sub["view"].unique().tolist())


def accounts_report(df, **selection):
    df = df[df["year"] >= 2011].copy()
    charts, tables = [], []
    for index, block in enumerate(ACCOUNT_BLOCKS, 1):
        msk_metric, rf_metric, msk_scale, rf_scale, unit, msk_label, rf_label, kind, title = block
        parts = []
        regions = selection[f"block{index}_regions"]
        for region, metric, scale, label in [(MSK, msk_metric, msk_scale, msk_label), (RF, rf_metric, rf_scale, rf_label)]:
            part = df[(df["metric"] == metric) & (df["region"] == region)][["year", "value"]].copy()
            part["value"] *= scale
            part["Показатель"] = label
            if region in regions:
                parts.append(part)
        if not parts:
            continue
        sub = pd.concat(parts, ignore_index=True).sort_values(["Показатель", "year"])
        if sub.empty:
            continue
        identifier = f"na_block{index}"
        charts.append(chart(identifier, title, kind, unit, sub, "year", "Показатель", "value",
                            sub.rename(columns={"value": unit})))
        tables.append(pivot_table(identifier, sub, "Показатель", "year", "value"))

    region, mode, chosen = selection["structure_region"], selection["structure_mode"], selection["structure_industries"]
    metric = "vds_structure" if mode == "share" else "vds_value"
    sub = df[(df["metric"] == metric) & (df["region"] == region) & (df["view"] != "Всего")].copy()
    if not sub.empty and chosen:
        unit = "%" if mode == "share" else "трлн руб"
        scale = 1 if mode == "share" else 1e-6 if sub["unit"].iloc[0] == "млн руб" else 1e-3
        sub["val"] = sub["value"] * scale
        total = sub.groupby("year")["val"].sum()
        selected = sub[sub["view"].isin(chosen)].sort_values(["year", "view"]).copy()
        rest = (total - selected.groupby("year")["val"].sum().reindex(total.index).fillna(0)).clip(lower=0)
        result = chart("na_block5", "Структура ВРП", "bar", unit, selected, "year", "view", "val",
                       selected[["year", "view", "region", "val"]].rename(columns={"val": unit}), stack=True)
        result["series"].append({"id": "rest", "name": "Остальные отрасли", "unit": unit,
                                 "points": [{"x": clean(year), "y": clean(value)} for year, value in rest.items()]})
        result["totals"] = [{"x": clean(year), "y": clean(value)} for year, value in total.items()]
        charts.append(result)
        pivot = selected.pivot_table(index="view", columns="year", values="val", aggfunc="first")
        pivot.loc["Остальные отрасли"] = rest
        pivot.loc["Всего"] = total
        pivot.columns = [str(int(col)) for col in pivot.columns]
        tables.append(table("na_block5_pivot", "Данные графика", pivot.reset_index()))

    region, chosen = selection["index_region"], selection["index_industries"]
    sub = df[(df["metric"] == "vds_index") & (df["region"] == region)].copy()
    if not sub.empty and chosen:
        plot = sub[sub["view"].isin(chosen)].sort_values(["view", "year"])
        total = sub[sub["view"] == "Всего"].sort_values("year")
        # The legacy download always contains total, regardless of the checkbox.
        full = sub[sub["view"].isin(chosen + (["Всего"] if not total.empty else []))]
        result = chart("na_block6", "Индекс физического объема ВРП по отраслям", "line", "%",
                       plot, "year", "view", "value",
                       full[["year", "view", "region", "value", "unit"]].rename(columns={"value": "значение"}))
        if selection["show_total"] and not total.empty:
            result["series"].append({"id": "total", "name": "Всего по всем отраслям", "unit": "%",
                                     "points": [{"x": row["year"], "y": row["value"]} for row in records(total)]})
        charts.append(result)
        tables.append(pivot_table("na_block6", full, "view", "year", "value"))
    return {"charts": charts, "tables": tables}
