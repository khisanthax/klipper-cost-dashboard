import os
import tempfile
import unittest
from unittest.mock import patch


class LivePauseResumeTests(unittest.TestCase):
    def setUp(self):
        import core.live as live

        self._tmp = tempfile.TemporaryDirectory()
        live.LIVE_JOBS_FILE = os.path.join(self._tmp.name, "live_jobs.json")
        live._jobs = {}
        self.live = live

    def tearDown(self):
        self._tmp.cleanup()

    def test_pause_then_resume_accumulates_paused_seconds(self):
        self.live.start_job("SV08", "test.gcode", start_time=1000.0)

        with patch.object(self.live.time, "time", return_value=1100.0):
            job = self.live.pause_job("SV08")
        self.assertEqual(job.get("status"), "paused")
        self.assertAlmostEqual(float(job.get("pause_time")), 1100.0, places=3)

        with patch.object(self.live.time, "time", return_value=1205.0):
            job = self.live.resume_job("SV08")
        self.assertEqual(job.get("status"), "printing")
        self.assertNotIn("pause_time", job)
        self.assertAlmostEqual(float(job.get("total_paused_duration")), 105.0, places=3)

        with patch.object(self.live.time, "time", return_value=1300.0):
            enriched = self.live.get_job("SV08")
        self.assertIsNotNone(enriched)
        self.assertEqual(enriched.get("status"), "printing")
        self.assertAlmostEqual(float(enriched.get("paused_seconds")), 105.0, places=3)
        self.assertAlmostEqual(float(enriched.get("elapsed_seconds")), 195.0, places=3)

    def test_pause_missing_job_is_noop(self):
        self.assertIsNone(self.live.pause_job("SV08"))

    def test_resume_non_paused_job_is_noop(self):
        self.live.start_job("SV08", "test.gcode", start_time=1000.0)
        with patch.object(self.live.time, "time", return_value=1100.0):
            job = self.live.resume_job("SV08")
        self.assertEqual(job.get("status"), "printing")
        self.assertEqual(float(job.get("total_paused_duration") or 0.0), 0.0)

