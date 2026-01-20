import os
import unittest
from pathlib import Path

from core.sql_only import SqlOnlyViolationError


class SqlOnlyGuardrailTests(unittest.TestCase):
    def setUp(self):
        self._prev = os.environ.get("KCD_STORAGE_BACKEND")
        os.environ["KCD_STORAGE_BACKEND"] = "sql"

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("KCD_STORAGE_BACKEND", None)
        else:
            os.environ["KCD_STORAGE_BACKEND"] = self._prev

    def test_csv_append_blocked(self):
        from core.storage import append_row
        with self.assertRaises(SqlOnlyViolationError):
            append_row("/tmp/print_costs.csv", ["printer"], {"printer": "TEST"})

    def test_system_events_blocked(self):
        from core.system_events import emit_event
        with self.assertRaises(SqlOnlyViolationError):
            emit_event("warning", "test", "blocked")

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


if __name__ == "__main__":
    unittest.main()
