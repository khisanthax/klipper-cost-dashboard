import os
import tempfile
import unittest

from core.sql_only import SqlOnlyViolationError
from core.storage import load_rows_raw


class SqlOnlyGuardTests(unittest.TestCase):
    def test_csv_read_raises_in_sql_only(self):
        old = os.environ.get("KCD_STORAGE_BACKEND")
        os.environ["KCD_STORAGE_BACKEND"] = "sql"
        try:
            with tempfile.TemporaryDirectory() as tmp:
                csv_path = os.path.join(tmp, "print_costs.csv")
                with self.assertRaises(SqlOnlyViolationError):
                    load_rows_raw(csv_path)
        finally:
            if old is None:
                os.environ.pop("KCD_STORAGE_BACKEND", None)
            else:
                os.environ["KCD_STORAGE_BACKEND"] = old


if __name__ == "__main__":
    unittest.main()