"""Verify pinned release -> candidate -> rollback on isolated SQL/objects."""
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

BASELINES={
    'v0.7.2':'ghcr.io/khisanthax/klipper-cost-dashboard:v0.7.2@sha256:63521ccbffb11963fbd0c2ea975e83e3571c4302089fd74c73043ce34a4378b8',
    'v0.7.3':'ghcr.io/khisanthax/klipper-cost-dashboard:v0.7.3@sha256:006588a2d1f50ca2db977a950cdae56266d76aa166d7279d5b1e7dea0264eb0d',
}
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
    parser=argparse.ArgumentParser();parser.add_argument('--image',required=True)
    parser.add_argument('--baseline',choices=BASELINES,default='v0.7.3');args=parser.parse_args()
    baseline=BASELINES[args.baseline]
    if os.name!='posix':raise SystemExit('Use the disposable Linux Docker CI fixture')
    def run(command,**kwargs):return subprocess.run(command,check=True,capture_output=True,text=True,**kwargs)
    run(['docker','pull',baseline],timeout=180)
    with tempfile.TemporaryDirectory(prefix='kcd-upgrade-') as tmp:
        data=Path(tmp)/'data';data.mkdir();name='kcd-upgrade-'+uuid.uuid4().hex[:10]
        mount=['-v',str(data)+':/app/data','-e','KCD_STORAGE_BACKEND=sql','-e','KCD_API_KEY=isolated-upgrade-key']
        try:
            sha=run(['docker','run','--rm',*mount,baseline,'python','-c',SEED]).stdout.strip().splitlines()[-1]
            def snapshot(image,all_tables=False):
                tables="[r[0] for r in c.execute(\"SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name\")]" if all_tables else "['jobs','printers','job_thumbnails','thumbnail_assets','user_settings']"
                code="import sqlite3,json,hashlib; c=sqlite3.connect('/app/data/kcd.db'); tables="+tables+"; print(hashlib.sha256(json.dumps({t:c.execute('SELECT * FROM '+t+' ORDER BY 1').fetchall() for t in tables},sort_keys=True,default=str).encode()).hexdigest())"
                return run(['docker','run','--rm',*mount,image,'python','-c',code]).stdout.strip()
            before=snapshot(baseline)
            upgraded=None
            for phase,image in [('baseline',baseline),('candidate',args.image),('rollback',baseline)]:
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
                if phase=='candidate':upgraded=snapshot(image,all_tables=True)
                if phase=='rollback':assert snapshot(image,all_tables=True)==upgraded,'Rollback changed post-upgrade SQL tables'
            print('Docker '+args.baseline+' -> candidate -> rollback passed: exact SQL state, historical zero prices, archived thumbnail, retained live job and post-upgrade tables survive actual image replacement')
        finally:
            subprocess.run(['docker','rm','-f',name],capture_output=True)
            # Only the disposable, resolved fixture tree is mounted for cleanup.
            run(['docker','run','--rm','-v',str(data)+':/fixture',args.image,'python','-c',"import os; from pathlib import Path; p=Path('/fixture'); [os.chown(f,"+str(os.getuid())+","+str(os.getgid())+") for f in p.rglob('*')]; os.chown(p,"+str(os.getuid())+","+str(os.getgid())+")"])


if __name__=='__main__':main()
