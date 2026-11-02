import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


class DashboardUXTests(unittest.TestCase):
    def test_summary_supplies_chart_groups_and_calendar_span(self):
        from core.reports import compute_summary
        result=compute_summary([
            dict(printer='One',timestamp='2026-11-01 12:00:00',duration_hours=1,filament_meters=2,total_cost=3),
            dict(printer='One',timestamp='2026-11-03 12:00:00',duration_hours=2,filament_meters=3,total_cost=0)])
        self.assertEqual(result['per_printer']['One'],dict(hours=3,cost=3,count=2))
        self.assertEqual(result['per_day']['2026-11-03']['cost'],0)
        self.assertEqual(result['days_span'],3)
        self.assertEqual(compute_summary([])['days_span'],0)

    def test_invalid_historical_weight_is_unknown_without_repricing(self):
        from core.filament import describe_job
        row=dict(filament_meters=3,grams_per_meter='broken',total_cost=0)
        describe_job(row)
        self.assertIsNone(row['_weight_grams'])
        self.assertIn('Invalid recorded measurement',row['_weight_source'])
        self.assertEqual(row['total_cost'],0)

    def test_project_profile_snapshots_explicit_spool_units(self):
        from core import profiles
        from core.filament import project_price_from_form
        profile=dict(name='PETG half spool',price_input_mode='spool',spool_price=9,net_spool_grams=500,filament_mode='per_kg',filament_rate=18,grams_per_meter=3)
        with patch.object(profiles,'get_profile',return_value=profile):
            kg,spec=project_price_from_form({'project_filament_profile_id':'half','pricing_amount':'999'})
        self.assertEqual(kg,18)
        self.assertEqual(spec['profile_id'],'half')
        self.assertEqual(float(spec['net_spool_grams']),500)
        with patch.object(profiles,'get_profile',return_value=None):
            with self.assertRaisesRegex(ValueError,'no longer exists'):
                project_price_from_form({'project_filament_profile_id':'deleted'})

    def test_status_cache_uses_endpoint_and_returns_detached_observations(self):
        from core import monitoring
        with patch.object(monitoring,'_cache',{}),patch.object(monitoring,'probe',return_value=dict(state='printing',connected=True)) as probe:
            result=monitoring.statuses({'One':'http://fixture:1'})
            result['One']['state']='changed by caller'
            self.assertEqual(monitoring.statuses({'One':'http://fixture:1'})['One']['state'],'printing')
            self.assertEqual(probe.call_count,1)
            monitoring.statuses({'One':'http://fixture:2'})
            self.assertEqual(probe.call_count,2)

    def test_failed_telemetry_is_unverified_not_idle(self):
        from core.monitoring import probe
        with patch('urllib.request.urlopen',side_effect=TimeoutError):
            result=probe('http://fixture')
        self.assertFalse(result['connected'])
        self.assertEqual(result['state'],'disconnected')

    def test_bulk_failure_message_survives_history_read(self):
        from core import db
        from core.registration import register_printer
        import app
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'KCD_STORAGE_BACKEND':'sql','KCD_SQL_ONLY_FAIL_FAST':'0'}),patch.object(db,'_db_path',lambda:str(Path(tmp)/'kcd.db')):
            register_printer('Fixture','http://127.0.0.1:1')
            with patch.object(app,'_delete_jobs_sql',side_effect=RuntimeError('fixture write failure')):
                response=app.app.test_client().post('/',data={'action':'delete_rows','selection_kind':'job_uid','delete_rows':'fixture'},follow_redirects=True)
            self.assertEqual(response.status_code,200)
            self.assertIn(b'Failed to delete jobs: fixture write failure',response.data)

    def test_compatibility_import_calls_the_supported_signature(self):
        from core import db
        from core.registration import register_printer
        import app
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'KCD_STORAGE_BACKEND':'sql','KCD_SQL_ONLY_FAIL_FAST':'0'}),patch.object(db,'_db_path',lambda:str(Path(tmp)/'kcd.db')):
            register_printer('Fixture','http://127.0.0.1:1')
            with patch.object(app,'_is_sql_only',return_value=False),patch.object(app,'import_moonraker_history_to_csv',autospec=True,return_value={'imported':0,'errors':0}) as importer:
                response=app.app.test_client().post('/settings/printers',data={'action':'import_moonraker_history','printer':'Fixture'})
            self.assertEqual(response.status_code,302)
            importer.assert_called_once()
            self.assertNotIn('history_source',importer.call_args.kwargs)

    def test_project_catalog_reads_do_not_grow_per_tracked_job(self):
        from core import db,projects,profiles
        from core.registration import register_printer
        import app
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'KCD_STORAGE_BACKEND':'sql','KCD_SQL_ONLY_FAIL_FAST':'0'}),patch.object(db,'_db_path',lambda:str(Path(tmp)/'kcd.db')):
            register_printer('Fixture','http://127.0.0.1:1')
            profiles.upsert_profile(dict(id='fixture',name='Fixture spool',filament_mode='per_kg',filament_rate=15,grams_per_meter=3))
            project=projects.create_project('Many tracked jobs')
            with db.connect_db() as conn:
                for i in range(20):db.upsert_job(conn,dict(job_uid='catalog-'+str(i),printer='Fixture',filename='part.gcode',status='completed',filament_mm=1000,filament_meters=1,grams_per_meter=3,filament_profile_id='fixture',time_cost=0,material_cost=0,total_cost=0))
                conn.execute("UPDATE jobs SET grams_per_meter='invalid legacy measurement' WHERE job_uid='catalog-0'")
            projects.assign_jobs(['catalog-'+str(i) for i in range(20)],project.id)
            with patch.object(profiles,'get_all_profiles',wraps=profiles.get_all_profiles) as catalog:
                response=app.app.test_client().get('/projects')
            self.assertEqual(response.status_code,200)
            self.assertIn(b'Weight unknown',response.data)
            self.assertEqual(catalog.call_count,1)
