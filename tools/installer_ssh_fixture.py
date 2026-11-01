"""Disposable, loopback-only SSH target; no production host configuration."""
from pathlib import Path
import os
import shutil
import socket
import subprocess
import time
import uuid


def exercise(env,base,moon,tmp):
    assert os.name=='posix','SSH Docker fixture requires Linux'
    root=Path(tmp);key=root/'fixture-key';config=root/'ssh-config';known=root/'known-hosts'
    real_ssh=shutil.which('ssh');real_scp=shutil.which('scp')
    assert real_ssh and real_scp
    def run(args,**kwargs):return subprocess.run(args,check=True,capture_output=True,text=True,**kwargs)
    run(['ssh-keygen','-q','-t','ed25519','-N','','-f',str(key)])
    build=root/'ssh-image';build.mkdir()
    (build/'Dockerfile').write_text('FROM python:3.11-slim\nRUN apt-get update && apt-get install -y --no-install-recommends openssh-server && rm -rf /var/lib/apt/lists/* && mkdir -p /run/sshd /root/.ssh && ssh-keygen -A\nCMD ["sleep","600"]\n')
    run(['docker','build','-t','kcd-installer-ssh-fixture',str(build)],timeout=180)
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    name='kcd-acceptance-'+uuid.uuid4().hex[:10]
    try:
        run(['docker','run','-d','--name',name,'--network','host','-v',str(root)+':'+str(root),'kcd-installer-ssh-fixture'])
        run(['docker','cp',str(key)+'.pub',name+':/root/.ssh/authorized_keys'])
        run(['docker','exec',name,'chown','root:root','/root/.ssh/authorized_keys'])
        run(['docker','exec',name,'chmod','600','/root/.ssh/authorized_keys'])
        pub=run(['docker','exec',name,'cat','/etc/ssh/ssh_host_ed25519_key.pub']).stdout.strip()
        known.write_text('[127.0.0.1]:'+str(port)+' '+pub+'\n')
        config.write_text(f'Host kcd-fixture\n HostName 127.0.0.1\n Port {port}\n User root\n IdentityFile {key}\n UserKnownHostsFile {known}\n StrictHostKeyChecking yes\n IdentitiesOnly yes\n')
        run(['docker','exec','-d',name,'/usr/sbin/sshd','-D','-e','-p',str(port),'-o','ListenAddress=127.0.0.1','-o','PasswordAuthentication=no','-o','PermitRootLogin=prohibit-password'])
        for _ in range(50):
            probe=subprocess.run([real_ssh,'-F',str(config),'kcd-fixture','true'],capture_output=True)
            if probe.returncode==0:break
            time.sleep(.1)
        else:raise RuntimeError('Disposable SSH server did not start: '+probe.stderr.decode(errors='replace')[-1500:])
        wrappers=root/'bin-ssh';wrappers.mkdir()
        for binary,actual in [('ssh',real_ssh),('scp',real_scp)]:
            path=wrappers/binary
            import shlex
            path.write_text('#!/bin/sh\nexec '+shlex.quote(actual)+' -F '+shlex.quote(str(config))+' "$@"\n');path.chmod(0o700)
        from installer.remote_workflow import remote_operation
        old_path=os.environ['PATH'];os.environ['PATH']=str(wrappers)+os.pathsep+old_path
        try:
            payload=dict(config_dir=moon.config_root,name='One',moonraker=f'http://127.0.0.1:{moon.server_port}',master=base,key=env['KCD_API_KEY'],installation_type='remote',host='kcd-fixture',relocate=True)
            before=(Path(moon.config_root)/'printer.cfg').read_bytes()
            assert not remote_operation('kcd-fixture','repair',payload)['applied']
            assert (Path(moon.config_root)/'printer.cfg').read_bytes()==before
            payload['apply']=True
            assert remote_operation('kcd-fixture','repair',payload)['applied']
            assert remote_operation('kcd-fixture','repair',payload)['changes']==[]
            payload['key']='wrong-fixture-credential'
            try:remote_operation('kcd-fixture','repair',payload);raise AssertionError('Wrong key accepted')
            except RuntimeError:pass
        finally:os.environ['PATH']=old_path
        print('SSH acceptance passed: actual OpenSSH, strict host-key verification, remote preview/apply/repeat and authentication failure; no Flask required on target')
    finally:
        subprocess.run(['docker','rm','-f',name],capture_output=True)
