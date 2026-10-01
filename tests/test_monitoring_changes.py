import unittest

from pipeline.monitoring_changes import compare_monitoring_snapshots


def _snapshot(items):
    grouped = {}
    for key, value in items:
        grouped.setdefault(key, []).append(value)
    return {"items": grouped, "row_counts": {"Реестр РВ": len(items)}}


class MonitoringChangesChecks(unittest.TestCase):
    def test_reordering_does_not_create_changes(self):
        first = (("Реестр РВ", "1", "rv-1"), {"sheet": "Реестр РВ", "uin": "1"})
        second = (("Реестр РВ", "2", "rv-2"), {"sheet": "Реестр РВ", "uin": "2"})
        event = compare_monitoring_snapshots(
            _snapshot([first, second]), _snapshot([second, first]),
            previous_file="old.xlsx", current_file="new.xlsx",
        )
        self.assertEqual(event["added_count"], 0)
        self.assertEqual(event["removed_count"], 0)

    def test_duplicate_business_keys_are_counted(self):
        key = ("Реестр РВ", "1", "rv-1")
        item = {"sheet": "Реестр РВ", "uin": "1", "object": "Дом"}
        event = compare_monitoring_snapshots(
            _snapshot([(key, item)]), _snapshot([(key, item), (key, item)]),
            previous_file="old.xlsx", current_file="new.xlsx",
        )
        self.assertEqual(event["added_count"], 1)
        self.assertEqual(event["removed_count"], 0)

    def test_added_and_removed_rows_keep_descriptions(self):
        old_key = ("Реестр РВ", "1", "rv-1")
        new_key = ("Реестр РВ", "2", "rv-2")
        old_item = {"sheet": "Реестр РВ", "uin": "1", "object": "Старый объект"}
        new_item = {"sheet": "Реестр РВ", "uin": "2", "object": "Новый объект"}
        event = compare_monitoring_snapshots(
            _snapshot([(old_key, old_item)]), _snapshot([(new_key, new_item)]),
            previous_file="old.xlsx", current_file="new.xlsx",
        )
        self.assertEqual(event["added"][0]["object"], "Новый объект")
        self.assertEqual(event["removed"][0]["object"], "Старый объект")


if __name__ == "__main__":
    unittest.main()
