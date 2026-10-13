"""Cross-sheet RV precedence preserves physical rows and legitimate multiplicity."""
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from pipeline.commissioning_validation import validate_primary_rv
from pipeline.data_access import DataAccess, DataContext
from pipeline.monitoring_registry import normalize_rv_years, select_rv_supplements
from pipeline.parsers.nashdom_monitoring_2_0 import _read_sheets


def row(**changes):
    return {"УИН": 101, "№ РВ": "Permit-1", "Год ввода по Мосстату": 2026,
            "Месяц ввода по Мосстату": "март", "Дата ввода по Мосстату": pd.Timestamp("2026-03-01"),
            "Отрасли": "Жилые объекты", "Подтип объекта": "МКД", "Группировка": "Жилье",
            "Группа компаний": "Company", "Назначение": "Жилье", "Общая площадь": 1200,
            "Жилая площадь": 1000, **changes}


class MonitoringRegistryTests(unittest.TestCase):
    @staticmethod
    def canonical(directory, primary, history):
        path = Path(directory) / "monitoring.xlsx"
        with pd.ExcelWriter(path) as writer:
            pd.DataFrame(primary).to_excel(writer, sheet_name="Реестр РВ", index=False)
            pd.DataFrame([row()]).to_excel(writer, sheet_name="Реестр ОКС", index=False)
            pd.DataFrame(history).to_excel(writer, sheet_name="History", index=False)
        access = DataAccess(DataContext(Path(directory), use_marts=False))
        return path, access.load_monitoring_2_0(source_path=path)["rv"]

    def test_exact_copies_reserve_capacity_before_corrections_in_both_orders(self):
        changed = row(**{"Жилая площадь": 999})
        for history in ([changed, row()], [row(), changed]):
            with self.subTest(history=history), tempfile.TemporaryDirectory() as directory:
                primary = pd.DataFrame([row()])
                admitted, audit = select_rv_supplements(primary, [("History", pd.DataFrame(history))])
                self.assertEqual(admitted[1]["Жилая площадь"].tolist(), [999])
                self.assertEqual(admitted[1].index.tolist(), [0 if history[0]["Жилая площадь"] == 999 else 1])
                self.assertEqual(audit["supplements"]["History"]["exact"], 1)
                self.assertEqual(audit["supplements"]["History"]["corrected"], 0)
                path, canonical = self.canonical(directory, [row()], history)
                validate_primary_rv(path, canonical)
                self.assertEqual(canonical.loc[canonical.source_sheet.eq("History"), "Жилая площадь"].tolist(), [999])
                # The former one-pass selector retained the exact 1000 copy
                # when 999 appeared first. The oracle must reject that result.
                old_wrong = canonical.loc[canonical.source_sheet.eq("Реестр РВ")].iloc[[0]].copy()
                old_wrong["source_sheet"] = "History"
                old_wrong["source_row"] = 2 if history[0]["Жилая площадь"] == 1000 else 3
                bad = pd.concat([canonical.loc[canonical.source_sheet.eq("Реестр РВ")], old_wrong])
                with self.assertRaisesRegex(ValueError, "required historical"):
                    validate_primary_rv(path, bad)

    def test_exact_numeric_text_normalization_keeps_every_primary_repeat(self):
        primary = pd.DataFrame([row(), row()])
        copies = pd.DataFrame([row(**{"УИН": "101.0", "№ РВ": "  PERMIT-1 ", "Общая площадь": "1\xa0200,0",
                                      "Жилая площадь": "1 000", "Год ввода по Мосстату": "2026"}), row()])
        admitted, audit = select_rv_supplements(primary, [("History", copies)])
        self.assertEqual(len(admitted[0]), 2)
        self.assertTrue(admitted[1].empty)
        self.assertEqual(audit["supplements"]["History"]["exact"], 2)

    def test_anonymous_old_internal_repeats_and_cross_sheet_capacity_are_preserved(self):
        older = row(**{"УИН": None, "№ РВ": None, "Год ввода по Мосстату": 2019})
        historical = pd.DataFrame([older, older])
        copies = pd.DataFrame([older, older, older])
        admitted, audit = select_rv_supplements(pd.DataFrame([row()]), [("Older", historical), ("Copy", copies)])
        self.assertEqual(len(admitted[1]), 2)
        self.assertEqual(len(admitted[2]), 1)
        self.assertEqual(audit["supplements"]["Copy"]["exact"], 2)
        self.assertEqual(admitted[2].index.tolist(), [2])

    def test_unique_corrected_versions_require_all_other_stable_fields(self):
        primary = pd.DataFrame([row(**{"УИН": index + 1, "№ РВ": f"P-{index + 1}"}) for index in range(3)])
        historical = primary.copy()
        historical.loc[0, "№ РВ"] = "older-number"
        historical.loc[1, "Общая площадь"] = 1199
        historical.loc[2, "Жилая площадь"] = 999
        admitted, audit = select_rv_supplements(primary, [("History", historical)])
        self.assertTrue(admitted[1].empty)
        self.assertEqual(audit["supplements"]["History"]["corrected"], 3)
        # Same UIN/permit alone does not justify removing another physical object.
        other = pd.DataFrame([row(**{"Подтип объекта": "ИЖС", "Общая площадь": 900, "Жилая площадь": 800})])
        self.assertEqual(len(select_rv_supplements(primary, [("History", other)])[0][1]), 1)

    def test_ambiguous_corrected_versions_are_retained_and_reported(self):
        primary = pd.DataFrame([row(**{"№ РВ": "A"}), row(**{"№ РВ": "B"})])
        admitted, audit = select_rv_supplements(primary, [("History", pd.DataFrame([row(**{"№ РВ": "C"})]))])
        self.assertEqual(len(admitted[1]), 1)
        self.assertEqual(audit["supplements"]["History"]["ambiguous"], 1)

    def test_shifted_2018_19_cells_retain_year_month_and_physical_indexes(self):
        date = pd.Timestamp("1899-12-30") + pd.Timedelta(days=2018)
        historical = pd.DataFrame([row(**{"Год ввода по Мосстату": "декабрь", "Дата ввода по Мосстату": date})], index=[23])
        normalized = normalize_rv_years(historical)
        self.assertEqual(normalized.loc[23, "Год ввода по Мосстату"], 2018)
        self.assertEqual(normalized.loc[23, "Месяц ввода по Мосстату"], "декабрь")
        self.assertEqual(historical.loc[23, "Год ввода по Мосстату"], "декабрь")

    def test_primary_matching_precedes_filters_and_gate_checks_raw_physical_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = root / "data/raw/realty/nashdom"
            raw.mkdir(parents=True)
            path = raw / "monitoring_2_0_20261013.xlsx"
            excluded = row(**{"УИН": 102, "№ РВ": "Excluded", "Группа компаний": "False"})
            primary = pd.DataFrame([row(), row(), excluded])
            old = row(**{"Год ввода по Мосстату": 2019, "УИН": None, "№ РВ": None})
            history = pd.DataFrame([row(), row(), excluded, old, old])
            with pd.ExcelWriter(path) as writer:
                primary.to_excel(writer, sheet_name="Реестр РВ", index=False)
                pd.DataFrame([row()]).to_excel(writer, sheet_name="Реестр ОКС", index=False)
                history.to_excel(writer, sheet_name="History", index=False)
            access = DataAccess(DataContext(root, use_marts=False))
            chosen = access.load_monitoring_2_0()["rv"]
            self.assertEqual(len(chosen), 4)  # 2 primary repeats and 2 unmatched anonymous old rows.
            self.assertFalse(chosen["УИН"].eq(102).any())
            self.assertEqual(chosen.loc[chosen.source_sheet.eq("History"), "source_row"].tolist(), [5, 6])
            self.assertEqual(validate_primary_rv(path, chosen)["primary_rows"], 2)
            sheets = dict(_read_sheets(path))
            self.assertEqual(len(sheets["Реестр РВ"]), 3)  # Admission does not perform metric filters.
            self.assertEqual(sheets["History"].index.tolist(), [3, 4])
            bad = pd.concat([chosen, chosen.loc[chosen.source_sheet.eq("Реестр РВ")].iloc[[0]]], ignore_index=True)
            with self.assertRaisesRegex(ValueError, "primary population"):
                validate_primary_rv(path, bad)
            copied = chosen.iloc[[0]].copy()
            copied["source_sheet"], copied["source_row"] = "History", 2
            with self.assertRaisesRegex(ValueError, "copied"):
                validate_primary_rv(path, pd.concat([chosen, copied]))

    def test_independent_gate_rejects_each_superseded_corrected_version(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "monitoring.xlsx"
            primary = pd.DataFrame([row(**{"УИН": index + 1, "№ РВ": f"P-{index + 1}"}) for index in range(3)])
            historical = primary.copy()
            historical.loc[0, "№ РВ"] = "older-number"
            historical.loc[1, "Общая площадь"] = 1199
            historical.loc[2, "Жилая площадь"] = 999
            with pd.ExcelWriter(path) as writer:
                primary.to_excel(writer, sheet_name="Реестр РВ", index=False)
                pd.DataFrame([row()]).to_excel(writer, sheet_name="Реестр ОКС", index=False)
                historical.to_excel(writer, sheet_name="History", index=False)
            access = DataAccess(DataContext(root, use_marts=False))
            canonical = access.load_monitoring_2_0(source_path=path)["rv"]
            validate_primary_rv(path, canonical)
            for index in range(3):
                bad = canonical.iloc[[index]].copy()
                for field in historical.columns:
                    bad[field] = historical.iloc[index][field]
                bad["source_sheet"], bad["source_row"] = "History", index + 2
                bad["category_жилое"] = float(historical.iloc[index]["Жилая площадь"])
                bad["category_моп"] = float(historical.iloc[index]["Общая площадь"] - historical.iloc[index]["Жилая площадь"])
                with self.subTest(index=index), self.assertRaisesRegex(ValueError, "corrected"):
                    validate_primary_rv(path, pd.concat([canonical, bad]))

    def test_required_unique_anonymous_and_internal_repeat_history_cannot_disappear(self):
        for anonymous in (False, True):
            old = row(**{"Год ввода по Мосстату": 2019, "УИН": None if anonymous else 200,
                         "№ РВ": None if anonymous else "Old-2019"})
            with self.subTest(anonymous=anonymous), tempfile.TemporaryDirectory() as directory:
                path, canonical = self.canonical(directory, [row()], [old, old])
                validate_primary_rv(path, canonical)
                historical = canonical.loc[canonical.source_sheet.eq("History")]
                self.assertEqual(len(historical), 2)
                for missing in (historical.index, historical.index[:1]):
                    with self.subTest(missing=list(missing)), self.assertRaisesRegex(ValueError, "required historical"):
                        validate_primary_rv(path, canonical.drop(missing))

    def test_all_historical_areas_and_consumed_categories_match_physical_cells(self):
        old = row(**{"Год ввода по Мосстату": 2019, "УИН": 200, "№ РВ": "Old-2019"})
        with tempfile.TemporaryDirectory() as directory:
            path, canonical = self.canonical(directory, [row()], [old])
            validate_primary_rv(path, canonical)
            historical = canonical.index[canonical.source_sheet.eq("History")][0]
            for field in ("Общая площадь", "Жилая площадь", "category_жилое", "category_моп",
                          "category_нежилое_в_жилом", "category_нежилое_отдельное"):
                bad = canonical.copy()
                bad.loc[historical, field] = 999999999
                with self.subTest(field=field), self.assertRaisesRegex(ValueError, "area differs|area category"):
                    validate_primary_rv(path, bad)


if __name__ == "__main__":
    unittest.main()
