"""Application mode is a reversible presentation preference."""

from contextlib import closing
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from core import application_mode, db, projects
from core.registration import register_printer
from core.sql_only import SqlOnlyFilesystemMonitor


class ApplicationModeTests(unittest.TestCase):
    def test_compatibility_backends_remember_preference_and_reject_invalid_mode(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(application_mode, "DATA_DIR", tmp):
            for backend in ("csv", "dual"):
                with self.subTest(backend=backend), patch.dict(os.environ, {"KCD_STORAGE_BACKEND": backend}):
                    mode_path = Path(tmp) / application_mode.SETTING_FILE
                    mode_path.unlink(missing_ok=True)
                    self.assertEqual(application_mode.get_application_mode(), "advanced")
                    self.assertFalse(mode_path.exists())
                    application_mode.set_application_mode("personal")
                    with self.assertRaises(ValueError):
                        application_mode.set_application_mode("unsupported")
                    self.assertEqual(application_mode.get_application_mode(), "personal")
                    application_mode.set_application_mode("business")
                    self.assertEqual(application_mode.get_application_mode(), "business")

    def test_sql_mode_switches_for_all_browsers_without_touching_history(self):
        import app

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.dict(os.environ, {"KCD_STORAGE_BACKEND": "sql", "KCD_SQL_ONLY_FAIL_FAST": "0"}),
            patch.object(application_mode, "DATA_DIR", tmp),
            patch.object(db, "_db_path", lambda: str(Path(tmp) / "kcd.db")),
            SqlOnlyFilesystemMonitor() as monitor,
        ):
            register_printer("Workshop", "http://127.0.0.1:1")
            with closing(db.connect_db()) as conn, conn:
                for uid, status, price in (("paid", "completed", 12.5), ("cancelled", "canceled", 0.0)):
                    db.upsert_job(conn, {
                        "job_uid": uid, "printer": "Workshop", "filename": uid + ".gcode",
                        "status": status, "timestamp": time.time(), "duration_seconds": 90,
                        "filament_mm": 200, "time_cost": price, "material_cost": 0, "total_cost": price,
                    })
            project = projects.create_project("Spool holders")
            projects.assign_jobs(["paid"], project.id)

            def snapshot():
                with closing(db.connect_db()) as conn:
                    return {
                        table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY 1")]
                        for table in ("jobs", "projects", "project_assignments", "printers")
                    }

            original = snapshot()
            editor = app.app.test_client()
            another_browser = app.app.test_client()
            self.assertIn(b'href="/projects"', another_browser.get("/").data)
            for mode in ("personal", "business", "advanced", "personal"):
                result = editor.post("/settings/other", data={"action": "update_application_mode", "mode": mode})
                self.assertEqual(result.status_code, 302)
                rendered = another_browser.get("/").data
                self.assertIn(b'href="/projects"', rendered)
                self.assertEqual(b'value="recalc_costs"' in rendered, mode != "personal")
                self.assertIn(b"paid.gcode", rendered)
                self.assertEqual(snapshot(), original)

            self.assertIn(b"Spool holders", another_browser.get("/projects").data)
            self.assertIn(b'value="update_application_mode"', another_browser.get("/settings/other").data)
            processors = app.app.template_context_processors[None]
            with patch.dict(app.app.template_context_processors, {None: [*processors, lambda: {
                "kcd_demo_mode": True, "demo_endpoint_available": lambda *_args: False,
            }]}):
                demo_html = another_browser.get("/settings/other").data
            self.assertNotIn(b'value="update_application_mode"', demo_html)
            self.assertIn(b"Application mode changes are unavailable in this demo.", demo_html)
            self.assertEqual(snapshot(), original)
            invalid = editor.post(
                "/settings/other",
                data={"action": "update_application_mode", "mode": "unsupported"},
                follow_redirects=True,
            )
            self.assertIn(b"Choose Personal, Advanced, or Business.", invalid.data)
            self.assertEqual(application_mode.get_application_mode(), "personal")
            self.assertFalse((Path(tmp) / application_mode.SETTING_FILE).exists())
            self.assertFalse(any(
                access["classification"] == "forbidden_runtime_state" for access in monitor.accesses
            ))


if __name__ == "__main__":
    unittest.main()
