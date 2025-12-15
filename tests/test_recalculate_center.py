import csv
import os
import tempfile
import unittest
from unittest.mock import patch


class RecalculateCenterPhase1Tests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.csv_path = os.path.join(self._tmp.name, "print_costs.csv")

        from core.config import HEADERS

        row = {h: "" for h in HEADERS}
        row.update(
            {
                "timestamp": "1700000000",
                "job_uid": "test-job-uid-1",
                "printer": "SV08",
                "filename": "Cube_PETG_0.2_11m55s.gcode",
                "duration_seconds": "3600",
                "filament_mm": "10000",
                "status": "completed",
                "total_cost": "0.00",
            }
        )

        with open(self.csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=HEADERS)
            writer.writeheader()
            writer.writerow(row)

        import app as app_module

        self.app_module = app_module
        self._orig_csv_file = app_module.CSV_FILE
        self._orig_data_dir = app_module.DATA_DIR
        app_module.CSV_FILE = self.csv_path
        app_module.DATA_DIR = self._tmp.name
        self.client = app_module.app.test_client()

    def tearDown(self):
        self.app_module.CSV_FILE = self._orig_csv_file
        self.app_module.DATA_DIR = self._orig_data_dir
        self._tmp.cleanup()

    def _read_total_cost(self):
        from core.config import HEADERS

        with open(self.csv_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].get("job_uid"), "test-job-uid-1")
        self.assertEqual(set(rows[0].keys()), set(HEADERS))
        return rows[0].get("total_cost")

    def test_recalculate_run_updates_known_job_uid(self):
        def fake_compute_costs(_printer_name, _duration_seconds, _filament_mm):
            return {"total_cost": 123.45}

        with patch.object(self.app_module, "compute_costs", fake_compute_costs):
            resp = self.client.post(
                "/recalculate/run",
                data={"job_uids": ["test-job-uid-1"]},
                follow_redirects=False,
            )
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/recalculate", resp.headers.get("Location", ""))
        self.assertEqual(self._read_total_cost(), "123.45")

    def test_recalculate_run_skips_missing_job_uid(self):
        def fake_compute_costs(_printer_name, _duration_seconds, _filament_mm):
            return {"total_cost": 999.99}

        before = self._read_total_cost()
        with patch.object(self.app_module, "compute_costs", fake_compute_costs):
            resp = self.client.post(
                "/recalculate/run",
                data={"job_uids": ["missing-job-uid"]},
                follow_redirects=False,
            )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self._read_total_cost(), before)

    def test_recalculate_run_with_rate_profile_override_changes_total(self):
        # Duration is 3600s -> billable_hours = 1.0, so total should match rate_per_hour when filament is 0.
        from core.config import HEADERS

        with open(self.csv_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        rows[0]["filament_mm"] = "0"
        with open(self.csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=HEADERS)
            writer.writeheader()
            writer.writerow(rows[0])

        def fake_get_rate_profile(_profile_id):
            return {"rate_per_hour": 12.0}

        with patch.object(self.app_module.pricing.rates, "get_rate_profile", fake_get_rate_profile):
            resp = self.client.post(
                "/recalculate/run",
                data={
                    "job_uids": ["test-job-uid-1"],
                    "recompute_mode": "pricing_only",
                    "apply_rate_profile": "1",
                    "rate_profile_id": "rp1",
                    "apply_filament_profile": "0",
                    "filament_profile_id": "",
                },
                follow_redirects=False,
            )
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/recalculate", resp.headers.get("Location", ""))
        self.assertEqual(self._read_total_cost(), "12.0")

    def test_recalculate_run_with_manual_hourly_override_changes_total(self):
        # Make material cost zero so hourly rate override drives total.
        from core.config import HEADERS

        with open(self.csv_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        rows[0]["filament_mm"] = "0"
        with open(self.csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=HEADERS)
            writer.writeheader()
            writer.writerow(rows[0])

        resp = self.client.post(
            "/recalculate/run",
            data={
                "job_uids": ["test-job-uid-1"],
                "recompute_mode": "pricing_only",
                "apply_rate_profile": "0",
                "apply_filament_profile": "0",
                "rate_per_hour_override": "12.0",
                "filament_rate_per_meter_override": "",
            },
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/recalculate", resp.headers.get("Location", ""))
        self.assertEqual(self._read_total_cost(), "12.0")

    def test_recalculate_preview_does_not_write_csv(self):
        def fake_compute_costs(_printer_name, _duration_seconds, _filament_mm):
            return {"total_cost": 123.45}

        before = self._read_total_cost()
        with patch.object(self.app_module, "compute_costs", fake_compute_costs):
            resp = self.client.post(
                "/recalculate/preview",
                data={
                    "job_uids": ["test-job-uid-1"],
                    "recompute_mode": "pricing_only",
                    "apply_rate_profile": "0",
                    "apply_filament_profile": "0",
                },
                follow_redirects=False,
            )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Preview", resp.get_data(as_text=True))
        self.assertEqual(self._read_total_cost(), before)

    def test_recalculate_run_appends_audit_log(self):
        def fake_compute_costs(_printer_name, _duration_seconds, _filament_mm):
            return {"total_cost": 5.0}

        log_path = os.path.join(self.app_module.DATA_DIR, "recalc_runs.jsonl")
        self.assertFalse(os.path.exists(log_path))

        with patch.object(self.app_module, "compute_costs", fake_compute_costs):
            resp = self.client.post(
                "/recalculate/run",
                data={"job_uids": ["test-job-uid-1"]},
                follow_redirects=False,
            )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(os.path.exists(log_path))
        with open(log_path, encoding="utf-8") as f:
            lines = [ln.strip() for ln in f.readlines() if ln.strip()]
        self.assertTrue(lines)
        import json as _json

        record = _json.loads(lines[-1])
        self.assertEqual(record.get("count_updated"), 1)
        self.assertIn("totals", record)


if __name__ == "__main__":
    unittest.main()
