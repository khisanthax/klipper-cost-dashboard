"""Real Chromium regression on disposable SQL data, never the live Master."""
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


def main():
    from playwright.sync_api import sync_playwright
    artifacts=ROOT/'browser-artifacts';artifacts.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        env={**os.environ,'KCD_DATA_DIR':tmp,'KCD_STORAGE_BACKEND':'sql','KCD_API_KEY':'isolated-browser-key'}
        seed="""from core.registration import register_printer
from core import db
register_printer('Fixture','http://127.0.0.1:1')
with db.connect_db() as c:
 for i in range(12):
  db.upsert_job(c,dict(job_uid='100' if i==0 else 'row-'+str(i),printer='Fixture',filename='fixture-'+str(i)+'.gcode',status='printing' if i==0 else 'failed',timestamp=1800000000-i,duration_seconds=3600,filament_mm=1000,duration_hours=1,filament_meters=1,grams_per_meter=3,time_cost=9,material_cost=9,total_cost=18))
"""
        subprocess.run([sys.executable,'-c',seed],env=env,cwd=ROOT,check=True)
        log=open(Path(tmp)/'server.log','w')
        server=subprocess.Popen([sys.executable,'-m','kcd.server','--port',str(port)],env=env,cwd=ROOT,stdout=log,stderr=log)
        base=f'http://127.0.0.1:{port}/?history_per_page=10'
        def rows():
            with closing(sqlite3.connect(Path(tmp)/'kcd.db')) as conn:
                return {r[0]:{'status':r[1],'time':r[2],'material':r[3],'total':r[4]} for r in conn.execute('SELECT job_uid,status,time_cost,material_cost,total_cost FROM jobs')}
        try:
            for _ in range(100):
                try:urllib.request.urlopen(base,timeout=2).close();break
                except OSError:time.sleep(.1)
            with sync_playwright() as pw:
                browser=pw.chromium.launch()
                page=browser.new_page(viewport={'width':1440,'height':950})
                # Previously persisted control widths must not hide selection.
                page.add_init_script("localStorage.setItem('kcd_colwidths::history_table',JSON.stringify({'__select__':1,filename:500,timestamp:160}))")
                page.goto(base)
                boxes=page.locator('#historyPostForm input[name="delete_rows"]')
                assert boxes.count()==10
                header=page.get_by_role('checkbox',name='Select all history rows on this page',exact=True)
                boxes.first.check()
                assert page.locator('#historySelectionCount').inner_text()=='1 selected on this page'
                assert header.evaluate('(e)=>e.indeterminate')
                header.check();assert page.locator('input[name="delete_rows"]:checked').count()==10
                boxes.nth(3).uncheck();assert header.evaluate('(e)=>e.indeterminate')
                header.click();assert page.locator('input[name="delete_rows"]:checked').count()==10
                header.uncheck();assert page.locator('input[name="delete_rows"]:checked').count()==0
                for width,height,zoom,label in [(1440,950,1,'desktop'),(768,950,1,'narrow'),(390,844,1,'mobile'),(1440,950,2,'zoom-200')]:
                    page.set_viewport_size({'width':width,'height':height})
                    page.evaluate('(z)=>document.body.style.zoom=z',zoom)
                    boxes.first.check();box=boxes.first.bounding_box()
                    assert box['width']>=20*zoom-1 and box['height']>=20*zoom-1,box
                    assert boxes.first.is_visible()
                    widths=page.locator('#history-table thead th:not([data-col=__select__])').evaluate_all('(es)=>es.filter(e=>e.getBoundingClientRect().width>0).map(e=>e.getBoundingClientRect().width)')
                    assert widths and min(widths)>=59*zoom,widths
                    page.evaluate('window.scrollTo(0,document.getElementById("historyPostForm").getBoundingClientRect().top+window.scrollY-80)')
                    page.screenshot(path=str(artifacts/('history-'+label+'-start.png')))
                    first_x=boxes.first.bounding_box()['x']
                    page.locator('#historyPostForm .table-responsive').evaluate('(e)=>e.scrollLeft=e.scrollWidth')
                    assert abs(boxes.first.bounding_box()['x']-first_x)<2
                    page.screenshot(path=str(artifacts/('history-'+label+'.png')))
                    boxes.first.uncheck()
                    page.locator('#historyPostForm .table-responsive').evaluate('(e)=>e.scrollLeft=0')
                page.evaluate("document.body.style.zoom=1");page.set_viewport_size({'width':1440,'height':950})
                baseline=rows()
                page.locator('input[value="100"][name="delete_rows"]').check()
                with page.expect_navigation(wait_until='load'):
                    page.get_by_role('button',name='Complete Selected',exact=True).click()
                after=rows();assert after['100']['status']=='completed',(page.url,after['100'])
                assert all(after[k]==v for k,v in baseline.items() if k!='100')
                page.locator('input[value="row-1"][name="delete_rows"]').check()
                with page.expect_navigation(wait_until='load'):
                    page.get_by_role('button',name='Recalculate cost (selected)',exact=True).click()
                assert rows()==after,'History recalculation must preview first'
                with page.expect_navigation(wait_until='load'):
                    page.get_by_role('button',name='Apply New Prices',exact=True).click()
                recalculated=rows();assert abs(recalculated['row-1']['total']-1.25)<1e-9
                page.goto(base)
                assert all(recalculated[k]==v for k,v in after.items() if k!='row-1')
                page.locator('input[value="row-2"][name="delete_rows"]').check()
                page.once('dialog',lambda dialog:dialog.accept())
                with page.expect_navigation(wait_until='load'):
                    page.get_by_role('button',name='Delete Selected',exact=True).click()
                deleted=rows();assert 'row-2' not in deleted
                assert all(deleted[k]==v for k,v in recalculated.items() if k!='row-2')
                assert len(deleted)==11
                browser.close()
            print('Browser history regression passed: visible controls, labels, partial/select-all counts, scrolling, responsive/zoom layouts and UID-specific bulk mutations')
        finally:
            server.terminate()
            try:server.wait(timeout=10)
            except subprocess.TimeoutExpired:server.kill();server.wait()
            log.close()


if __name__=='__main__':main()
