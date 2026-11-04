"""Reviewable client repair workflow; remote Master owns business state."""
import difflib
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat
import urllib.error
import urllib.request

from .discovery import active_config_files, probe_endpoint
from core.file_io import atomic_write
from core.registration import validate_registration


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward a credential to a redirect target.


_CLIENT_SCRIPTS = (
    ("kcd_job_start.sh", "generate_job_start_script"),
    ("kcd_job_cancel.sh", "generate_job_cancel_script"),
    ("kcd_job_pause.sh", "generate_job_pause_script"),
    ("kcd_job_resume.sh", "generate_job_resume_script"),
    ("send_print_cost.sh", "generate_job_end_script"),
)


def _web_source_state(directory):
    """Capture the file state reviewed by the operator, including active includes."""
    files, _ = active_config_files(directory, restrict_to_root=True)
    managed = {directory / name for name, _ in _CLIENT_SCRIPTS}
    managed.update({directory / "printer.cfg", directory / "print_cost.cfg"})
    state = {}
    for path in sorted(set(files) | managed):
        resolved = path.resolve()
        if not resolved.is_relative_to(directory) or (path in managed and path.is_symlink()):
            raise ValueError("Web repair requires regular managed files inside the selected directory")
        if path.exists():
            info = path.stat()
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("Web repair requires regular configuration and script files")
            state[str(path)] = (str(resolved), stat.S_IMODE(info.st_mode), hashlib.sha256(path.read_bytes()).hexdigest())
        else:
            state[str(path)] = None
    return state


def _require_idle_moonraker(moonraker, directory):
    opener = urllib.request.build_opener(NoRedirect)
    probe = probe_endpoint(moonraker, opener=opener)
    if not probe or probe["connected"] is not True or probe["config_dir"] != str(directory):
        raise ValueError("Cannot verify a connected Moonraker for the selected configuration")
    try:
        with opener.open(moonraker.rstrip("/") + "/printer/objects/query?print_stats", timeout=3) as response:
            state = json.load(response)["result"]["status"]["print_stats"]["state"]
    except (OSError, ValueError, KeyError, TypeError):
        raise ValueError("Cannot verify printer state. Retry when Klipper is reachable.") from None
    if state not in ("standby", "complete", "cancelled"):
        raise ValueError("Web repair requires an inactive printer with a verified state")
    return probe


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


def client_plan(config_dir, name, master, key, *, restrict_to_root=False):
    from . import utils
    directory = Path(config_dir).resolve()
    if not (directory / "printer.cfg").is_file():
        raise ValueError("Selected directory must contain printer.cfg")
    files, errors = active_config_files(directory, restrict_to_root=restrict_to_root)
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
        config = re.sub(r"""^(command:\s*)(?:bash\s+)?((?:'[^']*/|"[^"]*/|/[^\r\n]*?/|[A-Za-z]:[^\r\n]*?/))(kcd_job_\w+\.sh|send_print_cost\.sh)['"]?""",
                        lambda m: m[1]+"bash "+__import__('shlex').quote(str(directory)+"/"+m[3]), config, flags=re.M)
        config = re.sub(
            r'^(command:.*(?:kcd_job_(?:start|cancel|pause|resume)|send_print_cost)\.sh[\'"]?)[ \t]+"\{params\}"[ \t]*$',
            r'\1', config, flags=re.M,
        )
        # Upgrade older managed integrations by appending only absent helper
        # definitions. Existing start/end logic and custom settings are retained.
        rendered = utils._render_print_cost_cfg(str(directory), name)
        for section in re.finditer(r'^\[(gcode_shell_command kcd_job_\w+|gcode_macro KCD_JOB_\w+)\]\n.*?(?=^\[|\Z)', rendered, re.M | re.S):
            if not re.search(r'^\['+re.escape(section[1])+r'\]', config, re.M | re.I):
                config += "\n" + section[0]
    plan = {current: (config, 0o644)}
    for filename, generator in _CLIENT_SCRIPTS:
        plan[directory / filename] = (getattr(utils, generator)(master, key), 0o700)
    if current.resolve() not in files:
        printer = directory / "printer.cfg"
        plan[printer] = ("[include print_cost.cfg]\n"+printer.read_text(), 0o644)
    for path in plan:
        if not path.resolve().is_relative_to(directory):
            raise ValueError('Managed file escapes the selected config directory; repair its symlink explicitly')
    return plan


def repair_client(config_dir, name, moonraker, master, key, *, apply=False, relocate=False,
                  reactivate=False, host=None, installation_type="local", web_safe=False,
                  expected_revision=None):
    """Plan or apply the shared repair workflow.

    The web caller authorizes the target and credentials before invoking this
    engine. The reviewed revision binds configuration, destination and actions.
    """
    name, moonraker = validate_registration(name, moonraker)
    directory = Path(config_dir).resolve()
    if web_safe and apply and not (
        isinstance(expected_revision, str) and re.fullmatch(r"[0-9a-f]{64}", expected_revision)
    ):
        raise ValueError("Review the current repair preview before applying")
    status = master_request(master, key)  # Fail before touching client files.
    if status.get("backend") not in {"sql", "dual", "csv"}:
        raise ValueError("Master returned an invalid storage backend")
    probe = probe_endpoint(moonraker, opener=urllib.request.build_opener(NoRedirect)) if web_safe else probe_endpoint(moonraker)
    if not probe:
        raise ValueError("Moonraker discovery failed; verify its API, network address and authorization")
    if probe["config_dir"] != str(directory):
        raise ValueError("Moonraker serves a different config directory; select the matching instance/port")
    existing=next((client for client in status.get('clients',[]) if client.get('printer_name')==name),None)
    if existing and existing.get('hidden') and not reactivate:
        raise ValueError('Printer is retired; explicit reactivation is required')
    if existing and existing.get('moonraker_url') and existing['moonraker_url']!=moonraker and not relocate:
        raise ValueError('Endpoint changed; explicit relocation is required to preserve this identity')
    source_state = _web_source_state(directory) if web_safe else None
    plan = client_plan(config_dir, name, master, key, restrict_to_root=web_safe)
    if web_safe and _web_source_state(directory) != source_state:
        raise ValueError("The client configuration changed during planning. Generate a fresh preview.")
    changes = []
    for path, (content, mode) in plan.items():
        old = path.read_text() if path.exists() else ""
        if old==content and path.exists() and (os.name=='nt' or (path.stat().st_mode & 0o777)==mode):
            continue
        if web_safe:
            changes.append(f"{'Update' if path.exists() else 'Create'} {path.name}")
        elif path.suffix == ".sh":
            changes.append(f"{path}: {'update' if path.exists() else 'create'} private client script (credential omitted)")
        else:
            changes.append("".join(difflib.unified_diff(old.splitlines(True),content.splitlines(True),fromfile=str(path),tofile=str(path))))
    metadata = {"type": installation_type, "host": host or __import__('socket').gethostname(),
                "cfg_dir" if installation_type == "local" else "config_dir": str(Path(config_dir).resolve()), "master_url": master,
                "script_path": str(Path(config_dir).resolve()/"send_print_cost.sh")}
    payload = {"printer_name": name, "moonraker_url": moonraker, "metadata": metadata,
               "relocate": relocate, "reactivate": reactivate}
    result = {"changes": changes}
    if web_safe:
        fingerprint = {
            "registration": payload,
            "existing": existing,
            "backend": status["backend"],
            "moonraker_identity": probe["identity"],
            "before": source_state,
            "after": {
                str(path): (mode, hashlib.sha256(content.encode("utf-8")).hexdigest())
                for path, (content, mode) in plan.items()
            },
        }
        revision = hmac.new(key.encode("utf-8"), json.dumps(fingerprint, sort_keys=True).encode("utf-8"), "sha256").hexdigest()
        result.update(revision=revision, master_backend=status["backend"])
        if apply:
            if not hmac.compare_digest(revision, expected_revision):
                raise ValueError("The repair preview is stale. Generate a fresh preview.")
            checked_probe = _require_idle_moonraker(moonraker, directory)
            if checked_probe["identity"] != probe["identity"] or _web_source_state(directory) != source_state:
                raise ValueError("The printer or configuration changed. Generate a fresh preview.")
    if not apply:
        return {**result, "applied": False, "master_backend": status["backend"], "macro_action":"Client files only. Inspect active print-event hooks with macro-plan; registration is validated only on apply."}
    # Registration is atomic on Master. A later file failure remains repairable;
    # it never destroys an existing identity, price, or job.
    registration = master_request(master, key, payload)
    if web_safe:
        try:
            checked_probe = _require_idle_moonraker(moonraker, directory)
            if checked_probe["identity"] != probe["identity"] or _web_source_state(directory) != source_state:
                raise ValueError("The printer or configuration changed")
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"Master registration was retained. No client files were written. {exc}") from None
    try:
        for path, (content, mode) in plan.items():
            atomic_write(path, content, mode)
            if os.name=='posix' and os.geteuid()==0:
                owner=Path(config_dir).stat()
                os.chown(path,owner.st_uid,owner.st_gid)
    except OSError:
        raise RuntimeError("Master registered; client files incomplete. Private per-file backups exist. Rerun the same repair; no history was moved") from None
    if not registration.get("readiness", {}).get("ready"):
        raise RuntimeError("Client files written; Master readiness failed. Registration is retained; inspect Master readiness and rerun repair")
    from . import utils
    if not web_safe and not utils.is_sql_only():
        utils.register_client({**metadata,'printer_name':name,'moonraker_url':moonraker})
        utils.save_state('master_url',master)
    return {**result, "applied": True, "registration": registration,
            "macro_action": "Inspect active hooks with macro-plan; reload Klipper only while idle if configuration changed"}
