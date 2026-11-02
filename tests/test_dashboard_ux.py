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

    def test_mismatched_loaded_identity_is_not_presented_as_this_printer(self):
        from core import monitoring
        with patch.object(monitoring,'_cache',{}),patch.object(monitoring,'probe',return_value=dict(state='printing',connected=True,reported_identity='Different')):
            item=monitoring.statuses({'Expected':'http://fixture'})['Expected']
        self.assertEqual(item['state'],'identity_mismatch')
        self.assertFalse(item['connected'])
        self.assertNotIn('reported_identity',item)

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

    def test_history_filters_do_not_zero_another_printers_today_card(self):
        from core import db
        from core.registration import register_printer
        import time
        import app
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'KCD_STORAGE_BACKEND':'sql','KCD_SQL_ONLY_FAIL_FAST':'0'}),patch.object(db,'_db_path',lambda:str(Path(tmp)/'kcd.db')):
            for name in ('Fixture','Other'):register_printer(name,'http://127.0.0.1:1')
            with db.connect_db() as conn:
                db.upsert_job(conn,dict(job_uid='today-other',printer='Other',filename='other-today.gcode',status='completed',timestamp=time.time()-10,time_cost=5,material_cost=0,total_cost=5))
                db.upsert_job(conn,dict(job_uid='older-other',printer='Other',filename='older.gcode',status='completed',timestamp=time.time()-86400*3,time_cost=9,material_cost=0,total_cost=9))
            response=app.app.test_client().get('/?printer=Fixture&range=yesterday')
            self.assertEqual(response.status_code,200)
            self.assertIn(b'$5.00',response.data)
            self.assertIn(b'other-today.gcode',response.data)

    def test_weight_aggregate_reports_missing_measurements_explicitly(self):
        from core.filament import summarize_weights
        result=summarize_weights([dict(filament_meters=1,grams_per_meter=3),dict(filament_meters=2)],{})
        self.assertEqual(float(result['known_grams']),3)
        self.assertEqual(result['unknown_weights'],1)

    def test_assigned_profiles_cannot_be_deleted_or_break_readiness(self):
        from core import db,profiles,rates
        from core.registration import register_printer
        from core.readiness import check_sql_only_readiness
        import app
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'KCD_STORAGE_BACKEND':'sql','KCD_SQL_ONLY_FAIL_FAST':'0'}),patch.object(db,'_db_path',lambda:str(Path(tmp)/'kcd.db')):
            register_printer('Fixture','http://127.0.0.1:1')
            profiles.upsert_profile(dict(id='material',name='Material',filament_mode='per_kg',filament_rate=15,grams_per_meter=3))
            rates.upsert_rate_profile(dict(id='hourly',name='Hourly',rate_per_hour=0))
            self.assertTrue(profiles.set_printer_mapping('Fixture','material'))
            self.assertTrue(rates.set_printer_rate_profile_sql('Fixture','hourly'))
            for action,key,value in [('delete_filament_profile','profile_id','material'),('delete_rate_profile','rate_profile_id','hourly')]:
                response=app.app.test_client().post('/settings/profiles',data={'action':action,key:value},follow_redirects=True)
                self.assertEqual(response.status_code,200)
                self.assertIn(b'Select replacement or base pricing',response.data)
                self.assertTrue(check_sql_only_readiness()['ready'])
            with db.connect_db() as conn:self.assertEqual(conn.execute("SELECT COUNT(*) FROM system_events WHERE category='deleted'").fetchone()[0],0)
            profiles.set_printer_mapping('Fixture',None);rates.set_printer_rate_profile_sql('Fixture',None)
            self.assertTrue(profiles.delete_profile('material'));self.assertTrue(rates.delete_rate_profile('hourly'))
            self.assertTrue(check_sql_only_readiness()['ready'])

    def test_numeric_profile_uid_does_not_delete_an_unrelated_row_id(self):
        from core import db,rates
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'KCD_STORAGE_BACKEND':'sql'}),patch.object(db,'_db_path',lambda:str(Path(tmp)/'kcd.db')):
            rates.upsert_rate_profile(dict(id='first',name='First',rate_per_hour=1))
            rates.upsert_rate_profile(dict(id='1',name='Numeric stable UID',rate_per_hour=0))
            self.assertTrue(rates.delete_rate_profile('1'))
            self.assertIsNotNone(rates.get_rate_profile('first'))
            self.assertIsNone(rates.get_rate_profile('1'))

    def test_thumbnail_timeouts_are_shared_per_endpoint(self):
        from core import thumbnail_archive as archive,thumbnails
        with patch.object(archive,'_source_failures',{}),patch.object(thumbnails,'resolve_moonraker_base_url',return_value='http://offline-fixture'),patch('urllib.request.urlopen',side_effect=TimeoutError) as network,patch.object(archive.time,'monotonic',return_value=0) as clock:
            for filename in ('one.gcode','two.gcode','three.gcode'):
                self.assertEqual(archive.fetch_image('Fixture',filename)[1],'network_error')
            self.assertEqual(network.call_count,1)
            clock.return_value=31
            archive.fetch_image('Fixture','retry.gcode')
            self.assertEqual(network.call_count,2)

    def test_slow_thumbnail_recovery_does_not_consume_all_server_workers(self):
        from concurrent.futures import ThreadPoolExecutor
        import threading
        from core import db
        from core.registration import register_printer
        import app
        entered=threading.Barrier(3);release=threading.Event()
        def slow(*args,**kwargs):
            entered.wait(timeout=10);release.wait(timeout=10)
            return dict(asset=None,status='network_error')
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'KCD_STORAGE_BACKEND':'sql','KCD_SQL_ONLY_FAIL_FAST':'0'}),patch.object(db,'_db_path',lambda:str(Path(tmp)/'kcd.db')):
            register_printer('Fixture','http://127.0.0.1:1')
            with db.connect_db() as conn:
                for i in range(3):db.upsert_job(conn,dict(job_uid='slow-'+str(i),printer='Fixture',filename='part.gcode',status='completed',time_cost=0,material_cost=0,total_cost=0))
            with patch.object(app,'_thumbnail_recovery_slots',threading.BoundedSemaphore(2)),patch.object(app.archived_thumbs,'capture_job',side_effect=slow) as capture,ThreadPoolExecutor(max_workers=2) as pool:
                futures=[pool.submit(lambda uid:app.app.test_client().get('/jobs/'+uid+'/thumbnail'),'slow-'+str(i)) for i in range(2)]
                try:
                    entered.wait(timeout=10)
                    response=app.app.test_client().get('/jobs/slow-2/thumbnail')
                    self.assertEqual(response.headers['X-KCD-Thumbnail-Status'],'recovery_deferred')
                    self.assertEqual(app.app.test_client().get('/health').status_code,200)
                    self.assertEqual(app.app.test_client().head('/jobs/slow-2/thumbnail').status_code,404)
                    self.assertEqual(capture.call_count,2)
                finally:release.set()
                for future in futures:self.assertEqual(future.result().status_code,404)
