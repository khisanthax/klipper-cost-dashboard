"""Real UI consumption preferences on disposable SQL/Moonraker services."""
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
SEED = r'''
import os,time,json
from core import db,projects,inventory,storage,config
from core.registration import register_printer
register_printer('Fixture',os.environ['KCD_MEASUREMENT_MOON'])
project=projects.create_project('Mixed material display',filament_cost_per_kg_override=15,filament_price={'mode':'per_kg','rate':'15','grams_per_meter':'3'})
with db.connect_db() as c:
 for i in range(525):
  row=dict(job_uid='measure-'+str(i),printer='Fixture',filename=('very-long-model-name-'*12)+str(i)+'.gcode',status='completed',timestamp=time.time()-i,duration_seconds=3600,duration_hours=1,rate_per_hour=0,filament_mode='per_kg',filament_rate=15,time_cost=0,material_cost=0,total_cost=0)
  row.update(filament_mm=145450,filament_meters=145.45,grams_per_meter=3)
  if i==1:row.update(filament_mm=1000000,filament_meters=1000)
  if i==2:row.update(grams_per_meter=None)
  if i==3:row.update(filament_mm=None,filament_meters=None,filament_grams=321,filament_weight_kind='recorded',grams_per_meter=None)
  if i==4:row.update(filament_mm=0,filament_meters=0)
  if i==5:row.update(grams_per_meter=4)
  db.upsert_job(c,row)
projects.assign_jobs(['measure-0','measure-2','measure-3'],project.id)
projects.create_manual_job(project.id,'Manual material',1,300)
projects.create_plan_item(project.id,'Planned material',3600,900)
projects.create_plan_item(project.id,'Unknown planned material',3600,None)
inventory.create_spool('a'*32,manufacturer='Fixture',material='PETG',color='blue',net_grams=1000,remaining_grams=900,low_stock_grams=100,purchase_price=15,grams_per_meter=3)
inventory.create_spool('b'*32,manufacturer='Fixture',material='Unknown polymer',color='clear',net_grams=1000,remaining_grams=800,low_stock_grams=100,purchase_price=15)
display=storage.load_display_settings(config.DISPLAY_FILE,config.HEADERS)
display['visible_columns']=['timestamp','printer','filename','filament_meters','total_cost']
display['tables']['history']['visible_columns']=display['visible_columns']
storage.save_display_settings(config.DISPLAY_FILE,config.DATA_DIR,display)
print(project.id)
'''


def main():
    from playwright.sync_api import sync_playwright
    artifacts = ROOT / 'browser-artifacts' / 'measurement'
    artifacts.mkdir(parents=True, exist_ok=True)
    class Moonraker(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            if self.path.startswith('/printer/objects/query'):
                result = {'status': {'gcode_macro _KCD_VARS': {'printer_name': 'Fixture'},
                    'print_stats': {'state': 'printing', 'filename': 'live.gcode', 'print_duration': 600,
                                    'total_duration': 600, 'filament_used': 145450}, 'display_status': {'progress': .5}}}
            elif self.path == '/server/info':
                result = {'klippy_connected': True}
            else:
                self.send_response(404); self.end_headers(); return
            self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers()
            self.wfile.write(json.dumps({'result': result}).encode())
    moon = ThreadingHTTPServer(('127.0.0.1', 0), Moonraker)
    threading.Thread(target=moon.serve_forever, daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(prefix='kcd-measurement-') as tmp:
            env = {**os.environ, 'KCD_DATA_DIR': tmp, 'KCD_STORAGE_BACKEND': 'sql',
                   'KCD_API_KEY': 'disposable-measurement-key',
                   'KCD_MEASUREMENT_MOON': f'http://127.0.0.1:{moon.server_port}'}
            seeded = subprocess.run([sys.executable, '-c', SEED], env=env, cwd=ROOT, capture_output=True, text=True)
            assert seeded.returncode == 0, seeded.stdout + seeded.stderr
            project = seeded.stdout.strip().splitlines()[-1]
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
            base = f'http://127.0.0.1:{port}'
            log = open(Path(tmp) / 'server.log', 'w')
            def start():
                server = subprocess.Popen([sys.executable, '-m', 'kcd.server', '--port', str(port)], cwd=ROOT, env=env, stdout=log, stderr=log)
                for _ in range(120):
                    try:
                        urllib.request.urlopen(base + '/health', timeout=2).close(); return server
                    except OSError:
                        if server.poll() is not None:
                            raise AssertionError('Server failed: ' + (Path(tmp)/'server.log').read_text())
                        time.sleep(.1)
                raise AssertionError('Server readiness timeout')
            def stop(server):
                server.terminate()
                try: server.wait(timeout=10)
                except subprocess.TimeoutExpired: server.kill(); server.wait()
            def snapshot():
                with closing(sqlite3.connect(Path(tmp)/'kcd.db')) as conn:
                    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    snapshot={}
                    for name in sorted(names):
                        if name.startswith('sqlite_') or name in ('reports_cache','report_cache'):
                            continue
                        rows=conn.execute('SELECT * FROM "'+name.replace('"','""')+'"').fetchall()
                        if name=='job_thumbnails':
                            # GETs may record a negative source-availability cache.
                            # Permanent historical image associations must stay exact.
                            rows=[row for row in rows if row[1] is not None]
                        if name=='user_settings':
                            rows=[row for row in rows if row[0]!='display_settings']
                        snapshot[name]=sorted((tuple(row) for row in rows), key=repr)
                    return snapshot
            server = start()
            try:
                original = snapshot()
                with sync_playwright() as pw:
                    browser = pw.chromium.launch()
                    context = browser.new_context(viewport={'width':1440,'height':1000})
                    context.tracing.start(screenshots=True, snapshots=True, sources=True)
                    page = context.new_page(); errors = []; results = []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    try:
                        page.goto(base+'/settings/display')
                        assert page.locator('[name=filament_measurement]').input_value() == 'weight'
                        for pref in ('weight','length'):
                            page.goto(base+'/settings/display')
                            page.locator('[name=filament_measurement]').select_option(pref)
                            with page.expect_navigation():
                                page.locator('form').filter(has=page.locator('[name=filament_measurement]')).get_by_role('button').click()
                            page.reload()
                            assert page.locator('[name=filament_measurement]').input_value() == pref
                            expected = '436.35 g' if pref=='weight' else '145.45 m'
                            for route in ('/?history_per_page=10','/projects','/printers/Fixture','/reports','/inventory','/recalculate','/estimates'):
                                for width,height,zoom,label in ((1440,1000,1,'desktop'),(768,1024,1,'tablet'),(390,844,1,'mobile'),(1440,1000,2,'zoom200')):
                                    page.set_viewport_size({'width':width,'height':height})
                                    response=page.goto(base+route,wait_until='load')
                                    page.evaluate('(z)=>document.body.style.zoom=z',zoom)
                                    assert response.ok,(route,response.status)
                                    assert not page.evaluate('document.documentElement.scrollWidth>innerWidth+1'),(pref,route,label,'overflow')
                                    primary=page.locator('[data-filament-primary]:visible')
                                    for value in primary.all_inner_texts():
                                        assert 'g/m' not in value and ' · ' not in value,(route,value)
                                    assert page.locator('[data-filament-details][open]').count()==0
                                    if route.startswith('/?'):
                                        row=page.locator('#history-table tbody tr').filter(has=page.locator('input[value="measure-0"]'))
                                        assert expected in row.inner_text(),(pref,row.inner_text())
                                        assert row.bounding_box()['height'] < 160*zoom,(label,row.bounding_box())
                                        clamped=row.locator('[data-filename-preview]')
                                        assert clamped.evaluate('(e)=>e.getBoundingClientRect().height<=parseFloat(getComputedStyle(e).lineHeight)*2*parseFloat(document.body.style.zoom||1)+1')
                                        assert ('Unknown weight' if pref=='weight' else '145.45 m') in page.locator('#history-table tbody tr').filter(has=page.locator('input[value="measure-2"]')).inner_text()
                                        row.locator('.col-filament_meters').scroll_into_view_if_needed()
                                    if route=='/inventory' and pref=='length':
                                        assert '300.00 m' in page.locator('body').inner_text()
                                        assert 'Unknown length' in page.locator('body').inner_text()
                                    page.screenshot(path=str(artifacts/(pref+'-'+route.split('?')[0].strip('/').replace('/','_')+'-'+label+'.png')),full_page=True)
                                    results.append({'preference':pref,'route':route,'layout':label,'primaries':primary.count()})
                            # Independent browser context sees the installation preference.
                            other=browser.new_context(); check=other.new_page();check.goto(base+'/settings/display')
                            assert check.locator('[name=filament_measurement]').input_value()==pref
                            other.close()
                            telemetry=context.request.get(base+'/api/printer-status').json()['printers']['Fixture']
                            assert telemetry['filament_display']['text']==expected,telemetry
                            page.goto(base+'/projects')
                            page.locator('[data-bs-target="#proj-'+project+'"]').click()
                            page.wait_for_function("id=>document.getElementById(id).classList.contains('show')&&!document.getElementById(id).classList.contains('collapsing')",arg='proj-'+project)
                            project_detail=page.locator('#proj-'+project)
                            measures=project_detail.locator('[data-col=filament_g] [data-filament-primary]').all_inner_texts()
                            assert ('300.00 g' if pref=='weight' else '100.00 m') in measures,measures
                            assert ('900.00 g' if pref=='weight' else '300.00 m') in measures,measures
                            assert ('Unknown weight' if pref=='weight' else 'Unknown length') in measures,measures
                            page.goto(base+'/estimates')
                            for name,value in {'duration_seconds':'3600','hourly_rate':'0','filament_grams':'436.35','pricing_amount':'15','grams_per_meter':'3'}.items():
                                page.locator('[name='+name+']').fill(value)
                            with page.expect_navigation():
                                page.get_by_role('button',name='Calculate estimate',exact=True).click()
                            estimate=page.locator('section[aria-labelledby=estimate-result]')
                            assert estimate.locator('[data-filament-primary]').inner_text()==expected
                            assert '$6.55' in estimate.inner_text()
                            current=snapshot()
                            differences={name:{'before':original.get(name),'after':current.get(name)} for name in original.keys()|current.keys() if original.get(name)!=current.get(name)}
                            if differences:
                                (artifacts/'preservation-failure.json').write_text(json.dumps(differences,indent=2,default=str))
                            assert not differences,('Display changed measurements/pricing/business rows',list(differences))
                        # Optional evidence is accessible by keyboard, closed by default.
                        page.goto(base+'/?history_per_page=10')
                        full=page.locator('[data-filename-details]').first
                        full.locator('summary').focus();page.keyboard.press('Enter')
                        assert full.locator('[data-filename-full]').is_visible()
                        assert len(full.locator('[data-filename-full]').inner_text())>200
                        detail=page.locator('[data-filament-details]').first
                        if detail.count():
                            detail.locator('summary').focus(); page.keyboard.press('Enter')
                            assert detail.get_attribute('open') is not None
                        stop(server);server=start();page.goto(base+'/settings/display')
                        assert page.locator('[name=filament_measurement]').input_value()=='length'
                        assert snapshot()==original
                        assert not errors,errors
                        (artifacts/'results.json').write_text(json.dumps({'layouts':results,'browser_errors':errors,'history_preserved':True},indent=2))
                        print(json.dumps({'layouts':len(results),'history_preserved':True,'restart_persistence':True}))
                    except Exception:
                        page.screenshot(path=str(artifacts/'failure.png'),full_page=True)
                        raise
                    finally:
                        context.tracing.stop(path=str(artifacts/'trace.zip'));browser.close()
            finally:
                stop(server);log.close()
    finally:
        moon.shutdown();moon.server_close()


if __name__=='__main__':
    main()
