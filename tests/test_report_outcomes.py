"""Monthly reports use saved values and the installation's calendar."""

from contextlib import closing
from datetime import datetime, timedelta, timezone
import csv
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from core import db, reports, reports_repo, storage
from core.config import HEADERS
from core.registration import register_printer


class ReportOutcomeTests(unittest.TestCase):
    def test_monthly_breakdown_distinguishes_unknown_from_zero(self):
        rows = [
            dict(timestamp_raw=1700000000, status="completed", duration_hours="1.1", total_cost="0.1"),
            dict(timestamp_raw=1700000000, status="completed", duration_hours="0.0", total_cost="0.2"),
            dict(timestamp_raw=1700000000, status="cancelled", duration_hours="0.5", total_cost=None),
            dict(timestamp_raw=1700000000, status="error", duration_hours="broken", total_cost="0"),
            dict(timestamp_raw=1700000000, status="printing", duration_hours=None, total_cost="-0.05"),
            dict(timestamp_raw="invalid", status="", duration_hours="", total_cost="NaN"),
        ]
        monthly, unknown = reports.compute_monthly_breakdown(rows)
        self.assertEqual(monthly["count"], 5)
        self.assertEqual([monthly[key] for key in ("completed", "canceled", "failed", "unknown")], [2, 1, 1, 1])
        self.assertEqual(monthly["completion_rate"], 50.0)
        self.assertEqual(monthly["hours_display"], "1.60")
        self.assertEqual(monthly["cost_display"], "0.25")
        self.assertEqual(monthly["missing_hours"], 2)
        self.assertEqual(monthly["missing_cost"], 1)
        self.assertEqual(unknown["label"], "Unknown date")
        self.assertIsNone(unknown["completion_rate"])
        self.assertIsNone(unknown["cost_display"])

    def test_sql_calendar_matches_csv_and_cache_refreshes_after_status_edit(self):
        boundary = datetime(2026, 11, 1, 0, 30, tzinfo=timezone.utc).timestamp()
        local = timezone(timedelta(hours=-4))
        fixture = {
            "job_uid": "month-boundary", "printer": "Fixture", "filename": "part.gcode",
            "timestamp": boundary, "status": "printing", "duration_hours": 0.5, "total_cost": 0.0,
        }
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(storage, "_TIMEZONE_OBJ", local),
            patch.object(db, "_db_path", lambda: str(Path(tmp) / "kcd.db")),
            patch.dict(os.environ, {"KCD_STORAGE_BACKEND": "sql", "KCD_REPORTS_CACHE_TTL_SECONDS": "300"}),
        ):
            register_printer("Fixture", "http://127.0.0.1:1")
            with closing(db.connect_db()) as conn, conn:
                db.upsert_job(conn, fixture)
            with closing(db.connect_db()) as conn:
                sql_month = reports_repo._sql_monthly_breakdown(conn, "", [])[0]
            self.assertEqual(sql_month["label"], "2026-10")
            self.assertEqual(sql_month["unknown"], 1)
            self.assertEqual(sql_month["cost_display"], "0.00")

            csv_path = Path(tmp) / "fixture.csv"
            with csv_path.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=HEADERS)
                writer.writeheader()
                writer.writerow(fixture)
            with patch.dict(os.environ, {"KCD_STORAGE_BACKEND": "csv"}):
                csv_rows, error = storage.load_rows_raw(str(csv_path), normalize_status=False)
            self.assertIsNone(error)
            self.assertEqual(reports.compute_monthly_breakdown(csv_rows)[0], sql_month)

            def report():
                return reports_repo.get_reports_data_range(
                    start_dt=None, end_dt=None, range_label="All", quick_range="all", backend_override="sql"
                )["monthly_breakdown"][0]

            self.assertEqual(report()["unknown"], 1)
            with closing(db.connect_db()) as conn, conn:
                conn.execute("UPDATE jobs SET status='completed', updated_at='2027-01-01' WHERE job_uid=?", ("month-boundary",))
            self.assertEqual(report()["completed"], 1)


if __name__ == "__main__":
    unittest.main()
