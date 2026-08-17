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
import sys
import subprocess
import socket
import urllib.request
import urllib.parse
import urllib.error
from contextlib import closing
from typing import Any, Dict, List, Optional, Tuple
from . import remote as r
import installer_macro

from core.config import DATA_DIR, SETTINGS_FILE, CSV_FILE
from core.storage import (
    load_state as _load_state_key,
    save_state as _save_state_key,
    ensure_api_key,
    load_settings,
    save_settings,
)

STATE_FILE = os.path.join(DATA_DIR, "install_state.json")
DEFAULT_PORT = 5000
DEFAULT_SERVICE_NAME = "print-cost-dashboard"


def _sql_db_path() -> str:
    return os.path.join(DATA_DIR, "kcd.db")


def _detect_sql_state() -> Dict[str, Any]:
    db_path = _sql_db_path()
    db_exists = os.path.exists(db_path)
    csv_exists = os.path.exists(CSV_FILE)
    schema_ok = False
    schema_version = None

    if db_exists:
        try:
            from core import db as db_module
            with closing(db_module.connect_db()) as conn:
                # Apply migrations so we can validate schema state.
                db_module.apply_migrations(conn)
                schema_version = db_module.current_schema_version(conn)
                schema_ok = bool(schema_version)
        except Exception:
            schema_ok = False

    if db_exists and schema_ok:
        status = "sql_capable"
    elif db_exists and not schema_ok:
        status = "sql_needs_migration"
    elif csv_exists:
        status = "csv_only"
    else:
        status = "fresh"

    return {
        "status": status,
        "db_exists": db_exists,
        "schema_ok": schema_ok,
        "schema_version": schema_version,
        "csv_exists": csv_exists,
        "db_path": db_path,
    }


def _ensure_sql_capable(import_from_csv: bool = False) -> bool:
    try:
        from core import db as db_module
        from core import db_import
    except Exception as e:
        println(f"WARNING: SQL support unavailable: {e}")
        return False

    try:
        with closing(db_module.connect_db()) as conn:
            applied = db_module.apply_migrations(conn)
            if applied:
                println(f"SQL migrations applied: {', '.join(applied)}")
        if import_from_csv and os.path.exists(CSV_FILE):
            println("Importing CSV into SQLite (best-effort)...")
            db_import.run_import(skip_existing=True, overwrite=False)
        return True
    except Exception as e:
        println(f"WARNING: failed to initialize SQL database: {e}")
        return False


def _sync_printer_to_sql(printer_name: str, moonraker_url: str, external_id: Optional[str] = None) -> bool:
    try:
        from core import db as db_module
        from core import printer_lifecycle
        with closing(db_module.connect_db()) as conn:
            db_module.apply_migrations(conn)
            existing = conn.execute("SELECT id FROM printers WHERE name = ?", (printer_name,)).fetchone()
        if existing:
            printer_lifecycle.reactivate_printer(
                printer_name,
                moonraker_url=moonraker_url,
                external_id=external_id,
            )
        else:
            with closing(db_module.connect_db()) as conn:
                db_module.apply_migrations(conn)
                db_module.upsert_printer(conn, printer_name, moonraker_url, external_id=external_id)
                conn.commit()
        return True
    except Exception as e:
        println(f"WARNING: failed to sync printer to SQL: {e}")
        return False


# ----------------------------------------------------------------------
# Basic helpers
# ----------------------------------------------------------------------

def _mask_secret(value: str) -> str:
    raw = str(value or "")
    if not raw:
        return ""
    if len(raw) <= 8:
        return raw[0:1] + "***"
    return raw[:4] + "..." + raw[-4:]


def println(msg: str = "") -> None:
    import sys
    print(msg)
    sys.stdout.flush()


def _run_cmd(cmd: str, cwd: Optional[str] = None) -> int:
    println(f"  $ {cmd}")
    try:
        proc = subprocess.run(
            cmd,
            shell=True,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except Exception as e:
        println(f"  Command failed: {e}")
        return 1

    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if out:
        println(out)
    if err:
        println(err)
    return proc.returncode


def _systemctl_exists() -> bool:
    return bool(shutil.which("systemctl"))


def _service_exists(service_name: str) -> bool:
    if not _systemctl_exists():
        return False
    try:
        proc = subprocess.run(
            ["systemctl", "show", "-p", "LoadState", "--value", service_name],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if proc.returncode != 0:
            return False
        state = (proc.stdout or "").strip()
        return state == "loaded"
    except Exception:
        return False


def _service_is_active(service_name: str) -> str:
    if not _systemctl_exists():
        return "unknown"
    try:
        proc = subprocess.run(
            ["systemctl", "is-active", service_name],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if proc.returncode != 0:
            return (proc.stdout or proc.stderr or "inactive").strip()
        return (proc.stdout or "inactive").strip()
    except Exception:
        return "unknown"


def _service_is_enabled(service_name: str) -> str:
    if not _systemctl_exists():
        return "unknown"
    try:
        proc = subprocess.run(
            ["systemctl", "is-enabled", service_name],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if proc.returncode != 0:
            return (proc.stdout or proc.stderr or "disabled").strip()
        return (proc.stdout or "disabled").strip()
    except Exception:
        return "unknown"


def _check_port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=2.0):
            return True
    except Exception:
        return False


def _check_url(url: str) -> Tuple[bool, str]:
    try:
        req = urllib.request.Request(url, headers={"Accept": "text/html"})
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            status = getattr(resp, "status", None) or 0
        return status == 200, f"HTTP {status}"
    except Exception as e:
        return False, str(e)


def _repo_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _venv_paths(root_dir: str) -> Tuple[str, str, str]:
    venv_dir = os.path.join(root_dir, ".venv")
    if os.name == "nt":
        venv_python = os.path.join(venv_dir, "Scripts", "python.exe")
        venv_pip = os.path.join(venv_dir, "Scripts", "pip.exe")
    else:
        venv_python = os.path.join(venv_dir, "bin", "python")
        venv_pip = os.path.join(venv_dir, "bin", "pip")
    return venv_dir, venv_python, venv_pip


def _installer_runtime_backends(sql_enabled: bool) -> Tuple[str, str]:
    """Return the supported installer write/read backend pair."""
    return ("dual", "auto") if sql_enabled else ("csv", "csv")


def _render_systemd_service(
    service_name: str,
    workdir: str,
    venv_python: str,
    port: int,
    *,
    storage_backend: str,
    reports_backend: str,
) -> str:
    storage_backend = str(storage_backend or "").strip().lower()
    reports_backend = str(reports_backend or "").strip().lower()
    if (storage_backend, reports_backend) not in {("csv", "csv"), ("dual", "auto")}:
        raise ValueError(
            "installer service requires storage/reports backends csv/csv or dual/auto"
        )

    user = os.getenv("SUDO_USER") or os.getenv("USER") or ""
    env_lines = [
        "Environment=PYTHONUNBUFFERED=1",
        "Environment=FLASK_APP=app.py",
        f"Environment=KCD_STORAGE_BACKEND={storage_backend}",
        f"Environment=KCD_REPORTS_BACKEND={reports_backend}",
    ]
    unit = [
        "[Unit]",
        f"Description=Klipper Cost Dashboard ({service_name})",
        "After=network.target",
        "",
        "[Service]",
        "Type=simple",
        f"WorkingDirectory={workdir}",
    ]
    if user:
        unit.append(f"User={user}")
    unit.extend(env_lines)
    unit.extend(
        [
            f"ExecStart={venv_python} -m flask run --host 0.0.0.0 --port {port}",
            "Restart=always",
            "RestartSec=3",
            "",
            "[Install]",
            "WantedBy=multi-user.target",
            "",
        ]
    )
    return "\n".join(unit)


def _write_systemd_service(
    service_name: str,
    workdir: str,
    venv_python: str,
    port: int,
    *,
    storage_backend: str,
    reports_backend: str,
) -> str:
    service_path = f"/etc/systemd/system/{service_name}.service"
    content = _render_systemd_service(
        service_name,
        workdir,
        venv_python,
        port,
        storage_backend=storage_backend,
        reports_backend=reports_backend,
    )
    try:
        with open(service_path, "w", encoding="utf-8") as f:
            f.write(content)
        return service_path
    except Exception as e:
        println(f"Failed to write systemd service file: {e}")
        return ""


def _print_master_summary(status: str, service_name: str, port: int) -> None:
    if status == "success":
        println("\nOK: Master install complete")
    elif status == "skipped":
        println("\nSKIP: Master install skipped (already installed)")
    else:
        println("\nFAIL: Master install failed")
    println(f"Service name : {service_name}")
    println(f"Port         : {port}")
    println(f"Logs         : journalctl -u {service_name} -f")


def _perform_master_install(
    port: int,
    url: str,
    service_name: str,
    *,
    storage_backend: str,
    reports_backend: str,
) -> bool:
    repo_root = _repo_root()
    venv_dir, venv_python, venv_pip = _venv_paths(repo_root)
    python_bin = shutil.which("python3") or sys.executable
    if not python_bin:
        println("Python3 not found; cannot create virtualenv.")
        return False

    steps = [
        ("Creating virtual environment", f"{python_bin} -m venv \"{venv_dir}\""),
        ("Installing dependencies", f"\"{venv_pip}\" install -r \"{os.path.join(repo_root, 'requirements.txt')}\""),
    ]

    for idx, (label, cmd) in enumerate(steps, 1):
        println(f"[{idx}/{len(steps) + 3}] {label}...")
        code = _run_cmd(cmd, cwd=repo_root)
        if code != 0:
            return False

    println(f"[{len(steps) + 1}/{len(steps) + 3}] Writing config files...")
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        ensure_api_key(secret_file=os.path.join(DATA_DIR, "secret.json"), data_dir=DATA_DIR)
    except Exception as e:
        println(f"Failed to ensure config files: {e}")
        return False

    println(f"[{len(steps) + 2}/{len(steps) + 3}] Writing systemd service...")
    service_path = _write_systemd_service(
        service_name,
        repo_root,
        venv_python,
        port,
        storage_backend=storage_backend,
        reports_backend=reports_backend,
    )
    if not service_path:
        return False

    println(f"[{len(steps) + 3}/{len(steps) + 3}] Enabling and starting service...")
    _run_cmd("systemctl daemon-reload")
    if _run_cmd(f"systemctl enable --now {service_name}") != 0:
        return False

    status = _service_is_active(service_name)
    println(f"Service status : {status}")
    println(f"Port listening : {'yes' if _check_port_open(port) else 'no'}")
    ok, detail = _check_url(url)
    println(f"URL check      : {'ok' if ok else 'failed'} ({detail})")
    return True


def _master_install_or_status(
    host: str,
    port: int,
    url: str,
    service_name: str,
    *,
    storage_backend: str,
    reports_backend: str,
) -> None:
    println("\n=== Master Installation ===")
    if not _systemctl_exists():
        println("systemctl not found; cannot manage the master service automatically.")
        _print_master_summary("failed", service_name, port)
        return

    if _service_exists(service_name):
        println(f"Master already installed: service {service_name} found")
        enabled = _service_is_enabled(service_name)
        status = _service_is_active(service_name)
        println(f"Service enabled: {enabled}")
        println(f"Service active : {status}")
        println(f"Port listening : {'yes' if _check_port_open(port) else 'no'}")
        ok, detail = _check_url(url)
        println(f"URL check      : {'ok' if ok else 'failed'} ({detail})")
        println(f"Saved URL      : {url}")
        println(f"Storage backend: {storage_backend}")
        println(f"Reports backend: {reports_backend}")

        while True:
            choice = _safe_input(
                "Options: (R)estart service, (V)iew logs, (F)orce reinstall, (B)ack: "
            ).strip().lower()
            if choice in ("b", "", "back"):
                _print_master_summary("skipped", service_name, port)
                return
            if choice in ("r", "restart"):
                _run_cmd(f"systemctl restart {service_name}")
                status = _service_is_active(service_name)
                println(f"Service active : {status}")
                continue
            if choice in ("v", "view"):
                _run_cmd(f"journalctl -u {service_name} -n 100 --no-pager")
                continue
            if choice in ("f", "force"):
                ok_install = _perform_master_install(
                    port,
                    url,
                    service_name,
                    storage_backend=storage_backend,
                    reports_backend=reports_backend,
                )
                _print_master_summary("success" if ok_install else "failed", service_name, port)
                return
            println("Invalid option. Choose R, V, F, or B.")

    ok_install = _perform_master_install(
        port,
        url,
        service_name,
        storage_backend=storage_backend,
        reports_backend=reports_backend,
    )
    _print_master_summary("success" if ok_install else "failed", service_name, port)


def _emit_system_event(category: str, title: str, message: str, meta: Optional[Dict[str, Any]] = None) -> None:
    """
    Best-effort system event emitter for installer runs.

    Installer should never crash if the dashboard code isn't available.
    """
    try:
        from core import system_events
        system_events.emit_event(category, title, message, meta=meta)
    except Exception:
        return


def _safe_input(prompt: str) -> str:
    try:
        return input(prompt)
    except (EOFError, KeyboardInterrupt):
        println("\nInput cancelled. Exiting.")
        sys.exit(1)


def _format_valid_options(options: list[int]) -> str:
    if not options:
        return ""
    if len(options) == 1:
        return str(options[0])
    return ", ".join(map(str, options[:-1])) + f", or {options[-1]}"


def prompt_yes_no(question: str, default: bool = True) -> bool:
    prompt = "[Y/n]" if default else "[y/N]"
    while True:
        ans = _safe_input(f"{question} {prompt}: ").strip().lower()
        if ans == "":
            return default
        if ans in ("y", "yes"):
            return True
        if ans in ("n", "no"):
            return False
        println("Invalid input. Please enter Y or N.")


# ----------------------------------------------------------------------
# Moonraker URL detection (local-only)
# ----------------------------------------------------------------------

def _normalize_url(url: str) -> str:
    url = str(url or "").strip()
    if not url:
        return ""
    if "://" not in url:
        url = "http://" + url
    return url.rstrip("/")


def test_moonraker_url(url: str, timeout_s: float = 4.0) -> Tuple[bool, str]:
    """
    Validate Moonraker reachability by calling GET <url>/server/info.
    Returns (ok, detail).
    """
    base = _normalize_url(url)
    if not base:
        return False, "Empty URL"
    test_url = f"{base}/server/info"
    try:
        req = urllib.request.Request(test_url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            status = getattr(resp, "status", None)
            body = resp.read()
        if status != 200:
            return False, f"HTTP {status}"
        try:
            data = json.loads(body.decode("utf-8", errors="replace"))
        except Exception:
            return False, "Invalid JSON response"
        if not isinstance(data, dict):
            return False, "Unexpected response"
        # Moonraker typically returns {"result": {...}}.
        if "result" not in data:
            return False, "Missing 'result' key (not Moonraker?)"
        return True, "OK"
    except urllib.error.HTTPError as e:
        return False, f"HTTPError: {getattr(e, 'code', '')}"
    except urllib.error.URLError as e:
        return False, f"URLError: {e}"
    except Exception as e:
        return False, f"Error: {e}"


def _autodetect_moonraker_local() -> List[str]:
    """
    Auto-detect Moonraker on the local machine / docker-host only.
    No LAN scan.
    """
    hosts = [
        "127.0.0.1",
        "localhost",
        "host.docker.internal",
        "172.17.0.1",
    ]
    ports = [7125, 7126, 7127]
    found: List[str] = []
    for h in hosts:
        for p in ports:
            url = f"http://{h}:{p}"
            ok, _ = test_moonraker_url(url, timeout_s=4.0)
            if ok:
                found.append(url)
    # De-dupe while preserving order
    seen = set()
    unique = []
    for u in found:
        if u in seen:
            continue
        seen.add(u)
        unique.append(u)
    return unique


def _configure_moonraker_url_local(printer_name: str) -> Optional[str]:
    """
    Prompt for Moonraker URL, optionally auto-detecting only local/docker-host candidates.
    Saves nothing by itself; returns a validated URL string or None if user backs out.
    """
    printer_name = str(printer_name or "").strip()
    settings = load_settings(SETTINGS_FILE)
    current = ""
    if isinstance(settings, dict):
        current = str(settings.get(printer_name, {}).get("moonraker_url") or "").strip()

    if current:
        ok, detail = test_moonraker_url(current)
        if ok:
            println(f"[auto] Moonraker reachable at {current}")
            return _normalize_url(current)
        println(f"Saved Moonraker URL failed test: {current} ({detail})")

    while True:
        println("\nMoonraker URL setup:")
        println("  1) Enter Moonraker URL manually")
        println("  2) Auto-detect Moonraker on this machine (recommended)")
        println("  3) Back")
        choice = prompt_choice("Select option [1-3]: ", [1, 2, 3])
        if choice == 3:
            return None

        if choice == 2:
            println("[auto] Trying common local Moonraker addresses (no LAN scan)...")
            matches = _autodetect_moonraker_local()
            if not matches:
                println("Auto-detect failed. Moonraker may be on another machine; enter printer IP:port.")
                continue
            if len(matches) == 1:
                picked = matches[0]
                println(f"[auto] Found Moonraker: {picked}")
                ok, detail = test_moonraker_url(picked)
                if ok:
                    println(f"Moonraker reachable at {picked}")
                    return _normalize_url(picked)
                println(f"Auto-detected URL failed test: {detail}")
                continue

            println("Multiple Moonraker instances found:")
            for i, u in enumerate(matches, 1):
                println(f"  {i}) {u}")
            sel = prompt_choice(f"Select [1-{len(matches)}] (or 0 to cancel): ", range(0, len(matches) + 1))
            if sel is None or sel == 0:
                continue
            picked = matches[sel - 1]
            ok, detail = test_moonraker_url(picked)
            if ok:
                println(f"Moonraker reachable at {picked}")
                return _normalize_url(picked)
            println(f"Selected URL failed test: {detail}")
            continue

        # Manual entry
        raw = _safe_input("Moonraker URL (e.g. http://192.168.2.55:7125): ").strip()
        if not raw:
            println("Moonraker URL is required.")
            continue
        url = _normalize_url(raw)
        ok, detail = test_moonraker_url(url)
        if ok:
            println(f"Moonraker reachable at {url}")
            return url
        println(f"Failed to reach Moonraker at {url}: {detail}")
        if prompt_yes_no("Save anyway?", default=False):
            return url




def _configure_moonraker_url_remote(printer_name: str, default_url: Optional[str] = None) -> Optional[str]:
    # Prompt for Moonraker URL for remote printers (manual entry only).
    printer_name = str(printer_name or "").strip()
    settings = load_settings(SETTINGS_FILE)
    current = ""
    if isinstance(settings, dict):
        current = str(settings.get(printer_name, {}).get("moonraker_url") or "").strip()
    if default_url and not current:
        current = str(default_url).strip()

    if current:
        ok, detail = test_moonraker_url(current)
        if ok:
            println(f"[auto] Moonraker reachable at {current}")
            return _normalize_url(current)
        println(f"Saved Moonraker URL failed test: {current} ({detail})")

    while True:
        raw = _safe_input("Moonraker URL for this printer (leave blank to skip): ").strip()
        if not raw:
            if prompt_yes_no("Skip Moonraker URL setup?", default=True):
                return None
            continue
        url = _normalize_url(raw)
        ok, detail = test_moonraker_url(url)
        if ok:
            println(f"Moonraker reachable at {url}")
            return url
        println(f"Failed to reach Moonraker at {url}: {detail}")
        if prompt_yes_no("Save anyway?", default=False):
            return url


def prompt_choice(prompt: str, valid, allow_empty: bool = False, cancel_inputs: Optional[list[str]] = None) -> Optional[int]:
    options = list(valid)
    invalid_msg = f"That's not an option. Please choose {_format_valid_options(options)}."
    cancel_set = {c.lower() for c in cancel_inputs} if cancel_inputs else set()

    while True:
        ans = _safe_input(prompt).strip()
        if ans == "" and allow_empty:
            return None
        if ans.lower() in cancel_set:
            return None
        try:
            choice = int(ans)
        except ValueError:
            println(invalid_msg)
            continue
        if choice in options:
            return choice
        println(invalid_msg)


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

def _find_remote_registry_entry_by_host_dir(host: str, config_dir: str) -> Tuple[int, Dict[str, Any]]:
    """
    Find a remote client registry entry by host + config_dir.
    Returns (index, entry) or (-1, {}).
    """
    registry = get_client_registry()
    for i, entry in enumerate(registry):
        if entry.get("type") != "remote":
            continue
        if entry.get("host") == host and entry.get("config_dir") == config_dir:
            return i, entry
    return -1, {}


def _get_remote_macro_prefs(host: str, config_dir: str) -> Dict[str, Any]:
    _, entry = _find_remote_registry_entry_by_host_dir(host, config_dir)
    prefs = entry.get("macro_prefs", {})
    return prefs if isinstance(prefs, dict) else {}


def _save_remote_macro_prefs(
    host: str,
    config_dir: str,
    start: Optional[Dict[str, str]] = None,
    end: Optional[Dict[str, str]] = None,
    cancel: Optional[Dict[str, str]] = None,
) -> None:
    """
    Persist macro integration selections per remote printer in install_state.json.
    Matches a client by host + config_dir and stores:
      macro_prefs: { "start": {"file": ..., "macro": ...}, "end": {...}, "cancel": {...} }
    """
    registry = get_client_registry()
    idx, entry = _find_remote_registry_entry_by_host_dir(host, config_dir)
    if idx < 0:
        return

    prefs = entry.get("macro_prefs", {})
    if not isinstance(prefs, dict):
        prefs = {}

    if isinstance(start, dict) and start.get("file") and start.get("macro"):
        prefs["start"] = {"file": start["file"], "macro": start["macro"]}
    if isinstance(end, dict) and end.get("file") and end.get("macro"):
        prefs["end"] = {"file": end["file"], "macro": end["macro"]}
    if isinstance(cancel, dict) and cancel.get("file") and cancel.get("macro"):
        prefs["cancel"] = {"file": cancel["file"], "macro": cancel["macro"]}

    entry["macro_prefs"] = prefs
    registry[idx] = entry
    _set_client_registry(registry)


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
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                existing = f.read()
            has_expected = (
                ("[gcode_shell_command kcd_job_cancel]" in existing)
                and ("[gcode_macro KCD_JOB_CANCEL]" in existing)
                and ("[gcode_shell_command kcd_job_pause]" in existing)
                and ("[gcode_macro KCD_JOB_PAUSE]" in existing)
                and ("[gcode_shell_command kcd_job_resume]" in existing)
                and ("[gcode_macro KCD_JOB_RESUME]" in existing)
                and ("params.REASON" in existing)
            )
        except Exception:
            has_expected = False
        if has_expected and _try_update_kcd_vars_printer_name(path, printer_name):
            return True, path
    template = """
# ----------------------------------------------------------------------
# Auto-generated by installer
[gcode_shell_command send_print_cost]
# Quote {params} with double quotes so apostrophes in filenames stay shell-safe.
command: __PRINTER_DIR__/send_print_cost.sh "{params}"
timeout: 15.0
verbose: True

[gcode_shell_command kcd_job_start]
command: __PRINTER_DIR__/kcd_job_start.sh "{params}"
timeout: 10.0
verbose: True

[gcode_shell_command kcd_job_cancel]
command: __PRINTER_DIR__/kcd_job_cancel.sh "{params}"
timeout: 10.0
verbose: True

[gcode_shell_command kcd_job_pause]
command: __PRINTER_DIR__/kcd_job_pause.sh "{params}"
timeout: 10.0
verbose: True

[gcode_shell_command kcd_job_resume]
command: __PRINTER_DIR__/kcd_job_resume.sh "{params}"
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
    {% set fname = fname|replace('\\\\', '\\\\\\\\')|replace('\"', '\\\\\"') %}
    {% set est_dur = printer.print_stats.estimated_time|default(0)|float %}
    {% set est_filament = printer.print_stats.filament|default(0)|float %}
    {% set params = printer_name ~ "|" ~ fname ~ "|" ~ est_dur ~ "|" ~ est_filament %}
    RUN_SHELL_COMMAND CMD=kcd_job_start PARAMS="{params}"

[gcode_macro KCD_JOB_CANCEL]
description: Notify dashboard that a print was canceled
gcode:
    # KCD: log cancel to dashboard
    {% set printer_name = printer["gcode_macro _KCD_VARS"].printer_name|string %}
    {% set fname = printer.print_stats.filename|default("unknown.gcode", true)|string %}
    {% set fname = fname|replace('\\\\', '\\\\\\\\')|replace('\"', '\\\\\"') %}
    {% set elapsed = printer.print_stats.print_duration|default(0)|float %}
    {% set params = printer_name ~ "|" ~ fname ~ "|" ~ elapsed %}
    RUN_SHELL_COMMAND CMD=kcd_job_cancel PARAMS="{params}"

[gcode_macro KCD_JOB_PAUSE]
description: Notify dashboard that a print was paused
gcode:
    # KCD: log pause to dashboard
    {% set printer_name = printer["gcode_macro _KCD_VARS"].printer_name|string %}
    {% set fname = printer.print_stats.filename|default("unknown.gcode", true)|string %}
    {% set fname = fname|replace('\\\\', '\\\\\\\\')|replace('\"', '\\\\\"') %}
    {% set elapsed = printer.print_stats.print_duration|default(0)|float %}
    {% set reason = params.REASON|default("", true)|string %}
    {% set reason = reason|replace('\\\\', '\\\\\\\\')|replace('\"', '\\\\\"') %}
    {% set params = printer_name ~ "|" ~ fname ~ "|" ~ elapsed ~ "|" ~ reason %}
    RUN_SHELL_COMMAND CMD=kcd_job_pause PARAMS="{params}"

[gcode_macro KCD_JOB_RESUME]
description: Notify dashboard that a print was resumed
gcode:
    # KCD: log resume to dashboard
    {% set printer_name = printer["gcode_macro _KCD_VARS"].printer_name|string %}
    {% set fname = printer.print_stats.filename|default("unknown.gcode", true)|string %}
    {% set fname = fname|replace('\\\\', '\\\\\\\\')|replace('\"', '\\\\\"') %}
    {% set elapsed = printer.print_stats.print_duration|default(0)|float %}
    {% set params = printer_name ~ "|" ~ fname ~ "|" ~ elapsed %}
    RUN_SHELL_COMMAND CMD=kcd_job_resume PARAMS="{params}"

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


def generate_job_cancel_script(master_url: str, api_key: str) -> str:
    return f"""#!/usr/bin/env bash
set -euo pipefail

MASTER_URL="{master_url}"
API_KEY="{api_key}"

PARAMS="${{*:-}}"
IFS='|' read -r PRINTER_NAME FILENAME ELAPSED_SECONDS <<< "$PARAMS"
PRINTER_NAME="${{PRINTER_NAME:-}}"
FILENAME="${{FILENAME:-}}"
ELAPSED_SECONDS="${{ELAPSED_SECONDS:-0}}"

echo "KCD_JOB_CANCEL DEBUG: PARAMS='$PARAMS' PRINTER_NAME='$PRINTER_NAME' FILENAME='$FILENAME' ELAPSED_SECONDS='$ELAPSED_SECONDS'"

export PRINTER_NAME FILENAME ELAPSED_SECONDS

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
    "elapsed_seconds": to_float(os.environ.get("ELAPSED_SECONDS", "0")),
}}
print(json.dumps(data))
PY
)

curl -s -X POST "$MASTER_URL/job-cancel" \\
  -H "Content-Type: application/json" \\
  -H "X-API-Key: $API_KEY" \\
  -d "$JSON"
"""


def generate_job_pause_script(master_url: str, api_key: str) -> str:
    return f"""#!/usr/bin/env bash
set -euo pipefail

MASTER_URL="{master_url}"
API_KEY="{api_key}"

PARAMS="${{*:-}}"
IFS='|' read -r PRINTER_NAME FILENAME ELAPSED_SECONDS REASON <<< "$PARAMS"
PRINTER_NAME="${{PRINTER_NAME:-}}"
FILENAME="${{FILENAME:-}}"
ELAPSED_SECONDS="${{ELAPSED_SECONDS:-0}}"
REASON="${{REASON:-}}"

echo "KCD_JOB_PAUSE DEBUG: PARAMS='$PARAMS' PRINTER_NAME='$PRINTER_NAME' FILENAME='$FILENAME' ELAPSED_SECONDS='$ELAPSED_SECONDS' REASON='$REASON'"

export PRINTER_NAME FILENAME ELAPSED_SECONDS REASON

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
    "elapsed_seconds": to_float(os.environ.get("ELAPSED_SECONDS", "0")),
    "reason": os.environ.get("REASON", ""),
}}
print(json.dumps(data))
PY
)

curl -s -X POST "$MASTER_URL/job-pause" \\
  -H "Content-Type: application/json" \\
  -H "X-API-Key: $API_KEY" \\
  -d "$JSON"
"""


def generate_job_resume_script(master_url: str, api_key: str) -> str:
    return f"""#!/usr/bin/env bash
set -euo pipefail

MASTER_URL="{master_url}"
API_KEY="{api_key}"

PARAMS="${{*:-}}"
IFS='|' read -r PRINTER_NAME FILENAME ELAPSED_SECONDS <<< "$PARAMS"
PRINTER_NAME="${{PRINTER_NAME:-}}"
FILENAME="${{FILENAME:-}}"
ELAPSED_SECONDS="${{ELAPSED_SECONDS:-0}}"

echo "KCD_JOB_RESUME DEBUG: PARAMS='$PARAMS' PRINTER_NAME='$PRINTER_NAME' FILENAME='$FILENAME' ELAPSED_SECONDS='$ELAPSED_SECONDS'"

export PRINTER_NAME FILENAME ELAPSED_SECONDS

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
    "elapsed_seconds": to_float(os.environ.get("ELAPSED_SECONDS", "0")),
}}
print(json.dumps(data))
PY
)

curl -s -X POST "$MASTER_URL/job-resume" \\
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
        _emit_system_event(
            "warning",
            "Manual action required (installer)",
            f"printer.cfg not found; add [include {include_filename}] manually for the client install to work.",
            meta={"action": "install_client_local", "printer_cfg": "missing"},
        )
        return
    try:
        with open(printer_cfg_path, "r") as f:
            text = f.read()
    except Exception as e:
        println(f"WARNING: Failed to read {printer_cfg_path}: {e}")
        _emit_system_event(
            "warning",
            "Manual action required (installer)",
            "Could not read printer.cfg while trying to add the KCD include line. You may need to add it manually.",
            meta={"action": "install_client_local", "printer_cfg": "read_failed"},
        )
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
        _emit_system_event(
            "warning",
            "Manual action required (installer)",
            "Could not update printer.cfg to add the KCD include line. You may need to add it manually.",
            meta={"action": "install_client_local", "printer_cfg": "write_failed"},
        )


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

    auto_mode = prompt_yes_no("Use auto mode (reuse saved master settings)?", default=True)

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
        host = _safe_input(f"Master host [{current_host}]: ").strip() or current_host
        port_str = _safe_input(f"Master port [{current_port}]: ").strip() or current_port
        url_default = current_url or f"http://{host}:{port_str}"
        url = _safe_input(f"Master URL [{url_default}]: ").strip() or url_default
        service_name = _safe_input(f"Service name [{current_service}]: ").strip() or current_service
        api_key = _safe_input(f"API key for printers [{default_api}]: ").strip() or default_api

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
    println(f"  API key: {_mask_secret(api_key)}")

    sql_state = _detect_sql_state()
    sql_enabled = False
    if sql_state.get("status") == "sql_capable":
        println("[sql] SQL-capable install detected (CSV may also exist; running dual/compat mode).")
        sql_enabled = True
    elif sql_state.get("status") == "sql_needs_migration":
        println("[sql] SQLite DB detected but schema outdated; applying migrations...")
        sql_enabled = _ensure_sql_capable(import_from_csv=False)
    elif sql_state.get("status") == "csv_only":
        println("[sql] CSV-only install detected.")
        if prompt_yes_no("Enable SQL-capable mode (initialize DB and import CSV)?", default=False):
            sql_enabled = _ensure_sql_capable(import_from_csv=True)
    else:
        println("[sql] No CSV or DB detected (fresh install).")
        if prompt_yes_no("Enable SQL-capable mode (initialize DB)?", default=True):
            sql_enabled = _ensure_sql_capable(import_from_csv=False)

    if sql_enabled:
        println("[sql] SQL compatibility mode enabled: dual writes with automatic SQL Reports reads.")
        try:
            s = load_settings(SETTINGS_FILE)
            if isinstance(s, dict):
                synced = 0
                for pname, pdata in s.items():
                    if not isinstance(pdata, dict):
                        continue
                    mr = pdata.get("moonraker_url")
                    if not mr:
                        continue
                    if _sync_printer_to_sql(pname, mr):
                        synced += 1
                if synced:
                    println(f"[sql] Synced {synced} printers to SQL.")
        except Exception as e:
            println(f"[sql] WARNING: failed to sync printers to SQL: {e}")
    else:
        println("[sql] SQL mode not enabled; continuing in CSV-only mode.")

    storage_backend, reports_backend = _installer_runtime_backends(sql_enabled)
    save_state("master_storage_backend", storage_backend)
    save_state("master_reports_backend", reports_backend)
    println(f"[runtime] Service contract: storage={storage_backend}, reports={reports_backend}")
    _master_install_or_status(
        host,
        port,
        url,
        service_name,
        storage_backend=storage_backend,
        reports_backend=reports_backend,
    )

    if master_and_client:
        println("\nContinuing with local client installation on this machine...")
        install_client_local()


def install_client_local() -> None:
    println("\n=== Local Client Installation ===")

    auto_mode = prompt_yes_no("Use auto mode (reuse saved settings and printer dir)?", default=True)

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
        master_url = _safe_input(f"Master URL for dashboard [{master_url}]: ").strip() or master_url
        api_key = _safe_input(f"API key for this printer [{default_api}]: ").strip() or default_api

        printer_name = _safe_input("Printer name for dashboard (e.g., SV08): ").strip()
        if not printer_name:
            println("Printer name is required; aborting.")
            return

        printer_dir = _safe_input(
            f"Printer config directory (folder with printer.cfg) [{default_dir}]: "
        ).strip() or default_dir
        if not os.path.isdir(printer_dir):
            println(f"Directory does not exist: {printer_dir}")
            return
    else:
        println(f"[auto] Using saved master URL: {master_url}")
        println(f"[auto] Using printer: {printer_name}")
        println(f"[auto] Using config dir: {printer_dir}")

    moonraker_url = _configure_moonraker_url_local(printer_name)
    if not moonraker_url:
        println("Moonraker URL setup cancelled; aborting local client install.")
        return

    ok, cfg_path = make_print_cost_cfg(printer_dir, printer_name)
    if not ok:
        println("Failed to create print_cost.cfg; aborting.")
        return
    if auto_mode:
        println(f"[auto] Wrote print_cost.cfg to {cfg_path}")

    job_start_script = generate_job_start_script(master_url, api_key)
    job_cancel_script = generate_job_cancel_script(master_url, api_key)
    job_pause_script = generate_job_pause_script(master_url, api_key)
    job_resume_script = generate_job_resume_script(master_url, api_key)
    end_script = generate_job_end_script(master_url, api_key)

    job_start_path = os.path.join(printer_dir, "kcd_job_start.sh")
    job_cancel_path = os.path.join(printer_dir, "kcd_job_cancel.sh")
    job_pause_path = os.path.join(printer_dir, "kcd_job_pause.sh")
    job_resume_path = os.path.join(printer_dir, "kcd_job_resume.sh")
    end_script_path = os.path.join(printer_dir, "send_print_cost.sh")

    if not write_script(job_start_path, job_start_script):
        println("Failed to write kcd_job_start.sh; aborting.")
        return
    if not write_script(job_cancel_path, job_cancel_script):
        println("Failed to write kcd_job_cancel.sh; aborting.")
        return
    if not write_script(job_pause_path, job_pause_script):
        println("Failed to write kcd_job_pause.sh; aborting.")
        return
    if not write_script(job_resume_path, job_resume_script):
        println("Failed to write kcd_job_resume.sh; aborting.")
        return
    if not write_script(end_script_path, end_script):
        println("Failed to write send_print_cost.sh; aborting.")
        return
    if auto_mode:
        println(f"[auto] Wrote kcd_job_start.sh to {job_start_path}")
        println(f"[auto] Wrote kcd_job_cancel.sh to {job_cancel_path}")
        println(f"[auto] Wrote kcd_job_pause.sh to {job_pause_path}")
        println(f"[auto] Wrote kcd_job_resume.sh to {job_resume_path}")
        println(f"[auto] Wrote send_print_cost.sh to {end_script_path}")

    _ensure_include_in_printer_cfg(printer_dir, "print_cost.cfg")
    if auto_mode:
        println("[auto] Checked [include print_cost.cfg] in printer.cfg")

    try:
        installer_macro.run_macro_integration(printer_name, printer_dir)
    except Exception as e:
        println(f"WARNING: Macro integration wizard failed: {e}")
        println("You may need to add KCD blocks to your macros manually.")
        _emit_system_event(
            "warning",
            "Manual action required (installer)",
            "Macro integration failed; you may need to add KCD blocks to PRINT_START/END_PRINT manually.",
            meta={"action": "install_client_local", "printer": printer_name},
        )

    save_state("master_url", master_url)
    save_state("api_key", api_key)
    save_state("printer_dir", printer_dir)
    save_state("script_path", end_script_path)
    save_state("printer_name", printer_name)

    # Persist per-printer Moonraker URL for thumbnail fetching (critical for Docker installs).
    try:
        s = load_settings(SETTINGS_FILE)
        if not isinstance(s, dict):
            s = {}
        if printer_name not in s or not isinstance(s.get(printer_name), dict):
            s[printer_name] = {}
        s[printer_name]["moonraker_url"] = moonraker_url
        save_settings(SETTINGS_FILE, DATA_DIR, s)
        if auto_mode:
            println(f"[auto] Saved Moonraker URL for {printer_name}: {moonraker_url}")
    except Exception as e:
        println(f"WARNING: failed to save moonraker_url to settings.json: {e}")

    try:
        sql_state = _detect_sql_state()
        if moonraker_url and sql_state.get("status") in ("sql_capable",):
            if _sync_printer_to_sql(printer_name, moonraker_url):
                println(f"[auto] Synced {printer_name} to SQL (moonraker_url).")
        elif moonraker_url and sql_state.get("status") == "sql_needs_migration":
            if _ensure_sql_capable(import_from_csv=False):
                if _sync_printer_to_sql(printer_name, moonraker_url):
                    println(f"[auto] Synced {printer_name} to SQL (moonraker_url).")
    except Exception as e:
        println(f"WARNING: failed to sync printer to SQL: {e}")

    register_client({
        "type": "local",
        "printer_name": printer_name,
        "cfg_dir": printer_dir,
        "script_path": end_script_path,
        "moonraker_url": moonraker_url,
    })

    println("\nLocal client installation complete.")
    println(f"  Printer: {printer_name}")
    println(f"  Config dir: {printer_dir}")
    println(f"  print_cost.cfg: {cfg_path}")
    println(f"  Job-start script: {job_start_path}")
    println(f"  Job-cancel script: {job_cancel_path}")
    println(f"  Job-pause script: {job_pause_path}")
    println(f"  Job-resume script: {job_resume_path}")
    println(f"  Cost script: {end_script_path}")


def install_client_remote() -> None:
    """
    Install a remote Klipper client over SSH.
    """
    println("\n=== Remote Client Installation ===")

    auto_mode = prompt_yes_no(
        "Use auto mode (use saved master settings and known remote printers)?", default=True
    )

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
        master_url = _safe_input(f"Master URL for dashboard [{master_url}]: ").strip() or master_url
        api_key = _safe_input(f"API key for this printer [{default_api}]: ").strip() or default_api

    remote = ""
    printer_name = ""
    printer_dir = ""
    default_moonraker = ""

    registry = get_client_registry()
    remote_clients = [c for c in registry if c.get("type") == "remote"]

    if auto_mode and remote_clients:
        println("\n[auto] Registered remote printers:")
        for i, c in enumerate(remote_clients, 1):
            println(f"  {i}) {c.get('printer_name')} @ {c.get('host')} ({c.get('config_dir')})")
        choice = prompt_choice(
            f"Select printer to install/update [1-{len(remote_clients)}] or press Enter to cancel auto mode: ",
            range(1, len(remote_clients) + 1),
            allow_empty=True,
        )
        if choice:
            entry = remote_clients[choice - 1]
            remote = entry.get("host", "")
            printer_name = entry.get("printer_name", "")
            printer_dir = entry.get("config_dir", "")
            default_moonraker = entry.get("moonraker_url", "")
        else:
            auto_mode = False
    elif auto_mode:
        println("[auto] No registered remote printers found; falling back to manual setup.")
        auto_mode = False

    if not auto_mode:
        remote = _safe_input("Remote host (user@hostname): ").strip()
        if not remote:
            println("Remote host is required; aborting.")
            return

        printer_name = _safe_input("Printer name for dashboard (e.g., SV08): ").strip()
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
            valid_range = list(range(1, len(candidates) + 1))
            while True:
                choice = _safe_input(
                    f"Select [1-{len(candidates)}] or enter a custom path: "
                ).strip()
                if choice == "":
                    printer_dir = candidates[0]
                    break
                if choice.isdigit():
                    idx = int(choice)
                    if idx in valid_range:
                        printer_dir = candidates[idx - 1]
                        break
                    println(
                        f"That's not an option. Please choose {_format_valid_options(valid_range)}."
                    )
                    continue
                printer_dir = choice
                break
        else:
            printer_dir = _safe_input("Remote printer config dir (folder with printer.cfg): ").strip()

    if not printer_dir:
        println("No remote config directory provided; aborting.")
        return

    moonraker_url = _configure_moonraker_url_remote(printer_name, default_moonraker)
    if not moonraker_url:
        println("Moonraker URL not set; thumbnails will be disabled until configured.")

    remote_cfg_path = os.path.join(printer_dir, "print_cost.cfg")
    remote_job_start = os.path.join(printer_dir, "kcd_job_start.sh")
    remote_job_cancel = os.path.join(printer_dir, "kcd_job_cancel.sh")
    remote_job_pause = os.path.join(printer_dir, "kcd_job_pause.sh")
    remote_job_resume = os.path.join(printer_dir, "kcd_job_resume.sh")
    remote_end_script = os.path.join(printer_dir, "send_print_cost.sh")
    remote_printer_cfg = os.path.join(printer_dir, "printer.cfg")

    cfg_text = _render_print_cost_cfg(printer_dir, printer_name)
    job_start_script = generate_job_start_script(master_url, api_key)
    job_cancel_script = generate_job_cancel_script(master_url, api_key)
    job_pause_script = generate_job_pause_script(master_url, api_key)
    job_resume_script = generate_job_resume_script(master_url, api_key)
    end_script = generate_job_end_script(master_url, api_key)

    ok1 = r.remote_write_file(remote, remote_cfg_path, cfg_text, mode=0o644)
    ok2 = r.remote_write_file(remote, remote_job_start, job_start_script, mode=0o755)
    ok3 = r.remote_write_file(remote, remote_job_cancel, job_cancel_script, mode=0o755)
    ok4 = r.remote_write_file(remote, remote_job_pause, job_pause_script, mode=0o755)
    ok5 = r.remote_write_file(remote, remote_job_resume, job_resume_script, mode=0o755)
    ok6 = r.remote_write_file(remote, remote_end_script, end_script, mode=0o755)

    if not (ok1 and ok2 and ok3 and ok4 and ok5 and ok6):
        println("ERROR: Failed to write one or more files on the remote host; aborting.")
        return
    if auto_mode:
        println(f"[auto] Deployed print_cost.cfg to {remote_cfg_path}")
        println(f"[auto] Deployed kcd_job_start.sh to {remote_job_start}")
        println(f"[auto] Deployed kcd_job_cancel.sh to {remote_job_cancel}")
        println(f"[auto] Deployed kcd_job_pause.sh to {remote_job_pause}")
        println(f"[auto] Deployed kcd_job_resume.sh to {remote_job_resume}")
        println(f"[auto] Deployed send_print_cost.sh to {remote_end_script}")

    include_line = "[include print_cost.cfg]"
    if not r.remote_append_line_if_missing(remote, remote_printer_cfg, include_line):
        println("WARNING: Failed to ensure include line in remote printer.cfg; please check manually.")
        _emit_system_event(
            "warning",
            "Manual action required (remote installer)",
            "Could not ensure [include print_cost.cfg] on the remote printer.cfg. Please verify it manually.",
            meta={"action": "install_client_remote", "printer": printer_name, "host": remote, "config_dir": printer_dir},
        )
    elif auto_mode:
        println("[auto] Verified [include print_cost.cfg] in printer.cfg.")

    try:
        run_remote_macro_integration(printer_name, remote, printer_dir)
    except Exception as e:
        println(f"WARNING: Remote macro integration failed: {e}")
        println("You may need to add KCD blocks to your macros on the remote host manually.")
        _emit_system_event(
            "warning",
            "Manual action required (remote installer)",
            "Remote macro integration failed; you may need to add KCD blocks to START/END macros manually.",
            meta={"action": "install_client_remote", "printer": printer_name, "host": remote, "config_dir": printer_dir},
        )
    save_state("master_url", master_url)
    save_state("api_key", api_key)

    if moonraker_url:
        try:
            s = load_settings(SETTINGS_FILE)
            if not isinstance(s, dict):
                s = {}
            if printer_name not in s or not isinstance(s.get(printer_name), dict):
                s[printer_name] = {}
            s[printer_name]["moonraker_url"] = moonraker_url
            save_settings(SETTINGS_FILE, DATA_DIR, s)
            if auto_mode:
                println(f"[auto] Saved Moonraker URL for {printer_name}: {moonraker_url}")
        except Exception as e:
            println(f"WARNING: failed to save moonraker_url to settings.json: {e}")

        try:
            sql_state = _detect_sql_state()
            if sql_state.get("status") in ("sql_capable",):
                if _sync_printer_to_sql(printer_name, moonraker_url):
                    println(f"[auto] Synced {printer_name} to SQL (moonraker_url).")
            elif sql_state.get("status") == "sql_needs_migration":
                if _ensure_sql_capable(import_from_csv=False):
                    if _sync_printer_to_sql(printer_name, moonraker_url):
                        println(f"[auto] Synced {printer_name} to SQL (moonraker_url).")
        except Exception as e:
            println(f"WARNING: failed to sync printer to SQL: {e}")

    register_client({
        "type": "remote",
        "printer_name": printer_name,
        "host": remote,
        "config_dir": printer_dir,
        "moonraker_url": moonraker_url,
    })

    println("\nRemote client installation complete.")
    println(f"  Printer: {printer_name}")
    println(f"  Remote: {remote}")
    println(f"  Remote config dir: {printer_dir}")
    println(f"  print_cost.cfg: {remote_cfg_path}")
    println(f"  Job-start script: {remote_job_start}")
    println(f"  Job-cancel script: {remote_job_cancel}")
    println(f"  Job-pause script: {remote_job_pause}")
    println(f"  Job-resume script: {remote_job_resume}")
    println(f"  Cost script: {remote_end_script}")


# Legacy helper used by remote flow (uses per-file contents)
def _render_print_cost_cfg(printer_dir: str, printer_name: str) -> str:
    template = """
# Auto-generated by installer
[gcode_shell_command send_print_cost]
# Quote {params} with double quotes so apostrophes in filenames stay shell-safe.
command: __PRINTER_DIR__/send_print_cost.sh "{params}"
timeout: 15.0
verbose: True

[gcode_shell_command kcd_job_start]
command: __PRINTER_DIR__/kcd_job_start.sh "{params}"
timeout: 10.0
verbose: True

[gcode_shell_command kcd_job_cancel]
command: __PRINTER_DIR__/kcd_job_cancel.sh "{params}"
timeout: 10.0
verbose: True

[gcode_shell_command kcd_job_pause]
command: __PRINTER_DIR__/kcd_job_pause.sh "{params}"
timeout: 10.0
verbose: True

[gcode_shell_command kcd_job_resume]
command: __PRINTER_DIR__/kcd_job_resume.sh "{params}"
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
    {% set fname = fname|replace('\\\\', '\\\\\\\\')|replace('\"', '\\\\\"') %}
    {% set est_dur = printer.print_stats.estimated_time|default(0)|float %}
    {% set est_filament = printer.print_stats.filament|default(0)|float %}
    {% set params = printer_name ~ "|" ~ fname ~ "|" ~ est_dur ~ "|" ~ est_filament %}
    RUN_SHELL_COMMAND CMD=kcd_job_start PARAMS="{params}"

[gcode_macro KCD_JOB_CANCEL]
description: Notify dashboard that a print was canceled
gcode:
    # KCD: log cancel to dashboard
    {% set printer_name = printer["gcode_macro _KCD_VARS"].printer_name|string %}
    {% set fname = printer.print_stats.filename|default("unknown.gcode", true)|string %}
    {% set fname = fname|replace('\\\\', '\\\\\\\\')|replace('\"', '\\\\\"') %}
    {% set elapsed = printer.print_stats.print_duration|default(0)|float %}
    {% set params = printer_name ~ "|" ~ fname ~ "|" ~ elapsed %}
    RUN_SHELL_COMMAND CMD=kcd_job_cancel PARAMS="{params}"

[gcode_macro KCD_JOB_PAUSE]
description: Notify dashboard that a print was paused
gcode:
    # KCD: log pause to dashboard
    {% set printer_name = printer["gcode_macro _KCD_VARS"].printer_name|string %}
    {% set fname = printer.print_stats.filename|default("unknown.gcode", true)|string %}
    {% set fname = fname|replace('\\\\', '\\\\\\\\')|replace('\"', '\\\\\"') %}
    {% set elapsed = printer.print_stats.print_duration|default(0)|float %}
    {% set reason = params.REASON|default("", true)|string %}
    {% set reason = reason|replace('\\\\', '\\\\\\\\')|replace('\"', '\\\\\"') %}
    {% set params = printer_name ~ "|" ~ fname ~ "|" ~ elapsed ~ "|" ~ reason %}
    RUN_SHELL_COMMAND CMD=kcd_job_pause PARAMS="{params}"

[gcode_macro KCD_JOB_RESUME]
description: Notify dashboard that a print was resumed
gcode:
    # KCD: log resume to dashboard
    {% set printer_name = printer["gcode_macro _KCD_VARS"].printer_name|string %}
    {% set fname = printer.print_stats.filename|default("unknown.gcode", true)|string %}
    {% set fname = fname|replace('\\\\', '\\\\\\\\')|replace('\"', '\\\\\"') %}
    {% set elapsed = printer.print_stats.print_duration|default(0)|float %}
    {% set params = printer_name ~ "|" ~ fname ~ "|" ~ elapsed %}
    RUN_SHELL_COMMAND CMD=kcd_job_resume PARAMS="{params}"

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
    prefs = _get_remote_macro_prefs(remote, printer_dir)
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

        # Prefer stored defaults if they still exist in the scan results.
        default_end_macro = None
        default_end_file = None
        default_start_macro = None
        default_start_file = None
        default_cancel_macro = None
        default_cancel_file = None

        end_pref = prefs.get("end") if isinstance(prefs, dict) else None
        if isinstance(end_pref, dict):
            pref_file = end_pref.get("file")
            pref_macro = end_pref.get("macro")
            if pref_file and pref_macro:
                end_candidates, _ = installer_macro.find_macros_in_dir(tmp_dir)
                if any(f == pref_file and m == pref_macro for (f, m, _ln) in end_candidates):
                    default_end_file = pref_file
                    default_end_macro = pref_macro

        start_pref = prefs.get("start") if isinstance(prefs, dict) else None
        if isinstance(start_pref, dict):
            pref_file = start_pref.get("file")
            pref_macro = start_pref.get("macro")
            if pref_file and pref_macro:
                start_candidates = installer_macro.find_start_macros_in_dir(tmp_dir)
                if any(f == pref_file and m == pref_macro for (f, m, _ln) in start_candidates):
                    default_start_file = pref_file
                    default_start_macro = pref_macro

        cancel_pref = prefs.get("cancel") if isinstance(prefs, dict) else None
        if isinstance(cancel_pref, dict):
            pref_file = cancel_pref.get("file")
            pref_macro = cancel_pref.get("macro")
            if pref_file and pref_macro:
                cancel_candidates = installer_macro.find_cancel_macros_in_dir(tmp_dir)
                if any(f == pref_file and m == pref_macro for (f, m, _ln) in cancel_candidates):
                    default_cancel_file = pref_file
                    default_cancel_macro = pref_macro

        end_macro, end_file = installer_macro.prompt_macro_insertion(
            printer_name, tmp_dir, default_macro=default_end_macro, default_file=default_end_file
        )
        start_macro, start_file = installer_macro.prompt_start_macro_insertion(
            printer_name, tmp_dir, default_macro=default_start_macro, default_file=default_start_file
        )
        cancel_macro, cancel_file = installer_macro.prompt_cancel_macro_insertion(
            printer_name, tmp_dir, default_macro=default_cancel_macro, default_file=default_cancel_file
        )

        for remote_path, local_path in local_paths:
            with open(local_path, "r", encoding="utf-8") as f:
                updated = f.read()
            r.remote_write_file(remote, remote_path, updated, mode=0o644)

        # Persist selections (match remote client by host + config_dir).
        if end_macro and end_file:
            _save_remote_macro_prefs(remote, printer_dir, end={"file": end_file, "macro": end_macro})
        if start_macro and start_file:
            _save_remote_macro_prefs(remote, printer_dir, start={"file": start_file, "macro": start_macro})
        if cancel_macro and cancel_file:
            _save_remote_macro_prefs(remote, printer_dir, cancel={"file": cancel_file, "macro": cancel_macro})

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
    confirm = prompt_yes_no("This will clear saved master settings. Continue?", default=False)
    if not confirm:
        println("Master uninstall cancelled.")
        return

    for key in (
        "master_host",
        "master_port",
        "master_url",
        "master_service_name",
        "master_storage_backend",
        "master_reports_backend",
        "api_key",
    ):
        save_state(key, "")

    removed_files = []
    for candidate in ("docker-compose.yml", "docker-compose.yaml", "Dockerfile", "/etc/systemd/system/print-cost-dashboard.service"):
        if os.path.exists(candidate):
            ans = prompt_yes_no(f"Delete {candidate}?", default=False)
            if ans:
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
        for fname in (
            "print_cost.cfg",
            "kcd_job_start.sh",
            "kcd_job_cancel.sh",
            "kcd_job_pause.sh",
            "kcd_job_resume.sh",
            "send_print_cost.sh",
        ):
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
    _emit_system_event(
        "deleted",
        "Client removed",
        f"Uninstalled local client files for printer {printer_name!r}.",
        meta={"action": "uninstall_client_local", "printer": printer_name},
    )


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
            os.path.join(config_dir, "kcd_job_cancel.sh"),
            os.path.join(config_dir, "kcd_job_pause.sh"),
            os.path.join(config_dir, "kcd_job_resume.sh"),
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
    _emit_system_event(
        "deleted",
        "Client removed",
        f"Uninstalled remote client files for printer {printer_name!r}.",
        meta={"action": "uninstall_client_remote", "printer": printer_name, "host": host, "config_dir": config_dir},
    )


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
    job_cancel_script = generate_job_cancel_script(master_url, api_key)
    job_pause_script = generate_job_pause_script(master_url, api_key)
    job_resume_script = generate_job_resume_script(master_url, api_key)
    end_script = generate_job_end_script(master_url, api_key)
    job_start_path = os.path.join(cfg_dir, "kcd_job_start.sh")
    job_cancel_path = os.path.join(cfg_dir, "kcd_job_cancel.sh")
    job_pause_path = os.path.join(cfg_dir, "kcd_job_pause.sh")
    job_resume_path = os.path.join(cfg_dir, "kcd_job_resume.sh")
    end_script_path = os.path.join(cfg_dir, "send_print_cost.sh")

    if not write_script(job_start_path, job_start_script):
        println("Failed to write kcd_job_start.sh; aborting update.")
        return
    if not write_script(job_cancel_path, job_cancel_script):
        println("Failed to write kcd_job_cancel.sh; aborting update.")
        return
    if not write_script(job_pause_path, job_pause_script):
        println("Failed to write kcd_job_pause.sh; aborting update.")
        return
    if not write_script(job_resume_path, job_resume_script):
        println("Failed to write kcd_job_resume.sh; aborting update.")
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
    job_cancel_script = generate_job_cancel_script(master_url, api_key)
    job_pause_script = generate_job_pause_script(master_url, api_key)
    job_resume_script = generate_job_resume_script(master_url, api_key)
    end_script = generate_job_end_script(master_url, api_key)



    remote_cfg_path = os.path.join(config_dir, "print_cost.cfg")
    remote_job_start = os.path.join(config_dir, "kcd_job_start.sh")
    remote_job_cancel = os.path.join(config_dir, "kcd_job_cancel.sh")
    remote_job_pause = os.path.join(config_dir, "kcd_job_pause.sh")
    remote_job_resume = os.path.join(config_dir, "kcd_job_resume.sh")
    remote_end_script = os.path.join(config_dir, "send_print_cost.sh")

    ok1 = r.remote_write_file(host, remote_cfg_path, cfg_text, mode=0o644)
    ok2 = r.remote_write_file(host, remote_job_start, job_start_script, mode=0o755)
    ok3 = r.remote_write_file(host, remote_job_cancel, job_cancel_script, mode=0o755)
    ok4 = r.remote_write_file(host, remote_job_pause, job_pause_script, mode=0o755)
    ok5 = r.remote_write_file(host, remote_job_resume, job_resume_script, mode=0o755)
    ok6 = r.remote_write_file(host, remote_end_script, end_script, mode=0o755)

    if not (ok1 and ok2 and ok3 and ok4 and ok5 and ok6):
        println("Failed to update one or more remote files; aborting.")
        return

    include_line = "[include print_cost.cfg]"
    if not r.remote_append_line_if_missing(host, os.path.join(config_dir, "printer.cfg"), include_line):
        println("WARNING: Could not ensure include line on remote printer.cfg.")

    save_state("master_url", master_url)
    save_state("api_key", api_key)

    if moonraker_url:
        try:
            s = load_settings(SETTINGS_FILE)
            if not isinstance(s, dict):
                s = {}
            if printer_name not in s or not isinstance(s.get(printer_name), dict):
                s[printer_name] = {}
            s[printer_name]["moonraker_url"] = moonraker_url
            save_settings(SETTINGS_FILE, DATA_DIR, s)
            if auto_mode:
                println(f"[auto] Saved Moonraker URL for {printer_name}: {moonraker_url}")
        except Exception as e:
            println(f"WARNING: failed to save moonraker_url to settings.json: {e}")

        try:
            sql_state = _detect_sql_state()
            if sql_state.get("status") in ("sql_capable",):
                if _sync_printer_to_sql(printer_name, moonraker_url):
                    println(f"[auto] Synced {printer_name} to SQL (moonraker_url).")
            elif sql_state.get("status") == "sql_needs_migration":
                if _ensure_sql_capable(import_from_csv=False):
                    if _sync_printer_to_sql(printer_name, moonraker_url):
                        println(f"[auto] Synced {printer_name} to SQL (moonraker_url).")
        except Exception as e:
            println(f"WARNING: failed to sync printer to SQL: {e}")

    register_client({
        "type": "remote",
        "printer_name": printer_name,
        "host": host,
        "config_dir": config_dir,
    })

    println(f"Remote client update complete for '{printer_name}'.")
