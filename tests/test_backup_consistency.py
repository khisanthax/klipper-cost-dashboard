import json
import os
import sqlite3
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from contextlib import closing
from unittest.mock import patch


class BackupConsistencyTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = os.path.join(self._tmp.name, "data")
        self.backups_dir = os.path.join(self.data_dir, "backups")
        self.db_path = os.path.join(self.data_dir, "kcd.db")
        os.makedirs(self.backups_dir, exist_ok=True)

        from core import backup, db as db_module

        self.backup = backup
        self.db_module = db_module
        self.originals = {
            "data": backup.DATA_DIR,
            "backups": backup.BACKUPS_DIR,
            "csv": backup.CSV_FILE,
            "db_path": db_module._db_path,
        }
        backup.DATA_DIR = self.data_dir
        backup.BACKUPS_DIR = self.backups_dir
        backup.CSV_FILE = os.path.join(self.data_dir, "print_costs.csv")
        db_module._db_path = lambda: self.db_path

    def tearDown(self):
        self.backup.DATA_DIR = self.originals["data"]
        self.backup.BACKUPS_DIR = self.originals["backups"]
        self.backup.CSV_FILE = self.originals["csv"]
        self.db_module._db_path = self.originals["db_path"]
        self._tmp.cleanup()

    def _seed_sql_state(self):
        with closing(self.db_module.connect_db()) as conn:
            self.db_module.apply_migrations(conn)
            self.db_module.upsert_printer(conn, "SV08", moonraker_url="http://sv08.local")
            self.db_module.upsert_job(
                conn,
                {
                    "job_uid": "backup-job",
                    "printer": "SV08",
                    "filename": "part.gcode",
                    "status": "completed",
                    "timestamp": 1_700_000_000,
                    "total_cost": 4.25,
                },
            )
            conn.commit()

    def test_archive_restores_consistent_sqlite_state_without_wal_files(self):
        self._seed_sql_state()
        with open(os.path.join(self.data_dir, "secret.json"), "w", encoding="utf-8") as handle:
            json.dump({"api_key": "sensitive"}, handle)
        with open(os.path.join(self.backups_dir, "old.tar.gz"), "wb") as handle:
            handle.write(b"old")

        archive = self.backup.create_backup_archive()
        restore_dir = os.path.join(self._tmp.name, "restore")
        os.makedirs(restore_dir, exist_ok=True)
        with tarfile.open(archive, "r:gz") as tf:
            names = tf.getnames()
            tf.extractall(restore_dir, filter="data")

        self.assertIn("data/kcd.db", names)
        self.assertIn("data/secret.json", names)
        self.assertFalse(any(name.startswith("data/backups") for name in names))
        self.assertNotIn("data/kcd.db-wal", names)
        self.assertNotIn("data/kcd.db-shm", names)

        restored_db = os.path.join(restore_dir, "data", "kcd.db")
        with closing(sqlite3.connect(restored_db)) as conn:
            schema = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
            job = conn.execute("SELECT job_uid, total_cost FROM jobs WHERE job_uid = ?", ("backup-job",)).fetchone()
        self.assertEqual(schema, "0006_system_events")
        self.assertEqual(job, ("backup-job", 4.25))

    def test_archive_permission_hardening_is_applied(self):
        with patch.object(self.backup.os, "chmod", wraps=os.chmod) as chmod:
            archive = self.backup.create_backup_archive()
        chmod.assert_called_once_with(archive, 0o600)

    @unittest.skipIf(os.name == "nt", "Windows does not expose POSIX permission bits")
    def test_archive_permissions_are_owner_only(self):
        archive = self.backup.create_backup_archive()
        self.assertEqual(stat.S_IMODE(os.stat(archive).st_mode), 0o600)

    def test_chmod_failure_removes_published_archive(self):
        with patch.object(self.backup.os, "chmod", side_effect=OSError("chmod failed")):
            with self.assertRaisesRegex(OSError, "chmod failed"):
                self.backup.create_backup_archive()
        self.assertEqual(os.listdir(self.backups_dir), [])

    def test_retention_still_removes_old_archives(self):
        old_paths = []
        for index in range(3):
            path = os.path.join(self.backups_dir, f"kcd_backup_20000101_00000{index}.tar.gz")
            with open(path, "wb") as handle:
                handle.write(b"old")
            os.utime(path, (index + 1, index + 1))
            old_paths.append(path)

        archive = self.backup.create_backup_archive(keep=2)

        retained = sorted(os.listdir(self.backups_dir))
        self.assertEqual(len(retained), 2)
        self.assertIn(os.path.basename(archive), retained)
        self.assertFalse(os.path.exists(old_paths[0]))

    def test_snapshot_failure_removes_partial_archive(self):
        self._seed_sql_state()
        with patch.object(self.backup, "_create_sqlite_snapshot", side_effect=RuntimeError("snapshot failed")):
            with self.assertRaisesRegex(RuntimeError, "snapshot failed"):
                self.backup.create_backup_archive()
        self.assertEqual(os.listdir(self.backups_dir), [])

    def test_sql_only_automatic_backup_is_explicitly_disabled(self):
        previous_backend = os.environ.get("KCD_STORAGE_BACKEND")
        os.environ["KCD_STORAGE_BACKEND"] = "sql"
        try:
            ran, archive, error = self.backup.maybe_run_auto_backup()
            self.assertFalse(ran)
            self.assertIsNone(archive)
            self.assertEqual(error, "Automatic backups are disabled in SQL-only mode.")
            with self.assertRaisesRegex(ValueError, "unavailable in SQL-only mode"):
                self.backup.save_backup_settings(
                    auto_backup_enabled=True,
                    auto_backup_frequency="daily",
                    auto_backup_keep=2,
                )
        finally:
            if previous_backend is None:
                os.environ.pop("KCD_STORAGE_BACKEND", None)
            else:
                os.environ["KCD_STORAGE_BACKEND"] = previous_backend


class BackupToolInvocationTests(unittest.TestCase):
    def test_help_runs_outside_repository_without_pythonpath(self):
        repo_root = Path(__file__).resolve().parents[1]
        script = repo_root / "tools" / "kcd_backup.py"
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        with tempfile.TemporaryDirectory() as working_dir:
            result = subprocess.run(
                [sys.executable, str(script), "--help"],
                cwd=working_dir,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Create a KCD backup archive", result.stdout)

    def test_backup_runs_outside_repository_without_pythonpath(self):
        repo_root = Path(__file__).resolve().parents[1]
        script = repo_root / "tools" / "kcd_backup.py"
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        env["KCD_API_KEY"] = "isolated-backup-test-key"
        with tempfile.TemporaryDirectory() as working_dir:
            result = subprocess.run(
                [sys.executable, str(script), "--keep", "1"],
                cwd=working_dir,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            archives = list(Path(working_dir, "data", "backups").glob("kcd_backup_*.tar.gz"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(archives), 1)


if __name__ == "__main__":
    unittest.main()
