import unittest


class ImportInferenceTests(unittest.TestCase):
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
