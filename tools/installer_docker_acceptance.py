"""Actual Docker/source authority test using a disposable bind-mounted Master."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid


def main():
    root=Path(__file__).resolve().parents[1];name='kcd-installer-'+uuid.uuid4().hex[:10]
    subprocess.run(['docker','build','-q','-t','kcd-installer-master-fixture',str(root)],check=True,stdout=subprocess.DEVNULL)
    with tempfile.TemporaryDirectory() as tmp:
        data=Path(tmp)/'data';data.mkdir()
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        base='http://127.0.0.1:'+str(port)
        try:
            subprocess.run(['docker','run','-d','--name',name,'-p',f'127.0.0.1:{port}:5000','-e','KCD_STORAGE_BACKEND=sql','-e','KCD_API_KEY=isolated-docker-key','-v',str(data)+':/app/data','kcd-installer-master-fixture'],check=True,stdout=subprocess.DEVNULL)
            for _ in range(100):
                try:
                    with urllib.request.urlopen(base+'/health',timeout=2) as response:
                        assert json.load(response)['ready'];break
                except OSError:time.sleep(.1)
            else:raise RuntimeError('Disposable Docker Master not ready')
            env={**os.environ,'KCD_DATA_DIR':str(data),'KCD_API_KEY':'isolated-docker-key'}
            for key in ('KCD_STORAGE_BACKEND','KCD_REPORTS_BACKEND','KCD_INSTALLER_SKIP_DOCKER','KCD_MASTER_CONTAINER'):env.pop(key,None)
            run=subprocess.run([sys.executable,'install.py'],input='4\n1\n4\n1\n1\n5\n',cwd=root,env=env,text=True,capture_output=True,timeout=30)
            assert run.returncode==0,run.stdout[-1500:]+run.stderr
            assert 'Storage Backend: sql' in run.stdout and 'Docker Master already owns this data directory' in run.stdout,run.stdout
            assert not (data/'print_costs.csv').exists() and not (data/'install_state.json').exists()
            print('Docker/source acceptance passed: actual bind-mount detection, SQL authority, and interactive second-Master prevention')
        finally:
            subprocess.run(['docker','rm','-f',name],capture_output=True)
            # Fixture database is root-owned when the image runs as root.
            subprocess.run(['docker','run','--rm','-v',tmp+':/fixture','kcd-installer-master-fixture','python','-c',"import os; from pathlib import Path; root=Path('/fixture'); paths=list(root.rglob('*')); [(os.chown(p,"+str(os.getuid())+","+str(os.getgid())+")) for p in paths]; os.chown(root,"+str(os.getuid())+","+str(os.getgid())+")"],check=True,capture_output=True)


if __name__=='__main__':main()
