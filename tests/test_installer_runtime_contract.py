import os
import tempfile
import unittest


class InstallerRuntimeContractTests(unittest.TestCase):
    def test_csv_service_uses_consistent_compatibility_backends(self):
        from installer import utils

        storage, reports = utils._installer_runtime_backends(False)
        unit = utils._render_systemd_service(
            "kcd", "/opt/kcd", "/opt/kcd/.venv/bin/python", 5000,
            storage_backend=storage,
            reports_backend=reports,
        )

        self.assertIn("Environment=KCD_STORAGE_BACKEND=csv", unit)
        self.assertIn("Environment=KCD_REPORTS_BACKEND=csv", unit)

    def test_sql_capable_service_uses_dual_writes_and_auto_reports(self):
        from installer import utils

        storage, reports = utils._installer_runtime_backends(True)
        unit = utils._render_systemd_service(
            "kcd", "/opt/kcd", "/opt/kcd/.venv/bin/python", 5000,
            storage_backend=storage,
            reports_backend=reports,
        )

        self.assertIn("Environment=KCD_STORAGE_BACKEND=dual", unit)
        self.assertIn("Environment=KCD_REPORTS_BACKEND=auto", unit)

    def test_reports_auto_cannot_be_rendered_with_csv_only_writes(self):
        from installer import utils

        with self.assertRaisesRegex(ValueError, "csv/csv or dual/auto"):
            utils._render_systemd_service(
                "kcd", "/opt/kcd", "/opt/kcd/.venv/bin/python", 5000,
                storage_backend="csv",
                reports_backend="auto",
            )

    def test_dual_write_job_is_visible_to_auto_sql_reports(self):
        from core import db as db_module
        from core import reports_repo, storage_backend

        previous_storage = os.environ.get("KCD_STORAGE_BACKEND")
        previous_reports = os.environ.get("KCD_REPORTS_BACKEND")
        original_db_path = db_module._db_path
        original_csv_file = storage_backend.CSV_FILE
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["KCD_STORAGE_BACKEND"] = "dual"
                os.environ["KCD_REPORTS_BACKEND"] = "auto"
                db_module._db_path = lambda: os.path.join(tmp, "kcd.db")
                storage_backend.CSV_FILE = os.path.join(tmp, "print_costs.csv")

                storage_backend.write_job(
                    {
                        "job_uid": "dual-visible-job",
                        "printer": "SV08",
                        "filename": "part.gcode",
                        "status": "completed",
                        "timestamp": 1_700_000_000,
                        "duration_seconds": 3600,
                        "filament_mm": 1000.0,
                        "duration_hours": 1.0,
                        "filament_meters": 1.0,
                        "rate_per_hour": 5.0,
                        "filament_mode": "per_meter",
                        "filament_rate": 1.0,
                        "grams_per_meter": 3.0,
                        "time_cost": 5.0,
                        "material_cost": 1.0,
                        "total_cost": 6.0,
                    }
                )

                backend, error = reports_repo._reports_backend()
                report = reports_repo._reports_from_sql(None, None)
                self.assertEqual((backend, error), ("sql", None))
                self.assertEqual(report["summary"]["total_prints"], 1)
                self.assertEqual(report["summary"]["total_cost"], 6.0)
        finally:
            db_module._db_path = original_db_path
            storage_backend.CSV_FILE = original_csv_file
            if previous_storage is None:
                os.environ.pop("KCD_STORAGE_BACKEND", None)
            else:
                os.environ["KCD_STORAGE_BACKEND"] = previous_storage
            if previous_reports is None:
                os.environ.pop("KCD_REPORTS_BACKEND", None)
            else:
                os.environ["KCD_REPORTS_BACKEND"] = previous_reports


if __name__ == "__main__":
    unittest.main()
