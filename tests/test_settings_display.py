"""Display has its own route without widening demo or operator actions."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class SettingsDisplayTests(unittest.TestCase):
    def test_demo_display_route_has_a_narrow_explicit_policy(self):
        from core.demo.security import DemoAccessPolicy, DemoPolicyClass
        policy = DemoAccessPolicy()
        self.assertEqual(policy.classify('settings_display_page', 'GET').classification,
                         DemoPolicyClass.WORKSPACE_READ)
        for action in ('update_columns', 'update_measurement_display'):
            self.assertEqual(policy.classify('settings_display_page', 'POST', action).classification,
                             DemoPolicyClass.WORKSPACE_MUTATE)
        for action in ('', 'backup_now', 'update_application_mode', 'delete_printer',
                       'add_filament_profile', 'update_backup_settings'):
            self.assertEqual(policy.classify('settings_display_page', 'POST', action).classification,
                             DemoPolicyClass.BLOCKED_DEMO)

    def test_csv_and_dual_display_route_roundtrip_preserves_pricing_and_history(self):
        code = r'''
import csv
from pathlib import Path
from core import config, storage
settings={'Fixture':dict(rate_per_hour=0,filament_mode='per_meter',filament_rate=.25,grams_per_meter=3)}
storage.save_settings(config.SETTINGS_FILE,config.DATA_DIR,settings)
with open(config.CSV_FILE,'w',newline='',encoding='utf-8') as stream:
 writer=csv.DictWriter(stream,fieldnames=config.HEADERS)
 writer.writeheader()
 writer.writerow(dict(job_uid='unchanged',filename='preserve.gcode',printer='Fixture',status='completed',filament_meters=145.45,filament_mm=145450,grams_per_meter=3,filament_mode='per_meter',filament_rate=.25,rate_per_hour=0,time_cost=0,material_cost=36.3625,total_cost=36.3625))
import app
client=app.app.test_client()
response=client.get('/settings/display')
assert response.status_code==200,(response.status_code,response.data[:100])
assert b'name="filament_measurement"' in response.data
assert b'value="backup_now"' not in response.data
assert b'name="filament_measurement"' not in client.get('/settings/other').data
history=Path(config.CSV_FILE).read_bytes()
pricing=Path(config.SETTINGS_FILE).read_bytes()
for pref in ('length','weight','length'):
 result=client.post('/settings/display',data={'action':'update_measurement_display','filament_measurement':pref})
 assert result.status_code==302 and '/settings/display' in result.location
 assert storage.load_display_settings(config.DISPLAY_FILE,config.HEADERS)['filament_measurement']==pref
 assert Path(config.CSV_FILE).read_bytes()==history
 assert Path(config.SETTINGS_FILE).read_bytes()==pricing
legacy=client.post('/settings/other',data={'action':'update_measurement_display','filament_measurement':'weight'})
assert '/settings/display' in legacy.location
assert storage.load_display_settings(config.DISPLAY_FILE,config.HEADERS)['filament_measurement']=='weight'
assert Path(config.CSV_FILE).read_bytes()==history
assert Path(config.SETTINGS_FILE).read_bytes()==pricing
'''
        root = Path(__file__).resolve().parents[1]
        for backend in ('csv', 'dual'):
            with self.subTest(backend=backend), tempfile.TemporaryDirectory() as tmp:
                environment = {**os.environ, 'KCD_DATA_DIR': tmp, 'KCD_STORAGE_BACKEND': backend,
                               'KCD_SQL_ONLY_FAIL_FAST': '0', 'KCD_API_KEY': 'isolated-display-route'}
                result = subprocess.run([sys.executable, '-c', code], cwd=root, env=environment,
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
