import os
import unittest
import uuid


class DbConnectionTests(unittest.TestCase):
    def setUp(self):
        from core import db as db_module

        self._db_module = db_module
        scratch_root = os.path.join(os.getcwd(), "tests", ".tmp")
        os.makedirs(scratch_root, exist_ok=True)
        self._test_id = uuid.uuid4().hex
        self._scratch_dir = os.path.join(scratch_root, f"db_connection_{self._test_id}")
        os.makedirs(self._scratch_dir, exist_ok=True)
        self._db_file = os.path.join(self._scratch_dir, "kcd.db")
        self._orig_db_path = db_module._db_path
        db_module._db_path = lambda: self._db_file

    def tearDown(self):
        self._db_module._db_path = self._orig_db_path
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(self._db_file + suffix)
            except (FileNotFoundError, PermissionError):
                pass

    def test_connect_db_context_manager_closes_connection(self):
        import sqlite3

        with self._db_module.connect_db() as conn:
            self.assertIsNotNone(conn.execute("SELECT 1").fetchone())

        with self.assertRaises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")


if __name__ == "__main__":
    unittest.main()
