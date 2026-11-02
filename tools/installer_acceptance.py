"""Actual terminal and browser workflows against disposable HTTP fixtures.

Run through smoke_production.py --installer-acceptance. Never accepts a live URL.
"""
import os
from pathlib import Path
import subprocess
import sys
import json
import sqlite3
from contextlib import closing

ROOT=Path(__file__).resolve().parents[1]


def exercise(env,base,moons,tmp,*,browser=True):
    artifacts=ROOT/'browser-artifacts'/'installer'/env['KCD_STORAGE_BACKEND'];artifacts.mkdir(parents=True,exist_ok=True)
    def record(result):
        with open(artifacts/'terminal.txt','a',encoding='utf-8') as stream:
            stream.write((result.stdout+'\n'+result.stderr).replace(env['KCD_API_KEY'],'[redacted]'))
    def terminal(answers,expected=0):
        result=subprocess.run([sys.executable,'install.py'],input='\n'.join(answers)+'\n',cwd=ROOT,env=env,text=True,capture_output=True,timeout=30)
        record(result)
        assert result.returncode==expected,(result.returncode,result.stdout[-1500:],result.stderr[-400:])
        assert env['KCD_API_KEY'] not in result.stdout+result.stderr,'Credential exposed'
        return result.stdout
    seed="from core import db; c=db.connect_db(); db.upsert_job(c,dict(job_uid='installer-history',printer='One',filename='preserved.gcode',status='completed',duration_seconds=3600,filament_mm=1000,time_cost=40,material_cost=2,total_cost=42)); c.commit(); c.close()"
    subprocess.run([sys.executable,'-c',seed],cwd=ROOT,env=env,check=True,capture_output=True)
    assert 'One' in terminal(['4','2','4','5'])
    assert 'Goodbye' in terminal(['bad','2','b','5'])
    # Real cancellation at the configuration selection prompt.
    assert 'Goodbye' in terminal(['2','1','b','3','5'])
    terminal([],expected=1)  # EOF is bounded; no prompt loop.
    def cli(args,code=0):
        run=subprocess.run([sys.executable,'install.py',*args],cwd=ROOT,env=env,capture_output=True,text=True,timeout=30)
        record(run)
        assert run.returncode==code,(run.stdout,run.stderr)
        assert env['KCD_API_KEY'] not in run.stdout+run.stderr
        return run
    directory=moons[0].config_root
    common=['repair-client','--config-dir',directory,'--printer','One','--moonraker',f'http://127.0.0.1:{moons[0].server_port}','--master',base]
    preview=json.loads(cli(common).stdout)
    assert not preview['changes'],preview['changes']
    answers=['2','1','0',directory,'One',base,'','n','n','y','3','5']
    for _ in range(2 if os.name=='posix' else 0):
        output=terminal(answers)
        assert '"applied": true' in output and '[auto] Moonraker reachable' in output
    if os.name=='posix':terminal(['2','1','0',directory,'One'],expected=1)
    assert not json.loads(cli(common).stdout)['changes']
    if os.name=='posix' and env['KCD_STORAGE_BACKEND']=='sql':
        output=terminal(['3','4','1','0',directory,base,'','n','n','y','6','5'])
        assert '"applied": true' in output
        output=terminal(['3','2','1','','y','','6','5'])
        assert 'Custom hooks and files are retained' in output
        cli(common,2)
        cli(common+['--reactivate','--apply'])
    script=Path(directory)/'kcd_job_pause.sh';script.unlink()
    preview=json.loads(cli(common).stdout);assert any('kcd_job_pause.sh' in change for change in preview['changes'])
    assert not script.exists(),'Dry run modified files'
    cli(common+['--apply']);assert script.exists()
    wrong={**env,'KCD_API_KEY':'incorrect-fixture-key'}
    rejected=subprocess.run([sys.executable,'install.py',*common,'--apply'],env=wrong,cwd=ROOT,capture_output=True,text=True)
    assert rejected.returncode==2 and 'authentication failed' in rejected.stderr
    other=common.copy();other[other.index('--moonraker')+1]=f'http://127.0.0.1:{moons[1].server_port}'
    assert 'different config directory' in cli(other,2).stderr
    unreachable=common.copy();unreachable[unreachable.index('--master')+1]='http://127.0.0.1:1'
    assert 'Master network/response failure' in cli(unreachable,2).stderr
    unreachable=common.copy();unreachable[unreachable.index('--moonraker')+1]='http://127.0.0.1:1'
    assert 'Moonraker discovery failed' in cli(unreachable,2).stderr
    clone=other.copy();clone[clone.index('--config-dir')+1]=moons[1].config_root
    before=(Path(moons[1].config_root)/'print_cost.cfg').read_bytes()
    assert 'explicit relocation' in cli(clone+['--apply'],2).stderr
    assert (Path(moons[1].config_root)/'print_cost.cfg').read_bytes()==before
    # Fail after two actual writes, then converge with the ordinary repair.
    (Path(directory)/'kcd_job_start.sh').unlink();script.unlink()
    code="""import os
from installer import workflow
real=workflow.atomic_write
count=0
def interrupted(*args,**kwargs):
 global count
 count+=1
 if count==3:raise PermissionError('injected fixture permission failure')
 return real(*args,**kwargs)
workflow.atomic_write=interrupted
try:workflow.repair_client(CONFIG,'One',MOON,MASTER,os.environ['KCD_API_KEY'],apply=True)
except RuntimeError as exc:print(str(exc))
""".replace('CONFIG',repr(directory)).replace('MOON',repr(common[common.index('--moonraker')+1])).replace('MASTER',repr(base))
    fault=subprocess.run([sys.executable,'-c',code],cwd=ROOT,env=env,text=True,capture_output=True,check=True)
    assert 'client files incomplete' in fault.stdout and not script.exists()
    cli(common+['--apply']);assert script.exists()
    with closing(sqlite3.connect(Path(env['KCD_DATA_DIR'])/'kcd.db')) as conn:
        conn.execute("CREATE TRIGGER installer_failure BEFORE UPDATE ON printers BEGIN SELECT RAISE(ABORT,'fixture database failure'); END");conn.commit()
    try:cli(common+['--apply'],2)
    finally:
        with closing(sqlite3.connect(Path(env['KCD_DATA_DIR'])/'kcd.db')) as conn:conn.execute('DROP TRIGGER installer_failure');conn.commit()
    cli(common+['--apply'])
    # Active vs inactive alternatives and repeated patching.
    root=Path(directory);(root/'inactive.cfg').write_text('[gcode_macro UNUSED_START]\ngcode:\n    G28\n')
    args=['macro-plan','--config-dir',directory,'--file','printer.cfg','--macro','PRINT_START','--event','start']
    assert 'KCD_JOB_START' in cli(args).stdout
    cli(args+['--apply']);once=(root/'printer.cfg').read_bytes();cli(args+['--apply']);assert (root/'printer.cfg').read_bytes()==once
    inactive=args.copy();inactive[inactive.index('--file')+1]='inactive.cfg';inactive[inactive.index('--macro')+1]='UNUSED_START';cli(inactive,2)
    with closing(sqlite3.connect(Path(env['KCD_DATA_DIR'])/'kcd.db')) as conn:
        assert conn.execute('SELECT COUNT(*) FROM printers').fetchone()[0]==2
        assert conn.execute("SELECT total_cost FROM jobs WHERE job_uid='installer-history'").fetchone()[0]==42
    if not browser:
        print("Terminal installer acceptance passed (browser checks not requested in this invocation)")
        return
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser=pw.chromium.launch();context=browser.new_context();context.tracing.start(screenshots=True,snapshots=True,sources=True)
        page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        try:
            for width,height,zoom,label in [(1440,1000,1,'desktop'),(768,1000,1,'tablet'),(390,844,1,'mobile'),(1440,1000,2,'zoom200')]:
                page.set_viewport_size({'width':width,'height':height})
                page.goto(base+'/settings/printers',wait_until='domcontentloaded')
                page.evaluate('(z)=>document.body.style.zoom=z',zoom)
                assert page.get_by_role('heading',name='One',exact=True).count()==1
                assert page.get_by_role('heading',name='Two',exact=True).count()==1
                defaults=page.locator('form').filter(has=page.locator('input[name=printer][value=One]')).filter(has=page.locator('[name=rate_per_hour]'))
                assert defaults.locator('[name=rate_per_hour]').input_value()=='1.0'
                assert defaults.locator('[name=grams_per_meter]').input_value()=='3.0'
                rate=defaults.get_by_label('Rate per hour ($/hr)',exact=True)
                assert rate.is_visible() and rate.is_enabled()
                rate.focus();assert rate.evaluate('(e)=>document.activeElement===e')
                endpoint=defaults.get_by_label('Moonraker URL',exact=True)
                assert endpoint.is_visible() and endpoint.is_enabled()
                endpoint.focus();assert endpoint.evaluate('(e)=>document.activeElement===e')
                assert page.get_by_role('heading',level=5).all_text_contents()==['One','Two']
                page.screenshot(path=str(artifacts/(label+'.png')),full_page=True)
            page.evaluate("document.body.style.zoom=1")
            page.goto(base+'/settings/printers')
            form=page.locator('form').filter(has=page.locator('input[name=printer][value=One]')).filter(has=page.locator('[name=rate_per_hour]'))
            form.locator('[name=moonraker_url]').fill('http://127.0.0.1:1')
            with page.expect_navigation(wait_until='domcontentloaded'):form.get_by_role('button',name='Test Moonraker',exact=True).click()
            assert 'Moonraker test failed for One' in page.get_by_role('alert').inner_text()
            page.screenshot(path=str(artifacts/'diagnostic-failure.png'),full_page=True)
            form=page.locator('form').filter(has=page.locator('input[name=printer][value=One]')).filter(has=page.locator('[name=rate_per_hour]'))
            form.locator('[name=moonraker_url]').fill(common[common.index('--moonraker')+1])
            with page.expect_navigation(wait_until='domcontentloaded'):form.get_by_role('button',name='Test Moonraker',exact=True).click()
            assert 'Moonraker OK for One' in page.get_by_role('alert').inner_text()
            page.goto(base+'/printer-diagnostics');assert page.locator('body').inner_text().count('One')>=1
            assert not errors,errors
        except Exception:
            page.screenshot(path=str(artifacts/'failure.png'),full_page=True)
            raise
        finally:
            context.tracing.stop(path=str(artifacts/'trace.zip'));browser.close()
    print('Installer acceptance passed ('+env['KCD_STORAGE_BACKEND']+'): real menu/cancel/EOF, inventory, idempotency, missing-file repair, wrong credential/port, macro preview/inactive protection and four browser layouts')
