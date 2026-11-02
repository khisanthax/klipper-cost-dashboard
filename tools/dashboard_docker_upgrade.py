"""Verify the actual v0.7.2 -> candidate image upgrade on isolated SQL/objects."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import urllib.request
import uuid

BASELINE='ghcr.io/khisanthax/klipper-cost-dashboard:v0.7.2@sha256:63521ccbffb11963fbd0c2ea975e83e3571c4302089fd74c73043ce34a4378b8'
SEED="""import base64,json,hashlib
from core import db,thumbnail_archive as archive,live
from core.registration import register_printer
register_printer('Preserved','http://127.0.0.1:1')
with db.connect_db() as c:db.upsert_job(c,dict(job_uid='stable-history',printer='Preserved',filename='deleted.gcode',status='completed',duration_seconds=3600,filament_mm=1000,grams_per_meter=3,time_cost=0,material_cost=0,total_cost=0))
image=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a8t0AAAAASUVORK5CYII=')
sha=archive.retain_image(image);archive.capture_job('stable-history','Preserved','deleted.gcode',live_asset=sha)
live.start_job('Preserved','active.gcode',start_time=1790000000)
print(sha)
"""


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--image',required=True);args=parser.parse_args()
    if os.name!='posix':raise SystemExit('Use the disposable Linux Docker CI fixture')
    def run(command,**kwargs):return subprocess.run(command,check=True,capture_output=True,text=True,**kwargs)
    run(['docker','pull',BASELINE],timeout=180)
    with tempfile.TemporaryDirectory(prefix='kcd-upgrade-') as tmp:
        data=Path(tmp)/'data';data.mkdir();name='kcd-upgrade-'+uuid.uuid4().hex[:10]
        mount=['-v',str(data)+':/app/data','-e','KCD_STORAGE_BACKEND=sql','-e','KCD_API_KEY=isolated-upgrade-key']
        try:
            sha=run(['docker','run','--rm',*mount,BASELINE,'python','-c',SEED]).stdout.strip().splitlines()[-1]
            def snapshot(image):
                code="import sqlite3,json,hashlib; c=sqlite3.connect('/app/data/kcd.db'); print(hashlib.sha256(json.dumps({t:c.execute('SELECT * FROM '+t+' ORDER BY 1').fetchall() for t in ['jobs','printers','job_thumbnails','thumbnail_assets','user_settings']},sort_keys=True,default=str).encode()).hexdigest())"
                return run(['docker','run','--rm',*mount,image,'python','-c',code]).stdout.strip()
            before=snapshot(BASELINE)
            for image in (BASELINE,args.image):
                with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
                run(['docker','run','-d','--name',name,'-p',f'127.0.0.1:{port}:5000',*mount,image])
                for _ in range(100):
                    try:
                        with urllib.request.urlopen(f'http://127.0.0.1:{port}/health',timeout=2) as r:assert json.load(r)['ready']
                        break
                    except OSError:time.sleep(.1)
                else:raise RuntimeError('Upgrade image did not become ready')
                with urllib.request.urlopen(f'http://127.0.0.1:{port}/jobs/stable-history/thumbnail') as r:assert hashlib.sha256(r.read()).hexdigest()==sha
                run(['docker','stop','--time','10',name]);run(['docker','rm',name])
                assert snapshot(image)==before,'Image startup/shutdown changed stored history, objects or live state'
            print('Docker v0.7.2 -> candidate upgrade passed: exact SQL state, historical zero prices, archived thumbnail and retained live job survive actual image replacement')
        finally:
            subprocess.run(['docker','rm','-f',name],capture_output=True)
            # Only the disposable, resolved fixture tree is mounted for cleanup.
            run(['docker','run','--rm','-v',str(data)+':/fixture',args.image,'python','-c',"import os; from pathlib import Path; p=Path('/fixture'); [os.chown(f,"+str(os.getuid())+","+str(os.getgid())+") for f in p.rglob('*')]; os.chown(p,"+str(os.getuid())+","+str(os.getgid())+")"])


if __name__=='__main__':main()
