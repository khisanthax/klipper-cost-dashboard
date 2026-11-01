"""Resolve the host installer's storage mode before importing runtime modules."""
import json
import os
from pathlib import Path
import shutil
import subprocess


def configure_runtime():
    root = Path(__file__).resolve().parents[1]
    data = Path(os.environ.get("KCD_DATA_DIR", root / "data")).resolve()
    os.environ.setdefault("KCD_DATA_DIR", str(data))
    if not shutil.which("docker") or os.environ.get("KCD_INSTALLER_SKIP_DOCKER") == "1":
        return
    try:
        ids = subprocess.run(["docker", "ps", "-q"], capture_output=True, text=True, timeout=8, check=True).stdout.split()
        if not ids:
            return
        containers = json.loads(subprocess.run(["docker", "inspect", *ids], capture_output=True, text=True, timeout=8, check=True).stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        return
    modes = set()
    for container in containers:
        if any(m.get("Destination") == "/app/data" and Path(m.get("Source", "")).resolve() == data for m in container.get("Mounts", [])):
            env = dict(s.split("=", 1) for s in container["Config"].get("Env", []) if "=" in s)
            modes.add(env.get("KCD_STORAGE_BACKEND", "csv"))
            os.environ["KCD_MASTER_CONTAINER"] = container.get("Name", "").lstrip("/")
            bindings = container.get("NetworkSettings", {}).get("Ports", {}).get("5000/tcp") or []
            if bindings:
                os.environ.setdefault("KCD_MASTER_URL", "http://127.0.0.1:"+bindings[0]["HostPort"])
    if len(modes) > 1:
        raise RuntimeError("Conflicting Docker storage modes share this data directory; resolve before installation")
    if modes:
        mode = modes.pop()
        if os.environ.get("KCD_STORAGE_BACKEND", mode) != mode:
            raise RuntimeError("Installer storage mode conflicts with the running Docker Master")
        os.environ["KCD_STORAGE_BACKEND"] = mode
        os.environ["KCD_REPORTS_BACKEND"] = "sql" if mode == "sql" else "auto" if mode == "dual" else "csv"
