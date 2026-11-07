"""Consumption display never changes pricing or manufactures missing evidence."""
import copy
from decimal import Decimal
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from core.measurement_display import filament_display, filament_total, project_item_measurement


class MeasurementDisplayTests(unittest.TestCase):
    def test_units_rounding_zero_and_unknown(self):
        row = dict(filament_meters=145.45, grams_per_meter=3)
        original = copy.deepcopy(row)
        self.assertEqual(filament_display(row)["text"], "436.35 g")
        self.assertEqual(filament_display(row, preference="length")["text"], "145.45 m")
        self.assertEqual(filament_display(grams=2505.06)["text"], "2.51 kg")
        self.assertEqual(filament_display(grams=Decimal("1.005"))["text"], "1.01 g")
        self.assertEqual(filament_display(grams=0)["text"], "0.00 g")
        self.assertEqual(filament_display(meters=0)["text"], "0.00 g")
        self.assertEqual(filament_display(meters=10)["text"], "Unknown weight")
        self.assertEqual(filament_display(grams=10, preference="length")["text"], "Unknown length")
        self.assertEqual(row, original)
        for value in (None, "", -1, "NaN", "Infinity", True, "1e100000"):
            self.assertTrue(filament_display(grams=value)["unknown"], value)

    def test_material_geometry_and_saved_estimate_have_explicit_basis(self):
        petg = filament_display(dict(filament_meters=100, filament_density=1.25, filament_diameter=1.75))
        pla = filament_display(dict(filament_meters=100, filament_density=1.24, filament_diameter=1.75))
        self.assertGreater(petg["grams"], pla["grams"])
        self.assertIn("density", petg["source"])
        stored = dict(filament_meters=100, filament_grams=300, filament_weight_kind="estimated", grams_per_meter=4)
        self.assertEqual(filament_display(stored)["text"], "300.00 g")
        from core.filament import describe_job
        self.assertEqual(describe_job(dict(stored), {"grams_per_meter": 5})["_weight_grams"], 300)

    def test_mixed_subtotals_do_not_invent_one_conversion(self):
        total = filament_total([
            dict(filament_meters=10, grams_per_meter=3),
            dict(filament_meters=10, grams_per_meter=4),
            dict(filament_grams=5), dict(filament_meters=2), {},
        ])
        self.assertEqual(total["grams"], 75)
        self.assertEqual(total["meters"], 22)
        self.assertEqual(total["unknown_weights"], 2)
        self.assertEqual(total["unknown_lengths"], 2)
        self.assertEqual(filament_total([{}])["text"], "Unknown weight")
        self.assertEqual(filament_total([])["text"], "0.00 g")

    def test_inventory_and_plans_require_associated_conversion(self):
        self.assertEqual(filament_display(grams=900, factor=3, preference="length")["text"], "300.00 m")
        self.assertEqual(filament_display(grams=900, factor=0, preference="length")["text"], "Unknown length")
        item = project_item_measurement(300, '{"estimate":{"grams_per_meter":3}}', 4, estimated=True)
        self.assertEqual(filament_display(item, preference="length")["text"], "100.00 m")
        self.assertIn("Reviewed", item["conversion_source"])
        self.assertTrue(filament_display(project_item_measurement(300), preference="length")["unknown"])

    def test_csv_and_dual_preference_roundtrip_and_legacy_defaults(self):
        from core.storage import load_display_settings, save_display_settings
        from core.config import HEADERS
        with tempfile.TemporaryDirectory() as tmp:
            for backend in ('csv', 'dual'):
                display = str(Path(tmp)/('display-'+backend+'.json'))
                with patch.dict(os.environ, {'KCD_STORAGE_BACKEND':backend}):
                    self.assertEqual(load_display_settings(display, HEADERS)['filament_measurement'],'weight')
                    for pref in ('length','weight','length'):
                        save_display_settings(display,tmp,{'filament_measurement':pref})
                        self.assertEqual(load_display_settings(display,HEADERS)['filament_measurement'],pref)
                    save_display_settings(display,tmp,{'projects_show_cost_totals':False})
                    self.assertEqual(load_display_settings(display,HEADERS)['filament_measurement'],'length')

    def test_fresh_defaults_have_one_primary_but_preserve_explicit_raw_columns(self):
        from core.storage import _default_display_settings, _normalize_display_settings
        from core.config import HEADERS
        defaults = _default_display_settings(HEADERS)['visible_columns']
        self.assertIn('filament_meters', defaults)
        for raw in ('filament_mm','filament_grams','grams_per_meter','filament_weight_kind','filament_density','filament_diameter'):
            self.assertNotIn(raw, defaults)
        explicit = ['filament_mm','filament_meters','grams_per_meter']
        self.assertEqual(_normalize_display_settings({'visible_columns':explicit},HEADERS)['visible_columns'],explicit)

    def test_sql_preference_restart_backup_and_history_preservation(self):
        script = r'''
import json, sqlite3, tarfile
from contextlib import closing
from pathlib import Path
from core import db, storage, config, backup, application_mode
from core.registration import register_printer
register_printer('Fixture','http://127.0.0.1:1')
storage.save_display_settings(config.DISPLAY_FILE,config.DATA_DIR,dict(pause_include_paused_time_default=False))
import app
with closing(db.connect_db()) as conn, conn:
 db.upsert_job(conn, dict(job_uid='preserve',printer='Fixture',filename='part.gcode',status='completed',timestamp=1800000000,duration_seconds=60,filament_meters=145.45,filament_mm=145450,grams_per_meter=3,rate_per_hour=0,filament_mode='per_kg',filament_rate=15,time_cost=0,material_cost=6.54525,total_cost=6.54525))
def history():
 with closing(db.connect_db()) as conn:
  return tuple(tuple(r) for r in conn.execute('SELECT * FROM jobs'))
original=history()
client=app.app.test_client()
assert storage.load_display_settings(config.DISPLAY_FILE,config.HEADERS)['filament_measurement']=='weight'
for mode in ('personal','advanced','business'):
 application_mode.set_application_mode(mode)
 for pref in ('length','weight','length'):
  response=client.post('/settings/other',data=dict(action='update_measurement_display',filament_measurement=pref))
  assert response.status_code==302,(response.status_code,response.data)
  assert storage.load_display_settings(config.DISPLAY_FILE,config.HEADERS)['filament_measurement']==pref
  assert history()==original
before=storage.load_display_settings(config.DISPLAY_FILE,config.HEADERS)
client.post('/settings/other',data=dict(action='update_measurement_display',filament_measurement='kg'))
assert storage.load_display_settings(config.DISPLAY_FILE,config.HEADERS)==before
assert not (Path(config.DATA_DIR)/'display.json').exists()
archive=backup.create_backup_archive(keep=3)
with tarfile.open(archive) as tar:
 member=next(m for m in tar.getmembers() if m.name.endswith('kcd.db'))
 restored=Path(config.DATA_DIR)/'restored.db'
 restored.write_bytes(tar.extractfile(member).read())
with closing(sqlite3.connect(restored)) as conn:
 assert conn.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
 assert json.loads(conn.execute("SELECT value_json FROM user_settings WHERE key='display_settings'").fetchone()[0])['filament_measurement']=='length'
 assert tuple(tuple(r) for r in conn.execute('SELECT * FROM jobs'))==original
print('preserved')
'''
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, "KCD_DATA_DIR": tmp, "KCD_STORAGE_BACKEND": "sql", "KCD_API_KEY": "isolated-measurement-test"}
            run = subprocess.run([sys.executable, "-c", script], cwd=root, env=env, capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            restart = subprocess.run([sys.executable, "-c", "from core.storage import load_display_settings; from core.config import DISPLAY_FILE,HEADERS; assert load_display_settings(DISPLAY_FILE,HEADERS)['filament_measurement']=='length'"], cwd=root, env=env, capture_output=True, text=True)
            self.assertEqual(restart.returncode, 0, restart.stderr)


if __name__ == "__main__":
    unittest.main()
