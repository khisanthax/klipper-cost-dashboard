"""Launch a disposable synthetic demo in a Docker sandbox without external networking."""
import argparse
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory


SMOKE = """
import json
from pathlib import Path
import socket
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

base = 'http://127.0.0.1:5000'
for _ in range(120):
    try:
        with urlopen(base + '/health', timeout=1) as response:
            assert json.load(response) == {'status': 'available'}
        break
    except OSError:
        time.sleep(.25)
else:
    raise AssertionError('Demo did not become ready')

with urlopen(Request(base + '/demo/foundation/session', method='POST'), timeout=5) as response:
    assert response.status == 201
    cookie = response.headers['Set-Cookie'].split(';', 1)[0]
with urlopen(Request(base + '/api/printer-status', headers={'Cookie': cookie}), timeout=5) as response:
    printers = json.load(response)['printers']
    assert len(printers) == 3 and all(p['simulated'] for p in printers.values())
try:
    urlopen(base + '/api/installer/registration', timeout=5)
except HTTPError as error:
    assert error.code == 403
else:
    raise AssertionError('Demo exposed installer registration')
try:
    with socket.create_connection(('192.0.2.1', 443), timeout=1):
        raise AssertionError('Isolated demo had external network connectivity')
except OSError:
    pass
assert not Path('/demo/normal-data-sentinel').exists()
print('Isolated container passed: synthetic seed, workspace, simulated telemetry and denied egress')
"""


def docker(*args, capture=False):
    return subprocess.run(
        ["docker", *args], check=True, text=True, timeout=90,
        stdout=subprocess.PIPE if capture else None,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", help="Locally built candidate image or immutable image reference")
    args = parser.parse_args()
    if not hasattr(os, "getuid"):
        parser.error("Run on Linux with Docker available")
    if os.getuid() == 0:
        parser.error("Use a non-root operator account with Docker access")
    with TemporaryDirectory(prefix="kcd-demo-container-") as directory:
        root = Path(directory)
        options = [
            "--pull", "never", "--network", "none", "--read-only", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--user", f"{os.getuid()}:{os.getgid()}", "--memory", "256m", "--pids-limit", "64",
            "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=32m,mode=1777",
            "--mount", f"type=bind,source={root},target=/demo",
            "-e", "KCD_RUNTIME_MODE=demo", "-e", "KCD_STORAGE_BACKEND=sql", "-e", "KCD_REPORTS_BACKEND=sql",
            "-e", "KCD_DEMO_DATA_ROOT=/demo", "-e", "KCD_DATA_DIR=/demo/normal-data-sentinel",
            "-e", "KCD_DEMO_COOKIE_SECURE=0", "-e", "PYTHONDONTWRITEBYTECODE=1",
        ]
        docker("run", "--rm", *options, args.image, "python", "tools/create_demo_seed.py", "--data-root", "/demo")
        container = docker("run", "--detach", *options, args.image, capture=True).stdout.strip()
        try:
            docker("exec", container, "python", "-c", SMOKE)
        finally:
            docker("rm", "--force", container)


if __name__ == "__main__":
    main()
