import os
import tempfile
import unittest


class SystemEventsTests(unittest.TestCase):
    def setUp(self):
        from core import system_events

        self._system_events = system_events
        self._orig_file = system_events.EVENTS_FILE
        self._orig_max = system_events.MAX_EVENTS

        self._tmpdir = tempfile.TemporaryDirectory()
        system_events.EVENTS_FILE = os.path.join(self._tmpdir.name, "system_events.jsonl")
        system_events.MAX_EVENTS = 50

    def tearDown(self):
        self._system_events.EVENTS_FILE = self._orig_file
        self._system_events.MAX_EVENTS = self._orig_max
        self._tmpdir.cleanup()

    def test_emit_and_list_filters(self):
        se = self._system_events

        se.emit_event("activity", "A1", "hello")
        se.emit_event("warning", "W1", "warn")
        se.emit_event("deleted", "D1", "deleted")

        all_events = se.list_events("all", limit=10)
        self.assertEqual(len(all_events), 3)
        self.assertEqual(all_events[0].get("title"), "D1")  # newest first

        failures = se.list_events("failures", limit=10)
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].get("category"), "warning")

        deleted = se.list_events("deleted", limit=10)
        self.assertEqual(len(deleted), 1)
        self.assertEqual(deleted[0].get("category"), "deleted")

    def test_system_events_page_renders(self):
        # Import here so it picks up the patched system_events module state.
        import app as kcd_app

        self._system_events.emit_event("warning", "W1", "warn")
        client = kcd_app.app.test_client()
        resp = client.get("/system-events")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"System Events", resp.data)

    def test_emit_and_list_sql_only(self):
        import os as _os
        import tempfile as _tempfile
        from core import system_events as se
        from core import db as db_module

        orig_env = _os.environ.get("KCD_STORAGE_BACKEND")
        _os.environ["KCD_STORAGE_BACKEND"] = "sql"

        tmpdir = _tempfile.TemporaryDirectory()
        orig_db_path = db_module._db_path
        db_module._db_path = lambda: _os.path.join(tmpdir.name, "kcd.db")

        try:
            se.emit_event("activity", "SQL1", "hello-sql")
            events = se.list_events("all", limit=10)
            self.assertTrue(any(e.get("title") == "SQL1" for e in events))
        finally:
            db_module._db_path = orig_db_path
            if orig_env is None:
                _os.environ.pop("KCD_STORAGE_BACKEND", None)
            else:
                _os.environ["KCD_STORAGE_BACKEND"] = orig_env
            tmpdir.cleanup()


if __name__ == "__main__":
    unittest.main()
