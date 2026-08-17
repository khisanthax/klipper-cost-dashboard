import os
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse


class SqlMutationFailureTests(unittest.TestCase):
    def setUp(self):
        self._previous_backend = os.environ.get("KCD_STORAGE_BACKEND")
        self._previous_fail_fast = os.environ.get("KCD_SQL_ONLY_FAIL_FAST")
        os.environ["KCD_STORAGE_BACKEND"] = "sql"
        os.environ["KCD_SQL_ONLY_FAIL_FAST"] = "0"
        self._tmp = tempfile.TemporaryDirectory()

        from core import db as db_module

        self.db_module = db_module
        self._original_db_path = db_module._db_path
        db_module._db_path = lambda: os.path.join(self._tmp.name, "kcd.db")
        sys.modules.pop("app", None)
        import app as app_module

        self.app_module = app_module
        self.client = app_module.app.test_client()

    def tearDown(self):
        self.db_module._db_path = self._original_db_path
        sys.modules.pop("app", None)
        self._tmp.cleanup()
        if self._previous_backend is None:
            os.environ.pop("KCD_STORAGE_BACKEND", None)
        else:
            os.environ["KCD_STORAGE_BACKEND"] = self._previous_backend
        if self._previous_fail_fast is None:
            os.environ.pop("KCD_SQL_ONLY_FAIL_FAST", None)
        else:
            os.environ["KCD_SQL_ONLY_FAIL_FAST"] = self._previous_fail_fast

    def test_named_sql_helpers_surface_connection_failures(self):
        with patch.object(self.app_module.db_module, "connect_db", side_effect=RuntimeError("database unavailable")):
            with self.assertRaisesRegex(RuntimeError, "database unavailable"):
                self.app_module._sum_total_cost_sql(["job"])
            with self.assertRaisesRegex(RuntimeError, "database unavailable"):
                self.app_module._recalc_jobs_sql(["job"], lambda *_args: {})
            with self.assertRaisesRegex(RuntimeError, "database unavailable"):
                self.app_module._mark_completed_jobs_sql(["job"])
            with self.assertRaisesRegex(RuntimeError, "database unavailable"):
                self.app_module._delete_jobs_sql(["job"])

        with patch.object(
            self.app_module,
            "get_canonical_printer_names",
            side_effect=RuntimeError("database unavailable"),
        ):
            with self.assertRaisesRegex(RuntimeError, "database unavailable"):
                self.app_module._sql_only_printer_exists("SV08")

    def test_delete_route_reports_failure_without_audit_event(self):
        with patch.object(
            self.app_module,
            "_delete_jobs_sql",
            side_effect=RuntimeError("delete transaction failed"),
        ), patch.object(self.app_module.system_events, "emit_event") as emit_event:
            response = self.client.post(
                "/",
                data={"action": "delete_rows", "delete_rows": ["job-1"]},
            )

        self.assertEqual(response.status_code, 302)
        query = parse_qs(urlparse(response.location).query)
        self.assertIn("delete transaction failed", query["error"][0])
        emit_event.assert_not_called()

    def test_recalculate_route_reports_failure_without_audit_event(self):
        row = {
            "job_uid": "job-1",
            "printer": "SV08",
            "duration_seconds": 60,
            "filament_mm": 10,
            "paused_seconds_total": 0,
            "total_cost": 1,
        }
        with patch.object(self.app_module, "_load_history_rows_for_recalc", return_value=([row], None)), patch.object(
            self.app_module,
            "_recalc_jobs_sql",
            side_effect=RuntimeError("recalc transaction failed"),
        ), patch.object(self.app_module.system_events, "emit_event") as emit_event:
            response = self.client.post(
                "/recalculate/run",
                data={"recompute_mode": "pricing_only", "job_uids": ["job-1"]},
            )

        self.assertEqual(response.status_code, 302)
        query = parse_qs(urlparse(response.location).query)
        self.assertIn("recalc transaction failed", query["msg"][0])
        emit_event.assert_not_called()

    def test_delete_transaction_rolls_back_assignment_when_job_delete_fails(self):
        now = datetime.now(timezone.utc).isoformat()
        with closing(self.db_module.connect_db()) as conn:
            self.db_module.apply_migrations(conn)
            self.db_module.upsert_job(
                conn,
                {
                    "job_uid": "job-rollback",
                    "printer": "SV08",
                    "filename": "part.gcode",
                    "status": "completed",
                },
            )
            conn.execute(
                "INSERT INTO projects (project_uid, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
                ("project-1", "Project", now, now),
            )
            project_id = conn.execute("SELECT id FROM projects WHERE project_uid = 'project-1'").fetchone()["id"]
            conn.execute(
                "INSERT INTO project_assignments (project_id, job_uid, created_at) VALUES (?, ?, ?)",
                (project_id, "job-rollback", now),
            )
            conn.execute(
                "CREATE TRIGGER fail_job_delete BEFORE DELETE ON jobs "
                "BEGIN SELECT RAISE(ABORT, 'forced delete failure'); END"
            )
            conn.commit()

        with self.assertRaisesRegex(Exception, "forced delete failure"):
            self.app_module._delete_jobs_sql(["job-rollback"])

        with closing(self.db_module.connect_db()) as conn:
            job_count = conn.execute("SELECT COUNT(*) AS c FROM jobs WHERE job_uid = 'job-rollback'").fetchone()["c"]
            assignment_count = conn.execute(
                "SELECT COUNT(*) AS c FROM project_assignments WHERE job_uid = 'job-rollback'"
            ).fetchone()["c"]
        self.assertEqual(job_count, 1)
        self.assertEqual(assignment_count, 1)


if __name__ == "__main__":
    unittest.main()
