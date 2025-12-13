# --------------------------------------------------------------------------------------
# File: installer/utils.py
# Description: Utility helpers for Print Cost Dashboard installer.
#   - Installer state (DATA_DIR/STATE_FILE constants, key-based load/save)
#   - Client registry helpers
#   - Template generators for print_cost.cfg and shell scripts
#   - Installer entry points: master_setup, install_client_local, install_client_remote
# --------------------------------------------------------------------------------------

import os
import json
import re
import secrets
import tempfile
import shutil
from typing import Any, Dict, List
from . import remote as r
import installer_macro

from core.config import DATA_DIR
from core.storage import (
    load_state as _load_state_key,
    save_state as _save_state_key,
    ensure_api_key,
)

STATE_FILE = os.path.join(DATA_DIR, "install_state.json")
DEFAULT_PORT = 5000
DEFAULT_SERVICE_NAME = "print-cost-dashboard"


# ----------------------------------------------------------------------
# Basic helpers
# ----------------------------------------------------------------------

def println(msg: str = "") -> None:
    import sys
    print(msg)
    sys.stdout.flush()


def load_state(key: str, default: Any = "") -> Any:
    return _load_state_key(STATE_FILE, key, default)


def save_state(key: str, value: Any) -> None:
    _save_state_key(STATE_FILE, DATA_DIR, key, value)


# ----------------------------------------------------------------------
# Client registry helpers
# ----------------------------------------------------------------------

def get_client_registry() -> List[Dict[str, Any]]:
    registry = _load_state_key(STATE_FILE, "clients", [])
    if not isinstance(registry, list):
        return []
    return [c for c in registry if isinstance(c, dict)]


def _set_client_registry(clients: List[Dict[str, Any]]) -> None:
    _save_state_key(STATE_FILE, DATA_DIR, "clients", clients)


def register_client(entry: Dict[str, Any]) -> None:
    registry = get_client_registry()
    t = entry.get("type")
    pn = entry.get("printer_name")
    new = []
    replaced = False
    for c in registry:
        if c.get("type") == t and c.get("printer_name") == pn:
            new.append(entry)
            replaced = True
        else:
            new.append(c)
    if not replaced:
        new.append(entry)
    _set_client_registry(new)


def unregister_client(predicate) -> None:
    registry = get_client_registry()
    _set_client_registry([c for c in registry if not predicate(c)])


def _find_registry_entry(printer_name: str, client_type: str) -> Dict[str, Any]:
    """Return first registry entry matching printer_name and type, or {}."""
    for entry in get_client_registry():
        if entry.get("type") == client_type and entry.get("printer_name") == printer_name:
            return entry
    return {}


# ----------------------------------------------------------------------
# Templates
# ----------------------------------------------------------------------

def _try_update_kcd_vars_printer_name(cfg_path: str, printer_name: str) -> bool:
    """
    Update only the baked printer name inside an existing print_cost.cfg.

    Klipper macros cannot read JSON at runtime, so the installer must bake the
    printer name into config. This helper makes re-runs update-safe without
    duplicating macros or relying on brittle full-file replacements.
    """
    if not os.path.exists(cfg_path):
        return False

    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            text = f.read()
    except Exception:
        return False

    # Only do an in-place update if this print_cost.cfg already uses the baked
    # _KCD_VARS approach. If it's an older version, let the installer rewrite the
    # whole file so we eliminate dynamic printer name derivation.
    if 'printer["gcode_macro _KCD_VARS"].printer_name' not in text:
        return False

    m = re.search(r"(?ims)^\[gcode_macro\s+_KCD_VARS\]\s*.*?(?=^\[|\Z)", text)
    if not m:
        return False

    block = m.group(0)
    out_lines: list[str] = []
    updated = False
    for line in block.splitlines(keepends=True):
        if line.strip().lower().startswith("variable_printer_name:"):
            indent = line[: len(line) - len(line.lstrip())]
            out_lines.append(f'{indent}variable_printer_name: "{printer_name}"\n')
            updated = True
        else:
            out_lines.append(line)

    if not updated:
        return False

    new_block = "".join(out_lines)
    new_text = text[: m.start()] + new_block + text[m.end() :]
    if new_text == text:
        return True

    try:
        with open(cfg_path, "w", encoding="utf-8") as f:
            f.write(new_text)
        return True
    except Exception:
        return False


def make_print_cost_cfg(printer_dir: str, printer_name: str) -> (bool, str):
    """
    Write print_cost.cfg with KCD macros and shell commands.
    Avoid Python formatting on Jinja/klipper braces by using token replacement.
    """
    path = os.path.join(printer_dir, "print_cost.cfg")
    if _try_update_kcd_vars_printer_name(path, printer_name):
        return True, path
    template = """
# ----------------------------------------------------------------------
# Auto-generated by installer
[gcode_shell_command send_print_cost]
command: __PRINTER_DIR__/send_print_cost.sh
timeout: 15.0
verbose: True

[gcode_shell_command kcd_job_start]
command: __PRINTER_DIR__/kcd_job_start.sh
timeout: 10.0
verbose: True

[gcode_macro _KCD_VARS]
description: KCD installer variables (auto-generated)
variable_printer_name: "__PRINTER_NAME__"
gcode:

[gcode_macro KCD_JOB_START]
description: Notify dashboard that a print has started
gcode:
    # KCD: log job start to dashboard
    # KCD: use baked printer name from _KCD_VARS
    {% set printer_name = printer["gcode_macro _KCD_VARS"].printer_name|string %}
    {% set fname = printer.print_stats.filename|default("unknown.gcode", true)|string %}
    {% set est_dur = printer.print_stats.estimated_time|default(0)|float %}
    {% set est_filament = printer.print_stats.filament|default(0)|float %}
    {% set params = printer_name ~ "|" ~ fname ~ "|" ~ est_dur ~ "|" ~ est_filament %}
    RUN_SHELL_COMMAND CMD=kcd_job_start PARAMS="{params}"

[gcode_macro PRINT_COST_TEST]
description: Test sending dummy cost data to dashboard
gcode:
    # KCD: dummy payload for dashboard test
    {% set printer_name = printer["gcode_macro _KCD_VARS"].printer_name|string %}
    {% set fname = "test_file.gcode" %}
    {% set dur = 3600 %}
    {% set filament = 10000 %}
    {% set msg = "Sending cost test: printer=" ~ printer_name ~ ", file=" ~ fname ~ ", dur=" ~ dur ~ ", mm=" ~ filament %}
    RESPOND PREFIX="COST" MSG="{msg}"
    {% set params = printer_name ~ "|" ~ fname ~ "|" ~ dur ~ "|" ~ filament %}
    RUN_SHELL_COMMAND CMD=send_print_cost PARAMS="{params}"
"""
    cfg = template.replace("__PRINTER_DIR__", printer_dir).replace("__PRINTER_NAME__", printer_name)
    try:
        with open(path, "w") as f:
            f.write(cfg)
        return True, path
    except Exception as e:
        println(f"Failed to write {path}: {e}")
        return False, path


def generate_job_start_script(master_url: str, api_key: str) -> str:
    return f"""#!/usr/bin/env bash
set -euo pipefail

MASTER_URL="{master_url}"
API_KEY="{api_key}"

PARAMS="${{*:-}}"
IFS='|' read -r PRINTER_NAME FILENAME EST_DURATION EST_FILAMENT <<< "$PARAMS"
PRINTER_NAME="${{PRINTER_NAME:-}}"
FILENAME="${{FILENAME:-}}"
EST_DURATION="${{EST_DURATION:-0}}"
EST_FILAMENT="${{EST_FILAMENT:-0}}"

echo "KCD_JOB_START DEBUG: PARAMS='$PARAMS' PRINTER_NAME='$PRINTER_NAME' FILENAME='$FILENAME' EST_DURATION='$EST_DURATION' EST_FILAMENT='$EST_FILAMENT'"

export PRINTER_NAME FILENAME EST_DURATION EST_FILAMENT

PYBIN="$(command -v python3 || command -v python || true)"
if [ -z "$PYBIN" ]; then
  echo "ERROR: python3/python not found; install python3 or update script to not require python."
  exit 1
fi

JSON=$("$PYBIN" - <<'PY'
import json, os
def to_float(v):
    try: return float(v)
    except: return 0.0
data = {{
    "printer_name": os.environ.get("PRINTER_NAME", ""),
    "filename": os.environ.get("FILENAME", ""),
    "estimated_duration": to_float(os.environ.get("EST_DURATION", "0")),
    "estimated_filament_mm": to_float(os.environ.get("EST_FILAMENT", "0")),
}}
print(json.dumps(data))
PY
)

curl -s -X POST "$MASTER_URL/job-start" \\
  -H "Content-Type: application/json" \\
  -H "X-API-Key: $API_KEY" \\
  -d "$JSON"
"""


def generate_job_end_script(master_url: str, api_key: str) -> str:
    return f"""#!/usr/bin/env bash
set -euo pipefail

MASTER_URL="{master_url}"
API_KEY="{api_key}"

TS=$(date +%s)
PARAMS="${{*:-}}"
IFS='|' read -r PRINTER FILENAME DUR FILAMENT <<< "$PARAMS"
PRINTER="${{PRINTER:-}}"
FILENAME="${{FILENAME:-}}"
DUR="${{DUR:-0}}"
FILAMENT="${{FILAMENT:-0}}"

export TS PRINTER FILENAME DUR FILAMENT

PYBIN="$(command -v python3 || command -v python || true)"
if [ -z "$PYBIN" ]; then
  echo "ERROR: python3/python not found; install python3 or update script to not require python."
  exit 1
fi

JSON=$("$PYBIN" - <<'PY'
import json, os, time
def to_float(v):
    try: return float(v)
    except: return 0.0
def to_ts(v):
    try: return float(v)
    except: return time.time()
data = {{
    "timestamp": to_ts(os.environ.get("TS", "")),
    "printer": os.environ.get("PRINTER", ""),
    "filename": os.environ.get("FILENAME", ""),
    "duration_seconds": to_float(os.environ.get("DUR", "0")),
    "filament_mm": to_float(os.environ.get("FILAMENT", "0")),
}}
print(json.dumps(data))
PY
)

curl -s -X POST "$MASTER_URL/log-print" \\
  -H "Content-Type: application/json" \\
  -H "X-API-Key: $API_KEY" \\
  -d "$JSON"
"""


def write_script(path: str, content: str) -> bool:
    try:
        with open(path, "w") as f:
            f.write(content)
        os.chmod(path, 0o755)
        return True
    except Exception as e:
        println(f"Failed to write script {path}: {e}")
        return False


def _ensure_include_in_printer_cfg(printer_dir: str, include_filename: str) -> None:
    printer_cfg_path = os.path.join(printer_dir, "printer.cfg")
    if not os.path.exists(printer_cfg_path):
        println(f"WARNING: printer.cfg not found at {printer_cfg_path}; please add [include {include_filename}] manually.")
        return
    try:
        with open(printer_cfg_path, "r") as f:
            text = f.read()
    except Exception as e:
        println(f"WARNING: Failed to read {printer_cfg_path}: {e}")
        return
    include_line = f"[include {include_filename}]"
    if include_line in text:
        return
    new_text = include_line + "\n" + text
    try:
        with open(printer_cfg_path, "w") as f:
            f.write(new_text)
        println(f"Prepended {include_line} to {printer_cfg_path}")
    except Exception as e:
        println(f"WARNING: Failed to update {printer_cfg_path}: {e}")


def _remove_include_line(printer_dir: str, include_filename: str) -> None:
    """Remove a specific include line from printer.cfg without touching other content."""
    printer_cfg_path = os.path.join(printer_dir, "printer.cfg")
    if not os.path.exists(printer_cfg_path):
        println(f"printer.cfg not found at {printer_cfg_path}; skipping include removal.")
        return
    try:
        with open(printer_cfg_path, "r") as f:
            lines = f.readlines()
    except Exception as e:
        println(f"Failed to read {printer_cfg_path}: {e}")
        return

    include_line = f"[include {include_filename}]"
    new_lines = [ln for ln in lines if ln.strip() != include_line]
    if new_lines == lines:
        return
    try:
        with open(printer_cfg_path, "w") as f:
            f.writelines(new_lines)
        println(f"Removed include line from {printer_cfg_path}")
    except Exception as e:
        println(f"Failed to update {printer_cfg_path}: {e}")


# ----------------------------------------------------------------------
# API key helpers
# ----------------------------------------------------------------------

def _load_secret_api_key() -> str:
    key = ensure_api_key(secret_file=os.path.join(DATA_DIR, "secret.json"), data_dir=DATA_DIR)
    return key or ""


# ----------------------------------------------------------------------
# Installer entry points
# ----------------------------------------------------------------------

def master_setup(master_and_client: bool = False) -> None:
    println("\n=== Master Setup ===")

    use_auto = input("Use auto mode (reuse saved master settings)? [Y/n]: ").strip().lower()
    auto_mode = use_auto in ("", "y", "yes")

    current_host = load_state("master_host", "localhost")
    current_port = str(load_state("master_port", DEFAULT_PORT))
    current_url = load_state("master_url", f"http://{current_host}:{current_port}")
    current_service = load_state("master_service_name", DEFAULT_SERVICE_NAME)

    state_api = load_state("api_key", "")
    secret_api = _load_secret_api_key()
    default_api = state_api or secret_api or secrets.token_hex(16)

    host = current_host
    port_str = current_port
    url_default = current_url or f"http://{host}:{port_str}"
    url = url_default
    service_name = current_service
    api_key = default_api

    if auto_mode and host and port_str and url_default and service_name and api_key:
        println("[auto] Using saved master configuration.")
    else:
        if auto_mode:
            println("[auto] Saved master settings missing; switching to manual input.")
        auto_mode = False
        host = input(f"Master host [{current_host}]: ").strip() or current_host
        port_str = input(f"Master port [{current_port}]: ").strip() or current_port
        url_default = current_url or f"http://{host}:{port_str}"
        url = input(f"Master URL [{url_default}]: ").strip() or url_default
        service_name = input(f"Service name [{current_service}]: ").strip() or current_service
        api_key = input(f"API key for printers [{default_api}]: ").strip() or default_api

    try:
        port = int(port_str)
    except ValueError:
        port = DEFAULT_PORT
        println(f"Invalid port, using {DEFAULT_PORT}.")

    save_state("master_host", host)
    save_state("master_port", port)
    save_state("master_url", url)
    save_state("master_service_name", service_name)
    save_state("api_key", api_key)

    ensure_api_key(secret_file=os.path.join(DATA_DIR, "secret.json"), data_dir=DATA_DIR)

    println("\nSaved master configuration:")
    println(f"  Master URL: {url}")
    println(f"  Host: {host}")
    println(f"  Port: {port}")
    println(f"  Service: {service_name}")
    println(f"  API key: {api_key}")

    if master_and_client:
        println("\nContinuing with local client installation on this machine...")
        install_client_local()


def install_client_local() -> None:
    println("\n=== Local Client Installation ===")

    use_auto = input("Use auto mode (reuse saved settings and printer dir)? [Y/n]: ").strip().lower()
    auto_mode = use_auto in ("", "y", "yes")

    master_url = load_state("master_url", "http://localhost:5000")
    state_api = load_state("api_key", "")
    secret_api = _load_secret_api_key()
    default_api = state_api or secret_api or secrets.token_hex(16)

    saved_printer = load_state("printer_name", "")
    last_dir = load_state("printer_dir", "")
    default_dir = last_dir or "/home/pi/printer_data/config"

    printer_name = saved_printer
    printer_dir = default_dir
    api_key = default_api

    if auto_mode:
        if not (master_url and api_key and printer_name and printer_dir and os.path.isdir(printer_dir)):
            println("[auto] Saved settings incomplete; switching to manual input.")
            auto_mode = False
    if not auto_mode:
        master_url = input(f"Master URL for dashboard [{master_url}]: ").strip() or master_url
        api_key = input(f"API key for this printer [{default_api}]: ").strip() or default_api

        printer_name = input("Printer name for dashboard (e.g., SV08): ").strip()
        if not printer_name:
            println("Printer name is required; aborting.")
            return

        printer_dir = input(f"Printer config directory (folder with printer.cfg) [{default_dir}]: ").strip() or default_dir
        if not os.path.isdir(printer_dir):
            println(f"Directory does not exist: {printer_dir}")
            return
    else:
        println(f"[auto] Using saved master URL: {master_url}")
        println(f"[auto] Using printer: {printer_name}")
        println(f"[auto] Using config dir: {printer_dir}")

    ok, cfg_path = make_print_cost_cfg(printer_dir, printer_name)
    if not ok:
        println("Failed to create print_cost.cfg; aborting.")
        return
    if auto_mode:
        println(f"[auto] Wrote print_cost.cfg to {cfg_path}")

    job_start_script = generate_job_start_script(master_url, api_key)
    end_script = generate_job_end_script(master_url, api_key)

    job_start_path = os.path.join(printer_dir, "kcd_job_start.sh")
    end_script_path = os.path.join(printer_dir, "send_print_cost.sh")

    if not write_script(job_start_path, job_start_script):
        println("Failed to write kcd_job_start.sh; aborting.")
        return
    if not write_script(end_script_path, end_script):
        println("Failed to write send_print_cost.sh; aborting.")
        return
    if auto_mode:
        println(f"[auto] Wrote kcd_job_start.sh to {job_start_path}")
        println(f"[auto] Wrote send_print_cost.sh to {end_script_path}")

    _ensure_include_in_printer_cfg(printer_dir, "print_cost.cfg")
    if auto_mode:
        println("[auto] Checked [include print_cost.cfg] in printer.cfg")

    try:
        installer_macro.run_macro_integration(printer_name, printer_dir)
    except Exception as e:
        println(f"WARNING: Macro integration wizard failed: {e}")
        println("You may need to add KCD blocks to your macros manually.")

    save_state("master_url", master_url)
    save_state("api_key", api_key)
    save_state("printer_dir", printer_dir)
    save_state("script_path", end_script_path)
    save_state("printer_name", printer_name)

    register_client({
        "type": "local",
        "printer_name": printer_name,
        "cfg_dir": printer_dir,
        "script_path": end_script_path,
    })

    println("\nLocal client installation complete.")
    println(f"  Printer: {printer_name}")
    println(f"  Config dir: {printer_dir}")
    println(f"  print_cost.cfg: {cfg_path}")
    println(f"  Job-start script: {job_start_path}")
    println(f"  Cost script: {end_script_path}")


def install_client_remote() -> None:
    """
    Install a remote Klipper client over SSH.
    """
    println("\n=== Remote Client Installation ===")

    use_auto = input("Use auto mode (use saved master settings and known remote printers)? [Y/n]: ").strip().lower()
    auto_mode = use_auto in ("", "y", "yes")

    master_url = load_state("master_url", "http://localhost:5000")
    state_api = load_state("api_key", "")
    secret_api = _load_secret_api_key()
    default_api = state_api or secret_api or secrets.token_hex(16)

    if auto_mode and master_url and default_api:
        println(f"[auto] Using saved master URL: {master_url}")
        api_key = default_api
    else:
        if auto_mode and (not master_url or not default_api):
            println("[auto] Saved master URL/API missing; falling back to manual entry.")
        auto_mode = False
        println(f"Current master URL: {master_url}")
        master_url = input(f"Master URL for dashboard [{master_url}]: ").strip() or master_url
        api_key = input(f"API key for this printer [{default_api}]: ").strip() or default_api

    remote = ""
    printer_name = ""
    printer_dir = ""

    registry = get_client_registry()
    remote_clients = [c for c in registry if c.get("type") == "remote"]

    if auto_mode and remote_clients:
        println("\n[auto] Registered remote printers:")
        for i, c in enumerate(remote_clients, 1):
            println(f"  {i}) {c.get('printer_name')} @ {c.get('host')} ({c.get('config_dir')})")
        choice = input(f"Select printer to install/update [1-{len(remote_clients)}] or press Enter to cancel auto mode: ").strip()
        if choice.isdigit():
            idx = int(choice)
            if 1 <= idx <= len(remote_clients):
                entry = remote_clients[idx - 1]
                remote = entry.get("host", "")
                printer_name = entry.get("printer_name", "")
                printer_dir = entry.get("config_dir", "")
            else:
                auto_mode = False
        else:
            auto_mode = False
    elif auto_mode:
        println("[auto] No registered remote printers found; falling back to manual setup.")
        auto_mode = False

    if not auto_mode:
        remote = input("Remote host (user@hostname): ").strip()
        if not remote:
            println("Remote host is required; aborting.")
            return

        printer_name = input("Printer name for dashboard (e.g., SV08): ").strip()
        if not printer_name:
            println("Printer name is required; aborting.")
            return


    if not printer_dir:
        candidates: list[str] = []
        try:
            candidates = r.remote_find_printer_data(remote)
        except Exception as e:
            println(f"WARNING: Failed to scan remote for printer_data dirs: {e}")

        if candidates:
            if auto_mode:
                println("[auto] Scanning remote for printer_data directories...")
            println("\nFound the following remote printer_data/config candidates:")
            for i, path in enumerate(candidates, 1):
                println(f"  {i}) {path}")
            choice = input(f"Select [1-{len(candidates)}] or enter a custom path: ").strip()
            if choice.isdigit():
                idx = int(choice)
                if 1 <= idx <= len(candidates):
                    printer_dir = candidates[idx - 1]
            if not printer_dir:
                printer_dir = choice or candidates[0]
        else:
            printer_dir = input("Remote printer config dir (folder with printer.cfg): ").strip()

    if not printer_dir:
        println("No remote config directory provided; aborting.")
        return

    remote_cfg_path = os.path.join(printer_dir, "print_cost.cfg")
    remote_job_start = os.path.join(printer_dir, "kcd_job_start.sh")
    remote_end_script = os.path.join(printer_dir, "send_print_cost.sh")
    remote_printer_cfg = os.path.join(printer_dir, "printer.cfg")

    cfg_text = _render_print_cost_cfg(printer_dir, printer_name)
    job_start_script = generate_job_start_script(master_url, api_key)
    end_script = generate_job_end_script(master_url, api_key)

    ok1 = r.remote_write_file(remote, remote_cfg_path, cfg_text, mode=0o644)
    ok2 = r.remote_write_file(remote, remote_job_start, job_start_script, mode=0o755)
    ok3 = r.remote_write_file(remote, remote_end_script, end_script, mode=0o755)

    if not (ok1 and ok2 and ok3):
        println("ERROR: Failed to write one or more files on the remote host; aborting.")
        return
    if auto_mode:
        println(f"[auto] Deployed print_cost.cfg to {remote_cfg_path}")
        println(f"[auto] Deployed kcd_job_start.sh to {remote_job_start}")
        println(f"[auto] Deployed send_print_cost.sh to {remote_end_script}")

    include_line = "[include print_cost.cfg]"
    if not r.remote_append_line_if_missing(remote, remote_printer_cfg, include_line):
        println("WARNING: Failed to ensure include line in remote printer.cfg; please check manually.")
    elif auto_mode:
        println("[auto] Verified [include print_cost.cfg] in printer.cfg.")

    try:
        run_remote_macro_integration(printer_name, remote, printer_dir)
    except Exception as e:
        println(f"WARNING: Remote macro integration failed: {e}")
        println("You may need to add KCD blocks to your macros on the remote host manually.")
    save_state("master_url", master_url)
    save_state("api_key", api_key)

    register_client({
        "type": "remote",
        "printer_name": printer_name,
        "host": remote,
        "config_dir": printer_dir,
    })

    println("\nRemote client installation complete.")
    println(f"  Printer: {printer_name}")
    println(f"  Remote: {remote}")
    println(f"  Remote config dir: {printer_dir}")
    println(f"  print_cost.cfg: {remote_cfg_path}")
    println(f"  Job-start script: {remote_job_start}")
    println(f"  Cost script: {remote_end_script}")


# Legacy helper used by remote flow (uses per-file contents)
def _render_print_cost_cfg(printer_dir: str, printer_name: str) -> str:
    template = """
# Auto-generated by installer
[gcode_shell_command send_print_cost]
command: __PRINTER_DIR__/send_print_cost.sh
timeout: 15.0
verbose: True

[gcode_shell_command kcd_job_start]
command: __PRINTER_DIR__/kcd_job_start.sh
timeout: 10.0
verbose: True

[gcode_macro _KCD_VARS]
description: KCD installer variables (auto-generated)
variable_printer_name: "__PRINTER_NAME__"
gcode:

[gcode_macro KCD_JOB_START]
description: Notify dashboard that a print has started
gcode:
    # KCD: log job start to dashboard
    # KCD: use baked printer name from _KCD_VARS
    {% set printer_name = printer["gcode_macro _KCD_VARS"].printer_name|string %}
    {% set fname = printer.print_stats.filename|default("unknown.gcode", true)|string %}
    {% set est_dur = printer.print_stats.estimated_time|default(0)|float %}
    {% set est_filament = printer.print_stats.filament|default(0)|float %}
    {% set params = printer_name ~ "|" ~ fname ~ "|" ~ est_dur ~ "|" ~ est_filament %}
    RUN_SHELL_COMMAND CMD=kcd_job_start PARAMS="{params}"

[gcode_macro PRINT_COST_TEST]
description: Test sending dummy cost data to dashboard
gcode:
    # KCD: dummy payload for dashboard test
    {% set printer_name = printer["gcode_macro _KCD_VARS"].printer_name|string %}
    {% set fname = "test_file.gcode" %}
    {% set dur = 3600 %}
    {% set filament = 10000 %}
    {% set msg = "Sending cost test: printer=" ~ printer_name ~ ", file=" ~ fname ~ ", dur=" ~ dur ~ ", mm=" ~ filament %}
    RESPOND PREFIX="COST" MSG="{msg}"
    {% set params = printer_name ~ "|" ~ fname ~ "|" ~ dur ~ "|" ~ filament %}
    RUN_SHELL_COMMAND CMD=send_print_cost PARAMS="{params}"
"""
    return template.replace("__PRINTER_DIR__", printer_dir).replace("__PRINTER_NAME__", printer_name)


def run_remote_macro_integration(printer_name: str, remote: str, printer_dir: str) -> None:
    """
    Download .cfg files from remote printer_dir, run local macro integration, upload back.
    """
    tmp_dir = tempfile.mkdtemp(prefix="kcd_remote_cfg_")
    try:
        cfg_files = r.remote_list_cfg_files(remote, printer_dir)
        if not cfg_files:
            println(f"No .cfg files found in remote dir {printer_dir}; skipping macro integration.")
            return

        local_paths = []
        for remote_path in cfg_files:
            fname = os.path.basename(remote_path)
            local_path = os.path.join(tmp_dir, fname)
            content = r.remote_read_file(remote, remote_path)
            if not content:
                continue
            with open(local_path, "w", encoding="utf-8") as f:
                f.write(content)
            local_paths.append((remote_path, local_path))

        if not local_paths:
            println("Failed to download any remote .cfg files; skipping macro integration.")
            return

        installer_macro.run_macro_integration(printer_name, tmp_dir)

        for remote_path, local_path in local_paths:
            with open(local_path, "r", encoding="utf-8") as f:
                updated = f.read()
            r.remote_write_file(remote, remote_path, updated, mode=0o644)

        println("Remote macro integration completed successfully.")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ----------------------------------------------------------------------
# Uninstall helpers
# ----------------------------------------------------------------------

def uninstall_master() -> None:
    """
    Remove master-related installer state and optionally Docker/systemd artifacts.
    """
    println("\n=== Uninstall MASTER (dashboard) ===")
    confirm = input("This will clear saved master settings. Continue? [y/N]: ").strip().lower()
    if confirm not in ("y", "yes"):
        println("Master uninstall cancelled.")
        return

    for key in ("master_host", "master_port", "master_url", "master_service_name", "api_key"):
        save_state(key, "")

    removed_files = []
    for candidate in ("docker-compose.yml", "docker-compose.yaml", "Dockerfile", "/etc/systemd/system/print-cost-dashboard.service"):
        if os.path.exists(candidate):
            ans = input(f"Delete {candidate}? [y/N]: ").strip().lower()
            if ans in ("y", "yes"):
                try:
                    os.remove(candidate)
                    removed_files.append(candidate)
                except Exception as e:
                    println(f"Failed to delete {candidate}: {e}")

    println("Master uninstall complete.")
    if removed_files:
        println("Removed files: " + ", ".join(removed_files))


def uninstall_client_local(printer_name: str) -> None:
    """
    Remove local client artifacts and registry entry.
    """
    entry = _find_registry_entry(printer_name, "local")
    if not entry:
        println(f"No local client found for printer '{printer_name}'.")
        return

    cfg_dir = entry.get("cfg_dir", "")
    if not cfg_dir or not os.path.isdir(cfg_dir):
        println(f"Config directory not found for '{printer_name}': {cfg_dir}")
    else:
        for fname in ("print_cost.cfg", "kcd_job_start.sh", "send_print_cost.sh"):
            path = os.path.join(cfg_dir, fname)
            if os.path.exists(path):
                try:
                    os.remove(path)
                    println(f"Deleted {path}")
                except Exception as e:
                    println(f"Failed to delete {path}: {e}")
        _remove_include_line(cfg_dir, "print_cost.cfg")

    unregister_client(lambda c: c.get("type") == "local" and c.get("printer_name") == printer_name)
    println(f"Local client uninstall complete for '{printer_name}'.")


def uninstall_client_remote(printer_name: str) -> None:
    """
    Remove remote client artifacts via SSH and registry entry.
    """
    entry = _find_registry_entry(printer_name, "remote")
    if not entry:
        println(f"No remote client found for printer '{printer_name}'.")
        return

    host = entry.get("host", "")
    config_dir = entry.get("config_dir", "")
    if not host or not config_dir:
        println(f"Remote entry incomplete for '{printer_name}'.")
    else:


        remote_files = [
            os.path.join(config_dir, "print_cost.cfg"),
            os.path.join(config_dir, "kcd_job_start.sh"),
            os.path.join(config_dir, "send_print_cost.sh"),
        ]
        for path in remote_files:
            code, out, err = r.ssh_run(host, f"rm -f '{path}'")
            if code == 0:
                println(f"Deleted remote {path}")
            else:
                println(f"Failed to delete remote {path}: {err or out}")

        printer_cfg = os.path.join(config_dir, "printer.cfg")
        cmd = f"if [ -f '{printer_cfg}' ]; then sed -i '/\\[include print_cost\\.cfg\\]/d' '{printer_cfg}'; fi"
        code, out, err = r.ssh_run(host, cmd)
        if code != 0:
            println(f"Failed to update remote printer.cfg: {err or out}")

    unregister_client(lambda c: c.get("type") == "remote" and c.get("printer_name") == printer_name)
    println(f"Remote client uninstall complete for '{printer_name}'.")


# ----------------------------------------------------------------------
# Update helpers
# ----------------------------------------------------------------------

def update_client_local(printer_name: str) -> None:
    """
    Refresh local client scripts/config and rerun macro integration.
    """
    entry = _find_registry_entry(printer_name, "local")
    if not entry:
        println(f"No local client found for printer '{printer_name}'.")
        return

    cfg_dir = entry.get("cfg_dir", "")
    if not cfg_dir or not os.path.isdir(cfg_dir):
        println(f"Config directory not found for '{printer_name}': {cfg_dir}")
        return

    master_url = load_state("master_url", "http://localhost:5000")
    state_api = load_state("api_key", "")
    secret_api = _load_secret_api_key()
    api_key = state_api or secret_api or secrets.token_hex(16)

    ok, cfg_path = make_print_cost_cfg(cfg_dir, printer_name)
    if not ok:
        println("Failed to write print_cost.cfg; aborting update.")
        return

    job_start_script = generate_job_start_script(master_url, api_key)
    end_script = generate_job_end_script(master_url, api_key)
    job_start_path = os.path.join(cfg_dir, "kcd_job_start.sh")
    end_script_path = os.path.join(cfg_dir, "send_print_cost.sh")

    if not write_script(job_start_path, job_start_script):
        println("Failed to write kcd_job_start.sh; aborting update.")
        return
    if not write_script(end_script_path, end_script):
        println("Failed to write send_print_cost.sh; aborting update.")
        return

    _ensure_include_in_printer_cfg(cfg_dir, "print_cost.cfg")

    try:
        installer_macro.run_macro_integration(printer_name, cfg_dir)
    except Exception as e:
        println(f"WARNING: Macro integration wizard failed: {e}")

    save_state("master_url", master_url)
    save_state("api_key", api_key)
    save_state("printer_dir", cfg_dir)
    save_state("script_path", end_script_path)

    register_client({
        "type": "local",
        "printer_name": printer_name,
        "cfg_dir": cfg_dir,
        "script_path": end_script_path,
    })

    println(f"Local client update complete for '{printer_name}'.")


def update_client_remote(printer_name: str) -> None:
    """
    Refresh remote client scripts/config via SSH.
    """
    entry = _find_registry_entry(printer_name, "remote")
    if not entry:
        println(f"No remote client found for printer '{printer_name}'.")
        return

    host = entry.get("host", "")
    config_dir = entry.get("config_dir", "")
    if not host or not config_dir:
        println(f"Remote entry incomplete for '{printer_name}'.")
        return

    master_url = load_state("master_url", "http://localhost:5000")
    state_api = load_state("api_key", "")
    secret_api = _load_secret_api_key()
    api_key = state_api or secret_api or secrets.token_hex(16)

    cfg_text = _render_print_cost_cfg(config_dir, printer_name)
    job_start_script = generate_job_start_script(master_url, api_key)
    end_script = generate_job_end_script(master_url, api_key)



    remote_cfg_path = os.path.join(config_dir, "print_cost.cfg")
    remote_job_start = os.path.join(config_dir, "kcd_job_start.sh")
    remote_end_script = os.path.join(config_dir, "send_print_cost.sh")

    ok1 = r.remote_write_file(host, remote_cfg_path, cfg_text, mode=0o644)
    ok2 = r.remote_write_file(host, remote_job_start, job_start_script, mode=0o755)
    ok3 = r.remote_write_file(host, remote_end_script, end_script, mode=0o755)

    if not (ok1 and ok2 and ok3):
        println("Failed to update one or more remote files; aborting.")
        return

    include_line = "[include print_cost.cfg]"
    if not r.remote_append_line_if_missing(host, os.path.join(config_dir, "printer.cfg"), include_line):
        println("WARNING: Could not ensure include line on remote printer.cfg.")

    save_state("master_url", master_url)
    save_state("api_key", api_key)

    register_client({
        "type": "remote",
        "printer_name": printer_name,
        "host": host,
        "config_dir": config_dir,
    })

    println(f"Remote client update complete for '{printer_name}'.")

