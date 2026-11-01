"""Archive at print start, remove source, relocate, restart, then render all views."""
import base64
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
import uuid
from contextlib import closing
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer

ROOT=Path(__file__).resolve().parents[1]
PNG=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a8t0AAAAASUVORK5CYII=')


def main():
    from playwright.sync_api import sync_playwright
    artifacts=ROOT/'browser-artifacts';artifacts.mkdir(exist_ok=True)
    class Moonraker(BaseHTTPRequestHandler):
        available=True
        def log_message(self,*_args):pass
        def do_GET(self):
            route=self.path.split('?')[0]
            if not self.available and route.startswith('/server/files/'):
                self.send_error(404);return
            if route.endswith('/image.png'):
                self.send_response(200);self.send_header('Content-Type','image/png');self.end_headers();self.wfile.write(PNG);return
            result={
                '/server/info':{'klippy_connected':True},
                '/server/files/metadata':{'modified':time.time()-7200,'thumbnails':[{'width':300,'relative_path':'.thumbs/image.png'}]},
                '/server/history/list':{'jobs':[],'count':0}
            }.get(route,{})
            self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(json.dumps({'result':result}).encode())
    moon=ThreadingHTTPServer(('127.0.0.1',0),Moonraker)
    threading.Thread(target=moon.serve_forever,daemon=True).start()
    with tempfile.TemporaryDirectory() as tmp:
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        env={**os.environ,'KCD_DATA_DIR':tmp,'KCD_STORAGE_BACKEND':'sql','KCD_API_KEY':'isolated-browser-key'}
        base=f'http://127.0.0.1:{port}'
        seed="from core.registration import register_printer;register_printer('Fixture','http://127.0.0.1:"+str(moon.server_port)+"')"
        subprocess.run([sys.executable,'-c',seed],cwd=ROOT,env=env,check=True,capture_output=True)
        log=open(Path(tmp)/'server.log','w')
        def start():
            process=subprocess.Popen([sys.executable,'-m','kcd.server','--port',str(port)],env=env,cwd=ROOT,stdout=log,stderr=log)
            for _ in range(100):
                try:urllib.request.urlopen(base+'/health',timeout=2).close();return process
                except OSError:time.sleep(.1)
            raise RuntimeError('Fixture server failed to start')
        def stop(process):
            process.terminate()
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.kill();process.wait()
        def post(path,data):
            req=urllib.request.Request(base+path,data=json.dumps(data).encode(),headers={'Content-Type':'application/json','X-API-Key':'isolated-browser-key'})
            with urllib.request.urlopen(req,timeout=15) as response:return json.load(response)
        server=start()
        try:
            started=post('/job-start',dict(printer_name='Fixture',filename='part.gcode',start_time=time.time()-3600))
            digest=started['job']['thumbnail_asset'];assert len(digest)==64
            # Both G-code and slicer thumbnail disappear before completion.
            Moonraker.available=False
            post('/log-print',dict(printer='Fixture',filename='part.gcode',duration_seconds=3600,filament_mm=100000,timestamp=time.time(),event_id=str(uuid.uuid4())))
            with closing(sqlite3.connect(Path(tmp)/'kcd.db')) as conn:
                uid=conn.execute('SELECT job_uid FROM jobs').fetchone()[0]
                assert conn.execute('SELECT asset_sha256 FROM job_thumbnails WHERE job_uid=?',(uid,)).fetchone()[0]==digest
            stop(server)
            # Preserve the historical identity while the printer gets a new URL.
            subprocess.run([sys.executable,'-c',"from core import db,projects;from core.registration import register_printer;register_printer('Fixture','http://127.0.0.1:1',relocate=True);p=projects.create_project('Archived fixture');projects.assign_jobs(['"+uid+"'],p.id)"],cwd=ROOT,env=env,check=True,capture_output=True)
            server=start()
            with urllib.request.urlopen(base+'/thumbnails/'+digest) as response:
                assert response.read()==PNG and response.headers['X-KCD-Thumbnail-Status']=='archived'
            with sync_playwright() as pw:
                browser=pw.chromium.launch();page=browser.new_page(viewport={'width':1440,'height':1000})
                for path,label in [('/','dashboard'),('/reports','reports'),('/projects','projects')]:
                    page.goto(base+path,wait_until='domcontentloaded')
                    images=page.locator('img[src="/thumbnails/'+digest+'"]')
                    assert images.count()>0,(path,page.locator('body').inner_text()[:500])
                    image=images.first
                    if path=='/projects':
                        # Bootstrap project details are collapsed initially.
                        for target in page.locator('[data-bs-toggle=collapse]').all():
                            destination=target.get_attribute('data-bs-target') or ''
                            if destination.startswith('#proj-'):
                                target.click();break
                    image.scroll_into_view_if_needed()
                    image.evaluate("e=>{e.loading='eager'}")
                    page.wait_for_function('(src)=>Array.from(document.images).some(e=>e.getAttribute("src")===src && e.complete && e.naturalWidth>0)',arg='/thumbnails/'+digest)
                    assert image.evaluate('(e)=>e.naturalWidth')==1
                    page.screenshot(path=str(artifacts/('archived-thumbnail-'+label+'.png')))
                browser.close()
            print('Browser thumbnail regression passed: captured at start, source deleted before completion, stable job reference, changed printer URL, server restart, Dashboard/History/Reports/Projects rendering')
        except Exception:
            log.flush();print(Path(tmp,'server.log').read_text(errors='replace')[-5000:]);raise
        finally:
            stop(server);log.close();moon.shutdown();moon.server_close()


if __name__=='__main__':main()
