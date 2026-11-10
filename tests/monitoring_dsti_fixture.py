"""Only commissioning cells A4:Q8 from the user's Monitoring 2.0 copy.

Read-only comparison against the source: SHA256
f9fb4b48a3fdd0e5d8d23da1a63a236f29263243a1ed3d704f32992e6e7b721f.
No financial-discipline workbook or financial rows are distributed.
"""
from datetime import datetime


DSTI_ROWS = [
    ["№", "Показатель", "Единица измерения", "Период расчета",
     "Динамика\n за последнюю неделю\n (28.10.26-03.11.26)",
     "1 квартал 2026 (факт)", "1 квартал 2026 (план)", "% выполнения плана",
     "2 квартал 2026 (факт)", "2 квартал 2026 (план)", "% выполнения плана",
     "3 квартал 2026 (факт)", "3 квартал 2026 (план)", "% выполнения плана",
     "4 квартал 2026 (факт)", "4 квартал 2026 (план)", "% выполнения плана"],
    [datetime(2026, 1, 1), "Протяженность дорог", "км", "за текущий период", 0.4,
     10.2, 5.1, 2, 10.8, 13.9, 0.78, 24.6, 21.6, 1.14, 1.5, 34, 0.04],
    [datetime(2026, 2, 1), "Кол-во искусственных сооружений", "ед", "за текущий период", None,
     4, 4, 1, 3, 6, 0.5, 9, 7, 1.29, None, 10, 0],
    [datetime(2026, 3, 1), "Кол-во внеуличных пеш.переходов", "ед", "за текущий период", 3,
     5, 2, 2.5, 1, 5, 0.2, 3, 8, 0.38, 3, 19, 0.16],
    [datetime(2026, 4, 1), "Кол-во очистных сооружений", "ед", "за текущий период", None,
     3, 5, 0.6, 7, 5, 1.4, 5, 9, 0.56, None, 9, 0],
]


def add_monitoring_dsti(book):
    tab = book.create_sheet("ДСТИ")
    for row, values in enumerate(DSTI_ROWS, 4):
        for col, value in enumerate(values, 1):
            if value is not None:
                tab.cell(row, col, value)
    return tab
