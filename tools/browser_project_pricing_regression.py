"""Exercise unsaved project defaults, reviewed overlays and original-price view."""
from contextlib import closing
from decimal import Decimal
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

from browser_pricing_regression import SEED

ROOT=Path(__file__).resolve().parents[1]


def main():
    from playwright.sync_api import sync_playwright
    artifacts=ROOT/'browser-artifacts';artifacts.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        env={**os.environ,'KCD_DATA_DIR':tmp,'KCD_STORAGE_BACKEND':'sql','KCD_API_KEY':'isolated-project-browser-key'}
        extra="\nprojects.create_manual_job(p.id,'Manual work stays on saved defaults',hours=.5,filament_g=100,cost_override=2)\nprojects.create_plan_item(p.id,'Planned only.gcode',est_time_s=3600,est_filament_g=500,est_cost_override=999)\n"
        seeded=subprocess.run([sys.executable,'-c',SEED+extra],env=env,cwd=ROOT,check=True,capture_output=True,text=True)
        project=seeded.stdout.strip().splitlines()[-1]
        def query(sql,*args):
            with closing(sqlite3.connect(Path(tmp)/'kcd.db')) as conn:
                conn.row_factory=sqlite3.Row
                return [dict(row) for row in conn.execute(sql,args)]
        original=query('SELECT * FROM jobs ORDER BY job_uid')
        defaults=query('SELECT * FROM projects WHERE project_uid=?',project)[0]
        profiles=query('SELECT * FROM filament_profiles ORDER BY id')
        log=open(Path(tmp)/'server.log','w')
        server=subprocess.Popen([sys.executable,'-m','kcd.server','--port',str(port)],env=env,cwd=ROOT,stdout=log,stderr=log)
        base=f'http://127.0.0.1:{port}'
        try:
            for _ in range(100):
                try:urllib.request.urlopen(base+'/projects',timeout=2).close();break
                except OSError:time.sleep(.1)
            with sync_playwright() as pw:
                browser=pw.chromium.launch();context=browser.new_context(viewport={'width':1440,'height':1000})
                context.tracing.start(screenshots=True,snapshots=True,sources=True);page=context.new_page()
                def edit_form():
                    page.goto(base+'/projects?edit_project='+project)
                    panel=page.locator('#proj-'+project)
                    if not panel.is_visible():page.locator('[data-bs-target="#proj-'+project+'"]').click()
                    page.wait_for_function('(id)=>{const el=document.getElementById(id);return el.classList.contains("show")&&!el.classList.contains("collapsing")}',arg='proj-'+project)
                    return panel.locator('form').filter(has=page.locator('input[name=action][value=update_project]'))
                try:
                    form=edit_form()
                    assert form.get_by_role('button',name='Save Project Defaults',exact=True).is_visible()
                    assert "Save pricing for this project's manual and planned work. Existing completed print prices are not changed." in form.inner_text()
                    form.locator('[name=hourly_rate_override]').fill('0')
                    form.locator('[name=pricing_mode]').select_option('spool')
                    form.locator('[name=pricing_amount]').fill('15')
                    form.locator('[name=net_spool_grams]').fill('1000')
                    with page.expect_navigation(wait_until='domcontentloaded'):form.get_by_role('button',name='Preview New Project Costs',exact=True).click()
                    assert query('SELECT * FROM projects WHERE project_uid=?',project)[0]==defaults
                    assert query('SELECT * FROM jobs ORDER BY job_uid')==original
                    assert query('SELECT * FROM filament_profiles ORDER BY id')==profiles
                    assert page.locator('#project-pricing-preview thead th').all_inner_texts()==['Print','Original','Change','New']
                    assert page.locator('.project-calculation-details[open]').count()==0
                    assert 'Original Project Total' in page.locator('#project-pricing-totals').inner_text()
                    assert '$42.83' in page.locator('#project-pricing-totals').inner_text()
                    assert '$2.00' in page.locator('#project-pricing-preview tfoot').inner_text()
                    displayed=page.locator('#project-pricing-preview tbody [data-col=new]').all_inner_texts()
                    assert sum(Decimal(value.replace('$','')) for value in displayed)+Decimal('2')==Decimal('42.83')
                    details=page.locator('.project-calculation-details').first
                    details.locator('summary').first.focus();page.keyboard.press('Enter')
                    assert 'New Material Cost' in details.inner_text()
                    page.keyboard.press('Enter')
                    for width,height,zoom,label in ((1440,1000,1,'desktop'),(768,1024,1,'tablet'),(390,844,1,'mobile'),(1440,1000,2,'zoom200')):
                        page.set_viewport_size({'width':width,'height':height})
                        page.evaluate('(z)=>document.body.style.zoom=z',zoom)
                        assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth+1')
                        for summary in page.locator('#project-pricing-preview summary:visible').all():
                            assert summary.bounding_box()['height']>=24*zoom,(label,summary.bounding_box())
                        page.screenshot(path=str(artifacts/f'project-price-preview-{label}.png'),full_page=True)
                    page.evaluate('document.body.style.zoom=1')
                    page.set_viewport_size({'width':1440,'height':1000})
                    with page.expect_navigation(wait_until='domcontentloaded'):page.get_by_role('button',name='Apply to Project',exact=True).click()
                    assert len(query('SELECT * FROM project_job_pricing'))==15
                    assert query('SELECT * FROM jobs ORDER BY job_uid')==original
                    assert query('SELECT * FROM projects WHERE project_uid=?',project)[0]==defaults
                    assert query('SELECT * FROM filament_profiles ORDER BY id')==profiles
                    assert 'Project-Adjusted Cost' in page.locator('body').inner_text()
                    form=edit_form();form.locator('[name=pricing_amount]').fill('20')
                    with page.expect_navigation(wait_until='domcontentloaded'):form.get_by_role('button',name='Save Project Defaults',exact=True).click()
                    assert query('SELECT * FROM projects WHERE project_uid=?',project)[0]['filament_cost_per_kg_override']==20
                    assert query('SELECT * FROM jobs ORDER BY job_uid')==original
                    assert len(query('SELECT * FROM project_job_pricing'))==15
                    assert 'previously reviewed prices' in page.locator('body').inner_text()
                    page.locator('#proj-'+project).get_by_role('button',name='Use Original Prices',exact=True).click()
                    page.wait_for_load_state('domcontentloaded')
                    assert not query('SELECT * FROM project_job_pricing')
                    assert query('SELECT * FROM jobs ORDER BY job_uid')==original
                    assert 'Saved Historical Cost' in page.locator('body').inner_text()
                except Exception:
                    page.screenshot(path=str(artifacts/'project-pricing-failure.png'),full_page=True);raise
                finally:
                    context.tracing.stop(path=str(artifacts/'project-pricing-trace.zip'));browser.close()
            print('Project pricing browser passed: unsaved inputs, four-column review, exact totals/manual subtotal, overlay-only apply, stale defaults, original view and responsive keyboard details')
        except Exception:
            log.flush();print(Path(tmp,'server.log').read_text(errors='replace')[-5000:]);raise
        finally:
            server.terminate();server.wait(timeout=10);log.close()


if __name__=='__main__':main()
