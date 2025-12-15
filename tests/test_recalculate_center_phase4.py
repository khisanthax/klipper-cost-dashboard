import unittest
from unittest.mock import patch


class RecalculateCenterPhase4Tests(unittest.TestCase):
    def test_filter_by_project_uses_assignments(self):
        import app as app_module

        rows = [
            {"job_uid": "uid1", "printer": "SV08", "filename": "a.gcode", "status": "COMPLETED"},
            {"job_uid": "uid2", "printer": "SV08", "filename": "b.gcode", "status": "COMPLETED"},
        ]

        with patch.object(app_module.projects, "load_assignments", return_value={"uid1": "p1"}):
            filtered, _start_dt, _end_dt = app_module._filter_history_rows_for_recalc(rows, {"project": "p1"})

        self.assertEqual([r.get("job_uid") for r in filtered], ["uid1"])


if __name__ == "__main__":
    unittest.main()

