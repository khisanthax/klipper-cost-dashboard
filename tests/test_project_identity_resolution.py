"""Canonical project identities take precedence over display-name aliases."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class ProjectIdentityResolutionTests(unittest.TestCase):
    def test_plan_and_manual_creation_cannot_follow_a_colliding_display_name(self):
        source = """
from contextlib import closing
from core import db, projects
with closing(db.connect_db()) as conn,conn:
    db.apply_migrations(conn)
    conn.executemany(
        "INSERT INTO projects (id,project_uid,name,created_at,updated_at) VALUES (?,?,?,'now','now')",
        [(1,'earlier','canonical-target'),(2,'canonical-target','Target display name')],
    )
plan=projects.create_plan_item('canonical-target','Target plan',60,1)
manual=projects.create_manual_job('canonical-target','Target manual',1)
with closing(db.connect_db()) as conn:
    assert conn.execute('SELECT project_id FROM planned_items WHERE plan_id=?',(plan.plan_id,)).fetchone()[0]==2
    assert conn.execute('SELECT project_id FROM manual_jobs WHERE manual_job_id=?',(manual.manual_job_id,)).fetchone()[0]==2
    assert projects._resolve_project_db_id(conn,'Target display name')==2
"""
        self.fixture(source)

    def test_explicit_uid_precedes_legacy_numeric_alias_without_losing_legacy_support(self):
        source = """
from contextlib import closing
from core import db, projects
with closing(db.connect_db()) as conn,conn:
    db.apply_migrations(conn)
    conn.executemany(
        "INSERT INTO projects (id,project_uid,name,created_at,updated_at) VALUES (?,?,?,'now','now')",
        [(1,'','Legacy one'),(2,'1','Explicit numeric UID'),(3,None,'Legacy three')],
    )
plan=projects.create_plan_item('1','Explicit target',60,1)
manual=projects.create_manual_job('1','Explicit target',1)
legacy=projects.create_plan_item('3','Legacy target',60,1)
with closing(db.connect_db()) as conn:
    assert conn.execute('SELECT project_id FROM planned_items WHERE plan_id=?',(plan.plan_id,)).fetchone()[0]==2
    assert conn.execute('SELECT project_id FROM manual_jobs WHERE manual_job_id=?',(manual.manual_job_id,)).fetchone()[0]==2
    assert conn.execute('SELECT project_id FROM planned_items WHERE plan_id=?',(legacy.plan_id,)).fetchone()[0]==3
    assert projects._resolve_project_db_id(conn,'missing') is None
"""
        self.fixture(source)

    def fixture(self, source):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(
                [sys.executable, "-c", source], check=True, timeout=45,
                cwd=Path(__file__).resolve().parents[1],
                env={**os.environ, "KCD_DATA_DIR": tmp, "KCD_STORAGE_BACKEND": "sql",
                     "KCD_API_KEY": "project-identity-fixture"},
            )


if __name__ == "__main__":
    unittest.main()
