import unittest

from core import db_import


class DbImportTests(unittest.TestCase):
    def test_deterministic_job_uid_is_stable(self):
        row = {
            "printer": "SV08",
            "filename": "test.gcode",
            "timestamp": "1234567890",
            "duration_seconds": "3600",
            "filament_mm": "12000",
        }
        uid_a = db_import._deterministic_job_uid(row)
        uid_b = db_import._deterministic_job_uid(row)
        self.assertEqual(uid_a, uid_b)


if __name__ == "__main__":
    unittest.main()
