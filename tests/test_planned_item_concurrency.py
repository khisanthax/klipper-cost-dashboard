"""SQL plan edits and promotion must preserve concurrent, unrelated work."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SETUP = """
from contextlib import closing
from core import db, projects
from core.registration import register_printer
register_printer('Fixture','http://127.0.0.1:1')
project=projects.create_project('Concurrent estimates')
"""


class PlannedItemConcurrencyTests(unittest.TestCase):
    def fixture(self, source):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(
                [sys.executable, "-c", SETUP + source], check=True, timeout=45,
                cwd=Path(__file__).resolve().parents[1],
                env={**os.environ, "KCD_DATA_DIR": tmp, "KCD_STORAGE_BACKEND": "sql",
                     "KCD_API_KEY": "planned-concurrency-fixture"},
            )

    def test_edit_overlapping_reviewed_create_preserves_both_plans(self):
        self.fixture("""
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from threading import Event, current_thread
from unittest.mock import patch
existing=projects.create_plan_item(project.id,'Existing',60,1,est_cost_override=.25)
loaded,release=Event(),Event()
parse=projects.finite_float
def pause_after_read(value,**kwargs):
    if current_thread().name.startswith('editor') and kwargs.get('label')=='Estimated time':
        loaded.set()
        assert release.wait(10)
    return parse(value,**kwargs)
with patch.object(projects,'finite_float',pause_after_read):
    with ThreadPoolExecutor(1,thread_name_prefix='editor') as editor, ThreadPoolExecutor(1) as creator:
        edit=editor.submit(projects.update_plan_item,existing.plan_id,est_time_s=120,est_cost=.25)
        assert loaded.wait(10)
        create=creator.submit(projects.create_plan_item,project.id,'Reviewed',60,1,
                              est_cost_override=.1,plan_id='reviewed')
        try:
            create.result(timeout=.25)
        except TimeoutError:
            pass
        finally:
            release.set()
        edit.result()
        create.result()
items={item.plan_id:item for item in projects.load_plans()[project.id]}
assert set(items)=={existing.plan_id,'reviewed'},items
assert items[existing.plan_id].est_time_s==120
assert items['reviewed'].est_cost==.1
""")

    def test_manual_edit_overlapping_conversion_preserves_promoted_work(self):
        self.fixture("""
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from threading import Event, current_thread
from unittest.mock import patch
existing=projects.create_manual_job(project.id,'Existing work',1)
plan=projects.create_plan_item(project.id,'Promote',60,1,est_cost_override=.25)
loaded,release=Event(),Event()
clock=projects._iso_now
def pause_after_read():
    if current_thread().name.startswith('editor'):
        loaded.set()
        assert release.wait(10)
    return clock()
with patch.object(projects,'_iso_now',pause_after_read):
    with ThreadPoolExecutor(1,thread_name_prefix='editor') as editor, ThreadPoolExecutor(1) as converter:
        edit=editor.submit(projects.update_manual_job,existing.manual_job_id,'Edited work',2)
        assert loaded.wait(10)
        convert=converter.submit(projects.convert_plan_item_to_manual,project.id,plan.plan_id)
        try:
            convert.result(timeout=.25)
        except TimeoutError:
            pass
        finally:
            release.set()
        edit.result()
        promoted=convert.result()
items={item.manual_job_id:item for item in projects.load_manual_jobs()[project.id]}
assert set(items)=={existing.manual_job_id,promoted.manual_job_id},items
assert items[existing.manual_job_id].hours==2
saved=projects.load_plans()[project.id][0]
assert saved.converted_to_manual_job_id==promoted.manual_job_id and saved.status=='fulfilled'
try:
    projects.convert_plan_item_to_manual(project.id,plan.plan_id)
except ValueError:
    pass
else:
    raise AssertionError('Conversion retry must not create another manual job')
assert len(projects.load_manual_jobs()[project.id])==2
""")

    def test_targeted_mutations_preserve_unrelated_rows_and_history(self):
        self.fixture("""
plan=projects.create_plan_item(project.id,'Target',60,1)
other=projects.create_plan_item(project.id,'Unrelated',120,2,notes='Retained')
manual=projects.create_manual_job(project.id,'Target manual',1)
other_manual=projects.create_manual_job(project.id,'Unrelated manual',2,notes='Retained')
with closing(db.connect_db()) as conn,conn:
    db.upsert_job(conn,{'job_uid':'historical','printer':'Fixture','filename':'history.gcode','total_cost':5.25})
    plan_before=dict(conn.execute('SELECT * FROM planned_items WHERE plan_id=?',(other.plan_id,)).fetchone())
    manual_before=dict(conn.execute('SELECT * FROM manual_jobs WHERE manual_job_id=?',(other_manual.manual_job_id,)).fetchone())
    history_before=dict(conn.execute('SELECT * FROM jobs WHERE job_uid=?',('historical',)).fetchone())
projects.update_plan_item(plan.plan_id,filename='Updated',est_cost=0)
projects.set_plan_status(plan.plan_id,'fulfilled')
projects._set_plan_converted(plan.plan_id,manual.manual_job_id)
projects.update_manual_job(manual.manual_job_id,'Updated manual',2,cost_override=0)
projects.delete_plan_item(plan.plan_id)
projects.delete_manual_job(manual.manual_job_id)
with closing(db.connect_db()) as conn:
    assert dict(conn.execute('SELECT * FROM planned_items WHERE plan_id=?',(other.plan_id,)).fetchone())==plan_before
    assert dict(conn.execute('SELECT * FROM manual_jobs WHERE manual_job_id=?',(other_manual.manual_job_id,)).fetchone())==manual_before
    assert dict(conn.execute('SELECT * FROM jobs WHERE job_uid=?',('historical',)).fetchone())==history_before
""")

    def test_failed_conversion_rolls_back_and_concurrent_retries_create_one_job(self):
        self.fixture("""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import sqlite3
plan=projects.create_plan_item(project.id,'Reviewed',60,1,notes='Reviewed evidence',est_cost_override=.25)
with closing(db.connect_db()) as conn,conn:
    conn.execute("CREATE TRIGGER reject_conversion BEFORE UPDATE ON planned_items BEGIN SELECT RAISE(ABORT,'fixture failure'); END")
try:
    projects.convert_plan_item_to_manual(project.id,plan.plan_id)
except sqlite3.DatabaseError:
    pass
else:
    raise AssertionError('Expected fixture failure')
with closing(db.connect_db()) as conn,conn:
    assert conn.execute('SELECT count(*) FROM manual_jobs').fetchone()[0]==0
    saved=conn.execute('SELECT * FROM planned_items').fetchone()
    assert saved['status']=='active' and saved['converted_to_manual_job_id'] is None
    conn.execute('DROP TRIGGER reject_conversion')
start=Barrier(4)
def convert(_):
    start.wait(10)
    try:
        return projects.convert_plan_item_to_manual(project.id,plan.plan_id).manual_job_id
    except ValueError:
        return None
with ThreadPoolExecutor(4) as workers:
    outcomes=list(workers.map(convert,range(4)))
assert len([value for value in outcomes if value])==1,outcomes
with closing(db.connect_db()) as conn:
    assert conn.execute('SELECT count(*) FROM manual_jobs').fetchone()[0]==1
    saved=conn.execute('SELECT * FROM planned_items').fetchone()
    assert saved['converted_to_manual_job_id']==next(value for value in outcomes if value)
    assert saved['notes']=='Reviewed evidence' and saved['est_cost']==.25
""")

    def test_sql_cleanup_does_not_delete_data_newer_than_project_snapshot(self):
        self.fixture("""
from unittest.mock import patch
plan=projects.create_plan_item(project.id,'Newer plan',60,1)
manual=projects.create_manual_job(project.id,'Newer manual work',1)
with closing(db.connect_db()) as conn,conn:
    db.upsert_job(conn,{'job_uid':'historical','printer':'Fixture','filename':'history.gcode','total_cost':5.25})
    project_id=conn.execute('SELECT id FROM projects').fetchone()[0]
    conn.execute("INSERT INTO project_assignments VALUES (?, 'historical', 'now')",(project_id,))
# A project committed after the first read is valid SQL state, not an orphan.
with patch.object(projects,'load_projects',return_value={}):
    projects.recalculate_all()
assert projects.load_plans()[project.id][0].plan_id==plan.plan_id
assert projects.load_manual_jobs()[project.id][0].manual_job_id==manual.manual_job_id
assert projects.load_assignments()['historical']==project.id
""")

    def test_unknown_plan_weight_cannot_become_zero_manual_consumption(self):
        self.fixture("""
plan=projects.create_plan_item(project.id,'Unknown weight',60,None)
try:
    projects.convert_plan_item_to_manual(project.id,plan.plan_id)
except ValueError as exc:
    assert 'reviewed filament grams' in str(exc)
else:
    raise AssertionError('Unknown weight must not become zero consumption')
with closing(db.connect_db()) as conn:
    assert conn.execute('SELECT count(*) FROM manual_jobs').fetchone()[0]==0
    assert conn.execute('SELECT est_filament_g FROM planned_items').fetchone()[0] is None
projects.update_plan_item(plan.plan_id,est_filament_g=0)
assert projects.convert_plan_item_to_manual(project.id,plan.plan_id).filament_g==0
""")


if __name__ == "__main__":
    unittest.main()
