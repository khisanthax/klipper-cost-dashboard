import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from core import db
from installer.utils import generate_job_cancel_script
from tools import reconcile_cancelled_job as recovery


class CancelRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.addCleanup(self.conn.close)
        self.conn.row_factory = sqlite3.Row
        db.apply_migrations(self.conn)
        db.upsert_printer(self.conn, "SV07", "http://fixture")
        self.live = {"printer_name": "SV07", "filename": "same.gcode", "start_time": 100.3}
        self.conn.execute(
            "INSERT INTO user_settings (key,value_json,updated_at) VALUES ('live_jobs',?,'now')",
            (json.dumps({"jobs": {"SV07": self.live}}),),
        )
        self.job = {
            "job_id": "1", "filename": "same.gcode", "status": "cancelled",
            "start_time": 100, "end_time": 135, "print_duration": 20.5,
            "total_duration": 35, "filament_used": 12.25,
        }
        self.state = "cancelled"
        self.entries = [self.job, dict(self.job, job_id="2", start_time=40)]

    def get(self, base, path):
        if path.startswith("/printer/"):
            return {"status": {
                "print_stats": {"state": self.state},
                "gcode_macro _KCD_VARS": {"printer_name": "SV07"},
            }}
        if path.startswith("/server/history/job?"):
            return {"job": self.job}
        return {"jobs": self.entries}

    def test_preview_apply_and_repeat_preserve_measurements(self):
        with patch.object(recovery, "get", self.get), patch.dict(os.environ, {"KCD_STORAGE_BACKEND": "sql"}):
            plan = recovery.preview(self.conn, "SV07", "1")
            self.assertEqual(self.conn.execute("SELECT count(*) FROM jobs").fetchone()[0], 0)
            recovery.apply(self.conn, plan)
            self.assertEqual(recovery.preview(self.conn, "SV07", "1"), {"already_recorded": plan["after"]["job_uid"]})
        row = self.conn.execute("SELECT * FROM jobs").fetchone()
        self.assertEqual((row["status"], row["duration_seconds_raw"], row["filament_mm"], row["total_cost"]),
                         ("canceled", 20.5, 12.25, None))
        self.assertEqual(json.loads(self.conn.execute("SELECT value_json FROM user_settings").fetchone()[0]), {"jobs": {}})
        self.assertEqual(self.conn.execute("SELECT count(*) FROM system_events").fetchone()[0], 1)

    def test_active_disconnected_and_ambiguous_jobs_cannot_be_recovered(self):
        with patch.object(recovery, "get", self.get):
            for self.state in ("printing", "paused", "unknown"):
                with self.assertRaisesRegex(ValueError, "active or state"):
                    recovery.preview(self.conn, "SV07", "1")
            self.state = "cancelled"
            self.entries.append(dict(self.job, job_id="3", start_time=101))
            with self.assertRaisesRegex(ValueError, "ambiguous"):
                recovery.preview(self.conn, "SV07", "1")
        with patch.object(recovery, "get", side_effect=OSError("disconnected")):
            with self.assertRaises(OSError):
                recovery.preview(self.conn, "SV07", "1")
        self.assertEqual(self.conn.execute("SELECT count(*) FROM jobs").fetchone()[0], 0)

    @unittest.skipUnless(os.name == "posix" and shutil.which("bash"), "Requires Linux Bash")
    def test_legacy_literal_argument_is_removed_from_cancel_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / "cancel.sh"
            script.write_text(generate_job_cancel_script("http://fixture", "fixture-key"))
            curl = root / "curl"
            curl.write_text('#!/bin/bash\nprintf "%s" "${@: -1}"\n')
            curl.chmod(0o700)
            env = dict(os.environ, PATH=directory + os.pathsep + os.environ["PATH"])
            for prefix in ([], ["{params}"]):
                result = subprocess.check_output(
                    ["bash", str(script), *prefix, "SV07|Maker's same file.gcode|20.5"], env=env,
                )
                payload = json.loads(result)
                self.assertEqual(payload["printer_name"], "SV07")
                self.assertEqual(payload["filename"], "Maker's same file.gcode")
                self.assertEqual(payload["elapsed_seconds"], 20.5)
