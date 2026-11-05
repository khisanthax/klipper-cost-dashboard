from __future__ import annotations

from contextlib import closing
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from core import db as db_module
from core.demo.basic_seed import MANIFEST_NAME, validate_basic_seed
from core.demo.config import DemoConfiguration
from core.demo.manager import DemoWorkspaceManager, SeedValidationError
from core.readiness import check_sql_only_readiness
from core.workspace_context import bind_workspace_context, reset_workspace_context
from tools.create_demo_seed import create_seed


EXPECTED_COUNTS = {
    "printers": 3,
    "filament_profiles": 4,
    "hourly_rate_profiles": 3,
    "projects": 3,
    "jobs": 18,
    "project_assignments": 12,
    "manual_jobs": 3,
    "planned_items": 3,
    "system_events": 3,
    "report_cache": 0,
}


class BasicDemoSeedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "demo-data"
        self.root.mkdir()
        self.seed = create_seed(self.root, "basic-v1")
        self.config = DemoConfiguration(
            self.root, "basic-v1", 24, 72, 300, cookie_secure=False
        )

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def _database_hash(seed: Path) -> str:
        return hashlib.sha256((seed / "kcd.db").read_bytes()).hexdigest()

    def test_content_manifest_integrity_and_safe_local_only_data(self):
        manifest = validate_basic_seed(self.seed, self.config)
        self.assertEqual(manifest["counts"], EXPECTED_COUNTS)
        self.assertEqual(manifest["database_sha256"], self._database_hash(self.seed))
        self.assertEqual(
            {path.name for path in self.seed.iterdir()},
            {"kcd.db", MANIFEST_NAME},
        )

        uri = f"file:{(self.seed / 'kcd.db').as_posix()}?mode=ro&immutable=1"
        with closing(sqlite3.connect(uri, uri=True)) as conn:
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(
                [row[0] for row in conn.execute("SELECT name FROM printers ORDER BY id")],
                ["Voron 2.4", "Sovol SV08", "Ender 3 V2"],
            )
            self.assertEqual(
                conn.execute(
                    """SELECT COUNT(*) FROM printers
                       WHERE moonraker_url IS NOT NULL OR external_id IS NOT NULL"""
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                {row[0] for row in conn.execute("SELECT status FROM jobs")},
                {"completed", "failed", "cancelled"},
            )
            settings = "\n".join(row[0] for row in conn.execute("SELECT value_json FROM user_settings"))
            for forbidden in ("http://", "https://", "moonraker", "api_key", "ssh"):
                self.assertNotIn(forbidden, settings.lower())

    def test_two_generations_are_byte_and_logically_reproducible(self):
        second_root = Path(self.tmp.name) / "second"
        second_root.mkdir()
        second = create_seed(second_root, "basic-v1")
        self.assertEqual(self._database_hash(self.seed), self._database_hash(second))
        self.assertEqual(
            (self.seed / MANIFEST_NAME).read_bytes(),
            (second / MANIFEST_NAME).read_bytes(),
        )

    def test_seed_is_sql_ready_and_within_demo_quotas(self):
        manager = DemoWorkspaceManager(self.config)
        manager.initialize()
        session = manager.create_workspace()
        token = bind_workspace_context(session.context)
        try:
            with mock.patch.dict(os.environ, {
                "KCD_RUNTIME_MODE": "demo",
                "KCD_STORAGE_BACKEND": "sql",
                "KCD_REPORTS_BACKEND": "sql",
            }, clear=False):
                readiness = check_sql_only_readiness()
        finally:
            reset_workspace_context(token)
        self.assertTrue(readiness["ready"], readiness)
        self.assertLess(
            sum(path.stat().st_size for path in self.seed.rglob("*") if path.is_file()),
            self.config.max_workspace_bytes,
        )

    def test_workspace_modification_and_reset_restore_basic_content(self):
        seed_hash = self._database_hash(self.seed)
        manager = DemoWorkspaceManager(self.config)
        manager.initialize()
        session = manager.create_workspace()
        with closing(db_module.connect_db(session.context)) as conn:
            conn.execute("UPDATE projects SET name='Visitor edit' WHERE project_uid='demo-project-cosplay'")
            conn.execute("DELETE FROM jobs WHERE job_uid='demo-job-013'")
            conn.commit()
        reset = manager.reset_workspace(session)
        with closing(db_module.connect_db(reset.context)) as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT name FROM projects WHERE project_uid='demo-project-cosplay'"
                ).fetchone()[0],
                "Cosplay Helmet",
            )
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 18)
        self.assertEqual(reset.token, session.token)
        self.assertEqual(reset.record.workspace_id, session.record.workspace_id)
        self.assertEqual(reset.record.reset_count, 1)
        self.assertEqual(self._database_hash(self.seed), seed_hash)

    def test_manifest_tampering_and_too_small_quota_fail_closed(self):
        manifest_path = self.seed / MANIFEST_NAME
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["counts"]["jobs"] = 999
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaises(SeedValidationError):
            DemoWorkspaceManager(self.config).initialize()

        other_root = Path(self.tmp.name) / "quota"
        other_root.mkdir()
        create_seed(other_root, "basic-v1")
        limited = replace(self.config, data_root=other_root, max_printers=2)
        with self.assertRaises(SeedValidationError):
            DemoWorkspaceManager(limited).initialize()

    def test_generator_refuses_overwrite_and_unsafe_seed_names(self):
        with self.assertRaises(FileExistsError):
            create_seed(self.root, "basic-v1")
        for unsafe in ("", ".", "..", "../escape", str(Path(self.tmp.name).resolve())):
            with self.subTest(seed_name=unsafe):
                with self.assertRaises(ValueError):
                    create_seed(self.root, unsafe)

    def test_direct_cli_invocation_and_failed_build_cleanup(self):
        cli_root = Path(self.tmp.name) / "cli"
        cli_root.mkdir()
        script = Path(__file__).resolve().parents[1] / "tools" / "create_demo_seed.py"
        completed = subprocess.run(
            [sys.executable, str(script), "--data-root", str(cli_root)],
            cwd=self.tmp.name,
            text=True,
            capture_output=True,
            timeout=20,
            check=True,
        )
        generated = cli_root / "seeds" / "basic-v1"
        self.assertEqual(Path(completed.stdout.strip()), generated)
        validate_basic_seed(generated)

        failed_root = Path(self.tmp.name) / "failed"
        failed_root.mkdir()
        failed_builder = mock.Mock(side_effect=RuntimeError("injected build failure"))
        with mock.patch(
            "tools.create_demo_seed._seed_support",
            return_value=(failed_builder, mock.Mock(), mock.Mock()),
        ):
            with self.assertRaises(RuntimeError):
                create_seed(failed_root, "basic-v1")
        self.assertEqual(list((failed_root / "seeds").iterdir()), [])

    def test_generator_rejects_relative_missing_and_symlinked_roots(self):
        with self.assertRaises(ValueError):
            create_seed(Path("relative-demo-root"), "basic-v1")
        with self.assertRaises(ValueError):
            create_seed(Path(self.tmp.name) / "missing", "basic-v1")
        target = Path(self.tmp.name) / "target"
        target.mkdir()
        link = Path(self.tmp.name) / "linked-root"
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError:
            self.skipTest("Symlink creation unavailable")
        with self.assertRaises(ValueError):
            create_seed(link, "basic-v1")

    def test_generated_seed_renders_real_demo_pages_without_mutating_seed(self):
        seed_hash = self._database_hash(self.seed)
        code = (
            "import json\n"
            "import app as target\n"
            "client=target.app.test_client()\n"
            "created=client.post('/demo/foundation/session')\n"
            "pages={p:client.get(p) for p in ('/','/reports','/projects','/settings/profiles')}\n"
            "print(json.dumps({'created':created.status_code,'codes':[r.status_code for r in pages.values()],"
            "'project':'Cosplay Helmet' in pages['/projects'].get_data(as_text=True),"
            "'profile':'Everyday PLA' in pages['/settings/profiles'].get_data(as_text=True)}))\n"
        )
        environment = os.environ.copy()
        environment.update({
            "KCD_RUNTIME_MODE": "demo",
            "KCD_DEMO_DATA_ROOT": str(self.root),
            "KCD_DEMO_SEED_NAME": "basic-v1",
            "KCD_DEMO_COOKIE_SECURE": "0",
            "KCD_STORAGE_BACKEND": "sql",
            "KCD_REPORTS_BACKEND": "sql",
        })
        environment["PYTHONPATH"] = os.pathsep.join(filter(None, (
            str(Path(__file__).resolve().parents[1]), environment.get("PYTHONPATH", ""),
        )))
        completed = subprocess.run(
            [sys.executable, "-c", code], cwd=self.tmp.name, env=environment,
            text=True, capture_output=True, timeout=20, check=True,
        )
        result = json.loads(completed.stdout.strip().splitlines()[-1])
        self.assertEqual(result, {
            "created": 201,
            "codes": [200, 200, 200, 200],
            "project": True,
            "profile": True,
        })
        self.assertEqual(self._database_hash(self.seed), seed_hash)


if __name__ == "__main__":
    unittest.main()
