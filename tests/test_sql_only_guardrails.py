import json
import os
import sys
import unittest
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from core.sql_only import SqlOnlyViolationError


class SqlOnlyGuardrailTests(unittest.TestCase):
    def setUp(self):
        self._prev = os.environ.get("KCD_STORAGE_BACKEND")
        self._prev_fail_fast = os.environ.get("KCD_SQL_ONLY_FAIL_FAST")
        os.environ["KCD_STORAGE_BACKEND"] = "sql"

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("KCD_STORAGE_BACKEND", None)
        else:
            os.environ["KCD_STORAGE_BACKEND"] = self._prev
        if self._prev_fail_fast is None:
            os.environ.pop("KCD_SQL_ONLY_FAIL_FAST", None)
        else:
            os.environ["KCD_SQL_ONLY_FAIL_FAST"] = self._prev_fail_fast
        sys.modules.pop("app", None)

    def _seed_ready_db(self, db_module):
        now = datetime.now(timezone.utc).isoformat()
        with closing(db_module.connect_db()) as conn:
            db_module.apply_migrations(conn)
            conn.execute(
                """
                INSERT INTO user_settings (key, value_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json, updated_at = excluded.updated_at
                """,
                (
                    "display_settings",
                    json.dumps(
                        {
                            "visible_columns": ["printer"],
                            "pause_include_paused_time_default": False,
                        }
                    ),
                    now,
                ),
            )
            conn.commit()

    def _seed_printer(self, db_module, name="SV08", moonraker_url="http://moonraker.local"):
        now = datetime.now(timezone.utc).isoformat()
        with closing(db_module.connect_db()) as conn:
            db_module.apply_migrations(conn)
            db_module.upsert_printer(conn, name, moonraker_url=moonraker_url)
            conn.execute(
                """
                INSERT INTO user_settings (key, value_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json, updated_at = excluded.updated_at
                """,
                (
                    "printer_settings",
                    json.dumps(
                        {
                            name: {
                                "rate_per_hour": 6.0,
                                "filament_mode": "per_meter",
                                "filament_rate": 0.07,
                                "grams_per_meter": 2.5,
                            }
                        }
                    ),
                    now,
                ),
            )
            conn.commit()

    def test_csv_append_blocked(self):
        from core.storage import append_row
        with self.assertRaises(SqlOnlyViolationError):
            append_row("/tmp/print_costs.csv", ["printer"], {"printer": "TEST"})

    def test_system_events_use_sql_persistence(self):
        from core import system_events as se
        from core import db as db_module

        scratch_root = os.path.join(os.getcwd(), "tests", ".tmp")
        os.makedirs(scratch_root, exist_ok=True)
        tmpdir = os.path.join(scratch_root, f"sql_only_guardrails_{uuid.uuid4().hex}")
        os.makedirs(tmpdir, exist_ok=True)
        orig_db_path = db_module._db_path
        db_module._db_path = lambda: os.path.join(tmpdir, "kcd.db")
        try:
            se.emit_event("warning", "test", "written")
            events = se.list_events("all", limit=10)
            self.assertTrue(any(e.get("title") == "test" for e in events))
        finally:
            db_module._db_path = orig_db_path

    def test_guardrails_present_in_sources(self):
        base = Path(__file__).resolve().parents[1]
        storage_src = (base / "core" / "storage.py").read_text(encoding="utf-8")
        events_src = (base / "core" / "system_events.py").read_text(encoding="utf-8")
        self.assertIn("require_file_writes_allowed", storage_src)
        self.assertIn("require_file_writes_allowed", events_src)

    def test_history_repo_sql_only_override(self):
        import sqlite3
        from core.history_repo import HistoryQuery, list_history_rows
        conns = []
        orig_connect = sqlite3.connect

        def _patched_connect(*args, **kwargs):
            conn = orig_connect(*args, **kwargs)
            conns.append(conn)
            return conn

        sqlite3.connect = _patched_connect
        try:
            result = list_history_rows(HistoryQuery(), page=1, per_page=25)
            self.assertEqual(result.backend, "sql")
        finally:
            for c in conns:
                try:
                    c.close()
                except Exception:
                    pass
            sqlite3.connect = orig_connect

    def test_reports_repo_sql_only_override(self):
        import sqlite3
        from core.reports_repo import get_reports_data_range

        conns = []
        orig_connect = sqlite3.connect

        def _patched_connect(*args, **kwargs):
            conn = orig_connect(*args, **kwargs)
            conns.append(conn)
            return conn

        sqlite3.connect = _patched_connect
        try:
            data = get_reports_data_range(
                start_dt=None,
                end_dt=None,
                range_label="all",
                quick_range="all",
            )
            self.assertEqual(data.get("backend"), "sql")
        finally:
            for c in conns:
                try:
                    c.close()
                except Exception:
                    pass
            sqlite3.connect = orig_connect

    def test_representative_routes_do_not_touch_legacy_runtime_files_in_sql_only(self):
        from unittest.mock import patch
        from core import db as db_module
        from core import storage

        scratch_root = os.path.join(os.getcwd(), "tests", ".tmp")
        os.makedirs(scratch_root, exist_ok=True)
        tmpdir = os.path.join(scratch_root, f"sql_only_guardrails_{uuid.uuid4().hex}")
        os.makedirs(tmpdir, exist_ok=True)
        orig_db_path = db_module._db_path
        db_module._db_path = lambda: os.path.join(tmpdir, "kcd.db")
        try:
            self._seed_ready_db(db_module)
            self._seed_printer(db_module)
            sys.modules.pop("app", None)
            import app as kcd_app

            touched = []

            def _record_read(resource="", *, caller_hint=None):
                touched.append(("read", resource, caller_hint))

            def _record_write(resource="", *, caller_hint=None):
                touched.append(("write", resource, caller_hint))

            client = kcd_app.app.test_client()
            endpoints = [
                "/health",
                "/",
                "/reports",
                "/projects",
                "/settings/printers",
                "/settings/other",
                "/system-events",
            ]
            with patch.object(storage, "require_file_reads_allowed", side_effect=_record_read), patch.object(
                storage,
                "require_file_writes_allowed",
                side_effect=_record_write,
            ):
                for endpoint in endpoints:
                    response = client.get(endpoint)
                    self.assertLess(
                        response.status_code,
                        500,
                        f"{endpoint} returned {response.status_code}",
                    )

                post_requests = [
                    (
                        "/settings/pause",
                        {
                            "action": "update_pause_settings",
                            "pause_include_paused_time_default": "1",
                            "pause_use_global_SV08": "1",
                        },
                    ),
                    (
                        "/settings/printers",
                        {
                            "action": "save_printer_defaults",
                            "printer": "SV08",
                            "rate_per_hour": "7.5",
                            "filament_mode": "per_meter",
                            "filament_rate": "0.08",
                            "grams_per_meter": "3.0",
                        },
                    ),
                    (
                        "/settings/printers",
                        {
                            "action": "save_moonraker_url",
                            "printer": "SV08",
                            "moonraker_url": "http://printer.local",
                        },
                    ),
                    (
                        "/projects",
                        {
                            "action": "update_projects_display",
                            "projects_show_cost_totals": "1",
                        },
                    ),
                ]
                for endpoint, form in post_requests:
                    response = client.post(endpoint, data=form, follow_redirects=False)
                    self.assertIn(
                        response.status_code,
                        (302, 303),
                        f"{endpoint} POST returned {response.status_code}",
                    )

            self.assertEqual(touched, [], f"SQL-only route touched legacy runtime file guard(s): {touched}")

            with closing(db_module.connect_db()) as conn:
                display_row = conn.execute(
                    "SELECT value_json FROM user_settings WHERE key = ?",
                    ("display_settings",),
                ).fetchone()
                settings_row = conn.execute(
                    "SELECT value_json FROM user_settings WHERE key = ?",
                    ("printer_settings",),
                ).fetchone()
                printer_row = conn.execute(
                    "SELECT moonraker_url FROM printers WHERE name = ?",
                    ("SV08",),
                ).fetchone()

            display_settings = json.loads(display_row["value_json"])
            printer_settings = json.loads(settings_row["value_json"])
            self.assertTrue(display_settings.get("projects_show_cost_totals"))
            self.assertTrue(display_settings.get("pause_include_paused_time_default"))
            self.assertEqual(printer_settings.get("SV08", {}).get("rate_per_hour"), 7.5)
            self.assertEqual(printer_settings.get("SV08", {}).get("filament_mode"), "per_meter")
            self.assertEqual(printer_row["moonraker_url"], "http://printer.local")
        finally:
            db_module._db_path = orig_db_path


if __name__ == "__main__":
    unittest.main()
