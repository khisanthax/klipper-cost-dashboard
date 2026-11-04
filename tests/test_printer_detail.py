from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class PrinterDetailTests(unittest.TestCase):
    def test_history_uses_exact_printer_date_range_and_saved_costs(self):
        from core import db, history_repo
        from core.config import TIMEZONE_OBJ
        from core.registration import register_printer
        from core.sql_only import SqlOnlyFilesystemMonitor
        import app

        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"KCD_STORAGE_BACKEND": "sql"}), patch.object(
            db, "_db_path", return_value=str(Path(tmp) / "kcd.db")
        ):
            for name, port in (("Mercury 1", 7125), ("Mercury 2", 7129)):
                register_printer(name, f"http://fixture:{port}")
            jobs = [
                ("Mercury 1", "selected-cancel.gcode", 12, 23, "cancelled", None),
                ("Mercury 1", "outside-range.gcode", 13, 0, "failed", 0),
                ("Mercury 2", "other-printer.gcode", 12, 19, "completed", 9),
            ]
            with db.connect_db() as conn:
                for name, filename, day, hour, status, cost in jobs:
                    db.upsert_job(conn, dict(
                        printer=name, job_uid=filename, filename=filename, status=status,
                        timestamp=datetime(2025, 6, day, hour, tzinfo=TIMEZONE_OBJ).timestamp(),
                        duration_seconds=3600, filament_mm=1000, grams_per_meter=3,
                        time_cost=cost, material_cost=cost, total_cost=cost,
                    ))
            client = app.app.test_client()
            with SqlOnlyFilesystemMonitor(), patch.object(
                history_repo, "compute_job_cost_fields", side_effect=AssertionError("Detail must not reprice history")
            ):
                result = client.get("/printers/Mercury%201?start=2025-06-12&end=2025-06-12&compare=1")
            self.assertEqual(result.status_code, 200)
            self.assertIn(b"selected-cancel.gcode", result.data)
            self.assertIn(b"Canceled", result.data)
            self.assertIn(b"Unpriced", result.data)
            self.assertIn(b"3.00 g", result.data)
            self.assertIn(b"2025-06-11 to 2025-06-11", result.data)
            self.assertNotIn(b"outside-range.gcode", result.data)
            self.assertNotIn(b"other-printer.gcode", result.data)
            self.assertEqual(client.get("/printers/Unknown").status_code, 404)
            self.assertEqual(client.get("/printers/Mercury%201?start=invalid").status_code, 400)

    def test_calendar_utilization_handles_dst(self):
        from core.printer_reports import report_periods, summarize

        try:
            eastern = ZoneInfo("America/New_York")
        except ZoneInfoNotFoundError:
            self.skipTest("Host does not have IANA timezone data")
        now = datetime(2025, 3, 10, tzinfo=eastern)
        period, previous = report_periods("2025-03-09", "2025-03-09", now)
        self.assertEqual((period[1].timestamp() - period[0].timestamp()) / 3600, 23)
        self.assertEqual(previous[1], period[0])
        self.assertAlmostEqual(summarize([{"duration_seconds": 3600}], period, now)["utilization"], 100 / 23)

    def test_summary_preserves_unknowns_and_saved_zero(self):
        from core.printer_reports import report_periods, summarize

        now = datetime(2025, 3, 10, tzinfo=timezone.utc)
        period, _ = report_periods("2025-03-09", "2025-03-09", now)
        result = summarize([
            {"status": "error", "duration_seconds": 3600, "time_cost": 0},
            {"job_outcome": "canceled", "total_cost": None},
        ], period, now)
        self.assertEqual(result["counts"]["Failed"], 1)
        self.assertEqual(result["counts"]["Canceled"], 1)
        self.assertAlmostEqual(result["utilization"], 100 / 24)
        self.assertIsNone(result["totals"]["total_cost"])
        self.assertEqual(result["totals"]["time_cost"], 0)
        self.assertEqual(result["unknown"]["hours"], 1)

    def test_progress_is_optional_and_does_not_override_identity(self):
        from core import monitoring
        from unittest.mock import MagicMock

        connection = MagicMock()
        status = {
            "print_stats": {"state": "printing", "print_duration": 600},
            "gcode_macro _KCD_VARS": {"printer_name": "Mercury 2"},
            "display_status": {"progress": 0.5},
        }
        connection.__enter__.return_value.read.return_value = json.dumps({"result": {"status": status}}).encode()
        with patch("urllib.request.urlopen", return_value=connection), patch.object(monitoring, "_cache", {}):
            observations = monitoring.statuses({"Mercury 1": "http://fixture:7125", "Mercury 2": "http://fixture:7129"})
            self.assertFalse(observations["Mercury 1"]["connected"])
            self.assertTrue(observations["Mercury 2"]["identity_verified"])
            self.assertEqual(observations["Mercury 2"]["progress"], 0.5)
            status["display_status"]["progress"] = "invalid"
            connection.__enter__.return_value.read.return_value = json.dumps({"result": {"status": status}}).encode()
            self.assertIsNone(monitoring.probe("http://fixture:7129")["progress"])
