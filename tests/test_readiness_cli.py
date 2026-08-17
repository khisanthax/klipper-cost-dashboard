import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone


class ReadinessCliTests(unittest.TestCase):
    def setUp(self):
        from core import db as db_module

        self.db_module = db_module
        self._previous_db_path = db_module._db_path
        self._tmp = tempfile.TemporaryDirectory()
        db_module._db_path = lambda: os.path.join(self._tmp.name, "kcd.db")

    def tearDown(self):
        self.db_module._db_path = self._previous_db_path
        self._tmp.cleanup()

    def _run_readiness(self):
        from kcd.__main__ import main

        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = main(["db", "readiness"])
        return exit_code, output.getvalue()

    def test_readiness_command_returns_nonzero_with_actionable_failures(self):
        exit_code, output = self._run_readiness()

        self.assertEqual(exit_code, 2)
        self.assertIn("SQL-only readiness: NOT READY", output)
        self.assertIn("missing_pause_billing_default", output)
        self.assertIn("Required actions:", output)

    def test_readiness_command_returns_success_when_ready(self):
        with self.db_module.connect_db() as conn:
            self.db_module.apply_migrations(conn)
            conn.execute(
                "INSERT INTO user_settings (key, value_json, updated_at) VALUES (?, ?, ?)",
                (
                    "display_settings",
                    json.dumps(
                        {
                            "pause_include_paused_time_default": False,
                            "hidden_printers": [],
                        }
                    ),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

        exit_code, output = self._run_readiness()

        self.assertEqual(exit_code, 0)
        self.assertIn("SQL-only readiness: READY", output)
        self.assertIn("[PASS] configured_printer_pricing", output)


if __name__ == "__main__":
    unittest.main()
