import sqlite3
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
            writes = connection.total_changes
            result = project_document(connection, "a")
            self.assertEqual([row["job_uid"] for row in result["recorded_jobs"]], ["unknown"])
            self.assertIsNone(result["recorded_jobs"][0]["total_cost"])
            self.assertIsNone(result["recorded_jobs"][0]["filament_grams"])
            self.assertEqual(result["manual_work"][0]["cost_override"], 5)
            self.assertEqual(result["planned_items"][0]["est_cost"], 4.5)
            self.assertEqual(result["project_price_snapshots"][0]["calculation"]["total_cost"], 2.5)
            self.assertIsNone(project_document(connection, "absent"))
            self.assertEqual(connection.total_changes, writes)
