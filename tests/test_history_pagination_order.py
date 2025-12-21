import unittest
from datetime import datetime


class HistoryPaginationOrderTests(unittest.TestCase):
    def test_sort_before_paginate_desc(self):
        import app as kcd_app

        # Same date: 21:12 should sort before 10:35.
        dt_new = datetime(2025, 12, 20, 21, 12, 0, tzinfo=kcd_app.TIMEZONE_OBJ)
        dt_old = datetime(2025, 12, 20, 10, 35, 0, tzinfo=kcd_app.TIMEZONE_OBJ)

        rows = [
            {"timestamp_raw": dt_old.timestamp(), "timestamp": "2025-12-20 10:35:00"},
            {"timestamp_raw": dt_new.timestamp(), "timestamp": "2025-12-20 21:12:00"},
        ]

        sorted_rows = kcd_app._sort_history_rows(rows)
        self.assertEqual(sorted_rows[0]["timestamp"], "2025-12-20 21:12:00")
        self.assertEqual(sorted_rows[1]["timestamp"], "2025-12-20 10:35:00")

        # Pagination after sorting: per_page=1 should put 21:12 on page 1.
        page1, _meta1 = kcd_app._paginate(sorted_rows, page=1, per_page=1)
        page2, _meta2 = kcd_app._paginate(sorted_rows, page=2, per_page=1)
        self.assertEqual(page1[0]["timestamp"], "2025-12-20 21:12:00")
        self.assertEqual(page2[0]["timestamp"], "2025-12-20 10:35:00")


if __name__ == "__main__":
    unittest.main()

