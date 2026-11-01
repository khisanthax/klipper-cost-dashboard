"""Exercise the actual profile controls, preview and SQL writes in Chromium."""
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from contextlib import closing

ROOT=Path(__file__).resolve().parents[1]
SEED="""
import json
from core.registration import register_printer
from core import db,profiles,rates,projects
register_printer('Fixture','http://127.0.0.1:1')
rates.upsert_rate_profile(dict(id='free-time',name='Zero dollars per hour',rate_per_hour=0))
profiles.upsert_profile(dict(id='petg-15',name='PETG fifteen per kg',material='PETG',filament_mode='per_kg',filament_rate=15,cost_per_kg=15,grams_per_meter=3))
p=projects.create_project('RaRa fixture',filament_cost_per_kg_override=15)
with db.connect_db() as c:
 for i in range(16):
  db.upsert_job(c,dict(job_uid='mask-'+str(i),printer='Fixture',filename='mask-'+str(i)+'.gcode',status='completed',timestamp=1800000000-i,duration_seconds=3600,filament_mm=100000,duration_hours=1,filament_meters=100,grams_per_meter=3,rate_per_hour=1,filament_mode='per_meter',filament_rate=.25,time_cost=1,material_cost=25,total_cost=26))
projects.assign_jobs(['mask-'+str(i) for i in range(15)],p.id)
print(p.id)
"""


def main():
    from playwright.sync_api import sync_playwright
    artifacts=ROOT/'browser-artifacts';artifacts.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        env={**os.environ,'KCD_DATA_DIR':tmp,'KCD_STORAGE_BACKEND':'sql','KCD_API_KEY':'isolated-browser-key'}
        seeded=subprocess.run([sys.executable,'-c',SEED],env=env,cwd=ROOT,check=True,capture_output=True,text=True)
        project=seeded.stdout.strip().splitlines()[-1]
        log=open(Path(tmp)/'server.log','w')
        server=subprocess.Popen([sys.executable,'-m','kcd.server','--port',str(port)],env=env,cwd=ROOT,stdout=log,stderr=log)
        base=f'http://127.0.0.1:{port}'
        def query(sql,*args):
            with closing(sqlite3.connect(Path(tmp)/'kcd.db')) as conn:
                conn.row_factory=sqlite3.Row
                return [dict(r) for r in conn.execute(sql,args)]
        try:
            for _ in range(100):
                try:urllib.request.urlopen(base+'/recalculate',timeout=2).close();break
                except OSError:time.sleep(.1)
            with sync_playwright() as pw:
                browser=pw.chromium.launch();page=browser.new_page(viewport={'width':1440,'height':1000})
                page.set_default_navigation_timeout(30000)
                errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                page.goto(base+'/recalculate?project='+project)
                assert page.locator('.kcd-job-cb').count()==15
                # Do not submit the GET filter form: this is the reported failure.
                page.locator('#apply_rate_profile').check()
                page.locator('[name=rate_profile_id]').select_option('free-time')
                page.locator('#apply_filament_profile').check()
                page.locator('[name=filament_profile_id]').select_option('petg-15')
                page.locator('#kcd-select-page').click()
                assert page.locator('#kcd-selected-summary').inner_text()=='Selected: 15'
                assert page.locator('#kcd-recalc-btn').is_disabled()
                original=query('SELECT * FROM jobs ORDER BY job_uid')
                with page.expect_navigation(wait_until='domcontentloaded'):page.locator('#kcd-preview-btn').click()
                assert query('SELECT * FROM jobs ORDER BY job_uid')==original,'Preview mutated history'
                assert page.locator('#apply_rate_profile').is_checked()
                assert page.locator('[name=rate_profile_id]').input_value()=='free-time'
                assert page.locator('#recalc-preview-table tbody tr').count()==15
                assert page.locator('#kcd-recalc-btn').is_enabled()
                page.screenshot(path=str(artifacts/'pricing-preview.png'),full_page=True)
                with page.expect_navigation(wait_until='domcontentloaded'):page.locator('#kcd-recalc-btn').click()
                assert 'changed 15 job(s)' in page.locator('body').inner_text()
                rows=query('SELECT * FROM jobs WHERE job_uid!=?', 'mask-15')
                assert len(rows)==15
                for row in rows:
                    assert row['rate_per_hour']==0 and row['hourly_rate_profile_id']=='free-time',row
                    assert row['filament_mode']=='per_kg' and row['filament_rate']==15,row
                    assert row['filament_profile_id']=='petg-15' and row['grams_per_meter']==3,row
                    assert row['filament_grams']==300 and row['time_cost']==0 and row['material_cost']==4.5 and row['total_cost']==4.5,row
                untouched=query('SELECT * FROM jobs WHERE job_uid=?','mask-15')[0]
                assert untouched==next(r for r in original if r['job_uid']=='mask-15')
                assert len(query('SELECT * FROM job_cost_originals'))==15
                event=json.loads(query("SELECT meta_json FROM system_events WHERE title='Pricing recalculation completed' ORDER BY id DESC LIMIT 1")[0]['meta_json'])
                assert event['count_updated']==event['count_validated']==15 and event['count_unchanged']==0,event
                assert event['plan']['rate_profile_id']=='free-time'
                assert all(r['before']['filament_mode']=='per_meter' and r['after']['filament_mode']=='per_kg' and r['after']['rate_per_hour']==0 for r in event['comparisons'])
                page.goto(base+'/projects',wait_until='domcontentloaded')
                assert '67.50' in page.locator('body').inner_text(),'Project total failed to reflect stored recalculation'
                # Repeated operation converges and reports unchanged honestly.
                page.goto(base+'/recalculate?project='+project+'&apply_rate_profile=1&rate_profile_id=free-time&apply_filament_profile=1&filament_profile_id=petg-15')
                page.locator('#kcd-select-page').click()
                with page.expect_navigation(wait_until='domcontentloaded'):page.locator('#kcd-preview-btn').click()
                with page.expect_navigation(wait_until='domcontentloaded'):page.locator('#kcd-recalc-btn').click()
                assert 'changed 0 job(s). 15 already matched' in page.locator('body').inner_text()
                # Changing a reviewed plan must invalidate the apply button.
                page.locator('#kcd-select-page').click()
                with page.expect_navigation(wait_until='domcontentloaded'):page.locator('#kcd-preview-btn').click()
                page.locator('[name=pricing_amount]').fill('20');page.locator('[name=pricing_amount]').blur()
                assert page.locator('#kcd-recalc-btn').is_disabled()
                # Profile creation uses spool price and density directly.
                page.goto(base+'/settings/profiles')
                form=page.locator('form').filter(has=page.locator('input[name="action"][value="add_filament_profile"]'))
                form.locator('[name=profile_name]').fill('Browser PETG spool')
                form.locator('[name=material]').fill('PETG')
                form.locator('[name=pricing_mode]').select_option('spool')
                form.locator('[name=pricing_amount]').fill('15')
                assert form.locator('[name=net_spool_grams]').input_value()=='1000'
                form.locator('[name=conversion_basis]').select_option('geometry')
                form.locator('[name=density_g_cm3]').fill('1.25')
                form.locator('[name=diameter_mm]').fill('1.75')
                with page.expect_navigation(wait_until='domcontentloaded'):form.get_by_role('button',name='Add',exact=True).click()
                saved=query('SELECT * FROM filament_profiles WHERE name=?','Browser PETG spool')[0]
                assert saved['filament_mode']=='per_kg' and saved['filament_rate']==15 and saved['spool_price']==15 and saved['net_spool_grams']==1000,saved
                assert abs(saved['grams_per_meter']-3.0066023442558574)<1e-12,saved
                page.screenshot(path=str(artifacts/'filament-profile-spool.png'),full_page=True)
                # Project-only preview/apply does not overwrite recorded jobs.
                before_overlay=query('SELECT * FROM jobs ORDER BY job_uid')
                page.goto(base+'/projects/'+project+'/pricing')
                assert page.locator('#project-pricing-preview tbody tr').count()==30
                with page.expect_navigation(wait_until='domcontentloaded'):page.get_by_role('button',name='Apply project-adjusted snapshot').click()
                assert len(query('SELECT * FROM project_job_pricing'))==15
                assert query('SELECT * FROM jobs ORDER BY job_uid')==before_overlay
                assert 'Project-adjusted snapshot' in page.locator('body').text_content()
                assert not errors,errors
                browser.close()
            subprocess.run([sys.executable,'-c',"from core.recalculation import restore_originals; restore_originals(['mask-'+str(i) for i in range(15)],apply=True)"],env=env,cwd=ROOT,check=True)
            restored=query('SELECT * FROM jobs ORDER BY job_uid')
            for before,after in zip(original,restored):
                for field in ('time_cost','material_cost','total_cost','rate_per_hour','filament_mode','filament_rate','grams_per_meter'):
                    assert before[field]==after[field],(field,before,after)
            print('Browser pricing regression passed: 15 selected jobs, zero hourly profile, $15/kg, preview, verified SQL fields, audit evidence, project totals, idempotency and original-price recovery')
        except Exception:
            log.flush();print(Path(tmp,'server.log').read_text(errors='replace')[-6000:]);raise
        finally:
            server.terminate()
            try:server.wait(timeout=10)
            except subprocess.TimeoutExpired:server.kill();server.wait()
            log.close()


if __name__=='__main__':main()
