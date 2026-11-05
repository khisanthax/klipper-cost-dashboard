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
from core import db,thumbnail_archive as archive,live,profiles,rates,projects,project_pricing
from core.registration import register_printer
register_printer('Preserved','http://127.0.0.1:1')
filament=profiles.add_profile(dict(id='preserved-filament',name='Preserved PETG',material='PETG',filament_mode='per_kg',filament_rate=15,cost_per_kg=15,grams_per_meter=3))
hourly=rates.upsert_rate_profile(dict(id='preserved-rate',name='Preserved zero rate',rate_per_hour=0))
profiles.set_printer_active_profile('Preserved',filament)
with db.connect_db() as c:
    db.upsert_job(c,dict(job_uid='stable-history',printer='Preserved',filename='deleted.gcode',status='completed',timestamp=1700000000,duration_seconds=3600,duration_hours=1,filament_mm=1000,filament_meters=1,grams_per_meter=3,filament_profile_id=filament,hourly_rate_profile_id=hourly,time_cost=0,material_cost=0,total_cost=0))
    db.upsert_job(c,dict(job_uid='stable-canceled',printer='Preserved',filename='same.gcode',status='canceled',timestamp=1700000300,duration_seconds=30,filament_mm=15,time_cost=None,material_cost=None,total_cost=None))
    original=dict(c.execute("SELECT * FROM jobs WHERE job_uid='stable-history'").fetchone())
    c.execute('INSERT INTO job_cost_originals VALUES (?,?,?)',('stable-history',json.dumps(original,sort_keys=True),db._utc_now_iso()))
project=projects.create_project('Preserved project',notes='Upgrade and rollback business graph',hourly_rate_override=0,filament_cost_per_kg_override=15)
projects.assign_jobs(['stable-history','stable-canceled'],project.id)
projects.create_manual_job(project.id,'Preserved manual job',hours=.5,filament_g=15,cost_override=0)
projects.create_plan_item(project.id,'planned.gcode',est_time_s=120,est_filament_g=6,est_cost_override=0)
# Snapshot only the fully evidenced job; the canceled row remains unpriced.
projects.unassign_jobs(['stable-canceled'])
preview=project_pricing.preview(project.id);project_pricing.apply(project.id,preview['fingerprint'])
projects.assign_jobs(['stable-canceled'],project.id)
image=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a8t0AAAAASUVORK5CYII=')
sha=archive.retain_image(image);archive.capture_job('stable-history','Preserved','deleted.gcode',live_asset=sha)
live.start_job('Preserved','active.gcode',start_time=1790000000)
print(sha)
"""


CANDIDATE_SEED = """import json
from core import db
with db.connect_db() as c:
    tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    project=c.execute("SELECT project_uid FROM projects WHERE name='Preserved project'").fetchone()[0]
    printer=c.execute("SELECT id FROM printers WHERE name='Preserved'").fetchone()[0]
seeded=[]
if 'business_orders' in tables:
    from core import orders
    orders.save_order(project,dict(status='active',priority='normal',customer='Synthetic upgrade fixture',quote='10.00',selling_price='9.99',revenue='8.00',expenses='0',estimated_cost='1.00'))
    assert orders.get_order(project)['status']=='active'
    seeded.append('business_orders')
if 'filament_spools' in tables:
    from core import inventory
    spool,_=inventory.create_spool('00000000-0000-4000-8000-000000000001',manufacturer='Synthetic fixture',material='PETG',color='Blue',net_grams='1000',remaining_grams='800',low_stock_grams='100',purchase_price='15',grams_per_meter='3')
    inventory.assign_spool(spool,printer)
    inventory.record_consumption(spool,'00000000-0000-4000-8000-000000000002','stable-history','3',verified=True)
    inventory.adjust_stock(spool,'00000000-0000-4000-8000-000000000003','-0.125','Synthetic measured correction')
    seeded.extend(['filament_spools','filament_stock_events'])
print(json.dumps(seeded))
"""


SNAPSHOT = """import hashlib,json,sqlite3,sys
from pathlib import Path
def identifier(value):return '"'+value.replace('"','""')+'"'
def canonical(rows):return sorted([list(row) for row in rows],key=lambda row:json.dumps(row,sort_keys=True,default=str,separators=(',',':')))
c=sqlite3.connect(sys.argv[1])
tables={}
for (name,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall():
    tables[name]={'columns':canonical(c.execute('PRAGMA table_info('+identifier(name)+')')),'rows':canonical(c.execute('SELECT * FROM '+identifier(name)))}
objects=Path(sys.argv[2])
schema={r[0]:list(r[1:]) for r in c.execute("SELECT name,type,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY name")}
state={'tables':tables,'schema':schema,'objects':{p.name:[p.stat().st_size,hashlib.sha256(p.read_bytes()).hexdigest()] for p in sorted(objects.glob('*')) if p.is_file()},'integrity':[r[0] for r in c.execute('PRAGMA integrity_check')],'foreign_keys':canonical(c.execute('PRAGMA foreign_key_check'))}
c.close()
print(json.dumps(state,sort_keys=True,default=str))
"""


def assert_preserved(before, after):
    """Preserve every baseline table; only migration ledger additions are allowed."""
    for state in (before, after):
        assert state['integrity'] == ['ok'], 'SQL integrity check failed'
        assert not state['foreign_keys'], 'SQL foreign-key check failed'
    assert before['objects'] == after['objects'], 'Permanent thumbnail object inventory changed'
    for name, definition in before['schema'].items():
        assert after['schema'].get(name) == definition, 'Existing SQL schema changed: '+name
    for name, table in before['tables'].items():
        assert name in after['tables'], 'Existing SQL table was lost: '+name
        current=after['tables'][name]
        assert table['columns'] == current['columns'], 'Existing SQL columns changed: '+name
        if name == 'schema_migrations':
            # A candidate may add ledger rows, but cannot replace/delete an
            # applied baseline migration or silently alter its checksum/time.
            assert all(row in current['rows'] for row in table['rows']), 'Baseline migration evidence changed'
        else:
            assert table['rows'] == current['rows'], 'Existing SQL rows changed: '+name


def assert_rollback_preserved(upgraded, rolled_back):
    assert_preserved(upgraded, rolled_back)
    assert rolled_back == upgraded, 'Rollback changed post-upgrade SQL tables, schema or thumbnail objects'


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
            def snapshot(image):
                return json.loads(run(['docker','run','--rm',*mount,image,'python','-c',SNAPSHOT,'/app/data/kcd.db','/app/data/thumbnails/objects']).stdout)
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
                current=snapshot(image)
                if phase!='rollback':assert_preserved(before,current)
                if phase=='candidate':
                    # Verify migration preserved baseline state before adding
                    # explicit new-feature fixture records/audit evidence.
                    seeded=json.loads(run(['docker','run','--rm',*mount,image,'python','-c',CANDIDATE_SEED]).stdout)
                    print('Post-upgrade nonempty feature fixtures: '+(', '.join(seeded) if seeded else 'none available in this image'))
                    upgraded=snapshot(image)
                if phase=='rollback':assert_rollback_preserved(upgraded,current)
            print('Docker '+args.baseline+' -> candidate -> rollback passed: every baseline SQL table, nonempty project/manual/plan/profile/assignment/pricing graph, zero and NULL history prices, all thumbnail objects, retained live state and all post-upgrade tables preserved')
        finally:
            subprocess.run(['docker','rm','-f',name],capture_output=True)
            # Only the disposable, resolved fixture tree is mounted for cleanup.
            run(['docker','run','--rm','-v',str(data)+':/fixture',args.image,'python','-c',"import os; from pathlib import Path; p=Path('/fixture'); [os.chown(f,"+str(os.getuid())+","+str(os.getgid())+") for f in p.rglob('*')]; os.chown(p,"+str(os.getuid())+","+str(os.getgid())+")"])


if __name__=='__main__':main()
