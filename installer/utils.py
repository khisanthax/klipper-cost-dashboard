from pathlib import Path
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
import getpass
import shlex
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
from core.file_io import atomic_write
from core.sql_only import is_sql_only
import installer_macro

from core.config import DATA_DIR, SETTINGS_FILE, CSV_FILE, DEFAULT_PRICING
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


def _sync_printer_to_sql(printer_name: str, moonraker_url: str, external_id: Optional[str] = None, *, relocate=False, reactivate=False) -> bool:
    try:
        from core import db as db_module
        from core.registration import register_printer
        register_printer(printer_name, moonraker_url, external_id=external_id, relocate=relocate, reactivate=reactivate)
        return True
    except Exception as e:
        println(f"WARNING: failed to sync printer to SQL: {e}")
        return False


# ----------------------------------------------------------------------
# Basic helpers
# ----------------------------------------------------------------------

def _mask_secret(value: str) -> str:
    return "[configured]" if value else "[not set]"


def prompt_secret(label, default=""):
    return getpass.getpass(f"{label} ({'Enter keeps configured key' if default else 'required'}): ").strip() or default


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
    return ("sql", "sql") if is_sql_only() else (("dual", "auto") if sql_enabled else ("csv", "csv"))


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
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.@-]*", service_name) or not 0 < int(port) < 65536:
        raise ValueError("Invalid service name or port")
    if (storage_backend, reports_backend) not in {("csv", "csv"), ("dual", "auto"), ("sql", "sql")}:
        raise ValueError(
            "installer service requires storage/reports backends csv/csv or dual/auto"
        )

    user = os.getenv("SUDO_USER") or os.getenv("USER") or ""
    env_lines = [
        "Environment=PYTHONUNBUFFERED=1",
        "Environment=FLASK_APP=app.py",
        f"Environment=KCD_STORAGE_BACKEND={storage_backend}",
        f"Environment=KCD_REPORTS_BACKEND={reports_backend}",
        'Environment="KCD_DATA_DIR=' + str(DATA_DIR).replace('%','%%').replace('"','\\"') + '"',
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
            f"ExecStart={venv_python} -m kcd.server --port {port}",
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
        credential_path=Path(DATA_DIR)/"secret.json"
        key=ensure_api_key(secret_file=str(credential_path),data_dir=DATA_DIR)
        if credential_path.exists():
            if json.loads(credential_path.read_text()).get('api_key') != key:
                raise ValueError('Environment credential differs from persisted credential; configure the service credential authority explicitly')
        else:
            from core.file_io import atomic_write
            atomic_write(credential_path,json.dumps({'api_key':key}),0o600)
        if os.name=='posix' and os.geteuid()==0 and os.environ.get('SUDO_USER'):
            import pwd
            account=pwd.getpwnam(os.environ['SUDO_USER'])
            directory=Path(DATA_DIR)
            if directory.stat().st_uid not in (0,account.pw_uid):
                raise ValueError('Data directory belongs to another user; resolve ownership explicitly')
            if directory.stat().st_uid==0:
                from core import db
                with db.connect_db() as connection:
                    if connection.execute('SELECT COUNT(*) FROM printers').fetchone()[0]:
                        raise ValueError('Existing root-owned printer data requires explicit ownership repair before changing service user')
            os.chown(directory,account.pw_uid,account.pw_gid)
            for name in ('secret.json','kcd.db','kcd.db-wal','kcd.db-shm'):
                path=directory/name
                if path.exists() and path.stat().st_uid==0:os.chown(path,account.pw_uid,account.pw_gid)
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
    if _run_cmd("systemctl daemon-reload") != 0:
        return False
    if _run_cmd(f"systemctl enable {service_name}") != 0:
        return False
    if _run_cmd(f"systemctl restart {service_name}") != 0:
        return False

    for attempt in range(10):
        status = _service_is_active(service_name)
        listening = _check_port_open(port)
        ok, detail = _check_url(url)
        if status == "active" and listening and ok:
            return True
        if attempt < 9:
            __import__('time').sleep(1)
    println(f"Master verification failed: service={status}, listening={listening}, URL check={detail}. Service/config retained for repair.")
    return False


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
    from .discovery import discover_local, discover_endpoints
    candidates = discover_local()
    ports = [c["moonraker_port"] for c in candidates if c["moonraker_port"]]
    return [p["url"] for p in discover_endpoints(ports)]


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
    if key == "api_key":
        return _load_secret_api_key() or default
    if is_sql_only():
        from core.storage import _load_user_settings_sql
        return (_load_user_settings_sql("installer_metadata") or {}).get(key, default)
    return _load_state_key(STATE_FILE, key, default)


def save_state(key: str, value: Any) -> None:
    if key == "api_key":
        # Installer metadata is never a second authority for credentials.
        return
    if is_sql_only():
        from core.printer_lifecycle import _load_setting, _save_setting
        from core import db
        with db.connect_db() as conn:
            db.apply_migrations(conn)
            conn.execute("BEGIN IMMEDIATE")
            state = _load_setting(conn, "installer_metadata")
            state[key] = value
            _save_setting(conn, "installer_metadata", state)
        return
    _save_state_key(STATE_FILE, DATA_DIR, key, value)


# ----------------------------------------------------------------------
# Client registry helpers
# ----------------------------------------------------------------------

def get_client_registry() -> List[Dict[str, Any]]:
    if is_sql_only():
        from core.registration import registered_clients
        registry = load_state("clients", [])
        by_name = {c["printer_name"]: c for c in registry if isinstance(c, dict) and c.get("printer_name")}
        return [{**by_name.get(c["printer_name"], {}), **c} for c in registered_clients()]
    registry = load_state("clients", [])
    if not isinstance(registry, list):
        return []
    return [c for c in registry if isinstance(c, dict)]


def _set_client_registry(clients: List[Dict[str, Any]]) -> None:
    save_state("clients", clients)


def register_client(entry: Dict[str, Any]) -> None:
    registry = get_client_registry()
    pn = entry.get("printer_name")
    new = []
    replaced = False
    for c in registry:
        if c.get("printer_name") == pn:
            if not replaced:
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
command: bash __PRINTER_DIR__/send_print_cost.sh "{params}"
timeout: 15.0
verbose: True

[gcode_shell_command kcd_job_start]
command: bash __PRINTER_DIR__/kcd_job_start.sh "{params}"
timeout: 10.0
verbose: True

[gcode_shell_command kcd_job_cancel]
command: bash __PRINTER_DIR__/kcd_job_cancel.sh "{params}"
timeout: 10.0
verbose: True

[gcode_shell_command kcd_job_pause]
command: bash __PRINTER_DIR__/kcd_job_pause.sh "{params}"
timeout: 10.0
verbose: True

[gcode_shell_command kcd_job_resume]
command: bash __PRINTER_DIR__/kcd_job_resume.sh "{params}"
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

MASTER_URL={shlex.quote(master_url)}
API_KEY={shlex.quote(api_key)}

if [ "${{1:-}}" = "--kcd-self-check" ]; then
  for script in "$(dirname "${{BASH_SOURCE[0]}}")"/kcd_job_*.sh "$(dirname "${{BASH_SOURCE[0]}}")"/send_print_cost.sh; do
    [ ! -f "$script" ] || chmod 700 "$script"
  done
  curl --fail --silent --show-error --connect-timeout 3 --max-time 10 \
    -H @<(printf 'X-API-Key: %s\n' "$API_KEY") "$MASTER_URL/api/installer/registration" >/dev/null
  echo "KCD credential verification passed"
  exit 0
fi

PARAMS="${{*:-}}"
IFS='|' read -r PRINTER_NAME FILENAME EST_DURATION EST_FILAMENT <<< "$PARAMS"
PRINTER_NAME="${{PRINTER_NAME:-}}"
FILENAME="${{FILENAME:-}}"
EST_DURATION="${{EST_DURATION:-0}}"
EST_FILAMENT="${{EST_FILAMENT:-0}}"


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

curl --fail --silent --show-error --connect-timeout 3 --max-time 10 -X POST "$MASTER_URL/job-start" \\
  -H "Content-Type: application/json" \\
  -H @<(printf 'X-API-Key: %s\n' "$API_KEY") \\
  -d "$JSON"
"""


def generate_job_cancel_script(master_url: str, api_key: str) -> str:
    return f"""#!/usr/bin/env bash
set -euo pipefail

MASTER_URL={shlex.quote(master_url)}
API_KEY={shlex.quote(api_key)}

if [ "${{1:-}}" = "--kcd-self-check" ]; then
  for script in "$(dirname "${{BASH_SOURCE[0]}}")"/kcd_job_*.sh "$(dirname "${{BASH_SOURCE[0]}}")"/send_print_cost.sh; do
    [ ! -f "$script" ] || chmod 700 "$script"
  done
  curl --fail --silent --show-error --connect-timeout 3 --max-time 10 \
    -H @<(printf 'X-API-Key: %s\n' "$API_KEY") "$MASTER_URL/api/installer/registration" >/dev/null
  echo "KCD credential verification passed"
  exit 0
fi

PARAMS="${{*:-}}"
IFS='|' read -r PRINTER_NAME FILENAME ELAPSED_SECONDS <<< "$PARAMS"
PRINTER_NAME="${{PRINTER_NAME:-}}"
FILENAME="${{FILENAME:-}}"
ELAPSED_SECONDS="${{ELAPSED_SECONDS:-0}}"


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

curl --fail --silent --show-error --connect-timeout 3 --max-time 10 -X POST "$MASTER_URL/job-cancel" \\
  -H "Content-Type: application/json" \\
  -H @<(printf 'X-API-Key: %s\n' "$API_KEY") \\
  -d "$JSON"
"""


def generate_job_pause_script(master_url: str, api_key: str) -> str:
    return f"""#!/usr/bin/env bash
set -euo pipefail

MASTER_URL={shlex.quote(master_url)}
API_KEY={shlex.quote(api_key)}

if [ "${{1:-}}" = "--kcd-self-check" ]; then
  for script in "$(dirname "${{BASH_SOURCE[0]}}")"/kcd_job_*.sh "$(dirname "${{BASH_SOURCE[0]}}")"/send_print_cost.sh; do
    [ ! -f "$script" ] || chmod 700 "$script"
  done
  curl --fail --silent --show-error --connect-timeout 3 --max-time 10 \
    -H @<(printf 'X-API-Key: %s\n' "$API_KEY") "$MASTER_URL/api/installer/registration" >/dev/null
  echo "KCD credential verification passed"
  exit 0
fi

PARAMS="${{*:-}}"
IFS='|' read -r PRINTER_NAME FILENAME ELAPSED_SECONDS REASON <<< "$PARAMS"
PRINTER_NAME="${{PRINTER_NAME:-}}"
FILENAME="${{FILENAME:-}}"
ELAPSED_SECONDS="${{ELAPSED_SECONDS:-0}}"
REASON="${{REASON:-}}"


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

curl --fail --silent --show-error --connect-timeout 3 --max-time 10 -X POST "$MASTER_URL/job-pause" \\
  -H "Content-Type: application/json" \\
  -H @<(printf 'X-API-Key: %s\n' "$API_KEY") \\
  -d "$JSON"
"""


def generate_job_resume_script(master_url: str, api_key: str) -> str:
    return f"""#!/usr/bin/env bash
set -euo pipefail

MASTER_URL={shlex.quote(master_url)}
API_KEY={shlex.quote(api_key)}

if [ "${{1:-}}" = "--kcd-self-check" ]; then
  for script in "$(dirname "${{BASH_SOURCE[0]}}")"/kcd_job_*.sh "$(dirname "${{BASH_SOURCE[0]}}")"/send_print_cost.sh; do
    [ ! -f "$script" ] || chmod 700 "$script"
  done
  curl --fail --silent --show-error --connect-timeout 3 --max-time 10 \
    -H @<(printf 'X-API-Key: %s\n' "$API_KEY") "$MASTER_URL/api/installer/registration" >/dev/null
  echo "KCD credential verification passed"
  exit 0
fi

PARAMS="${{*:-}}"
IFS='|' read -r PRINTER_NAME FILENAME ELAPSED_SECONDS <<< "$PARAMS"
PRINTER_NAME="${{PRINTER_NAME:-}}"
FILENAME="${{FILENAME:-}}"
ELAPSED_SECONDS="${{ELAPSED_SECONDS:-0}}"


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

curl --fail --silent --show-error --connect-timeout 3 --max-time 10 -X POST "$MASTER_URL/job-resume" \\
  -H "Content-Type: application/json" \\
  -H @<(printf 'X-API-Key: %s\n' "$API_KEY") \\
  -d "$JSON"
"""


def generate_job_end_script(master_url: str, api_key: str) -> str:
    return f"""#!/usr/bin/env bash
set -euo pipefail

MASTER_URL={shlex.quote(master_url)}
API_KEY={shlex.quote(api_key)}

if [ "${{1:-}}" = "--kcd-self-check" ]; then
  for script in "$(dirname "${{BASH_SOURCE[0]}}")"/kcd_job_*.sh "$(dirname "${{BASH_SOURCE[0]}}")"/send_print_cost.sh; do
    [ ! -f "$script" ] || chmod 700 "$script"
  done
  curl --fail --silent --show-error --connect-timeout 3 --max-time 10 \
    -H @<(printf 'X-API-Key: %s\n' "$API_KEY") "$MASTER_URL/api/installer/registration" >/dev/null
  echo "KCD credential verification passed"
  exit 0
fi

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
import json, os, time, uuid
def to_float(v):
    try: return float(v)
    except: return 0.0
def to_ts(v):
    try: return float(v)
    except: return time.time()
data = {{
    "event_id": str(uuid.uuid4()),
    "duration_basis": "active",
    "timestamp": to_ts(os.environ.get("TS", "")),
    "printer": os.environ.get("PRINTER", ""),
    "filename": os.environ.get("FILENAME", ""),
    "duration_seconds": to_float(os.environ.get("DUR", "0")),
    "filament_mm": to_float(os.environ.get("FILAMENT", "0")),
}}
print(json.dumps(data))
PY
)

curl --fail --silent --show-error --retry 2 --retry-delay 1 --connect-timeout 2 --max-time 4 -X POST "$MASTER_URL/log-print" \\
  -H "Content-Type: application/json" \\
  -H @<(printf 'X-API-Key: %s\n' "$API_KEY") \\
  -d "$JSON"
"""


def write_script(path: str, content: str) -> bool:
    try:
        atomic_write(path, content, 0o700)
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
    if os.environ.get("KCD_MASTER_CONTAINER"):
        println("Docker Master already owns this data directory. Upgrade its image with Docker; source-service installation is blocked to prevent two Masters.")
        return

    auto_mode = prompt_yes_no("Use auto mode (reuse saved master settings)?", default=True)

    current_host = load_state("master_host", "localhost")
    current_port = str(load_state("master_port", DEFAULT_PORT))
    current_url = load_state("master_url", f"http://{current_host}:{current_port}")
    current_service = load_state("master_service_name", DEFAULT_SERVICE_NAME)

    state_api = load_state("api_key", "")
    secret_api = _load_secret_api_key()
    default_api = secret_api or state_api or secrets.token_hex(16)

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
        println("Using the configured Master credential. Use tools/rotate_api_key.py for synchronized rotation.")

    try:
        port = int(port_str)
        if not 0 < port < 65536 or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.@-]*', service_name):
            raise ValueError('Port must be 1–65535 and service name must contain only letters, numbers, dot, underscore, @ or hyphen, starting with a letter or number')
        from core.registration import validate_registration
        validate_registration('Master',url)
    except ValueError as exc:
        println(f"Invalid Master configuration: {exc}. Saved configuration retained; no service changes made.")
        return

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
        println("[sql] Existing SQL database detected; preserving the selected runtime storage contract.")
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
        println("[sql] Strict SQL-only storage selected." if is_sql_only() else "[sql] SQL compatibility mode enabled: dual writes with automatic SQL Reports reads.")
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


def _client_api_key(master, directory=None):
    """Reuse only a credential associated with the selected Master."""
    if os.environ.get('KCD_API_KEY'):
        return os.environ['KCD_API_KEY']
    if directory:
        try:
            values = {}
            for line in (Path(directory)/'kcd_job_start.sh').read_text().splitlines():
                if line.startswith(('MASTER_URL=', 'API_KEY=')):
                    key, value = line.split('=', 1)
                    parsed = shlex.split(value)
                    if len(parsed) == 1:
                        values[key] = parsed[0]
            if values.get('MASTER_URL', '').rstrip('/') == master.rstrip('/'):
                return values.get('API_KEY', '')
        except (OSError, ValueError):
            pass
    # A standalone printer's locally generated secret is not its Master's key.
    if (os.environ.get('KCD_MASTER_CONTAINER') or load_state('master_service_name', '')) and load_state('master_url', '').rstrip('/') == master.rstrip('/'):
        return _load_secret_api_key()
    return ''


def install_client_local(printer_name=None) -> None:
    from .discovery import discover_local
    from .workflow import repair_client
    candidates = discover_local()
    println("\n=== Local client installation / repair ===")
    for index, candidate in enumerate(candidates, 1):
        println(f"  {index}) {candidate['config_dir']} [{candidate['classification']}] identity={candidate['printer_name'] or 'unset'} port={candidate['moonraker_port'] or 'unknown'}")
    println("  0) Enter a custom directory")
    selected = prompt_choice("Select configuration (b to cancel): ", range(len(candidates)+1), cancel_inputs=["b"])
    if selected is None:
        return
    candidate = candidates[selected-1] if selected else {}
    directory = candidate.get("config_dir") or _safe_input("Config directory containing printer.cfg: ").strip()
    existing_name = candidate.get("printer_name", "")
    if printer_name and existing_name and printer_name!=existing_name:
        println("Selected configuration belongs to a different identity; update cancelled without changes.")
        return
    name = printer_name or _safe_input(f"Printer identity [{existing_name}]: ").strip() or existing_name
    if not name:
        println("Identity is required. A clone must use its own identity; relocation keeps the existing identity.")
        return
    master_default = load_state("master_url", os.environ.get("KCD_MASTER_URL", "http://localhost:5000"))
    master = _safe_input(f"Master URL reachable by printer [{master_default}]: ").strip() or master_default
    key = prompt_secret("Master API key", _client_api_key(master, directory))
    moonraker = _configure_moonraker_url_local(name)
    if not moonraker:
        return
    relocate = prompt_yes_no("Relocating the SAME printer / correcting its endpoint while preserving history?", default=False)
    reactivate = prompt_yes_no("Explicitly reactivate a retired printer?", default=False)
    try:
        preview = repair_client(directory, name, moonraker, master, key, relocate=relocate, reactivate=reactivate)
        println(json.dumps(preview, indent=2))
        if not prompt_yes_no("Apply this client repair?", default=False):
            return
        result = repair_client(directory, name, moonraker, master, key, apply=True, relocate=relocate, reactivate=reactivate)
        save_state("master_url", master)
        println(json.dumps(result, indent=2))
        println("Inspect active macro hooks with: python3 install.py macro-plan --config-dir " + shlex.quote(directory))
    except (ValueError, RuntimeError, OSError) as exc:
        println(f"Client install/repair failed: {exc}")


def install_client_remote(printer_name=None) -> None:
    from .remote_workflow import remote_operation
    println("\n=== Remote client installation / repair ===")
    host = _safe_input("SSH alias or user@host: ").strip()
    try:
        candidates = remote_operation(host, "discover", {})
        for index, c in enumerate(candidates, 1):
            println(f"  {index}) {c['config_dir']} [{c['classification']}] identity={c['printer_name'] or 'unset'} port={c['moonraker_port'] or 'unknown'}")
        println("  0) Custom configuration directory")
        selected = prompt_choice("Select configuration (b to cancel): ", range(len(candidates)+1), cancel_inputs=["b"])
        if selected is None:
            return
        candidate = candidates[selected-1] if selected else {}
        if printer_name and candidate.get('printer_name') and candidate['printer_name'] != printer_name:
            println("Selected configuration belongs to a different identity; update cancelled without changes.")
            return
        directory = candidate.get("config_dir") or _safe_input("Remote config directory: ").strip()
        name = printer_name or _safe_input(f"Printer identity [{candidate.get('printer_name', '')}]: ").strip() or candidate.get("printer_name", "")
        master = _safe_input("Master URL reachable from the remote printer: ").strip()
        key = prompt_secret("Master API key", _client_api_key(master))
        moonraker = _safe_input("Moonraker URL reachable from Master (include actual port): ").strip()
        payload = dict(config_dir=directory, name=name, moonraker=moonraker, master=master, key=key,
                       relocate=prompt_yes_no("Explicit relocation of the same historical identity?", default=False),
                       reactivate=prompt_yes_no("Explicitly reactivate retired printer?", default=False),
                       host=host, installation_type="remote")
        println(json.dumps(remote_operation(host, "repair", payload), indent=2))
        if prompt_yes_no("Apply this remote repair?", default=False):
            payload["apply"] = True
            println(json.dumps(remote_operation(host, "repair", payload), indent=2))
            if not is_sql_only():
                register_client({'printer_name':name,'type':'remote','host':host,'config_dir':directory,'master_url':master,'moonraker_url':moonraker})
                save_state('master_url',master)
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
        println("Remote installation incomplete: " + str(exc))


def _render_print_cost_cfg(printer_dir: str, printer_name: str) -> str:
    template = """
# Auto-generated by installer
[gcode_shell_command send_print_cost]
# Quote {params} with double quotes so apostrophes in filenames stay shell-safe.
command: bash __PRINTER_DIR__/send_print_cost.sh "{params}"
timeout: 15.0
verbose: True

[gcode_shell_command kcd_job_start]
command: bash __PRINTER_DIR__/kcd_job_start.sh "{params}"
timeout: 10.0
verbose: True

[gcode_shell_command kcd_job_cancel]
command: bash __PRINTER_DIR__/kcd_job_cancel.sh "{params}"
timeout: 10.0
verbose: True

[gcode_shell_command kcd_job_pause]
command: bash __PRINTER_DIR__/kcd_job_pause.sh "{params}"
timeout: 10.0
verbose: True

[gcode_shell_command kcd_job_resume]
command: bash __PRINTER_DIR__/kcd_job_resume.sh "{params}"
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
    template=re.sub(r'__PRINTER_DIR__/([A-Za-z0-9_]+\.sh)',lambda m:shlex.quote(printer_dir.rstrip('/\\')+'/'+m[1]),template)
    return template.replace("__PRINTER_NAME__", printer_name)


def run_remote_macro_integration(printer_name: str, remote: str, printer_dir: str) -> None:
    println("Use install.py macro-plan on the printer host to inspect active includes and preview targeted macro changes.")


# ----------------------------------------------------------------------
# Uninstall helpers
# ----------------------------------------------------------------------

def uninstall_master() -> None:
    """Remove only the selected source service, retaining data and source files."""
    println("\n=== Uninstall MASTER (dashboard) ===")
    if os.environ.get('KCD_MASTER_CONTAINER'):
        println('Docker Master owns this directory. Manage its service through Docker Compose; installer uninstall made no changes.')
        return
    service = load_state('master_service_name', '')
    if not service or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.@-]*', service):
        println('No valid saved source service. Inspect systemd and recover the service name before uninstalling.')
        return
    unit = Path('/etc/systemd/system')/(service+'.service')
    if unit.is_symlink() or (unit.exists() and 'WorkingDirectory='+_repo_root() not in unit.read_text().splitlines()):
        println('Service does not belong to this checkout; uninstall refused without changes.')
        return
    confirm = prompt_yes_no(f"Stop and remove source service {service}? Database, credentials, clients and repository files will be retained", default=False)
    if not confirm:
        println("Master uninstall cancelled.")
        return

    if unit.exists():
        try:
            from core.file_io import atomic_write
            backup = Path(DATA_DIR)/'backups'/(service+'.service.'+str(__import__('time').time_ns()))
            atomic_write(backup,unit.read_text(),0o600,backup=False)
            if _run_cmd('systemctl disable --now '+shlex.quote(service)) != 0:
                println('Master uninstall incomplete: service stop/disable failed. Unit and saved settings retained.')
                return
            unit.unlink()
            if _run_cmd('systemctl daemon-reload') != 0:
                println('Master uninstall incomplete: unit removed but daemon-reload failed. Private unit backup and saved settings retained; rerun daemon-reload.')
                return
        except OSError:
            println('Master uninstall incomplete: cannot back up/remove unit. Inspect permissions; saved settings and data retained.')
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

    println("Master uninstall complete. Database, credentials, client integrations and repository retained; reinstall can reuse them.")


def _retire_client(printer_name: str) -> None:
    from .workflow import master_request
    master = load_state("master_url", os.environ.get("KCD_MASTER_URL", "http://localhost:5000"))
    master = _safe_input(f"Master URL [{master}]: ").strip() or master
    if not prompt_yes_no("Retire this identity while retaining history and custom macro files?", default=False):
        return
    try:
        entry = next((c for c in get_client_registry() if c.get('printer_name') == printer_name), {})
        directory = entry.get('cfg_dir') if entry.get('type') == 'local' else None
        result = master_request(master, prompt_secret("Master API key", _client_api_key(master, directory)),
                                {"action":"retire", "printer_name":printer_name})
        println(json.dumps(result,indent=2))
        println("Custom hooks and files are retained to avoid invalid Klipper references. Remove reviewed hooks before deleting files. Use --reactivate when reinstalling this identity.")
    except (ValueError, RuntimeError, OSError) as exc:
        println("Retirement failed: " + str(exc))


def uninstall_client_local(printer_name: str) -> None:
    _retire_client(printer_name)


def uninstall_client_remote(printer_name: str) -> None:
    _retire_client(printer_name)


def update_client_local(printer_name: str) -> None:
    install_client_local(printer_name=printer_name)


def update_client_remote(printer_name: str) -> None:
    install_client_remote(printer_name=printer_name)
