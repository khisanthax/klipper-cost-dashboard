import unittest
from unittest.mock import patch


class SqlPersistenceErrorTests(unittest.TestCase):
    def test_user_setting_write_propagates_connection_failure(self):
        from core import storage

        with patch("core.db.connect_db", side_effect=RuntimeError("database unavailable")):
            with self.assertRaisesRegex(RuntimeError, "database unavailable"):
                storage._save_user_settings_sql("printer_settings", {"SV08": {}})

    def test_backup_setting_write_propagates_connection_failure(self):
        from core import backup

        with patch("core.db.connect_db", side_effect=RuntimeError("database unavailable")):
            with self.assertRaisesRegex(RuntimeError, "database unavailable"):
                backup._save_backup_settings_sql({"auto_backup_enabled": True})


if __name__ == "__main__":
    unittest.main()
