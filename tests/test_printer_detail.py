from datetime import datetime, timezone
import json
import os
import re
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
            self.assertIn(b'href="/printers/Mercury%201"', client.get("/").data)
            processors = app.app.template_context_processors[None]
            with patch.dict(app.app.template_context_processors, {None: [*processors, lambda: {
                "kcd_demo_mode": True, "demo_endpoint_available": lambda *_args: False,
            }]}):
                demo_html = client.get("/").data
            self.assertNotIn(b'href="/printers/', demo_html)
            self.assertIn(b"Mercury 1", demo_html)

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

    def test_pagination_is_stable_for_equal_timestamps_and_keeps_filters(self):
        from core import db
        from core.config import TIMEZONE_OBJ
        from core.registration import register_printer
        import app

        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"KCD_STORAGE_BACKEND": "sql"}), patch.object(
            db, "_db_path", return_value=str(Path(tmp) / "kcd.db")
        ):
            register_printer("Mercury 1", "http://fixture:7125")
            with db.connect_db() as conn:
                for i in range(30):
                    db.upsert_job(conn, dict(
                        job_uid=f"page-{i:02}", printer="Mercury 1", filename=f"page-{i:02}.gcode",
                        timestamp=datetime(2025, 6, 12, 12, tzinfo=TIMEZONE_OBJ).timestamp(),
                        status="completed", duration_seconds=0,
                        time_cost=0, material_cost=0, total_cost=0,
                    ))
            client = app.app.test_client()
            path = "/printers/Mercury%201?start=2025-06-12&end=2025-06-12&compare=1"
            first = client.get(path).get_data(as_text=True)
            second = client.get(path + "&page=2").get_data(as_text=True)
            first_files = set(re.findall(r"page-\d{2}\.gcode", first))
            second_files = set(re.findall(r"page-\d{2}\.gcode", second))
            self.assertEqual(len(first_files), 25)
            self.assertEqual(len(second_files), 5)
            self.assertFalse(first_files & second_files)
            self.assertEqual(first_files | second_files, {f"page-{i:02}.gcode" for i in range(30)})
            self.assertIn("start=2025-06-12", first)
            self.assertIn("end=2025-06-12", first)
            self.assertIn("compare=1", first)
            self.assertEqual(client.get(path + "&page=999").get_data(as_text=True), second)
            self.assertEqual(client.get(path + "&page=invalid").get_data(as_text=True), first)

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
            status["print_stats"].pop("print_duration")
            connection.__enter__.return_value.read.return_value = json.dumps({"result": {"status": status}}).encode()
            item = monitoring.probe("http://fixture:7129")
            self.assertIsNone(item["print_seconds"])
            self.assertIsNone(item["filament_mm"])

    def test_live_estimate_keeps_missing_measurements_separate_from_zero(self):
        from core import monitoring
        import app

        for seconds in (None, 0):
            with self.subTest(seconds=seconds), patch.object(app, "get_canonical_printer_names", return_value={"Fixture"}), patch.object(
                monitoring, "statuses", return_value={"Fixture": dict(connected=True, state="printing", print_seconds=seconds, total_seconds=seconds, filament_mm=0)}
            ), patch.object(app.live, "get_job", return_value=None), patch.object(
                app.thumbs, "resolve_moonraker_base_url", return_value="http://fixture"
            ), patch.object(
                app.pricing, "_get_effective_filament_pricing", return_value=dict(grams_per_meter=3, filament_mode="per_meter", filament_rate=0)
            ), patch.object(app.pricing, "_include_paused_time_for_printer", return_value=False), patch.object(
                app.pricing, "compute_live_time_cost", return_value=0
            ):
                result = app.app.test_client().get("/api/printer-status").get_json()["printers"]["Fixture"]
            self.assertEqual(result["material_cost"], 0)
            self.assertEqual(result["total_cost"], None if seconds is None else 0)
