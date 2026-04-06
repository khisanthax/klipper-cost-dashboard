import json
import os
import unittest
import uuid
from datetime import datetime, timezone


class SqlOnlyReadinessTests(unittest.TestCase):
    def setUp(self):
        from core import db as db_module

        self._db_module = db_module
        self._prev_backend = os.environ.get("KCD_STORAGE_BACKEND")
        os.environ["KCD_STORAGE_BACKEND"] = "sql"
        data_root = os.path.join(os.getcwd(), "data")
        os.makedirs(data_root, exist_ok=True)
        self._test_id = uuid.uuid4().hex
        self._db_file = os.path.join(data_root, f"test_sql_ready_{self._test_id}.db")
        self._orig_db_path = db_module._db_path
        db_module._db_path = lambda: self._db_file

    def tearDown(self):
        self._db_module._db_path = self._orig_db_path
        if self._prev_backend is None:
            os.environ.pop("KCD_STORAGE_BACKEND", None)
        else:
            os.environ["KCD_STORAGE_BACKEND"] = self._prev_backend
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(self._db_file + suffix)
            except (FileNotFoundError, PermissionError):
                pass

    def _connect(self):
        conn = self._db_module.connect_db()
        self._db_module.apply_migrations(conn)
        return conn

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

    def test_check_sql_only_readiness_fails_without_pause_billing_default(self):
        from core.readiness import check_sql_only_readiness

        readiness = check_sql_only_readiness()

        self.assertFalse(readiness.get("ready"))
        self.assertEqual(readiness.get("backend"), "sql")
        self.assertTrue(any(err.get("code") == "missing_pause_billing_default" for err in readiness.get("errors", [])))
        self.assertTrue(any(check.get("name") == "required_tables" and check.get("ok") for check in readiness.get("checks", [])))

    def test_check_sql_only_readiness_accepts_persisted_pause_billing_default(self):
        from core.readiness import check_sql_only_readiness

        self._upsert_user_setting(
            "display_settings",
            {
                "visible_columns": ["printer"],
                "pause_include_paused_time_default": False,
            },
        )

        readiness = check_sql_only_readiness()

        self.assertTrue(readiness.get("ready"))
        self.assertEqual(readiness.get("backend"), "sql")
        self.assertTrue(any(check.get("name") == "pause_billing_default" and check.get("ok") for check in readiness.get("checks", [])))

    def test_health_sql_only_reports_readiness_failure(self):
        import app as kcd_app

        client = kcd_app.app.test_client()
        response = client.get("/health")
        payload = response.get_json()

        self.assertEqual(response.status_code, 503)
        self.assertEqual(payload.get("status"), "error")
        self.assertFalse(payload.get("ready"))
        self.assertEqual(payload.get("backend"), "sql")
        self.assertTrue(any(err.get("code") == "missing_pause_billing_default" for err in payload.get("errors", [])))

    def test_health_sql_only_reports_ready_when_validator_passes(self):
        import app as kcd_app

        self._upsert_user_setting(
            "display_settings",
            {
                "visible_columns": ["printer"],
                "pause_include_paused_time_default": True,
            },
        )

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
