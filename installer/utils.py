# --------------------------------------------------------------------------------------
# File: utils.py
# Description: Utility helpers for Print Cost Dashboard installer.
# Generates print_cost.cfg, shell scripts, and handles local state files.
# --------------------------------------------------------------------------------------

import os
import json
import secrets


# ======================================================================
# File helpers
# ======================================================================

def println(msg=""):
    """Print message with flush."""
    import sys
    print(msg)
    sys.stdout.flush()


def load_state(path):
    """Load installer state JSON file safely."""
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(path, data):
    """Save state JSON file safely."""
    try:
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        return True
    except Exception:
        return False


def load_settings(path):
    """Load settings.json if present."""
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def save_settings(path, data):
    """Save settings.json safely."""
    try:
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        return True
    except Exception:
        return False


# ======================================================================
# File creation templates
# ======================================================================

def make_print_cost_cfg(printer_dir, printer_name):
    """
    Create print_cost.cfg with the shell commands + KCD_JOB_START macro.
    No hard-coded printer names — job start/stop macros derive printer at runtime.
    """
    path = os.path.join(printer_dir, "print_cost.cfg")

    cfg = f"""# ------------------------------------------------------------------------------
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
    RUN_SHELL_COMMAND CMD=kcd_job_start PARAMS="{{params}}"

"""

    try:
        with open(path, "w") as f:
            f.write(cfg)
        return True, path
    except Exception:
        return False, None


# ======================================================================
# Script generators
# ======================================================================

def generate_job_start_script(master_url, api_key):
    """
    Universal job-start script template.
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
    try: return float(v)
    except: return 0.0

data = {{
    "printer_name": os.environ.get("PRINTER", ""),
    "filename": os.environ.get("FILENAME", ""),
    "estimated_duration": to_float(os.environ.get("DUR", "0")),
    "estimated_filament_mm": to_float(os.environ.get("FILAMENT", "0")),
}}

print(json.dumps(data))
PY
)

curl -s -X POST "$MASTER_URL/job-start" \
    -H "Content-Type: application/json" \
    -H "X-API-Key: $API_KEY" \
    -d "$JSON"
"""


def generate_job_end_script(master_url, api_key):
    """
    Universal job-end (cost log) script template.
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
DURATION_SEC="${{3:-0}}"
FILAMENT_MM="${{4:-0}}"

export TS PRINTER FILENAME DURATION_SEC FILAMENT_MM

JSON=$(python3 - <<'PY'
import json, os

def to_float(v):
    try: return float(v)
    except: return 0.0

def to_int(v):
    try: return int(float(v))
    except: return 0

data = {{
    "timestamp": to_int(os.environ.get("TS", "0")),
    "printer": os.environ.get("PRINTER", ""),
    "filename": os.environ.get("FILENAME", ""),
    "duration_seconds": to_float(os.environ.get("DURATION_SEC", "0")),
    "filament_mm": to_float(os.environ.get("FILAMENT_MM", "0")),
}}

print(json.dumps(data))
PY
)

curl -s -X POST "$MASTER_URL/log-print" \
    -H "Content-Type: application/json" \
    -H "X-API-Key: $API_KEY" \
    -d "$JSON"
"""


def write_script(path, content):
    """Write script with executable permissions."""
    try:
        with open(path, "w") as f:
            f.write(content)
        os.chmod(path, 0o755)
        return True
    except Exception:
        return False
