"""Run the same repair workflow over SSH, transmitting credentials only on stdin."""
import json
from pathlib import Path
import shlex
import subprocess
import tarfile
import tempfile


def remote_operation(host, operation, payload):
    if not host or host.startswith("-") or any(c.isspace() for c in host):
        raise ValueError("Invalid SSH host/alias")
    root = Path(__file__).resolve().parents[1]
    remote = subprocess.run(["ssh", "-o", "BatchMode=yes", host, "umask 077; mktemp -d /tmp/kcd-installer.XXXXXXXX"],
                            capture_output=True, text=True, timeout=15, check=True).stdout.strip()
    if not remote.startswith("/tmp/kcd-installer.") or "/" in remote[len("/tmp/"):] or len(remote) > 80:
        raise RuntimeError("Invalid SSH staging directory")
    try:
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary)/"source.tar"
            with tarfile.open(archive, "w") as tar:
                for directory in ("core", "installer"):
                    for path in (root/directory).rglob("*"):
                        if path.is_file() and path.suffix in {".py", ".sql"} and "__pycache__" not in path.parts:
                            tar.add(path, arcname=path.relative_to(root))
                tar.add(root/"installer_macro.py", arcname="installer_macro.py")
            subprocess.run(["scp", "-q", str(archive), host+":"+remote+"/source.tar"], check=True, timeout=30, capture_output=True)
        if operation == "discover":
            source = "import json; from installer.discovery import discover_local; print(json.dumps(discover_local()))"
        else:
            source = "import json,sys; from installer.workflow import repair_client; print(json.dumps(repair_client(**json.load(sys.stdin))))"
        command = ("cd "+shlex.quote(remote)+" && tar -xf source.tar && KCD_INSTALLER_SKIP_DOCKER=1 python3 -c "+shlex.quote(source))
        run = subprocess.run(["ssh", "-o", "BatchMode=yes", host, command], input=json.dumps(payload), capture_output=True, text=True, timeout=90)
        if run.returncode:
            # Do not echo remote stderr or proxy responses that could contain credentials.
            raise RuntimeError("Remote repair failed; verify Master readiness, identity conflicts, instance port, paths and SSH file permissions; private backups permit retry")
        return json.loads(run.stdout)
    finally:
        cleanup = "import shutil; shutil.rmtree("+repr(remote)+")"
        subprocess.run(["ssh", "-o", "BatchMode=yes", host, "python3 -c "+shlex.quote(cleanup)], capture_output=True, timeout=15)
