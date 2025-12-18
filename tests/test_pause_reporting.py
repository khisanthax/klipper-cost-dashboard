import unittest


class PauseReportingTests(unittest.TestCase):
    def test_compute_pause_analytics(self):
        from core.reports import compute_pause_analytics

        rows = [
            {"printer": "SV08", "paused_seconds_total": "60", "runout_count": "1"},
            {"printer": "SV08", "paused_seconds_total": "0", "runout_count": "0"},
            {"printer": "SV07", "paused_seconds_total": "120", "runout_count": "2"},
            {"printer": "", "paused_seconds_total": "bad", "runout_count": "bad"},
        ]

        out = compute_pause_analytics(rows)
        self.assertAlmostEqual(out["total_paused_seconds"], 180.0, places=6)
        self.assertAlmostEqual(out["average_paused_seconds"], 45.0, places=6)  # 180 / 4 rows
        self.assertEqual(out["runouts_by_printer"].get("SV08"), 1)
        self.assertEqual(out["runouts_by_printer"].get("SV07"), 2)


if __name__ == "__main__":
    unittest.main()

