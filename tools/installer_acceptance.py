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
    artifacts=ROOT/'browser-artifacts'/'installer';artifacts.mkdir(parents=True,exist_ok=True)
    def terminal(answers,expected=0):
        result=subprocess.run([sys.executable,'install.py'],input='\n'.join(answers)+'\n',cwd=ROOT,env=env,text=True,capture_output=True,timeout=30)
        assert result.returncode==expected,(result.returncode,result.stdout[-1500:],result.stderr[-400:])
        assert env['KCD_API_KEY'] not in result.stdout+result.stderr,'Credential exposed'
        with open(artifacts/'terminal.txt','a',encoding='utf-8') as stream:stream.write(result.stdout)
        return result.stdout
    assert 'One' in terminal(['4','2','4','5'])
    assert 'Goodbye' in terminal(['bad','2','b','5'])
    # Real cancellation at the configuration selection prompt.
    assert 'Goodbye' in terminal(['2','1','b','3','5'])
    terminal([],expected=1)  # EOF is bounded; no prompt loop.
    def cli(args,code=0):
        run=subprocess.run([sys.executable,'install.py',*args],cwd=ROOT,env=env,capture_output=True,text=True,timeout=30)
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
    if os.name=='posix':
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
                page.screenshot(path=str(artifacts/(label+'.png')),full_page=True)
            page.goto(base+'/printer-diagnostics');assert page.locator('body').inner_text().count('One')>=1
            assert not errors,errors
        finally:
            context.tracing.stop(path=str(artifacts/'trace.zip'));browser.close()
    print('Installer acceptance passed: real menu/cancel/EOF, SQL inventory, idempotency, missing-file repair, wrong credential/port, macro preview/inactive protection and four browser layouts')
