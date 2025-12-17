import csv
import glob
import os
import tempfile
import unittest


class CsvSchemaMigrationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.csv_path = os.path.join(self._tmp.name, "print_costs.csv")

    def tearDown(self):
        self._tmp.cleanup()

    def test_ensure_csv_schema_repairs_mixed_schema_file(self):
        from core.config import HEADERS
        from core.storage import ensure_csv_schema

        # Simulate an older CSV header missing newer columns that cause shifts.
        old_headers = [h for h in HEADERS if h not in ("filament_mm", "filament_profile_id")]

        old_row = {h: "" for h in old_headers}
        old_row.update(
            {
                "timestamp": "1700000000",
                "job_uid": "old-uid",
                "printer": "SV07",
                "filename": "old_job.gcode",
                "duration_seconds": "3600",
                "duration_hours": "1.0",
                "filament_meters": "10.0",
                "rate_per_hour": "1.0",
                "filament_mode": "per_meter",
                "filament_rate": "0.25",
                "grams_per_meter": "3.0",
                "time_cost": "1.0",
                "material_cost": "2.5",
                "total_cost": "3.5",
                "filament_material": "PLA",
                "status": "completed",
                "failure_reason": "",
            }
        )

        new_row = {h: "" for h in HEADERS}
        new_row.update(
            {
                "timestamp": "1700000100",
                "job_uid": "new-uid",
                "printer": "SV08",
                "filename": "GEN2 Ryobi Tool Holder - V2511_PETG_0.2_1h18m.gcode",
                "thumbnail": "",
                "duration_seconds": "4251",
                "duration_hours": "1.1808333333",
                "filament_mm": "17476",
                "filament_meters": "17.476",
                "rate_per_hour": "1.0",
                "filament_mode": "per_meter",
                "filament_rate": "0.25",
                "grams_per_meter": "3.0",
                "time_cost": "1.18",
                "material_cost": "4.37",
                "total_cost": "5.55",
                "filament_profile_id": "",
                "filament_material": "PETG",
                "status": "completed",
                "failure_reason": "",
            }
        )

        with open(self.csv_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(old_headers)
            w.writerow([old_row.get(h, "") for h in old_headers])
            # Mixed-schema append: row written in current HEADERS order under an old header.
            w.writerow([new_row.get(h, "") for h in HEADERS])

        migrated = ensure_csv_schema(self.csv_path, HEADERS)
        self.assertTrue(migrated)

        backups = glob.glob(self.csv_path + ".bak.*")
        self.assertTrue(backups, "Expected a timestamped .bak backup to be created")

        with open(self.csv_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            self.assertEqual(list(reader.fieldnames or []), HEADERS)
            rows = list(reader)

        self.assertEqual(len(rows), 2)

        # The legacy row should keep its original mapping.
        self.assertEqual(rows[0].get("printer"), "SV07")
        self.assertEqual(rows[0].get("filename"), "old_job.gcode")
        self.assertEqual(rows[0].get("filament_material"), "PLA")
        self.assertEqual(rows[0].get("status"), "completed")

        # The mixed-schema row should no longer be shifted.
        self.assertEqual(rows[1].get("printer"), "SV08")
        self.assertIn("Ryobi", rows[1].get("filename") or "")
        self.assertEqual(rows[1].get("duration_seconds"), "4251")
        self.assertEqual(rows[1].get("duration_hours"), "1.1808333333")
        self.assertEqual(rows[1].get("filament_mm"), "17476")
        self.assertEqual(rows[1].get("filament_meters"), "17.476")
        self.assertEqual(rows[1].get("filament_material"), "PETG")
        self.assertEqual(rows[1].get("status"), "completed")

