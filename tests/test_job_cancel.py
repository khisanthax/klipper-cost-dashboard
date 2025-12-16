import csv
import os
import tempfile
import unittest
from unittest.mock import patch


class JobCancelTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.csv_path = os.path.join(self._tmp.name, "print_costs.csv")

        from core.config import HEADERS

        with open(self.csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=HEADERS)
            writer.writeheader()

        import app as app_module

        self.app_module = app_module
        self._orig_csv_file = app_module.CSV_FILE
        self._orig_data_dir = getattr(app_module, "DATA_DIR", None)
        app_module.CSV_FILE = self.csv_path
        if self._orig_data_dir is not None:
            app_module.DATA_DIR = self._tmp.name

        # Isolate core.live persistence (live_jobs.json) to the temp dir.
        from core import live as live_module

        self.live_module = live_module
        self._orig_live_data_dir = live_module.DATA_DIR
        self._orig_live_file = live_module.LIVE_JOBS_FILE
        live_module.DATA_DIR = self._tmp.name
        live_module.LIVE_JOBS_FILE = os.path.join(self._tmp.name, "live_jobs.json")
        live_module._jobs = {}
        live_module._save_state()

        self.client = app_module.app.test_client()

    def tearDown(self):
        # Restore app module globals
        self.app_module.CSV_FILE = self._orig_csv_file
        if self._orig_data_dir is not None:
            self.app_module.DATA_DIR = self._orig_data_dir

        # Restore core.live globals
        self.live_module.DATA_DIR = self._orig_live_data_dir
        self.live_module.LIVE_JOBS_FILE = self._orig_live_file
        self.live_module._jobs = {}
        self.live_module._load_state()

        self._tmp.cleanup()

    def _read_rows(self):
        with open(self.csv_path, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))

    def test_job_cancel_logs_canceled_and_clears_live_job(self):
        # Create a live job so cancel can compute elapsed_seconds.
        self.live_module.start_job("SV08", "Cube_PETG_0.2_11m55s.gcode", start_time=1000.0)

        def fake_compute_costs(_printer_name, _duration_seconds, _filament_mm):
            return {"total_cost": 1.23}

        with patch.object(self.app_module, "compute_costs", fake_compute_costs), patch.object(
            self.app_module, "get_canonical_printer_names", lambda: {"SV08"}
        ), patch.object(self.live_module.time, "time", lambda: 1005.0):
            resp = self.client.post(
                "/job-cancel",
                json={"printer_name": "SV08", "filename": "Cube_PETG_0.2_11m55s.gcode"},
            )

        self.assertEqual(resp.status_code, 200)
        payload = resp.get_json()
        self.assertTrue(payload.get("success"))

        # Live state should be cleared.
        self.assertIsNone(self.live_module.get_job("SV08"))
        self.assertEqual(self.live_module.list_active_jobs(), [])

        # History should include a canceled row.
        rows = self._read_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].get("printer"), "SV08")
        self.assertEqual(rows[0].get("filename"), "Cube_PETG_0.2_11m55s.gcode")
        self.assertEqual(rows[0].get("status"), "canceled")
        self.assertEqual(rows[0].get("failure_reason"), "")
        self.assertEqual(float(rows[0].get("duration_seconds") or 0.0), 5.0)

    def test_job_cancel_twice_is_idempotent(self):
        self.live_module.start_job("SV08", "Cube_PETG_0.2_11m55s.gcode", start_time=1000.0)

        def fake_compute_costs(_printer_name, _duration_seconds, _filament_mm):
            return {"total_cost": 1.23}

        with patch.object(self.app_module, "compute_costs", fake_compute_costs), patch.object(
            self.app_module, "get_canonical_printer_names", lambda: {"SV08"}
        ), patch.object(self.live_module.time, "time", lambda: 1005.0):
            resp1 = self.client.post(
                "/job-cancel",
                json={"printer_name": "SV08", "filename": "Cube_PETG_0.2_11m55s.gcode"},
            )
            resp2 = self.client.post(
                "/job-cancel",
                json={"printer_name": "SV08", "filename": "Cube_PETG_0.2_11m55s.gcode"},
            )

        self.assertEqual(resp1.status_code, 200)
        self.assertEqual(resp2.status_code, 200)
        rows = self._read_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].get("status"), "canceled")

    def test_job_cancel_with_blank_filename_uses_active_job_filename(self):
        self.live_module.start_job("SV08", "Cube_PETG_0.2_11m55s.gcode", start_time=1000.0)

        def fake_compute_costs(_printer_name, _duration_seconds, _filament_mm):
            return {"total_cost": 1.23}

        with patch.object(self.app_module, "compute_costs", fake_compute_costs), patch.object(
            self.app_module, "get_canonical_printer_names", lambda: {"SV08"}
        ), patch.object(self.live_module.time, "time", lambda: 1005.0):
            resp = self.client.post(
                "/job-cancel",
                json={"printer_name": "SV08", "filename": ""},
            )

        self.assertEqual(resp.status_code, 200)
        rows = self._read_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].get("filename"), "Cube_PETG_0.2_11m55s.gcode")

    def test_job_cancel_after_completed_does_not_append_canceled(self):
        # Simulate a completed history row and no live job.
        from core.config import HEADERS

        row = {h: "" for h in HEADERS}
        row.update(
            {
                "timestamp": "1700000000",
                "job_uid": "test-job-uid-completed",
                "printer": "SV08",
                "filename": "Cube_PETG_0.2_11m55s.gcode",
                "duration_seconds": "3600",
                "filament_mm": "10000",
                "status": "completed",
            }
        )
        with open(self.csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=HEADERS)
            writer.writeheader()
            writer.writerow(row)

        with patch.object(self.app_module, "get_canonical_printer_names", lambda: {"SV08"}), patch.object(
            self.app_module.time, "time", lambda: 1700000100.0
        ):
            resp = self.client.post(
                "/job-cancel",
                json={"printer_name": "SV08", "filename": "Cube_PETG_0.2_11m55s.gcode"},
            )

        self.assertEqual(resp.status_code, 200)
        rows = self._read_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].get("status"), "completed")

    def test_job_cancel_rejects_printer_that_looks_like_filename(self):
        with patch.object(self.app_module, "get_canonical_printer_names", lambda: {"SV08"}):
            resp = self.client.post(
                "/job-cancel",
                json={"printer_name": "Cube_PETG_0.2_11m55s.gcode", "filename": "real.gcode"},
            )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self._read_rows(), [])
