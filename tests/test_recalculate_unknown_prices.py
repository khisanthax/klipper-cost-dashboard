"""NULL originals remain unknown in previews and recoverable after explicit pricing."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class UnknownOriginalPricesTests(unittest.TestCase):
    def test_sql_null_original_preview_apply_and_restore(self):
        code = r'''
from flask import template_rendered
from core import db, profiles, rates
from core.registration import register_printer
from core.recalculation import restore_originals
register_printer('Fixture','http://127.0.0.1:1')
profiles.upsert_profile(dict(id='material',name='Material',filament_mode='per_kg',filament_rate=15,grams_per_meter=3))
rates.upsert_rate_profile(dict(id='free',name='Free',rate_per_hour=0))
with db.connect_db() as conn:
 for uid,price in [('unknown',None),('zero',0),('known',5)]:
  db.upsert_job(conn,dict(job_uid=uid,printer='Fixture',filename=uid+'.gcode',status='completed',timestamp=1800000000,duration_seconds=3600,filament_mm=10000,filament_meters=10,grams_per_meter=3,rate_per_hour=1,filament_mode='per_meter',filament_rate=.25,time_cost=price,material_cost=0 if price is not None else None,total_cost=price))
 conn.execute("UPDATE jobs SET time_cost=NULL,material_cost=NULL,total_cost=NULL WHERE job_uid='unknown'")
import app
client=app.app.test_client(); contexts=[]
def capture(sender,template,context,**extra):contexts.append(context)
template_rendered.connect(capture, app.app)
form=dict(job_uids=['unknown','zero','known'],recompute_mode='pricing_only',apply_rate_profile='1',rate_profile_id='free',apply_filament_profile='1',filament_profile_id='material')
response=client.post('/recalculate/preview',data=form)
assert response.status_code==200,response.data
preview=contexts[-1]['preview']; by_uid={r['job_uid']:r for r in preview['rows']}
assert not by_uid['unknown']['original_price_known']
assert by_uid['unknown']['recorded_prices']==dict(total_cost=None,time_cost=None,material_cost=None)
assert by_uid['zero']['original_price_known'] and by_uid['zero']['display_old_total_cost']==0
assert preview['totals']['unknown_original_count']==1
assert preview['totals']['before']==5 and abs(preview['totals']['after']-1.35)<1e-9
html=response.get_data(as_text=True)
assert 'Known Original Subtotal' in html and 'partial subtotal' in html
assert 'data-col="old">Unpriced</td>' in html
assert 'data-col="delta">Unavailable</td>' in html
assert '-$4.55' in html and '$-4.55' not in html
assert 'Original Time Cost: Unavailable' in html and 'Original Material Cost: Unavailable' in html
with db.connect_db() as conn:
 row=conn.execute("SELECT * FROM jobs WHERE job_uid='unknown'").fetchone()
 assert row['total_cost'] is None and row['time_cost'] is None
form['preview_hash']=contexts[-1]['preview_hash']
response=client.post('/recalculate/run',data=form)
assert response.status_code==302,response.data
with db.connect_db() as conn:
 row=conn.execute("SELECT * FROM jobs WHERE job_uid='unknown'").fetchone()
 assert row['rate_per_hour']==0 and row['filament_rate']==15
 assert row['time_cost']==0 and abs(row['total_cost']-.45)<1e-9
restore_originals(['unknown'],apply=True)
with db.connect_db() as conn:
 row=conn.execute("SELECT * FROM jobs WHERE job_uid='unknown'").fetchone()
 assert row['time_cost'] is None and row['material_cost'] is None and row['total_cost'] is None
'''
        with tempfile.TemporaryDirectory() as tmp:
            result=subprocess.run([sys.executable,'-c',code],cwd=Path(__file__).resolve().parents[1],
                env={**os.environ,'KCD_STORAGE_BACKEND':'sql','KCD_DATA_DIR':tmp,'KCD_API_KEY':'isolated-test-key'},
                capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
