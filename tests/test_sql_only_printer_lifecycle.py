import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch


class SqlOnlyPrinterLifecycleTests(unittest.TestCase):
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

    def _save_setting(self, conn, key, value):
        conn.execute(
            """
            INSERT INTO user_settings (key, value_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json, updated_at = excluded.updated_at
            """,
            (key, json.dumps(value), datetime.now(timezone.utc).isoformat()),
        )

    def _seed_printer(self, name, *, url=None, external_id=None, job_uid=None):
        with self.db_module.connect_db() as conn:
            self.db_module.apply_migrations(conn)
            printer_id = self.db_module.upsert_printer(
                conn,
                name,
                moonraker_url=url,
                external_id=external_id,
            )
            if job_uid:
                self.db_module.upsert_job(
                    conn,
                    {
                        "job_uid": job_uid,
                        "printer": name,
                        "filename": f"{name}.gcode",
                        "status": "completed",
                    },
                )
                conn.execute(
                    """
                    INSERT INTO events (created_at, type, printer_id, payload_json)
                    VALUES (?, ?, ?, ?)
                    """,
                    (datetime.now(timezone.utc).isoformat(), "test", printer_id, "{}"),
                )

    def _load_setting(self, conn, key):
        row = conn.execute(
            "SELECT value_json FROM user_settings WHERE key = ?",
            (key,),
        ).fetchone()
        return json.loads(row["value_json"]) if row else None

    def test_rename_uses_sql_only_and_preserves_linked_state(self):
        self._seed_printer("Old", url="http://old.local", external_id="client-old", job_uid="job-old")
        with self.db_module.connect_db() as conn:
            self._save_setting(conn, "printer_settings", {"Old": {"rate_per_hour": 7.0}})
            self._save_setting(conn, "filament_mappings", {"Old": "pla"})
            self._save_setting(conn, "display_settings", {"hidden_printers": ["Old"]})

        with patch.object(self.app_module, "rename_printer", side_effect=AssertionError("legacy rename called")):
            response = self.client.post(
                "/settings/printers",
                data={"action": "rename_printer", "old_name": "Old", "new_name": "New"},
            )

        self.assertEqual(response.status_code, 302)
        with self.db_module.connect_db() as conn:
            printer = conn.execute(
                "SELECT id, moonraker_url, external_id FROM printers WHERE name = ?",
                ("New",),
            ).fetchone()
            self.assertIsNotNone(printer)
            self.assertIsNone(conn.execute("SELECT id FROM printers WHERE name = 'Old'").fetchone())
            job = conn.execute(
                "SELECT p.name FROM jobs j JOIN printers p ON p.id = j.printer_id WHERE j.job_uid = ?",
                ("job-old",),
            ).fetchone()
            event = conn.execute(
                "SELECT p.name FROM events e JOIN printers p ON p.id = e.printer_id"
            ).fetchone()
            self.assertEqual(job["name"], "New")
            self.assertEqual(event["name"], "New")
            self.assertEqual(printer["moonraker_url"], "http://old.local")
            self.assertEqual(printer["external_id"], "client-old")
            self.assertEqual(self._load_setting(conn, "printer_settings"), {"New": {"rate_per_hour": 7.0}})
            self.assertEqual(self._load_setting(conn, "filament_mappings"), {"New": "pla"})
            self.assertEqual(self._load_setting(conn, "display_settings")["hidden_printers"], ["New"])

    def test_merge_retargets_history_and_primary_configuration_wins(self):
        self._seed_printer("Primary", job_uid="job-primary")
        self._seed_printer("Secondary", url="http://secondary.local", external_id="client-secondary", job_uid="job-secondary")
        with self.db_module.connect_db() as conn:
            self._save_setting(
                conn,
                "printer_settings",
                {
                    "Primary": {"rate_per_hour": 9.0},
                    "Secondary": {"rate_per_hour": 4.0, "filament_mode": "per_meter"},
                },
            )
            self._save_setting(conn, "filament_mappings", {"Secondary": "petg"})
            self._save_setting(conn, "display_settings", {"hidden_printers": ["Primary", "Secondary"]})

        with patch.object(self.app_module, "merge_printers", side_effect=AssertionError("legacy merge called")):
            response = self.client.post(
                "/settings/printers",
                data={"action": "merge_printers", "primary": "Primary", "secondary": "Secondary"},
            )

        self.assertEqual(response.status_code, 302)
        with self.db_module.connect_db() as conn:
            self.assertIsNone(conn.execute("SELECT id FROM printers WHERE name = 'Secondary'").fetchone())
            primary = conn.execute(
                "SELECT id, moonraker_url, external_id FROM printers WHERE name = 'Primary'"
            ).fetchone()
            linked_jobs = conn.execute(
                "SELECT COUNT(*) AS count FROM jobs WHERE printer_id = ?",
                (primary["id"],),
            ).fetchone()
            linked_events = conn.execute(
                "SELECT COUNT(*) AS count FROM events WHERE printer_id = ?",
                (primary["id"],),
            ).fetchone()
            settings = self._load_setting(conn, "printer_settings")
            self.assertEqual(linked_jobs["count"], 2)
            self.assertEqual(linked_events["count"], 2)
            self.assertEqual(primary["moonraker_url"], "http://secondary.local")
            self.assertEqual(primary["external_id"], "client-secondary")
            self.assertEqual(settings["Primary"]["rate_per_hour"], 9.0)
            self.assertEqual(settings["Primary"]["filament_mode"], "per_meter")
            self.assertNotIn("Secondary", settings)
            self.assertEqual(self._load_setting(conn, "filament_mappings"), {"Primary": "petg"})
            self.assertEqual(self._load_setting(conn, "display_settings")["hidden_printers"], [])

    def test_delete_removes_active_config_but_preserves_history(self):
        self._seed_printer("Delete Me", url="http://delete.local", external_id="client-delete", job_uid="job-delete")
        with self.db_module.connect_db() as conn:
            self._save_setting(conn, "printer_settings", {"Delete Me": {"rate_per_hour": 5.0}})
            self._save_setting(conn, "filament_mappings", {"Delete Me": "abs"})
            self._save_setting(conn, "display_settings", {"hidden_printers": ["Other"]})

        with patch.object(self.app_module.pricing, "delete_printer", side_effect=AssertionError("legacy delete called")), patch.object(
            self.app_module.pricing, "hide_printer", side_effect=AssertionError("legacy hide called")
        ):
            response = self.client.post(
                "/settings/printers",
                data={"action": "delete_printer", "printer": "Delete Me"},
            )

        self.assertEqual(response.status_code, 302)
        with self.db_module.connect_db() as conn:
            printer = conn.execute(
                "SELECT id, moonraker_url, external_id FROM printers WHERE name = 'Delete Me'"
            ).fetchone()
            job = conn.execute("SELECT printer_id FROM jobs WHERE job_uid = 'job-delete'").fetchone()
            event = conn.execute("SELECT printer_id FROM events WHERE type = 'test'").fetchone()
            self.assertIsNotNone(printer)
            self.assertEqual(job["printer_id"], printer["id"])
            self.assertEqual(event["printer_id"], printer["id"])
            self.assertIsNone(printer["moonraker_url"])
            self.assertIsNone(printer["external_id"])
            self.assertEqual(self._load_setting(conn, "printer_settings"), {})
            self.assertEqual(self._load_setting(conn, "filament_mappings"), {})
            self.assertEqual(
                self._load_setting(conn, "display_settings")["hidden_printers"],
                ["Delete Me", "Other"],
            )

    def test_rename_rolls_back_when_setting_persistence_fails(self):
        self._seed_printer("Before", job_uid="job-before")
        with self.db_module.connect_db() as conn:
            self._save_setting(conn, "printer_settings", {"Before": {"rate_per_hour": 5.0}})
        from core import printer_lifecycle

        with patch.object(printer_lifecycle, "_save_setting", side_effect=RuntimeError("write failed")):
            with self.assertRaisesRegex(RuntimeError, "write failed"):
                printer_lifecycle.rename_printer("Before", "After")

        with self.db_module.connect_db() as conn:
            self.assertIsNotNone(conn.execute("SELECT id FROM printers WHERE name = 'Before'").fetchone())
            self.assertIsNone(conn.execute("SELECT id FROM printers WHERE name = 'After'").fetchone())

    def test_merge_rejects_two_external_identities_without_changes(self):
        self._seed_printer("Primary", external_id="client-primary", job_uid="job-primary")
        self._seed_printer("Secondary", external_id="client-secondary", job_uid="job-secondary")
        from core import printer_lifecycle

        with self.assertRaisesRegex(ValueError, "distinct external IDs"):
            printer_lifecycle.merge_printers("Primary", "Secondary")

        with self.db_module.connect_db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) AS count FROM printers").fetchone()["count"], 2)
            self.assertEqual(conn.execute("SELECT COUNT(*) AS count FROM jobs").fetchone()["count"], 2)


if __name__ == "__main__":
    unittest.main()
