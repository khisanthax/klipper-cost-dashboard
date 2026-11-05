"""Isolated real-HTTP smoke test for fresh SQL startup, registration and job lifecycle."""
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
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main():
    with tempfile.TemporaryDirectory() as tmp:
        data=Path(tmp)/'data'
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
        env={**os.environ,'KCD_STORAGE_BACKEND':'sql','KCD_REPORTS_BACKEND':'sql',
             'KCD_DATA_DIR':str(data),'KCD_API_KEY':'isolated-smoke-key','KCD_SQL_ONLY_FAIL_FAST':'1'}
        if '--compat-backend' in sys.argv:
            backend=sys.argv[sys.argv.index('--compat-backend')+1]
            assert backend in ('csv','dual')
            env['KCD_STORAGE_BACKEND']=backend;env['KCD_REPORTS_BACKEND']='auto' if backend=='dual' else 'csv'
        base=f'http://127.0.0.1:{port}'
        class Moonraker(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                route=self.path.split('?')[0]
                result={"/server/info":{"klippy_connected":True},
                        "/machine/system_info":{"system_info":{"network":{"eth0":{"mac_address":"02:01:02:03:04:05"}},"instance_ids":{"moonraker":"moonraker"+str(self.server.server_port)}}},
                        "/server/files/roots":[{"name":"config","path":self.server.config_root}],
                        "/server/history/list":{"jobs":[],"count":0},
                        "/printer/objects/query":{"status":{"print_stats":{"state":"standby"}}}}.get(route,{})
                body=json.dumps({"result":result}).encode()
                self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(body)
        mock=[]
        for name in ['One','Two']:
            directory=Path(tmp)/(name+' printer')/'config';directory.mkdir(parents=True)
            (directory/'printer.cfg').write_text('[gcode_macro PRINT_START]\ngcode:\n    G28\n')
            server=ThreadingHTTPServer(('127.0.0.1',0),Moonraker);server.config_root=str(directory)
            threading.Thread(target=server.serve_forever,daemon=True).start();mock.append(server)
        def request(path,payload=None,key='isolated-smoke-key'):
            req=urllib.request.Request(base+path,data=json.dumps(payload).encode() if payload is not None else None,
                headers={'Content-Type':'application/json','X-API-Key':key})
            with urllib.request.urlopen(req,timeout=15) as response:
                return json.load(response) if response.headers.get('Content-Type','').startswith('application/json') else response.read().decode()
        log=open(Path(tmp)/'server.log','w')
        process=subprocess.Popen([sys.executable,'-m','kcd.server','--port',str(port)],cwd=ROOT,env=env,stdout=log,stderr=log)
        try:
            for _ in range(100):
                try:
                    request('/health');break
                except OSError:
                    if process.poll() is not None:raise RuntimeError('Server failed to start')
                    time.sleep(.1)
            else:raise RuntimeError('Server readiness timed out')
            try:
                request('/api/installer/registration',key='wrong')
                raise AssertionError('Missing authentication enforcement')
            except urllib.error.HTTPError as exc:
                assert exc.code==403
            for name,server in zip(['One','Two'],mock):
                command=[sys.executable,'install.py','repair-client','--config-dir',server.config_root,
                         '--printer',name,'--moonraker',f'http://127.0.0.1:{server.server_port}','--master',base]
                preview=subprocess.run(command,cwd=ROOT,env=env,capture_output=True,text=True,check=True)
                assert 'isolated-smoke-key' not in preview.stdout
                assert not (Path(server.config_root)/'print_cost.cfg').exists()
                for _ in range(2):
                    subprocess.run(command+['--apply'],cwd=ROOT,env=env,capture_output=True,text=True,check=True)
                assert (Path(server.config_root)/'kcd_job_start.sh').exists()
            assert len(request('/api/installer/registration')['clients'])==2
            if '--installer-acceptance' in sys.argv or '--installer-terminal-only' in sys.argv:
                from installer_acceptance import exercise
                exercise(env,base,mock,tmp,browser='--installer-terminal-only' not in sys.argv)
            if '--installer-ssh' in sys.argv or '--installer-ssh-only' in sys.argv:
                from installer_ssh_fixture import exercise as ssh_exercise
                ssh_exercise(env,base,mock[0],tmp,browser_admin='--installer-web-ssh' in sys.argv)
            if '--installer-acceptance' in sys.argv or '--installer-ssh-only' in sys.argv or '--installer-terminal-only' in sys.argv:
                return
            # Model Moonraker replacing a script with mode 0644. Its bash
            # interpreter still executes the diagnostic and restores 0700.
            executable=Path(mock[0].config_root)/'kcd_job_start.sh'
            executable.chmod(0o644)
            bin_dir=Path(tmp)/'bin';bin_dir.mkdir()
            curl=bin_dir/'curl'
            curl.write_text('#!'+sys.executable+'\nimport sys,urllib.request\nargs=sys.argv[1:]\nheader=next(a[1:] for a in args if a.startswith("@"))\nkey=open(header).read().split(":",1)[1].strip()\nurl=args[-1]\nrequest=urllib.request.Request(url,headers={"X-API-Key":key})\nurllib.request.urlopen(request,timeout=5).read()\n')
            curl.chmod(0o700)
            subprocess.run(['bash',str(executable),'--kcd-self-check'],env={**env,'PATH':str(bin_dir)+os.pathsep+os.environ['PATH']},check=True,capture_output=True)
            assert executable.stat().st_mode & 0o777==0o700
            request('/job-start',{'printer_name':'One','filename':'test.gcode','start_time':time.time()-3600})
            request('/job-pause',{'printer_name':'One'})
            request('/job-resume',{'printer_name':'One'})
            event={'event_id':str(uuid.uuid4()),'printer':'One','filename':'test.gcode',
                   'timestamp':time.time(),'duration_seconds':3600,'filament_mm':1000}
            request('/log-print',event)
            assert request('/log-print',event)['duplicate']
            request('/job-start',{'printer_name':'Two','filename':'cancel.gcode','start_time':time.time()-10})
            request('/job-cancel',{'printer_name':'Two','filename':'cancel.gcode'})
            request('/job-cancel',{'printer_name':'Two','filename':'cancel.gcode'})
            for path in ['/','/settings/printers','/reports','/projects','/recalculate','/system-events','/printer-diagnostics']:
                request(path)
            with sqlite3.connect(data/'kcd.db') as conn:
                assert conn.execute('pragma integrity_check').fetchone()[0]=='ok'
                assert conn.execute('select count(*) from jobs').fetchone()[0]==2
                assert conn.execute('select count(*) from printers').fetchone()[0]==2
                total=conn.execute("select total_cost from jobs where filename='test.gcode'").fetchone()[0]
                assert abs(total-1.25)<.01,total
            assert not any((data/f).exists() for f in ['settings.json','install_state.json','print_costs.csv','live_jobs.json'])
            # Docker's SIGTERM must drain Waitress and retain an active SQL job.
            request('/job-start',{'printer_name':'One','filename':'restart.gcode','start_time':time.time()-60})
            stopped_at=time.monotonic()
            process.terminate();process.wait(timeout=8)
            assert process.returncode==0 and time.monotonic()-stopped_at<6
            process=subprocess.Popen([sys.executable,'-m','kcd.server','--port',str(port)],cwd=ROOT,env=env,stdout=log,stderr=log)
            for _ in range(100):
                try:request('/health');break
                except OSError:time.sleep(.1)
            resumed=request('/job-pause',{'printer_name':'One'})
            assert resumed['success'] and resumed['job']['filename']=='restart.gcode'
            request('/job-resume',{'printer_name':'One'})
            request('/job-end',{'printer_name':'One'})
            from installer import utils
            for generator in [utils.generate_job_start_script,utils.generate_job_end_script,utils.generate_job_pause_script,utils.generate_job_resume_script,utils.generate_job_cancel_script]:
                script=generator('http://master:6060',"test-'-$-key")
                subprocess.run(['bash','-n'],input=script,text=True,check=True)
            print('Production smoke passed: fresh SQL HTTP server, two printers, repeat registration, auth, pause/resume, completion dedup, cancellation, pages, costs, integrity, graceful SIGTERM/live-job restart and shell syntax')
        finally:
            process.terminate()
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.kill();process.wait()
            log.close()
            for server in mock:
                server.shutdown();server.server_close()
            if sys.exc_info()[0]:
                print((Path(tmp)/'server.log').read_text(),file=sys.stderr)


if __name__=='__main__':main()
