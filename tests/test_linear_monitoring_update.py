"""Normal monitoring collection discovers DSTI without importing an attachment."""
from contextlib import redirect_stdout
from datetime import date, datetime
from io import BytesIO, StringIO
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from openpyxl import Workbook

import nashdom_checker as nc
from backend.linear_service import LinearService
from pipeline.commissioning_forecasts import FORECAST_FIELDS
from pipeline.data_access import DataContext
from pipeline.linear_dashboard import HISTORY_SHEET
from tests.monitoring_dsti_fixture import add_monitoring_dsti


def monitoring_bytes(*, dsti):
    book = Workbook()
    book.remove(book.active)
    rv = book.create_sheet("Реестр РВ")
    rv.append(["УИН", "Группа компаний", "Год ввода по Мосстату", "Отрасли", "Группировка", "Общая площадь", "Жилая площадь"])
    rv.append(["test-rv", "Developer", 2026, "Жилые объекты", "Жилье", 100, 70])
    oks = book.create_sheet("Реестр ОКС")
    oks.append(["УИН", "Группа компаний", "Назначение", "Общая площадь", "Жилая площадь"])
    oks.append(["test-oks", "Developer", "Жилье", 100, 70])
    history = book.create_sheet(HISTORY_SHEET)
    for cell, value in {"D254": "Ввод линейные объекты", "D255": "Показатель", "E255": "ед. изм",
                        "F255": 2025, "G255": 2026, "D256": "дороги", "E256": "км",
                        "F256": 105.3, "G256": 45.6}.items():
        history[cell] = value
    forecasts = book.create_sheet("Прогнозы")
    forecasts["D2"] = 2026
    for row, (_, label) in FORECAST_FIELDS.items():
        forecasts.cell(row, 3, label)
    for row, value in ((40, 100), (41, 28), (43, 29)):
        forecasts.cell(row, 4, value)
    if dsti:
        add_monitoring_dsti(book)
    stream = BytesIO()
    book.save(stream)
    book.close()
    return stream.getvalue()


class MonitoringLinearUpdateTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.raw = self.root / "data/raw/realty/nashdom"
        self.raw.mkdir(parents=True)
        self.old = self.raw / "monitoring_2_0_20261016.xlsx"
        self.old_content = monitoring_bytes(dsti=False)
        self.old.write_bytes(self.old_content)
        os.utime(self.old, (1, 1))
        self.service = LinearService(DataContext(self.root), check_interval=0)

    def collect_and_check(self):
        body = monitoring_bytes(dsti=True)
        state = {"monitoring_2_0": {"filename": self.old.name, "sha256": nc._sha256_bytes(self.old_content),
                                   "size_bytes": len(self.old_content)}}
        response = SimpleNamespace(status_code=200, url=nc.GSHEETS_EXPORT_URL, content=body,
                                   headers={"content-type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"})
        with patch.object(nc, "DOWNLOAD_DIR", self.raw), \
                patch.object(nc, "MONITORING_CHANGES_LOG", self.root / "changes.jsonl"), \
                patch.object(nc, "datetime") as collector_clock, \
                patch("backend.linear_service.date") as service_clock, \
                patch.dict(os.environ, {"NASHDOM_FORCE": "0"}), \
                patch.object(nc.requests, "get", return_value=response) as request, redirect_stdout(StringIO()):
            collector_clock.now.return_value = datetime(2026, 11, 10, 12)
            service_clock.today.return_value = date(2026, 11, 10)
            before = self.service.dashboard()
            self.assertEqual(before["metrics"][0]["fact"], 45.6)
            self.assertEqual(before["metrics"][0]["factSource"], "monitoring")
            files, ok = nc.fetch_monitoring_2_0(state)
            self.assertTrue(ok)
            self.assertEqual(files, [self.raw / "monitoring_2_0_20261110.xlsx"])
            self.assertEqual(files[0].read_bytes(), body)
            request.assert_called_once_with(nc.GSHEETS_EXPORT_URL, timeout=120, allow_redirects=True)
            after = self.service.dashboard()
        self.assertNotEqual(before["version"], after["version"])
        for metric, fact, forecast in zip(after["metrics"], [47.1, 16, 12], [100, 28, 29]):
            self.assertAlmostEqual(metric["fact"], fact)
            self.assertEqual(metric["factSource"], "dsti")
            self.assertEqual(metric["forecast"], forecast)
        self.assertTrue(after["sources"][0]["file"].endswith(files[0].name))
        self.assertEqual(after["sources"][0]["sheet"], "ДСТИ")

    def test_ordinary_download_replaces_old_october_monitoring_without_manual_import(self):
        self.collect_and_check()

    def test_same_day_sheet_addition_is_downloaded_even_when_registries_are_unchanged(self):
        target = self.raw / "monitoring_2_0_20261110.xlsx"
        self.old.rename(target)
        self.old = target
        self.collect_and_check()


if __name__ == "__main__":
    unittest.main()
