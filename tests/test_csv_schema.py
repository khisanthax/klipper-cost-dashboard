import csv
import os
import tempfile
import unittest


class CsvSchemaTests(unittest.TestCase):
    def test_ensure_csv_schema_preserves_appended_columns_when_header_is_prefix(self):
        from core.storage import ensure_csv_schema

        expected = ["a", "b", "c", "d"]

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "test.csv")

            # Simulate a schema drift situation:
            # header has only a,b, but rows contain 4 values (c,d were appended later).
            with open(path, "w", newline="") as f:
                f.write("a,b\n")
                f.write("1,2,3,4\n")
                f.write("5,6,7,8\n")

            migrated = ensure_csv_schema(path, expected)
            self.assertTrue(migrated)

            with open(path, newline="") as f:
                reader = csv.DictReader(f)
                self.assertEqual(list(reader.fieldnames or []), expected)
                rows = list(reader)

            self.assertEqual(rows[0]["a"], "1")
            self.assertEqual(rows[0]["b"], "2")
            self.assertEqual(rows[0]["c"], "3")
            self.assertEqual(rows[0]["d"], "4")


if __name__ == "__main__":
    unittest.main()

