import json
import os
import importlib
import sys
import unittest
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone


class SqlOnlyReadinessTests(unittest.TestCase):
    def setUp(self):
        from core import db as db_module

        self._db_module = db_module
        self._prev_backend = os.environ.get("KCD_STORAGE_BACKEND")
        self._prev_fail_fast = os.environ.get("KCD_SQL_ONLY_FAIL_FAST")
        os.environ["KCD_STORAGE_BACKEND"] = "sql"
        os.environ.pop("KCD_SQL_ONLY_FAIL_FAST", None)
        scratch_root = os.path.join(os.getcwd(), "tests", ".tmp")
        os.makedirs(scratch_root, exist_ok=True)
        self._test_id = uuid.uuid4().hex
        self._scratch_dir = os.path.join(scratch_root, f"sql_only_readiness_{self._test_id}")
        os.makedirs(self._scratch_dir, exist_ok=True)
        self._db_file = os.path.join(self._scratch_dir, "kcd.db")
        self._orig_db_path = db_module._db_path
        db_module._db_path = lambda: self._db_file

    def tearDown(self):
        self._db_module._db_path = self._orig_db_path
        if self._prev_backend is None:
            os.environ.pop("KCD_STORAGE_BACKEND", None)
        else:
            os.environ["KCD_STORAGE_BACKEND"] = self._prev_backend
        if self._prev_fail_fast is None:
            os.environ.pop("KCD_SQL_ONLY_FAIL_FAST", None)
        else:
            os.environ["KCD_SQL_ONLY_FAIL_FAST"] = self._prev_fail_fast
        sys.modules.pop("app", None)
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(self._db_file + suffix)
            except (FileNotFoundError, PermissionError):
                pass

    @contextmanager
    def _connect(self):
        conn = self._db_module.connect_db()
        try:
            self._db_module.apply_migrations(conn)
            yield conn
        finally:
            conn.close()

    def _upsert_user_setting(self, key, value):
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO user_settings (key, value_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json, updated_at = excluded.updated_at
                """,
                (key, json.dumps(value), now),
            )
            conn.commit()

    def _upsert_printer(self, name):
        with self._connect() as conn:
            self._db_module.upsert_printer(conn, name)
            conn.commit()

    def _insert_filament_profile(self, profile_uid="pla-red"):
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO filament_profiles
                    (profile_uid, name, material, filament_mode, filament_rate, cost_per_kg, grams_per_meter, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (profile_uid, "PLA Red", "PLA", "per_meter", 0.08, None, 3.0, now, now),
            )
            conn.commit()

    def _insert_rate_profile(self, profile_uid="rate-fast"):
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO hourly_rate_profiles
                    (profile_uid, name, description, rate_per_hour, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (profile_uid, "Fast", "", 12.0, now, now),
            )
            conn.commit()

    def _persist_pause_default(self):
        self._upsert_user_setting(
            "display_settings",
            {
                "visible_columns": ["printer"],
                "pause_include_paused_time_default": False,
            },
        )

    def _import_app_fresh(self):
        sys.modules.pop("app", None)
        return importlib.import_module("app")

    def test_check_sql_only_readiness_fails_without_pause_billing_default(self):
        from core.readiness import check_sql_only_readiness

        readiness = check_sql_only_readiness()

        self.assertFalse(readiness.get("ready"))
        self.assertEqual(readiness.get("backend"), "sql")
        self.assertTrue(any(err.get("code") == "missing_pause_billing_default" for err in readiness.get("errors", [])))
        self.assertTrue(any(check.get("name") == "required_tables" and check.get("ok") for check in readiness.get("checks", [])))

    def test_check_sql_only_readiness_accepts_persisted_pause_billing_default(self):
        from core.readiness import check_sql_only_readiness

        self._persist_pause_default()

        readiness = check_sql_only_readiness()

        self.assertTrue(readiness.get("ready"))
        self.assertEqual(readiness.get("backend"), "sql")
        self.assertTrue(any(check.get("name") == "pause_billing_default" and check.get("ok") for check in readiness.get("checks", [])))

    def test_check_sql_only_readiness_fails_configured_printer_without_pricing_config(self):
        from core.readiness import check_sql_only_readiness

        self._persist_pause_default()
        self._upsert_printer("SV08")

        readiness = check_sql_only_readiness()

        self.assertFalse(readiness.get("ready"))
        self.assertTrue(any(err.get("code") == "invalid_printer_pricing_config" for err in readiness.get("errors", [])))
        self.assertTrue(any(check.get("name") == "configured_printer_pricing" and not check.get("ok") for check in readiness.get("checks", [])))

    def test_check_sql_only_readiness_accepts_configured_printer_with_direct_pricing_config(self):
        from core.readiness import check_sql_only_readiness

        self._persist_pause_default()
        self._upsert_printer("SV08")
        self._upsert_user_setting(
            "printer_settings",
            {
                "SV08": {
                    "rate_per_hour": 7.0,
                    "filament_mode": "per_meter",
                    "filament_rate": 0.08,
                    "grams_per_meter": 3.0,
                },
            },
        )

        readiness = check_sql_only_readiness()

        self.assertTrue(readiness.get("ready"))
        self.assertTrue(any(check.get("name") == "configured_printer_pricing" and check.get("ok") for check in readiness.get("checks", [])))

    def test_check_sql_only_readiness_accepts_configured_printer_with_valid_profiles(self):
        from core.readiness import check_sql_only_readiness

        self._persist_pause_default()
        self._upsert_printer("SV08")
        self._insert_rate_profile("rate-fast")
        self._insert_filament_profile("pla-red")
        self._upsert_user_setting("printer_settings", {"SV08": {"active_rate_profile_id": "rate-fast"}})
        self._upsert_user_setting("filament_mappings", {"SV08": "pla-red"})

        readiness = check_sql_only_readiness()

        self.assertTrue(readiness.get("ready"))
        self.assertTrue(any(check.get("name") == "configured_printer_pricing" and check.get("ok") for check in readiness.get("checks", [])))

    def test_retired_printer_keeps_history_but_is_not_active_or_required_for_readiness(self):
        from core import printer_lifecycle
        from core.history_repo import HistoryQuery, list_history_rows_sql
        from core.printers import get_canonical_printer_names
        from core.readiness import check_sql_only_readiness

        self._persist_pause_default()
        self._upsert_printer("Active")
        self._upsert_printer("Retired")
        self._upsert_user_setting(
            "printer_settings",
            {
                "Active": {
                    "rate_per_hour": 7.0,
                    "filament_mode": "per_meter",
                    "filament_rate": 0.08,
                    "grams_per_meter": 3.0,
                },
                "Retired": {
                    "rate_per_hour": 5.0,
                    "filament_mode": "per_meter",
                    "filament_rate": 0.06,
                    "grams_per_meter": 3.0,
                },
            },
        )
        with self._connect() as conn:
            self._db_module.upsert_job(
                conn,
                {
                    "job_uid": "retired-history-job",
                    "printer": "Retired",
                    "filename": "retained.gcode",
                    "status": "completed",
                },
            )
            conn.commit()

        printer_lifecycle.delete_printer("Retired")

        readiness = check_sql_only_readiness()
        self.assertTrue(readiness.get("ready"), readiness.get("errors"))
        pricing_check = next(
            check for check in readiness.get("checks", []) if check.get("name") == "configured_printer_pricing"
        )
        self.assertEqual(pricing_check.get("printers_checked"), 1)
        self.assertEqual(get_canonical_printer_names(), {"Active"})
        self.assertEqual(get_canonical_printer_names(include_hidden=True), {"Active", "Retired"})

        history = list_history_rows_sql(
            HistoryQuery(printer="Retired"),
            page=1,
            per_page=25,
            error=None,
        )
        self.assertEqual(history.total, 1)
        self.assertEqual(history.rows_page[0].get("job_uid"), "retired-history-job")

        kcd_app = self._import_app_fresh()
        response = kcd_app.app.test_client().post(
            "/job-start",
            json={"printer_name": "Retired", "filename": "new.gcode"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Unknown printer_name", response.get_json().get("error", ""))

    def test_check_sql_only_readiness_fails_stale_active_profile_references(self):
        from core.readiness import check_sql_only_readiness

        self._persist_pause_default()
        self._upsert_printer("SV08")
        self._upsert_user_setting("printer_settings", {"SV08": {"active_rate_profile_id": "missing-rate"}})
        self._upsert_user_setting("filament_mappings", {"SV08": "missing-filament"})

        readiness = check_sql_only_readiness()

        self.assertFalse(readiness.get("ready"))
        err = next(err for err in readiness.get("errors", []) if err.get("code") == "invalid_printer_pricing_config")
        self.assertIn("SV08", err.get("missing_rate_printers", []))
        self.assertIn("SV08", err.get("missing_filament_printers", []))

    def test_fail_fast_is_enabled_by_default_in_sql_only_mode(self):
        from core.readiness import sql_only_fail_fast_enabled

        self.assertTrue(sql_only_fail_fast_enabled())

    def test_enforce_sql_only_startup_readiness_raises_by_default_when_not_ready(self):
        from core.readiness import (
            enforce_sql_only_startup_readiness,
            SqlOnlyStartupReadinessError,
        )

        with self.assertRaises(SqlOnlyStartupReadinessError) as ctx:
            enforce_sql_only_startup_readiness()

        self.assertIn("SQL-only startup readiness failed", str(ctx.exception))
        self.assertIn("missing_pause_billing_default", str(ctx.exception))

    def test_fail_fast_can_be_explicitly_disabled(self):
        from core.readiness import sql_only_fail_fast_enabled

        os.environ["KCD_SQL_ONLY_FAIL_FAST"] = "0"

        self.assertFalse(sql_only_fail_fast_enabled())

    def test_app_import_fails_fast_by_default_when_not_ready(self):
        from core.readiness import SqlOnlyStartupReadinessError

        with self.assertRaises(SqlOnlyStartupReadinessError):
            self._import_app_fresh()

    def test_app_import_succeeds_when_fail_fast_disabled(self):
        os.environ["KCD_SQL_ONLY_FAIL_FAST"] = "0"

        module = self._import_app_fresh()

        self.assertIsNotNone(module.app)

    def test_app_import_succeeds_when_ready(self):
        self._persist_pause_default()

        module = self._import_app_fresh()

        self.assertIsNotNone(module.app)

    def test_health_sql_only_reports_readiness_failure(self):
        os.environ["KCD_SQL_ONLY_FAIL_FAST"] = "0"
        kcd_app = self._import_app_fresh()

        client = kcd_app.app.test_client()
        response = client.get("/health")
        payload = response.get_json()

        self.assertEqual(response.status_code, 503)
        self.assertEqual(payload.get("status"), "error")
        self.assertFalse(payload.get("ready"))
        self.assertEqual(payload.get("backend"), "sql")
        self.assertTrue(any(err.get("code") == "missing_pause_billing_default" for err in payload.get("errors", [])))

    def test_health_sql_only_reports_ready_when_validator_passes(self):
        os.environ["KCD_SQL_ONLY_FAIL_FAST"] = "0"

        self._upsert_user_setting(
            "display_settings",
            {
                "visible_columns": ["printer"],
                "pause_include_paused_time_default": True,
            },
        )

        kcd_app = self._import_app_fresh()

        client = kcd_app.app.test_client()
        response = client.get("/health")
        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload.get("status"), "ok")
        self.assertTrue(payload.get("ready"))
        self.assertTrue(payload.get("db_ok"))
        self.assertIn("schema_version", payload)
        self.assertTrue(any(check.get("name") == "pause_billing_default" and check.get("ok") for check in payload.get("checks", [])))


if __name__ == "__main__":
    unittest.main()
