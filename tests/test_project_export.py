import sqlite3
import json
import os
from pathlib import Path
import tempfile
import subprocess
import sys
import unittest
from contextlib import closing

from core import db
from core.project_export import project_document


class ProjectExportTests(unittest.TestCase):
    def test_snapshot_preserves_unknown_and_separates_cost_sources(self):
        with closing(sqlite3.connect(":memory:")) as connection:
            connection.row_factory = sqlite3.Row
            db.apply_migrations(connection)
            connection.execute("INSERT INTO projects (id,project_uid,name,created_at,updated_at) VALUES (1,'a','A','now','now')")
            connection.execute("INSERT INTO projects (id,project_uid,name,created_at,updated_at) VALUES (2,'b','B','now','now')")
            db.upsert_job(connection, {"job_uid": "unknown", "printer": "Fixture", "filename": "unpriced.gcode",
                                       "status": "canceled", "filament_grams": None})
            db.upsert_job(connection, {"job_uid": "other", "printer": "Fixture", "filename": "elsewhere.gcode",
                                       "status": "completed", "total_cost": 8.25})
            connection.execute("INSERT INTO project_assignments VALUES (1,'unknown','now')")
            connection.execute("INSERT INTO project_assignments VALUES (2,'other','now')")
            connection.execute("INSERT INTO planned_items (plan_id,project_id,filename,est_time_s,est_cost,created_at,updated_at) VALUES ('p',1,'planned.gcode',200,4.5,'now','now')")
            connection.execute("INSERT INTO manual_jobs (manual_job_id,project_id,title,hours,cost_override,created_at,updated_at) VALUES ('m',1,'Assembly',2,5,'now','now')")
            connection.execute("INSERT INTO project_job_pricing VALUES (1,'unknown',?,'fixture','now')", ('{"total_cost": 2.5}',))
            connection.execute("UPDATE jobs SET override_rate_per_hour=0,override_material_cost=-0.125,override_total_cost=0.12345678912345 WHERE job_uid='unknown'")
            connection.execute("INSERT INTO job_cost_originals VALUES ('unknown',?,'now')", (
                json.dumps({"total_cost": -0.12345678912345, "filament_mode": "per_meter",
                            "filament_rate": 0.25, "private_extra": "excluded"}),))
            writes = connection.total_changes
            result = project_document(connection, "a")
            self.assertEqual([row["job_uid"] for row in result["recorded_jobs"]], ["unknown"])
            self.assertIsNone(result["recorded_jobs"][0]["total_cost"])
            self.assertIsNone(result["recorded_jobs"][0]["filament_grams"])
            self.assertEqual(result["manual_work"][0]["cost_override"], 5)
            self.assertEqual(result["planned_items"][0]["est_cost"], 4.5)
            self.assertEqual(result["project_price_snapshots"][0]["calculation"]["total_cost"], 2.5)
            self.assertEqual(result["recorded_jobs"][0]["override_rate_per_hour"], 0)
            self.assertEqual(result["recorded_jobs"][0]["override_material_cost"], -0.125)
            self.assertEqual(json.loads(json.dumps(result))["recorded_jobs"][0]["override_total_cost"], 0.12345678912345)
            self.assertEqual(result["original_prices"][0]["pricing"]["total_cost"], -0.12345678912345)
            self.assertNotIn("private_extra", result["original_prices"][0]["pricing"])
            self.assertIsNone(project_document(connection, "absent"))
            self.assertEqual(connection.total_changes, writes)

    def test_export_uses_one_snapshot_during_concurrent_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "snapshot.db"
            with closing(sqlite3.connect(path)) as writer, closing(sqlite3.connect(path)) as reader:
                writer.row_factory = reader.row_factory = sqlite3.Row
                writer.execute("PRAGMA journal_mode=WAL")
                db.apply_migrations(writer)
                writer.execute("INSERT INTO projects (id,project_uid,name,created_at,updated_at) VALUES (1,'','Before','now','now')")
                db.upsert_job(writer, {"job_uid": "job", "printer": "Fixture", "filename": "before.gcode", "total_cost": 1.25})
                writer.execute("INSERT INTO project_assignments VALUES (1,'job','now')")
                writer.commit()
                changed = []
                def change_after_first_select(sql):
                    if "FROM project_assignments a JOIN jobs" in sql and not changed:
                        writer.execute("UPDATE projects SET name='After'")
                        writer.execute("UPDATE jobs SET total_cost=9.5")
                        writer.commit()
                        changed.append(True)
                reader.set_trace_callback(change_after_first_select)
                reader.execute("BEGIN")
                result = project_document(reader, "1")
                self.assertEqual(result["project"]["name"], "Before")
                self.assertEqual(result["recorded_jobs"][0]["total_cost"], 1.25)
                self.assertEqual(writer.execute("SELECT total_cost FROM jobs").fetchone()[0], 9.5)

    def test_http_download_and_project_exclusion(self):
        source = """
from core.registration import register_printer
from core import projects
register_printer('Fixture','http://127.0.0.1:1')
project=projects.create_project('Download')
projects.create_project('Private other project',notes='Unrelated private note')
import app
mode_context={'show_advanced':True}
app.app.context_processor(lambda:mode_context)
client=app.app.test_client()
response=client.get('/projects/'+project.id+'/export.json')
assert response.status_code==200
assert response.headers['Content-Disposition'].startswith('attachment;')
assert response.headers['Cache-Control']=='no-store'
assert 'Unrelated private note' not in response.get_data(as_text=True)
assert client.get('/projects/absent/export.json').status_code==404
assert 'Download project JSON' in client.get('/projects').get_data(as_text=True)
mode_context['show_advanced']=False
assert 'Download project JSON' not in client.get('/projects').get_data(as_text=True)
assert client.get('/projects/'+project.id+'/export.json').status_code==200
"""
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(
                [sys.executable, "-c", source], check=True, timeout=90,
                cwd=Path(__file__).resolve().parents[1],
                env={**os.environ, "KCD_DATA_DIR": tmp, "KCD_STORAGE_BACKEND": "sql", "KCD_API_KEY": "export-fixture"},
            )
