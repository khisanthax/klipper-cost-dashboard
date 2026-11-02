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
