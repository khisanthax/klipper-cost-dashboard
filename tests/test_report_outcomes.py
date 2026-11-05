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
    def test_explicit_terminal_status_overrides_stale_job_outcome_without_cost_changes(self):
        rows=[{"timestamp_raw": 1700000000, "status": "canceled", "job_outcome": "completed", "total_cost": None},
              {"timestamp_raw": 1700000000, "status": "failed", "job_outcome": "completed", "total_cost": 0}]
        original=[dict(row) for row in rows]
        month=reports.compute_monthly_breakdown(rows)[0]
        self.assertEqual((month["completed"], month["canceled"], month["failed"]), (0, 1, 1))
        self.assertEqual(month["cost_display"], "0.00")
        self.assertEqual(month["missing_cost"], 1)
        self.assertEqual(rows, original)

    def test_recent_rows_preserve_unpriced_cost_and_saved_estimated_weight(self):
        row = {"printer": "One", "filament_grams": "12.5", "filament_weight_kind": "estimated",
               "filament_meters": "4", "grams_per_meter": None, "filament_profile_id": "changed-profile",
               "material_cost": 0, "total_cost": None, "filament_mode": "per_kg"}
        recent = reports.prepare_recorded_recent([row])[0]
        self.assertEqual(recent["_weight_grams"], 12.5)
        self.assertEqual(recent["_weight_source"], "Saved estimated weight")
        self.assertEqual(recent["_material_cost_display"], "0.00")
        self.assertIsNone(recent["_total_cost_display"])
        self.assertIsNone(recent["total_cost"])

    def test_measurement_cost_components_and_adjacent_month_changes_are_exact(self):
        def row(month, **values):
            return dict(timestamp_raw=datetime(2026, month, 15, tzinfo=timezone.utc).timestamp(),
                        status="completed", duration_hours="1.1", filament_meters="10",
                        grams_per_meter="3", time_cost="0.10", material_cost="0.20", total_cost="0.25", **values)
        prior = row(9)
        current = row(10)
        current.update(filament_grams="40", filament_weight_kind="estimated", material_cost="0.3", total_cost="-0.05")
        months = reports.compute_monthly_breakdown([prior, current])
        self.assertEqual(months[0]["grams_display"], "40.00")
        self.assertEqual(months[0]["grams_change_display"], "+10.00")
        self.assertEqual(months[0]["material_cost_change_display"], "+0.10")
        self.assertEqual(months[0]["cost_change_display"], "-0.30")
        self.assertEqual(months[0]["estimated_weights"], 1)
        self.assertEqual(reports.compute_recorded_totals(months)["cost_display"], "0.20")
        self.assertEqual(reports.compute_recorded_totals(months)["time_cost_display"], "0.20")
        incomplete = dict(current, filament_grams=None, grams_per_meter="broken", total_cost=None)
        months = reports.compute_monthly_breakdown([prior, incomplete])
        self.assertIsNone(months[0]["grams_display"])
        self.assertEqual(months[0]["meters_display"], "10.00")
        self.assertIsNone(months[0]["cost_change_display"])
        self.assertEqual(months[0]["missing_grams"], 1)
        self.assertIsNone(reports.compute_monthly_breakdown([row(8), row(10)])[0]["previous_month"])

    def test_manual_end_date_includes_the_entire_day(self):
        with patch.object(storage, "_TIMEZONE_OBJ", timezone.utc):
            start, end, _, _ = reports.get_date_range_from_params({"start_date": "2026-11-01", "end_date": "2026-11-01"})
        self.assertEqual(start.hour, 0)
        self.assertEqual((end.hour, end.minute, end.second, end.microsecond), (23, 59, 59, 999999))

    def test_dst_calendar_day_includes_both_repeated_hours(self):
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        try:
            zone = ZoneInfo("America/New_York")
        except ZoneInfoNotFoundError:
            self.skipTest("Host has no IANA timezone database; Linux release validation verifies DST")
        with patch.object(storage, "_TIMEZONE_OBJ", zone):
            start, end, _, _ = reports.get_date_range_from_params({"start_date": "2026-11-01", "end_date": "2026-11-01"})
        self.assertAlmostEqual(end.timestamp() - start.timestamp(), 25*3600 - .000001, places=5)

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
            # An older recovery edit with a preserved updated_at also invalidates.
            with closing(db.connect_db()) as conn, conn:
                conn.execute("UPDATE jobs SET total_cost=3.75, time_cost=0, material_cost=3.75 WHERE job_uid=?", ("month-boundary",))
            self.assertEqual(report()["cost_display"], "3.75")

    def test_printer_and_dates_filter_all_reports_with_sql_csv_and_dual_parity(self):
        fixture = [{"job_uid": f"row-{n}", "printer": name, "filename": "same.gcode", "status": status, "job_outcome": "completed",
                    "timestamp": datetime(2026, 10, day, 20, tzinfo=timezone.utc).timestamp(),
                    "duration_hours": 1, "filament_meters": 5, "grams_per_meter": 3,
                    "time_cost": 0, "material_cost": 1.25, "total_cost": 1.25}
                   for n, (name, day, status) in enumerate([("One", 2, "completed"), ("Two", 2, "canceled"), ("One", 3, "failed")])]
        with tempfile.TemporaryDirectory() as tmp, patch.object(db, "_db_path", lambda: str(Path(tmp)/"kcd.db")), patch.object(storage, "_TIMEZONE_OBJ", timezone.utc), patch.dict(os.environ, {"KCD_STORAGE_BACKEND": "sql"}):
            for name in ("One", "Two"):
                register_printer(name, f"http://{name.lower()}:1")
            with closing(db.connect_db()) as conn, conn:
                for row in fixture:
                    db.upsert_job(conn, row)
                before = [tuple(row) for row in conn.execute("SELECT * FROM jobs ORDER BY job_uid")]
            path=Path(tmp)/"history.csv"
            with path.open("w", encoding="utf-8", newline="") as stream:
                writer=csv.DictWriter(stream, fieldnames=HEADERS); writer.writeheader(); writer.writerows(fixture)
            args={"printer": "One", "start_date": "2026-10-02", "end_date": "2026-10-02"}
            sql=reports_repo.get_reports_data(args)
            self.assertEqual(sql["summary"]["total_prints"], 1)
            self.assertEqual(sql["monthly_breakdown"][0]["grams_display"], "15.00")
            self.assertEqual(sql["top_printers"][0]["name"], "One")
            self.assertEqual([row["printer"] for row in sql["recent_jobs"]], ["One"])
            self.assertEqual(reports_repo.get_reports_data(dict(args, printer="Two"))["monthly_breakdown"][0]["canceled"], 1)
            self.assertEqual(reports_repo.get_reports_data(dict(args, start_date="2026-10-03", end_date="2026-10-03"))["monthly_breakdown"][0]["failed"], 1)
            self.assertEqual(reports_repo.get_reports_data(dict(args, printer="missing"))["monthly_breakdown"], [])
            with closing(db.connect_db()) as conn:
                self.assertEqual([tuple(row) for row in conn.execute("SELECT * FROM jobs ORDER BY job_uid")], before)
            for mode in ("csv", "dual"):
                with patch.dict(os.environ, {"KCD_STORAGE_BACKEND": mode, "KCD_REPORTS_BACKEND": "csv"}), patch.object(reports_repo, "CSV_FILE", str(path)):
                    result=reports_repo.get_reports_data(args)
                self.assertEqual(result["monthly_breakdown"], sql["monthly_breakdown"])
                self.assertEqual(result["summary"]["recorded"], sql["summary"]["recorded"])
                self.assertEqual(result["printer_choices"], ["One", "Two"])


if __name__ == "__main__":
    unittest.main()
