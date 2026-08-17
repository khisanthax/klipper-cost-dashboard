import builtins
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone

from core.sql_only import (
    SqlOnlyFilesystemMonitor,
    SqlOnlyViolationError,
    classify_sql_only_filesystem_path,
)


class SqlOnlyFilesystemContractTests(unittest.TestCase):
    def setUp(self):
        self._previous_backend = os.environ.get("KCD_STORAGE_BACKEND")
        self._previous_api_key = os.environ.get("KCD_API_KEY")
        os.environ["KCD_STORAGE_BACKEND"] = "sql"

    def tearDown(self):
        if self._previous_backend is None:
            os.environ.pop("KCD_STORAGE_BACKEND", None)
        else:
            os.environ["KCD_STORAGE_BACKEND"] = self._previous_backend
        if self._previous_api_key is None:
            os.environ.pop("KCD_API_KEY", None)
        else:
            os.environ["KCD_API_KEY"] = self._previous_api_key

    def test_contract_classifies_business_state_and_explicit_exceptions(self):
        self.assertEqual(
            classify_sql_only_filesystem_path(os.path.join("data", "settings.json")),
            "forbidden_runtime_state",
        )
        self.assertEqual(
            classify_sql_only_filesystem_path(os.path.join("data", "secret.json")),
            "allowed_credential",
        )
        self.assertEqual(
            classify_sql_only_filesystem_path(os.path.join("data", "thumb_cache", "SV08", "thumb.png")),
            "allowed_cache",
        )
        self.assertEqual(
            classify_sql_only_filesystem_path(os.path.join("data", "backups", "backup.tar.gz")),
            "allowed_backup",
        )

    def test_monitor_blocks_direct_legacy_access_but_allows_secret(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            forbidden = os.path.join(tmpdir, "settings.json")
            secret = os.path.join(tmpdir, "secret.json")
            with SqlOnlyFilesystemMonitor() as monitor:
                with builtins.open(secret, "w", encoding="utf-8") as handle:
                    handle.write("{}")
                with self.assertRaises(SqlOnlyViolationError):
                    builtins.open(forbidden, "w", encoding="utf-8")

        self.assertTrue(any(item["classification"] == "allowed_credential" for item in monitor.accesses))
        self.assertTrue(any(item["classification"] == "forbidden_runtime_state" for item in monitor.accesses))

    def test_environment_api_key_avoids_secret_file_creation(self):
        from core.storage import ensure_api_key

        os.environ["KCD_API_KEY"] = "environment-secret"
        with tempfile.TemporaryDirectory() as tmpdir:
            secret = os.path.join(tmpdir, "secret.json")
            self.assertEqual(ensure_api_key(secret_file=secret, data_dir=tmpdir), "environment-secret")
            self.assertFalse(os.path.exists(secret))

    def test_sql_recalculation_audit_uses_system_events_not_jsonl(self):
        from core import db as db_module

        previous_db_path = db_module._db_path
        previous_fail_fast = os.environ.get("KCD_SQL_ONLY_FAIL_FAST")
        os.environ.pop("KCD_SQL_ONLY_FAIL_FAST", None)
        with tempfile.TemporaryDirectory() as tmpdir:
            db_module._db_path = lambda: os.path.join(tmpdir, "kcd.db")
            try:
                now = datetime.now(timezone.utc).isoformat()
                with db_module.connect_db() as conn:
                    db_module.apply_migrations(conn)
                    db_module.upsert_printer(conn, "SV08")
                    db_module.upsert_job(
                        conn,
                        {
                            "job_uid": "sql-recalc-job",
                            "printer": "SV08",
                            "filename": "recalc.gcode",
                            "status": "completed",
                            "duration_seconds": 3600,
                            "filament_mm": 1000,
                            "total_cost": 0,
                        },
                    )
                    for key, value in (
                        (
                            "display_settings",
                            {"pause_include_paused_time_default": False, "hidden_printers": []},
                        ),
                        (
                            "printer_settings",
                            {
                                "SV08": {
                                    "rate_per_hour": 6.0,
                                    "filament_mode": "per_meter",
                                    "filament_rate": 0.1,
                                    "grams_per_meter": 3.0,
                                }
                            },
                        ),
                    ):
                        conn.execute(
                            "INSERT INTO user_settings (key, value_json, updated_at) VALUES (?, ?, ?)",
                            (key, json.dumps(value), now),
                        )

                sys.modules.pop("app", None)
                with SqlOnlyFilesystemMonitor():
                    import app as app_module

                    original_data_dir = app_module.DATA_DIR
                    app_module.DATA_DIR = tmpdir
                    try:
                        response = app_module.app.test_client().post(
                            "/recalculate/run",
                            data={"job_uids": ["sql-recalc-job"], "recompute_mode": "pricing_only"},
                        )
                    finally:
                        app_module.DATA_DIR = original_data_dir

                self.assertEqual(response.status_code, 302)
                self.assertFalse(os.path.exists(os.path.join(tmpdir, "recalc_runs.jsonl")))
                with db_module.connect_db() as conn:
                    event = conn.execute(
                        "SELECT title FROM system_events WHERE title = ?",
                        ("Pricing recalculation completed",),
                    ).fetchone()
                self.assertIsNotNone(event)
            finally:
                db_module._db_path = previous_db_path
                sys.modules.pop("app", None)
                if previous_fail_fast is None:
                    os.environ.pop("KCD_SQL_ONLY_FAIL_FAST", None)
                else:
                    os.environ["KCD_SQL_ONLY_FAIL_FAST"] = previous_fail_fast


if __name__ == "__main__":
    unittest.main()
