"""Real source-service install on the disposable GitHub Actions VM only."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import urllib.request
import uuid


def main():
    if os.environ.get('GITHUB_ACTIONS')!='true':
        raise SystemExit('Native systemd fixture is restricted to the disposable CI VM')
    root=Path(__file__).resolve().parents[1]
    artifacts=root/'browser-artifacts/installer';artifacts.mkdir(parents=True,exist_ok=True)
    service='kcd-acceptance-'+uuid.uuid4().hex[:12]
    unit='/etc/systemd/system/'+service+'.service'
    with tempfile.TemporaryDirectory(prefix='kcd-systemd-') as tmp:
        source=Path(tmp)/'source'
        shutil.copytree(root,source,ignore=shutil.ignore_patterns('.git','.venv','data','browser-artifacts','__pycache__','audit-*'))
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        base='http://127.0.0.1:'+str(port)
        env={**os.environ,'KCD_DATA_DIR':str(Path(tmp)/'data'),'KCD_STORAGE_BACKEND':'sql','KCD_REPORTS_BACKEND':'sql','KCD_INSTALLER_SKIP_DOCKER':'1','KCD_API_KEY':'isolated-source-service-key'}
        def terminal(answers):
            command=['sudo','-E',sys.executable,'install.py']
            result=subprocess.run(command,cwd=source,env=env,input='\n'.join(answers)+'\n',text=True,capture_output=True,timeout=180)
            assert env['KCD_API_KEY'] not in result.stdout+result.stderr
            with open(artifacts/'systemd-terminal.txt','a') as stream:stream.write(result.stdout+result.stderr)
            assert result.returncode==0,result.stdout[-2000:]+result.stderr[-1000:]
            return result.stdout
        def verify():
            with urllib.request.urlopen(base+'/health',timeout=5) as response:assert json.load(response)['ready']
            req=urllib.request.Request(base+'/api/installer/registration',headers={'X-API-Key':env['KCD_API_KEY']})
            with urllib.request.urlopen(req,timeout=5) as response:assert json.load(response)['backend']=='sql'
        try:
            assert 'OK: Master install complete' in terminal(['1','1','n','127.0.0.1',str(port),base,service,'5'])
            verify()
            assert 'SKIP: Master install skipped' in terminal(['1','1','y','b','5'])
            verify()
            assert 'OK: Master install complete' in terminal(['1','1','y','f','5'])
            verify()
            print('Source systemd acceptance passed: real interactive fresh install, saved automatic mode, repeat/status and forced upgrade; SQL readiness and intended API credential verified')
        finally:
            subprocess.run(['sudo','systemctl','disable','--now',service],capture_output=True)
            assert unit.startswith('/etc/systemd/system/kcd-acceptance-')
            subprocess.run(['sudo','rm','-f','--',unit],check=True)
            subprocess.run(['sudo','systemctl','daemon-reload'],check=True)
            # Root installer created the private fixture files. Return ownership
            # of this exact temporary tree to the CI runner for normal cleanup.
            subprocess.run(['sudo','chown','-R',str(os.getuid())+':'+str(os.getgid()),tmp],check=True)


if __name__=='__main__':main()
