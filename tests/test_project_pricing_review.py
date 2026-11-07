"""Real SQL project defaults, unsigned inputs and signed overlay review boundaries."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class ProjectPricingReviewTests(unittest.TestCase):
    def test_unsaved_inputs_ticket_staleness_manual_totals_and_originals(self):
        code=r'''
import json
from decimal import Decimal
from unittest.mock import patch
from flask import template_rendered
from core import db,projects,project_pricing,profiles
from core.registration import register_printer
register_printer('Fixture','http://127.0.0.1:1')
p=projects.create_project('Reviewed',hourly_rate_override=10,filament_cost_per_kg_override=20)
other=projects.create_project('Other')
profiles.upsert_profile(dict(id='profile',name='Profile',filament_mode='per_kg',filament_rate=15,grams_per_meter=3))
with db.connect_db() as c:
 for uid,value in [('known',10),('zero',0),('unknown',None)]:
  db.upsert_job(c,dict(job_uid=uid,printer='Fixture',filename=uid+'.gcode',status='completed',timestamp=1800000000,duration_seconds=3600,filament_mm=10000,filament_meters=10,grams_per_meter=3,rate_per_hour=1,time_cost=value,material_cost=0 if value is not None else None,total_cost=value))
 c.execute("UPDATE jobs SET time_cost=NULL,material_cost=NULL,total_cost=NULL WHERE job_uid='unknown'")
projects.assign_jobs(['known','zero','unknown'],p.id)
projects.create_manual_job(p.id,'Unchanged manual',hours=.5,filament_g=100,cost_override='1.235')
projects.create_plan_item(p.id,'not-actual.gcode',est_time_s=3600,est_filament_g=100,est_cost_override=999)
import app
app.app.config['TESTING']=True
client=app.app.test_client(); contexts=[]
def capture(sender,template,context,**extra):contexts.append(context)
template_rendered.connect(capture,app.app)
def source():
 with db.connect_db() as c:return {t:[dict(r) for r in c.execute('SELECT * FROM '+t)] for t in ('jobs','projects','filament_profiles','manual_jobs','planned_items','project_assignments')}
before=source()
form=dict(action='update_project',project_id=p.id,name='Unsaved name',hourly_rate_override='0',pricing_mode='spool',pricing_amount='15',net_spool_grams='1000')
response=client.post('/projects/'+p.id+'/pricing',data=form)
assert response.status_code==200,response.data
plan=contexts[-1]['plan'];ticket=contexts[-1]['review_token']
assert source()==before,'Preview changed source data'
assert plan['reviewed_inputs']['hourly_rate_override']==0
assert plan['manual_rows'][0]['after']['total_cost']==Decimal('1.235')
assert plan['after_totals']['total_cost']==Decimal('2.58'),plan['after_totals']
assert plan['unknown_originals']==1
assert sum(r['after']['display_total_cost'] for r in plan['rows'])+plan['manual_after']==plan['after_totals']['total_cost']
text=response.get_data(as_text=True)
assert 'Known Original Project Subtotal' in text and 'Unpriced' in text and 'Unavailable' in text
assert 'Original Time Cost: Unavailable' in text
assert 'manual job(s)' in text and 'hypothetical and excluded' in text
assert client.post('/projects/'+p.id+'/pricing',data=dict(action='apply',review_token=ticket+'broken')).status_code==409
assert client.post('/projects/'+other.id+'/pricing',data=dict(action='apply',review_token=ticket)).status_code==409
assert project_pricing.saved_overlays(p.id)=={}
response=client.post('/projects/'+p.id+'/pricing',data=dict(action='apply',review_token=ticket))
assert response.status_code==303,response.data
assert source()==before,'Apply changed historical/default/source data'
overlays=project_pricing.saved_overlays(p.id)
assert len(overlays)==3 and not project_pricing.overlay_status(p.id,overlays)['stale']
assert overlays['known']['reviewed_inputs']['hourly_rate_override']==0
response=client.get('/projects');assert response.status_code==200,response.data
assert 'Project-Adjusted Cost' in response.get_data(as_text=True)
from itsdangerous import URLSafeTimedSerializer
with patch('itsdangerous.timed.TimestampSigner.get_timestamp',return_value=1):
 expired=URLSafeTimedSerializer(app.API_KEY,salt='kcd-project-pricing-review-v1').dumps(dict(project_uid=p.id,inputs=plan['inputs'],fingerprint=plan['fingerprint']))
assert client.post('/projects/'+p.id+'/pricing',data=dict(action='apply',review_token=expired)).status_code==409
# Saving defaults changes manual/planned pricing only and leaves the reviewed overlay intact.
response=client.post('/projects',data=dict(form,action='update_project',hourly_rate_override='2',name='Reviewed'))
assert response.status_code==302,response.data
with db.connect_db() as c:
 assert [dict(r) for r in c.execute('SELECT * FROM jobs')]==before['jobs']
assert len(project_pricing.saved_overlays(p.id))==3
assert project_pricing.overlay_status(p.id,project_pricing.saved_overlays(p.id))['stale']
assert 'previously reviewed prices' in client.get('/projects').get_data(as_text=True)
assert client.post('/projects/'+p.id+'/pricing',data=dict(action='apply',review_token=ticket)).status_code==409
client.post('/projects/'+p.id+'/pricing',data=dict(action='clear'))
assert project_pricing.saved_overlays(p.id)=={}
with db.connect_db() as c:assert [dict(r) for r in c.execute('SELECT * FROM jobs')]==before['jobs']
fresh=project_pricing.preview(p.id)
with db.connect_db() as c:c.execute("UPDATE manual_jobs SET title='Changed manual' WHERE project_id=(SELECT id FROM projects WHERE project_uid=?)",(p.id,))
try:project_pricing.apply(p.id,fresh['fingerprint'])
except ValueError:pass
else:raise AssertionError('Stale manual source applied')
# Non-completed jobs, including an active and canceled job with the same
# filename, never enter repricing or require missing filament conversions.
with db.connect_db() as c:
 for uid,status,value in [('canceled','canceled',None),('failed','failed',3),('active','printing',4)]:
  db.upsert_job(c,dict(job_uid=uid,printer='Fixture',filename='known.gcode',status=status,timestamp=1800000000,time_cost=value,material_cost=0 if value is not None else None,total_cost=value))
 legacy=json.dumps(dict(after=dict(time_cost=2,material_cost=1,adjustment=3,total_cost=6),grams=None,grams_per_meter=None,conversion_source='Legacy snapshot'))
 c.execute('INSERT INTO project_job_pricing VALUES(?,?,?,?,?)',(c.execute('SELECT id FROM projects WHERE project_uid=?',(p.id,)).fetchone()[0],'failed',legacy,'legacy','old'))
projects.assign_jobs(['canceled','failed','active'],p.id)
inputs=project_pricing.inputs_from_form(form)
scope=project_pricing.preview(p.id,inputs)
assert len(scope['rows'])==3 and not scope['errors']
assert {r['job_uid'] for r in scope['unchanged_rows']}=={'canceled','failed','active'}
assert scope['unknown_new']==1 and scope['retained_overlay_count']==1
assert scope['after_totals']['total_cost']==Decimal('12.59'),scope['after_totals']
with db.connect_db() as c:all_jobs=[dict(r) for r in c.execute('SELECT * FROM jobs')]
project_pricing.apply(p.id,scope['fingerprint'],inputs)
with db.connect_db() as c:
 assert [dict(r) for r in c.execute('SELECT * FROM jobs')]==all_jobs
 assert c.execute("SELECT calculation_json FROM project_job_pricing WHERE job_uid='failed'").fetchone()[0]==legacy
 assert not c.execute("SELECT 1 FROM project_job_pricing WHERE job_uid IN ('canceled','active')").fetchall()
project_pricing.clear(p.id)
assert not project_pricing.saved_overlays(p.id)
# Incomplete original components with a known total cannot invent a signed
# adjustment even when both new rates are explicit. The real apply route rejects
# the signed review and writes no partial overlay.
with db.connect_db() as c:
 db.upsert_job(c,dict(job_uid='incomplete',printer='Fixture',filename='incomplete.gcode',status='completed',filament_meters=10,grams_per_meter=3,duration_seconds=3600,time_cost=None,material_cost=None,total_cost=10))
 c.execute("UPDATE jobs SET time_cost=NULL,material_cost=NULL WHERE job_uid='incomplete'")
projects.assign_jobs(['incomplete'],p.id)
response=client.post('/projects/'+p.id+'/pricing',data=form)
assert response.status_code==200
assert any('signed adjustment cannot be verified' in error['error'] for error in contexts[-1]['plan']['errors'])
assert 'value="apply" disabled>Apply to Project</button>' in response.get_data(as_text=True)
assert client.post('/projects/'+p.id+'/pricing',data=dict(action='apply',review_token=contexts[-1]['review_token'])).status_code==409
assert not project_pricing.saved_overlays(p.id)
# A wholly unpriced project without explicit rates stays unavailable, rather
# than proposing fabricated zero prices.
unpriced=projects.create_project('Unpriced defaults')
projects.assign_jobs(['unknown'],unpriced.id)
response=client.get('/projects/'+unpriced.id+'/pricing')
assert response.status_code==200
assert not contexts[-1]['plan']['rows'] and len(contexts[-1]['plan']['errors'])==1
assert 'Original time cost is unavailable' in response.get_data(as_text=True)
assert '<td>Unavailable</td><td>Unavailable</td><td>Unavailable</td>' in response.get_data(as_text=True)
'''
        with tempfile.TemporaryDirectory() as tmp:
            result=subprocess.run([sys.executable,'-c',code],cwd=Path(__file__).resolve().parents[1],
                env={**os.environ,'KCD_STORAGE_BACKEND':'sql','KCD_DATA_DIR':tmp,'KCD_API_KEY':'isolated-project-test-key'},capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
