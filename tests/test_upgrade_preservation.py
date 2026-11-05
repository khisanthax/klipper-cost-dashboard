"""Upgrade rehearsal rejects business-data loss and canonicalizes composite keys."""
from copy import deepcopy
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from tools.dashboard_docker_upgrade import SNAPSHOT, assert_preserved, assert_rollback_preserved


class UpgradePreservationTests(unittest.TestCase):
    def setUp(self):
        self.scratch=tempfile.TemporaryDirectory();self.addCleanup(self.scratch.cleanup)
        self.database=Path(self.scratch.name)/'kcd.db'
        self.objects=Path(self.scratch.name)/'objects';self.objects.mkdir()
        (self.objects/'fixture.png').write_bytes(b'original archived image')
        with closing(sqlite3.connect(self.database)) as conn, conn:
            conn.executescript('''CREATE TABLE projects (id INTEGER PRIMARY KEY,name TEXT);
                INSERT INTO projects VALUES (1,'Preserved project');
                CREATE TABLE project_assignments (project_id INTEGER,job_uid TEXT,PRIMARY KEY(project_id,job_uid));
                INSERT INTO project_assignments VALUES (1,'b'),(1,'a');
                CREATE TABLE schema_migrations (version TEXT PRIMARY KEY,applied_at TEXT,checksum TEXT);
                INSERT INTO schema_migrations VALUES ('0008','then','stable');''')

    def snapshot(self):
        result=subprocess.run([sys.executable,'-c',SNAPSHOT,str(self.database),str(self.objects)],
                              check=True,capture_output=True,text=True,timeout=10)
        return json.loads(result.stdout)

    def test_baseline_project_loss_and_migration_rewrite_cannot_pass(self):
        before=self.snapshot()
        with closing(sqlite3.connect(self.database)) as conn, conn:
            conn.execute("INSERT INTO schema_migrations VALUES ('0009','now','new')")
        assert_preserved(before,self.snapshot())
        with closing(sqlite3.connect(self.database)) as conn, conn:
            conn.execute("UPDATE projects SET name='Damaged' WHERE id=1")
        with self.assertRaisesRegex(AssertionError,'projects'):
            assert_preserved(before,self.snapshot())
        changed=deepcopy(before)
        changed['tables']['schema_migrations']['rows'][0][2]='rewritten'
        with self.assertRaisesRegex(AssertionError,'migration'):
            assert_preserved(before,changed)

    def test_composite_row_order_is_canonical_and_nonempty_new_rows_survive_rollback(self):
        before=self.snapshot()
        with closing(sqlite3.connect(self.database)) as conn, conn:
            conn.executescript("DELETE FROM project_assignments; INSERT INTO project_assignments VALUES (1,'a'),(1,'b');")
        self.assertEqual(self.snapshot(),before)
        with closing(sqlite3.connect(self.database)) as conn, conn:
            conn.executescript("CREATE TABLE business_orders (project_id INTEGER PRIMARY KEY,revenue TEXT); INSERT INTO business_orders VALUES (1,'8.00');")
        upgraded=self.snapshot()
        assert_preserved(before,upgraded)
        assert_rollback_preserved(upgraded,self.snapshot())
        with closing(sqlite3.connect(self.database)) as conn, conn:
            conn.execute('DELETE FROM business_orders')
        with self.assertRaisesRegex(AssertionError,'business_orders'):
            assert_rollback_preserved(upgraded,self.snapshot())

    def test_archived_object_changes_cannot_pass(self):
        before=self.snapshot()
        (self.objects/'fixture.png').write_bytes(b'damaged archived image')
        with self.assertRaisesRegex(AssertionError,'thumbnail'):
            assert_preserved(before,self.snapshot())

    def test_baseline_index_loss_cannot_pass(self):
        with closing(sqlite3.connect(self.database)) as conn, conn:
            conn.execute('CREATE INDEX projects_name ON projects(name)')
        before=self.snapshot()
        with closing(sqlite3.connect(self.database)) as conn, conn:
            conn.execute('DROP INDEX projects_name')
        with self.assertRaisesRegex(AssertionError,'schema'):
            assert_preserved(before,self.snapshot())
