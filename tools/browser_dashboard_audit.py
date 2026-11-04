"""Whole-dashboard acceptance on disposable SQL and Moonraker HTTP fixtures."""
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
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT=Path(__file__).resolve().parents[1]
SEED='''
import json,os,time
from core import db,projects,profiles,rates,live
from core.registration import register_printer
moons=json.loads(os.environ['KCD_AUDIT_MOONS'])
for name,url in moons.items():register_printer(name,url)
profiles.upsert_profile(dict(id='petg-15',name='PETG 15/kg',material='PETG',filament_mode='per_kg',filament_rate=15,cost_per_kg=15,grams_per_meter=3))
rates.upsert_rate_profile(dict(id='free',name='Free printing time',rate_per_hour=0))
project=projects.create_project('RaRa acceptance',hourly_rate_override=0,filament_cost_per_kg_override=15)
with db.connect_db() as conn:
 for i in range(15):
  meters=60 if i<14 else 67.294
  material=meters*3*15/1000
  db.upsert_job(conn,dict(job_uid='mask-'+str(i),printer='Idle',filename='mask-'+str(i)+'.gcode',status='completed',timestamp=time.time()-i,duration_seconds=3600,duration_hours=1,filament_mm=meters*1000,filament_meters=meters,filament_grams=meters*3,filament_weight_kind='estimated',grams_per_meter=3,rate_per_hour=0,filament_mode='per_kg',filament_rate=15,time_cost=0,material_cost=material,total_cost=material))
 for i in range(int(os.environ.get('KCD_AUDIT_HISTORY_JOBS','2000'))):
  db.upsert_job(conn,dict(job_uid='bulk-'+str(i),printer='Idle',filename='bulk-'+str(i)+'.gcode',status='completed',timestamp=time.time()-86400-i,duration_seconds=60,duration_hours=1/60,filament_mm=1000,filament_meters=1,grams_per_meter=3,rate_per_hour=0,filament_mode='per_kg',filament_rate=15,time_cost=0,material_cost=.045,total_cost=.045))
projects.assign_jobs(['mask-'+str(i) for i in range(15)],project.id)
live.start_job('Printing','active.gcode',start_time=time.time()-600)
live.start_job('Paused','paused.gcode',start_time=time.time()-600);live.pause_job('Paused')
live.start_job('Cancelled','stale.gcode',start_time=time.time()-86400)
print(project.id)
'''


def main():
    from playwright.sync_api import sync_playwright
    count=int(sys.argv[sys.argv.index('--history-jobs')+1]) if '--history-jobs' in sys.argv else 2000
    if not 100<=count<=10000:raise SystemExit('Disposable history size must be 100–10000 jobs')
    label='dashboard-workflows' if '--workflows-only' in sys.argv else 'dashboard-audit' if count==2000 else 'dashboard-audit-'+str(count)
    artifacts=ROOT/'browser-artifacts'/label;artifacts.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='kcd-ux-') as tmp:
        moons=[];urls={}
        class Moonraker(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                if self.path.startswith('/printer/objects/query'):
                    result={'status':{'gcode_macro _KCD_VARS':{'printer_name':self.server.identity},'print_stats':{'state':self.server.state,'filename':'paused.gcode' if self.server.state=='paused' else 'active.gcode','print_duration':600,'total_duration':660,'filament_used':10000}}}
                elif self.path=='/server/info':result={'klippy_connected':True}
                else:self.send_response(404);self.end_headers();return
                self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(json.dumps({'result':result}).encode())
        for name,state in [('Idle','standby'),('Printing','printing'),('Paused','paused'),('Cancelled','cancelled'),('Completed','complete')]:
            moon=ThreadingHTTPServer(('127.0.0.1',0),Moonraker);moon.state=state;moon.identity=name;moons.append(moon)
            threading.Thread(target=moon.serve_forever,daemon=True).start();urls[name]=f'http://127.0.0.1:{moon.server_port}'
        urls['Offline']='http://127.0.0.1:1'
        env={**os.environ,'KCD_DATA_DIR':tmp,'KCD_STORAGE_BACKEND':'sql','KCD_API_KEY':'disposable-ux-key','KCD_AUDIT_MOONS':json.dumps(urls),'KCD_AUDIT_HISTORY_JOBS':str(count)}
        project=subprocess.run([sys.executable,'-c',SEED],cwd=ROOT,env=env,text=True,capture_output=True,check=True).stdout.strip().splitlines()[-1]
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        base=f'http://127.0.0.1:{port}'
        log=open(Path(tmp)/'server.log','w')
        server=subprocess.Popen([sys.executable,'-m','kcd.server','--port',str(port)],cwd=ROOT,env=env,stdout=log,stderr=log)
        try:
            for _ in range(100):
                try:urllib.request.urlopen(base+'/health',timeout=2).close();break
                except OSError:
                    if server.poll() is not None:raise RuntimeError('Fixture server failed to start')
                    time.sleep(.1)
            else:raise RuntimeError('Fixture server readiness timed out')
            with sync_playwright() as pw:
                browser=pw.chromium.launch();context=browser.new_context();context.tracing.start(screenshots=True,snapshots=True,sources=True)
                page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                cdp=context.new_cdp_session(page);cdp.send('Performance.enable')
                failed_requests=[]
                page.on('requestfailed',lambda request:failed_requests.append({'url':request.url,'failure':request.failure}))
                results=[]
                routes=['/','/reports','/projects','/projects/'+project+'/pricing','/orders?project='+project,'/recalculate','/settings/printers','/printer-diagnostics','/settings/profiles','/settings/pause','/settings/other','/system-events']
                if '--workflows-only' in sys.argv:routes=[]
                try:
                    sizes=[(1920,1080,1,'large'),(1440,1000,1,'desktop'),(900,900,1,'narrow'),(768,1024,1,'tablet'),(390,844,1,'mobile'),(1440,1000,2,'zoom200')]
                    if '--baseline' in sys.argv:sizes=[sizes[1],sizes[4]]
                    for route in routes:
                        for width,height,zoom,label in sizes:
                            page.set_viewport_size({'width':width,'height':height});start=time.monotonic()
                            response=page.goto(base+route,wait_until='load');page.evaluate('(z)=>document.body.style.zoom=z',zoom)
                            missing=page.locator('input:not([type=hidden]):not([type=submit]),select,textarea').evaluate_all('''es=>es.filter(e=>!e.disabled&&!e.labels?.length&&!e.getAttribute('aria-label')&&!e.getAttribute('aria-labelledby')&&!['button','reset'].includes(e.type)).map(e=>({tag:e.tagName,name:e.name,id:e.id}))''')
                            result={'route':route,'layout':label,'status':response.status,'seconds':round(time.monotonic()-start,3),'overflow':page.evaluate('document.documentElement.scrollWidth>innerWidth+1'),'unlabelled':missing}
                            if result['overflow']:
                                result['overflow_elements']=page.locator('body *').evaluate_all("es=>es.filter(e=>!e.closest('.table-responsive')&&e.getBoundingClientRect().right>innerWidth+1).slice(0,15).map(e=>({tag:e.tagName,id:e.id,classes:e.className,right:e.getBoundingClientRect().right}))")
                            if label in ('desktop','mobile') and '--axe' in sys.argv:
                                vendor=ROOT/'tools/vendor/axe-core-4.14.0'
                                import hashlib
                                assert hashlib.sha256((vendor/'axe.min.js').read_bytes()).hexdigest()==json.loads((vendor/'manifest.json').read_text())['sha256']
                                page.add_script_tag(path=str(vendor/'axe.min.js'))
                                audit=page.evaluate("async()=>{const r=await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa','wcag22aa','best-practice']}});return {violations:r.violations.map(v=>({id:v.id,impact:v.impact,help:v.help,nodes:v.nodes.map(n=>({target:n.target,summary:n.failureSummary}))})),incomplete:r.incomplete.length}}")
                                result['accessibility']=audit
                            results.append(result)
                            page.screenshot(path=str(artifacts/(route.split('?',1)[0].replace('/','_').strip('_')+'-'+label+'.png')),full_page=True)
                            assert response.status==200,result
                    (artifacts/'page-audit.json').write_text(json.dumps({'pages':results,'browser_errors':errors},indent=2))
                    assert not errors,errors
                    print(json.dumps({'pages':len(results),'overflow_cases':sum(r['overflow'] for r in results),'unlabelled_cases':sum(len(r['unlabelled']) for r in results),'slowest_seconds':max((r['seconds'] for r in results),default=0)}))
                    if '--baseline' not in sys.argv:
                        assert not any(r['overflow'] or r['unlabelled'] for r in results),'Resolve layout/label findings in page-audit.json'
                        assert not any(r.get('accessibility',{}).get('violations') for r in results),'Resolve automated accessibility findings in page-audit.json'
                        page.set_viewport_size({'width':1440,'height':1000});page.goto(base+'/')
                        page.get_by_role('link',name='Projects',exact=True).click()
                        page.get_by_role('link',name='Business orders',exact=True).click()
                        page.get_by_label('Project',exact=True).select_option(project)
                        page.get_by_role('button',name='Open order').click()
                        page.get_by_label('Customer (optional)').fill('Fixture customer')
                        page.get_by_label('Status',exact=True).select_option('active')
                        page.get_by_label('Quoted price ($)').fill('80')
                        page.get_by_label('Selling price ($)').fill('70')
                        page.get_by_label('Recorded revenue ($)').fill('60')
                        page.get_by_label('Additional expenses ($)').fill('2')
                        page.get_by_role('button',name='Save order').click()
                        profit=page.get_by_role('row').filter(has=page.get_by_role('rowheader',name='Profit',exact=True))
                        profit.get_by_role('cell',name='$17.17').wait_for()
                        page.get_by_label('Status',exact=True).select_option('complete')
                        page.get_by_role('button',name='Save order').click()
                        page.get_by_text('Completed at',exact=False).wait_for()
                        page.screenshot(path=str(artifacts/'business-order.png'),full_page=True)
                        page.goto(base+'/')
                        from dashboard_workflows import exercise
                        exercise(page,context,base,project,tmp,artifacts)
                        assert not errors,errors
                    metrics={item['name']:item['value'] for item in cdp.send('Performance.getMetrics')['metrics']}
                    (artifacts/'browser-performance.json').write_text(json.dumps({'metrics':metrics,'failed_requests':failed_requests,'browser_errors':errors},indent=2))
                except Exception:
                    page.screenshot(path=str(artifacts/'failure.png'),full_page=True);raise
                finally:context.tracing.stop(path=str(artifacts/'trace.zip'));browser.close()
        finally:
            server.terminate();server.wait(timeout=15);log.close()
            content=Path(tmp,'server.log').read_text(errors='replace').replace(env['KCD_API_KEY'],'[redacted]')
            (artifacts/'server.log').write_text(content)
            if 'Traceback' in content:print(content[-4500:])
            for moon in moons:moon.shutdown();moon.server_close()


if __name__=='__main__':main()
