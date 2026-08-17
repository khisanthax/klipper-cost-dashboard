import os
import unittest
from unittest.mock import patch


class SqlCanonicalReadFailureTests(unittest.TestCase):
    def setUp(self):
        self._previous_backend = os.environ.get("KCD_STORAGE_BACKEND")
        os.environ["KCD_STORAGE_BACKEND"] = "sql"

    def tearDown(self):
        if self._previous_backend is None:
            os.environ.pop("KCD_STORAGE_BACKEND", None)
        else:
            os.environ["KCD_STORAGE_BACKEND"] = self._previous_backend

    def test_canonical_sql_readers_do_not_convert_db_failure_to_empty_state(self):
        from core import profiles, projects, rates, storage, system_events

        readers = (
            projects.load_projects,
            profiles.get_all_profiles,
            rates.list_rate_profiles,
            lambda: storage.load_settings("unused-in-sql-only.json"),
            system_events.list_events,
        )
        for reader in readers:
            with self.subTest(reader=getattr(reader, "__name__", repr(reader))):
                with patch("core.db.connect_db", side_effect=RuntimeError("database unavailable")):
                    with self.assertRaisesRegex(RuntimeError, "database unavailable"):
                        reader()


if __name__ == "__main__":
    unittest.main()
