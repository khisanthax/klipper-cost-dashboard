# --------------------------------------------------------------------------------------
# File: utils.py
# Description: Utility helpers for Print Cost Dashboard installer.
#  - Thin wrapper around core.storage install-state helpers
#  - Generates print_cost.cfg and shell scripts
#  - Provides master/client installer entry points used by install.py
# --------------------------------------------------------------------------------------

import os
import json
import secrets
from typing import Any, Dict, List, Callable, Tuple

# These live in the core package in the real repo
from core.config import DATA_DIR, SECRET_FILE
from core.storage import (
    load_state as _load_state_internal,
    save_state as _save_state_internal,
    ensure_api_key,
)


# ======================================================================
# Installer-wide constants
# ======================================================================

STATE_FILE = os.path.join(DATA_DIR, "install_state.json")
DEFAULT_PORT = 5000
DEFAULT_SERVICE_NAME = "print-cost-dashboard"


# ======================================================================
# Basic I/O helpers
# ======================================================================

def println(msg: str = "") -> None:
    """Print a message and flush stdout (used consistently across installer)."""
    import sys
    print(msg)
    sys.stdout.flush()


# ----------------------------------------------------------------------
# Install-state wrappers (key-based)
# ----------------------------------------------------------------------

def load_state(key: str, default: Any = "") -> Any:
    """
    Load a single value from the installer state.

    This is a thin wrapper around core.storage.load_state that always
    targets STATE_FILE so existing install.py code can stay simple.
    """
    return _load_state_internal(STATE_FILE, key, default)


def save_state(key: str, value: Any) -> None:
    """
    Save a single value into the installer state.

    This wraps core.storage.save_state with STATE_FILE and DATA_DIR.
    """
    _save_state_internal(STATE_FILE, DATA_DIR, key, value)


# ----------------------------------------------------------------------
# Client registry helpers
# ----------------------------------------------------------------------

def get_client_registry() -> List[Dict[str, Any]]:
    """Return list of registered clients from installer state."""
    registry = load_state("clients", [])
    if not isinstance(registry, list):
        return []
    # Ensure each item is a dict
    return [c for c in registry if isinstance(c, dict)]


def _set_client_registry(registry: List[Dict[str, Any]]) -> None:
    """Persist the client registry back to installer state."""
    save_state("clients", registry)


def register_client(entry: Dict[str, Any]) -> None:
    """
    Add or update a client entry.

    Uniqueness is based on (type, printer_name) pair.
    """
    registry = get_client_registry()
    t = entry.get("type")
    pn = entry.get("printer_name")
    new_registry: List[Dict[str, Any]] = []
    replaced = False

    for existing in registry:
        if existing.get("type") == t and existing.get("printer_name") == pn:
            new_registry.append(entry)
            replaced = True
        else:
            new_registry.append(existing)

    if not replaced:
        new_registry.append(entry)

    _set_client_registry(new_registry)


def unregister_client(predicate: Callable[[Dict[str, Any]], bool]) -> None:
    """
    Remove any registry entries matching predicate(entry) -> bool.
    Currently not used by install.py but kept for completeness.
    """
    registry = get_client_registry()
    new_registry = [c for c in registry if not predicate(c)]
    _set_client_registry(new_registry)


# ======================================================================
# File creation templates
# ======================================================================

def _render_print_cost_cfg(printer_dir: str, printer_name: str) -> str:
    """
    Render the contents of print_cost.cfg.

    The shell commands are wired to kcd_job_start.sh and send_print_cost.sh
    in the same directory; KCD_JOB_START macro derives filename and estimates.
    """
    return f"""# ------------------------------------------------------------------------------
# File: print_cost.cfg (auto-generated)
# Shell commands + KCD_JOB_START macro for Print Cost Dashboard
# ------------------------------------------------------------------------------

[gcode_shell_command send_print_cost]
command: {printer_dir}/send_print_cost.sh
timeout: 15.0
verbose: True

[gcode_shell_command kcd_job_start]
command: {printer_dir}/kcd_job_start.sh
timeout: 10.0
verbose: True

[gcode_macro KCD_JOB_START]
description: Notify dashboard that a print has started
gcode:
    # KCD: log job start to dashboard
    {{% set printer_name = "{printer_name}" %}}
    {{% set fname = printer.print_stats.filename|string %}}
    {{% set est_dur = printer.print_stats.estimated_time|default(0)|float %}}
    {{% set est_filament = printer.print_stats.filament|default(0)|float %}}
    {{% set params = printer_name ~ ' ' ~ fname ~ ' ' ~ est_dur ~ ' ' ~ est_filament %}}
    RUN_SHELL_COMMAND CMD=kcd_job_start PARAMS="{{{{params}}}}"

"""


def make_print_cost_cfg(printer_dir: str, printer_name: str) -> Tuple[bool, str]:
    """
    Create print_cost.cfg on disk using the rendered template.

    Returns (True, path) on success, (False, "") on failure.
    """
    path = os.path.join(printer_dir, "print_cost.cfg")
    cfg = _render_print_cost_cfg(printer_dir, printer_name)

    try:
        with open(path, "w") as f:
            f.write(cfg)
        return True, path
    except Exception as e:
        println(f"Failed to write {path}: {e}")
        return False, ""


def _ensure_include_in_printer_cfg(printer_dir: str, include_filename: str = "print_cost.cfg") -> bool:
    """
    Ensure printer.cfg in printer_dir has an [include ...] for include_filename.

    If printer.cfg is missing, a warning is printed and False is returned.
    """
    printer_cfg = os.path.join(printer_dir, "printer.cfg")
    include_line = f"[include {include_filename}]"

    if not os.path.exists(printer_cfg):
        println(f"WARNING: {printer_cfg} not found; please add '{include_line}' manually.")
        return False

    try:
        with open(printer_cfg) as f:
            lines = f.read().splitlines()

        if any(include_line in line.strip() for line in lines):
            return True

        # Prepend include at the top
        lines.insert(0, include_line)
        with open(printer_cfg, "w") as f:
            f.write("\n".join(lines) + "\n")
        return True
    except Exception as e:
        println(f"Failed to update {printer_cfg}: {e}")
        return False


# ======================================================================
# Script generators
# ======================================================================

def generate_job_start_script(master_url: str, api_key: str) -> str:
    """
    Universal job-start script template.

    Expects 4 arguments from Klipper macro:
      1) printer name
      2) filename
      3) estimated duration (seconds)
      4) estimated filament (mm)
    """
    return f"""#!/usr/bin/env bash
# ------------------------------------------------------------------------------
# File: kcd_job_start.sh (auto-generated)
# Sends initial job info to Print Cost Dashboard
# ------------------------------------------------------------------------------

set -euo pipefail

MASTER_URL="{master_url}"
API_KEY="{api_key}"

PRINTER="${{1:-}}"
FILENAME="${{2:-}}"
DUR="${{3:-0}}"
FILAMENT="${{4:-0}}"

export PRINTER FILENAME DUR FILAMENT

JSON=$(python3 - <<'PY'
import json, os

def to_float(v):
    try:
        return float(v)
    except Exception:
        return 0.0

data = {{
    "printer_name": os.environ.get("PRINTER", ""),
    "filename": os.environ.get("FILENAME", ""),
    "estimated_duration": to_float(os.environ.get("DUR", "0")),
    "estimated_filament_mm": to_float(os.environ.get("FILAMENT", "0")),
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
    """
    Universal job-end (cost log) script template.

    Expects 4 arguments from Klipper macro:
      1) printer name
      2) filename
      3) elapsed duration (seconds)
      4) filament used (mm)
    """
    return f"""#!/usr/bin/env bash
# ------------------------------------------------------------------------------
# File: send_print_cost.sh (auto-generated)
# Sends final print cost info to Print Cost Dashboard
# ------------------------------------------------------------------------------

set -euo pipefail

MASTER_URL="{master_url}"
API_KEY="{api_key}"

TS=$(date +%s)

PRINTER="${{1:-}}"
FILENAME="${{2:-}}"
DUR="${{3:-0}}"
FILAMENT="${{4:-0}}"

export TS PRINTER FILENAME DUR FILAMENT

JSON=$(python3 - <<'PY'
import json, os, time

def to_float(v):
    try:
        return float(v)
    except Exception:
        return 0.0

def to_timestamp(v):
    try:
        return float(v)
    except Exception:
        return time.time()

data = {{
    "timestamp": to_timestamp(os.environ.get("TS", "")),
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
    """Write script with executable permissions."""
    try:
        with open(path, "w") as f:
            f.write(content)
        os.chmod(path, 0o755)
        return True
    except Exception as e:
        println(f"Failed to write script {path}: {e}")
        return False


# ======================================================================
# API key helpers
# ======================================================================

def _load_secret_api_key() -> str:
    """
    Try to load API key from secret.json via core.storage.ensure_api_key.

    This will generate a new key only if the file doesn't exist; it will not
    overwrite an existing key.
    """
    key = ensure_api_key(secret_file=SECRET_FILE, data_dir=DATA_DIR)
    return key or ""


def _write_secret_api_key(key: str) -> None:
    """Write the given API key into SECRET_FILE."""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(SECRET_FILE, "w") as f:
            json.dump({"api_key": key}, f, indent=2)
    except Exception as e:
        println(f"WARNING: Failed to write API key to {SECRET_FILE}: {e}")


# ======================================================================
# Installer entry points (master + clients)
# ======================================================================

def master_setup(master_and_client: bool = False) -> None:
    """
    Configure master settings used by local/remote clients.

    This function does NOT attempt to create Docker or systemd services;
    it just records the URL/port/service name + API key into install_state.json
    and secret.json. The actual dashboard process should be managed separately
    (e.g. docker-compose up -d).
    """
    println("\n=== Master Setup ===")

    # Defaults from existing state or sensible fallbacks
    current_host = load_state("master_host", "localhost")
    current_port = str(load_state("master_port", DEFAULT_PORT))
    current_url = load_state("master_url", f"http://{current_host}:{current_port}")
    current_service = load_state("master_service_name", DEFAULT_SERVICE_NAME)

    # API key (prefer state, then secret.json, then new random)
    state_api = load_state("api_key", "")
    secret_api = _load_secret_api_key()
    default_api = state_api or secret_api or secrets.token_hex(16)

    host = input(f"Master host [{current_host}]: ").strip() or current_host
    port_str = input(f"Master port [{current_port}]: ").strip() or current_port
    try:
        port = int(port_str)
    except ValueError:
        port = DEFAULT_PORT
        println(f"Invalid port, using {DEFAULT_PORT}.")

    url_default = current_url or f"http://{host}:{port}"
    url = input(f"Master URL [{url_default}]: ").strip() or url_default

    service_name = input(f"Service name [{current_service}]: ").strip() or current_service
    api_key = input(f"API key for printers [{default_api}]: ").strip() or default_api

    # Persist configuration
    save_state("master_host", host)
    save_state("master_port", port)
    save_state("master_url", url)
    save_state("master_service_name", service_name)
    save_state("api_key", api_key)

    # Keep secret.json in sync so the Flask app accepts this key
    _write_secret_api_key(api_key)

    println("\nSaved master configuration:")
    println(f"  Master URL: {url}")
    println(f"  Host: {host}")
    println(f"  Port: {port}")
    println(f"  Service name: {service_name}")
    println(f"  API key: {api_key}")
    println("\nNOTE: This installer does not yet manage Docker/systemd. "
            "Use the provided docker-compose.yml or your own process manager to run the dashboard.")

    if master_and_client:
        println("\nContinuing with local client installation on this machine...")
        install_client_local()


def install_client_local() -> None:
    """
    Install a local Klipper client on this machine.

    Creates:
      - print_cost.cfg in the chosen printer config dir
      - kcd_job_start.sh and send_print_cost.sh
      - ensures [include print_cost.cfg] is present in printer.cfg
      - runs the macro integration wizard
      - registers the client in install_state.json
    """
    println("\n=== Local Client Installation ===")

    master_url = load_state("master_url", "http://localhost:5000")
    state_api = load_state("api_key", "")
    secret_api = _load_secret_api_key()
    default_api = state_api or secret_api or secrets.token_hex(16)

    println(f"Current master URL: {master_url}")
    master_url = input(f"Master URL for dashboard [{master_url}]: ").strip() or master_url
    api_key = input(f"API key for this printer [{default_api}]: ").strip() or default_api

    printer_name = input("Printer name (e.g., SV08): ").strip()
    if not printer_name:
        println("Printer name is required; aborting.")
        return

    last_dir = load_state("printer_dir", "")
    default_dir = last_dir or "/home/pi/printer_data/config"
    printer_dir = input(
        f"Printer config directory (folder with printer.cfg) [{default_dir}]: "
    ).strip() or default_dir

    if not os.path.isdir(printer_dir):
        println(f"Directory does not exist: {printer_dir}")
        return

    # Create cfg + scripts
    ok, cfg_path = make_print_cost_cfg(printer_dir, printer_name)
    if not ok:
        println("Failed to create print_cost.cfg; aborting.")
        return

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

    _ensure_include_in_printer_cfg(printer_dir, "print_cost.cfg")

    # Persist master/client settings
    save_state("master_url", master_url)
    save_state("api_key", api_key)
    save_state("printer_dir", printer_dir)
    save_state("script_path", end_script_path)

    # Run macro integration wizard
    try:
        from installer import installer_macro
        installer_macro.prompt_macro_insertion(printer_name, printer_dir)
        installer_macro.prompt_start_macro_insertion(printer_name, printer_dir)
    except Exception as e:
        println(f"WARNING: Macro integration wizard failed: {e}")
        println("You may need to add KCD blocks to your macros manually.")

    # Register client
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

    Relies on helper functions in installer.remote to write files and
    patch printer.cfg on the remote host.
    """
    println("
=== Remote Client Installation ===")

    use_auto = input("Use auto mode (use saved master settings and known remote printers)? [Y/n]: ").strip().lower()
    auto_mode = use_auto in ("", "y", "yes")

    master_url = load_state("master_url", "http://localhost:5000")
    state_api = load_state("api_key", "")
    secret_api = _load_secret_api_key()
    default_api = state_api or secret_api or secrets.token_hex(16)

    if auto_mode and master_url and default_api:
        println(f"Using saved master URL: {master_url}")
        api_key = default_api
    else:
        if auto_mode and (not master_url or not default_api):
            println("Saved master URL/API missing; falling back to manual entry.")
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
        println("
Registered remote printers:")
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
        println("No registered remote printers found; falling back to manual setup.")
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

    # Discover remote printer_data dirs
    try:
        from installer import remote as r
    except Exception as e:
        println(f"ERROR: Failed to import installer.remote: {e}")
        return

    if not printer_dir:
        candidates: list[str] = []
        try:
            candidates = r.remote_find_printer_data(remote)
        except Exception as e:
            println(f"WARNING: Failed to scan remote for printer_data dirs: {e}")

        if candidates:
            println("
Found the following remote printer_data/config candidates:")
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

    # Paths on remote
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

    include_line = "[include print_cost.cfg]"
    if not r.remote_append_line_if_missing(remote, remote_printer_cfg, include_line):
        println("WARNING: Failed to ensure include line in remote printer.cfg; please check manually.")

    # Persist master settings for future clients
    save_state("master_url", master_url)
    save_state("api_key", api_key)

    # Register remote client
    register_client({
        "type": "remote",
        "printer_name": printer_name,
        "host": remote,
        "config_dir": printer_dir,
    })

    println("
Remote client installation complete.")
    println(f"  Printer: {printer_name}")
    println(f"  Remote: {remote}")
    println(f"  Remote config dir: {printer_dir}")
    println(f"  print_cost.cfg: {remote_cfg_path}")
    println(f"  Job-start script: {remote_job_start}")
    println(f"  Cost script: {remote_end_script}")

