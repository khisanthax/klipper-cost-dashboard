import unittest
from unittest.mock import patch


class ImportInferenceTests(unittest.TestCase):
    def test_explicit_terminal_status_precedes_positive_print_time(self):
        from core.import_moonraker import _infer_outcome
        for status, expected in (("canceled", "cancelled"), ("cancelled", "cancelled"),
                                 ("failed", "failed"), ("error", "failed"), ("completed", "completed")):
            with self.subTest(status=status):
                self.assertEqual(_infer_outcome({"status": status}, 60, 100), expected)
        self.assertEqual(_infer_outcome({"result": "cancelled"}, 60, 100), "cancelled")
        self.assertEqual(_infer_outcome({"status": "completed"}, 0, 100), "completed")

    def test_explicit_unknown_is_not_inferred_completed_but_legacy_time_is_supported(self):
        from core.import_moonraker import _infer_outcome
        for status in ("unknown", "in_progress", "printing", "future-status"):
            self.assertEqual(_infer_outcome({"status": status}, 60, 100), "unknown")
        self.assertEqual(_infer_outcome({}, 60, 100), "completed")

    def test_terminal_status_fix_keeps_measured_time_and_filament(self):
        from core import import_moonraker
        for status in ("cancelled", "failed", "unknown", "completed"):
            job={"job_id": "one", "filename": "same.gcode", "status": status,
                 "start_time": 1700000000, "end_time": 1700000900,
                 "print_duration": 60.5, "estimated_time": 1000, "filament_used": 12.5}
            with patch.object(import_moonraker, "compute_costs", return_value={"time_cost": 0, "material_cost": None, "total_cost": None}) as cost:
                row=import_moonraker._build_row_from_history_job("One", job)
            self.assertEqual(row["status"], status)
            self.assertEqual(row["duration_seconds"], 60.5)
            self.assertEqual(row["filament_mm"], 12.5)
            self.assertEqual(row["time_cost"], 0)
            self.assertIsNone(row["total_cost"])
            cost.assert_called_once_with("One", 60.5, 12.5)

    def test_cancelled_duration_prefers_elapsed_and_caps_to_estimate(self):
        from core.import_moonraker import infer_cancelled_effective_duration_seconds

        out = infer_cancelled_effective_duration_seconds(
            estimated_seconds=1000.0,
            elapsed_seconds=1200.0,
            cancelled_attempt_index=3,
        )
        self.assertEqual(out, 1000.0)

        out2 = infer_cancelled_effective_duration_seconds(
            estimated_seconds=1000.0,
            elapsed_seconds=400.0,
            cancelled_attempt_index=3,
        )
        self.assertEqual(out2, 400.0)

    def test_cancelled_duration_ramps_by_attempt_index(self):
        from core.import_moonraker import infer_cancelled_effective_duration_seconds

        out1 = infer_cancelled_effective_duration_seconds(
            estimated_seconds=1000.0,
            elapsed_seconds=0.0,
            cancelled_attempt_index=1,
        )
        self.assertEqual(out1, 100.0)

        out3 = infer_cancelled_effective_duration_seconds(
            estimated_seconds=1000.0,
            elapsed_seconds=0.0,
            cancelled_attempt_index=3,
        )
        self.assertAlmostEqual(out3, 300.0, places=6)

        out7 = infer_cancelled_effective_duration_seconds(
            estimated_seconds=1000.0,
            elapsed_seconds=0.0,
            cancelled_attempt_index=7,
        )
        self.assertAlmostEqual(out7, 600.0, places=6)

    def test_cancelled_filament_scales_by_progress(self):
        from core.import_moonraker import infer_cancelled_effective_filament_mm

        out = infer_cancelled_effective_filament_mm(
            filament_mm_raw=0.0,
            filament_mm_est=10000.0,
            duration_seconds_effective=200.0,
            duration_seconds_est=1000.0,
        )
        self.assertEqual(out, 2000.0)


if __name__ == "__main__":
    unittest.main()
