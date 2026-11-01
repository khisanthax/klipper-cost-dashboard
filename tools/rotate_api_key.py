"""Rotate a file-backed Master key with bounded overlap and verified client propagation.

Run on the Docker host from the release checkout. No secret is accepted on the
command line or printed. If propagation fails, both keys remain valid until the
overlap expires; rerun with --resume after fixing connectivity.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def http(url, data=None, headers=None):
    request = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(request, timeout=15) as response:
        return response.read()


def get_json(url):
    return json.loads(http(url))


def upload(base, filename, content):
    boundary = "kcd"+uuid.uuid4().hex
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"root\"\r\n\r\nconfig\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n"
            "Content-Type: text/plain\r\n\r\n").encode()+content.encode()+f"\r\n--{boundary}--\r\n".encode()
    http(base+"/server/files/upload",body,{"Content-Type":"multipart/form-data; boundary="+boundary})


def rotate(args):
    # Configure paths before importing modules that resolve credential locations.
    os.environ["KCD_DATA_DIR"] = str(Path(args.data_dir).resolve())
    os.environ["KCD_STORAGE_BACKEND"] = "sql"
    from core.file_io import atomic_write
    from installer.workflow import master_request
    from installer import utils
    data = Path(args.data_dir).resolve()
    secret = data/"secret.json"
    credentials = json.loads(secret.read_text())
    inspect = json.loads(subprocess.check_output(["docker","inspect",args.container]))[0]
    env = dict(s.split("=",1) for s in inspect["Config"]["Env"] if "=" in s)
    if env.get("KCD_API_KEY"):
        raise RuntimeError("Environment-managed credential detected; rotate at its deployment authority")
    from core.registration import registered_clients
    clients = registered_clients()
    if not clients:
        raise RuntimeError("No SQL clients; refusing credential rotation without a propagation inventory")
    inventory=[]
    for client in clients:
        if client.get("hidden"):
            continue
        base = str(client.get("moonraker_url") or "").rstrip("/")
        state=get_json(base+"/printer/objects/query?print_stats&configfile")["result"]["status"]
        if state.get("print_stats",{}).get("state") in {"printing","paused"}:
            raise RuntimeError("A printer has an active job; retry rotation while idle")
        loaded=state.get("configfile",{}).get("config",{})
        baked=str(loaded.get("gcode_macro _KCD_VARS",{}).get("variable_printer_name","")).strip('"\'')
        if baked != client["printer_name"]:
            raise RuntimeError("Loaded Klipper identity does not match SQL; repair the instance before rotating")
        listing=get_json(base+"/server/files/list?root=config")["result"]
        names={item["path"] for item in listing}
        scripts={}
        for name in ["kcd_job_start.sh","kcd_job_cancel.sh","kcd_job_pause.sh","kcd_job_resume.sh","send_print_cost.sh"]:
            if name in names:
                scripts[name]=http(base+"/server/files/config/"+name).decode()
        if not {"kcd_job_start.sh","send_print_cost.sh"}.issubset(scripts):
            raise RuntimeError("Required client scripts missing; repair before rotating")
        inventory.append((client,base,scripts))
    if args.resume:
        if not credentials.get("previous_api_keys"):
            raise RuntimeError("No staged rotation exists")
        new = credentials["api_key"]
    else:
        if credentials.get("previous_api_keys"):
            raise RuntimeError("A rotation is staged; use --resume to finish it")
        master_request(args.master,credentials["api_key"])
        new=secrets.token_hex(32)
        credentials={"api_key":new,"previous_api_keys":[{"key":credentials["api_key"],"expires_at":time.time()+86400}]}
    backup=data/"backups"/("credential-rotation-"+str(time.time_ns()))
    backup.mkdir(parents=True,mode=0o700)
    for client,base,scripts in inventory:
        for filename,text in scripts.items():
            safe_name=str(uuid.uuid5(uuid.NAMESPACE_URL,client["printer_name"]))
            atomic_write(backup/(safe_name+"-"+filename),text,backup=False)
    atomic_write(secret,json.dumps(credentials,indent=2))
    subprocess.run(["docker","restart",args.container],capture_output=True,check=True,timeout=45)
    for attempt in range(30):
        try:
            master_request(args.master,new)
            break
        except RuntimeError:
            time.sleep(1)
    else:
        raise RuntimeError("Master restart failed; staged credentials and private backups retained")
    generators={"kcd_job_start.sh":utils.generate_job_start_script,"kcd_job_cancel.sh":utils.generate_job_cancel_script,
                "kcd_job_pause.sh":utils.generate_job_pause_script,"kcd_job_resume.sh":utils.generate_job_resume_script,
                "send_print_cost.sh":utils.generate_job_end_script}
    for client,base,scripts in inventory:
        for filename in scripts:
            content=generators[filename](args.master,new)
            upload(base,filename,content)
            fetched=http(base+"/server/files/config/"+filename).decode()
            if fetched != content:
                raise RuntimeError("Client file verification failed; overlap remains enabled, rerun --resume")
        # Execute only the fixed diagnostic branch: authenticates from the printer,
        # sets private script modes, emits no print events and moves no hardware.
        command={"script":'RUN_SHELL_COMMAND CMD=kcd_job_start PARAMS="--kcd-self-check"'}
        before=time.time()
        http(base+"/printer/gcode/script",json.dumps(command).encode(),{"Content-Type":"application/json"})
        messages=get_json(base+"/server/gcode_store?count=20")["result"]["gcode_store"]
        if not any("KCD credential verification passed" in m.get("message","") and m.get("time",0)>=before-2 for m in messages):
            raise RuntimeError("Printer-side authentication not verified; overlap remains enabled, rerun --resume")
        print("Credential and client verified:",client["printer_name"])
    # Old credentials are revoked only after every active SQL client verifies.
    atomic_write(secret,json.dumps({"api_key":new},indent=2))
    legacy=data/"install_state.json"
    if legacy.exists():
        state=json.loads(legacy.read_text())
        state.pop("api_key",None)
        atomic_write(legacy,json.dumps(state,indent=2))
    print("Rotation complete; previous key revoked. Private recovery backups:",backup)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir",required=True)
    parser.add_argument("--master",required=True,help="Master URL reachable from every printer")
    parser.add_argument("--container",default="print-cost-dashboard")
    parser.add_argument("--resume",action="store_true")
    args=parser.parse_args()
    try:
        rotate(args)
    except Exception as exc:
        # Exception bodies can include URLs/headers from remote services.
        detail=str(exc) if isinstance(exc,RuntimeError) else type(exc).__name__
        print("Rotation incomplete: "+detail+". Check idle state, SQL endpoints, client scripts and Master health; if staged, rerun with --resume. Previous key overlap is limited to 24 hours.",file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
