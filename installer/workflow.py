"""Reviewable client repair workflow; remote Master owns business state."""
import difflib
import json
from pathlib import Path
import re
import urllib.error
import urllib.request

from .discovery import active_config_files, probe_endpoint
from core.file_io import atomic_write
from core.registration import validate_registration


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward a credential to a redirect target.


def master_request(master, key, payload=None):
    _, master = validate_registration("Master", master)
    request = urllib.request.Request(master.rstrip("/")+"/api/installer/registration",
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"X-API-Key": key, "Content-Type": "application/json"})
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=15) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        # Responses may contain request headers on proxies; never echo raw bodies.
        if exc.code == 403:
            raise RuntimeError("Master authentication failed; verify the configured API credential") from None
        if exc.code == 404:
            raise RuntimeError("Master lacks the registration API; upgrade Master before installing clients") from None
        raise RuntimeError(f"Master registration failed (HTTP {exc.code}); inspect readiness or identity conflicts; no automatic history merge") from None
    except (OSError, ValueError):
        raise RuntimeError("Master network/response failure; registration was not verified, rerun repair") from None


def client_plan(config_dir, name, master, key):
    from . import utils
    directory = Path(config_dir).resolve()
    if not (directory / "printer.cfg").is_file():
        raise ValueError("Selected directory must contain printer.cfg")
    files, errors = active_config_files(directory)
    current = directory / "print_cost.cfg"
    errors = [error for error in errors if error != "Missing include: "+str(current)]
    if errors:
        raise ValueError("Resolve active configuration errors before repair: " + "; ".join(errors))
    config = current.read_text() if current.exists() else utils._render_print_cost_cfg(str(directory), name)
    if current.exists():
        # Targeted identity and script-path repair, retaining custom macro logic.
        if not re.search(r'^\s*variable_printer_name:', config, re.M):
            raise ValueError("Legacy print_cost.cfg needs manual migration; refusing to replace custom macros")
        config = re.sub(r'^(\s*variable_printer_name:)\s*.*$', lambda m: m[1]+' "'+name+'"', config, flags=re.M)
        config = re.sub(r'^(command:\s*)(?:bash\s+)?(\S*/)(kcd_job_\w+\.sh|send_print_cost\.sh)',
                        lambda m: m[1]+"bash "+str(directory)+"/"+m[3], config, flags=re.M)
        # Upgrade older managed integrations by appending only absent helper
        # definitions. Existing start/end logic and custom settings are retained.
        rendered = utils._render_print_cost_cfg(str(directory), name)
        for section in re.finditer(r'^\[(gcode_shell_command kcd_job_\w+|gcode_macro KCD_JOB_\w+)\]\n.*?(?=^\[|\Z)', rendered, re.M | re.S):
            if not re.search(r'^\['+re.escape(section[1])+r'\]', config, re.M | re.I):
                config += "\n" + section[0]
    plan = {current: (config, 0o644)}
    for filename, generator in [("kcd_job_start.sh", utils.generate_job_start_script),
             ("kcd_job_cancel.sh", utils.generate_job_cancel_script),
             ("kcd_job_pause.sh", utils.generate_job_pause_script),
             ("kcd_job_resume.sh", utils.generate_job_resume_script),
             ("send_print_cost.sh", utils.generate_job_end_script)]:
        plan[directory / filename] = (generator(master, key), 0o700)
    if current.resolve() not in files:
        printer = directory / "printer.cfg"
        plan[printer] = ("[include print_cost.cfg]\n"+printer.read_text(), 0o644)
    return plan


def repair_client(config_dir, name, moonraker, master, key, *, apply=False, relocate=False, reactivate=False, host=None, installation_type="local"):
    name, moonraker = validate_registration(name, moonraker)
    status = master_request(master, key)  # Fail before touching client files.
    if status.get("backend") not in {"sql", "dual", "csv"}:
        raise ValueError("Master returned an invalid storage backend")
    probe = probe_endpoint(moonraker)
    if not probe:
        raise ValueError("Moonraker discovery failed; verify its API, network address and authorization")
    if probe["config_dir"] != str(Path(config_dir).resolve()):
        raise ValueError("Moonraker serves a different config directory; select the matching instance/port")
    existing=next((client for client in status.get('clients',[]) if client.get('printer_name')==name),None)
    if existing and existing.get('hidden') and not reactivate:
        raise ValueError('Printer is retired; explicit reactivation is required')
    if existing and existing.get('moonraker_url') and existing['moonraker_url']!=moonraker and not relocate:
        raise ValueError('Endpoint changed; explicit relocation is required to preserve this identity')
    plan = client_plan(config_dir, name, master, key)
    changes = []
    for path, (content, mode) in plan.items():
        old = path.read_text() if path.exists() else ""
        if old==content and path.exists() and (__import__('os').name=='nt' or (__import__('os').stat(path).st_mode & 0o777)==mode):
            continue
        if path.suffix == ".sh":
            changes.append(f"{path}: {'update' if path.exists() else 'create'} private client script (credential omitted)")
        else:
            changes.append("".join(difflib.unified_diff(old.splitlines(True),content.splitlines(True),fromfile=str(path),tofile=str(path))))
    metadata = {"type": installation_type, "host": host or __import__('socket').gethostname(),
                "cfg_dir" if installation_type == "local" else "config_dir": str(Path(config_dir).resolve()), "master_url": master,
                "script_path": str(Path(config_dir).resolve()/"send_print_cost.sh")}
    payload = {"printer_name": name, "moonraker_url": moonraker, "metadata": metadata,
               "relocate": relocate, "reactivate": reactivate}
    if not apply:
        return {"applied": False, "changes": changes, "master_backend": status["backend"], "macro_action":"Client files only. Inspect active print-event hooks with macro-plan; registration is validated only on apply."}
    # Registration is atomic on Master. A later file failure remains repairable;
    # it never destroys an existing identity, price, or job.
    registration = master_request(master, key, payload)
    try:
        for path, (content, mode) in plan.items():
            if not path.resolve().is_relative_to(Path(config_dir).resolve()):
                raise ValueError('Managed file escapes the selected config directory; repair its symlink explicitly')
            atomic_write(path, content, mode)
            import os
            if os.name=='posix' and os.geteuid()==0:
                owner=Path(config_dir).stat()
                os.chown(path,owner.st_uid,owner.st_gid)
    except OSError:
        raise RuntimeError("Master registered; client files incomplete. Private per-file backups exist. Rerun the same repair; no history was moved") from None
    if not registration.get("readiness", {}).get("ready"):
        raise RuntimeError("Client files written; Master readiness failed. Registration is retained; inspect Master readiness and rerun repair")
    return {"applied": True, "changes": changes, "registration": registration,
            "macro_action": "Inspect active hooks with macro-plan; reload Klipper only while idle if configuration changed"}
