"""Read-only inspection of an operator-authorized Klipper instance."""
from collections import Counter
import configparser
from pathlib import Path
import re
from urllib.parse import urlsplit
import urllib.request

from .discovery import active_config_files, effective_macro_sources, probe_endpoint, process_instances


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_HOOKS = {
    "KCD_JOB_START": "start", "KCD_JOB_CANCEL": "cancel",
    "KCD_JOB_PAUSE": "pause", "KCD_JOB_RESUME": "resume",
}


def inspect_client(config_dir, moonraker):
    root = Path(config_dir).resolve(strict=True)
    if not root.is_dir() or not (root / "printer.cfg").is_file():
        raise ValueError("Selected directory must contain printer.cfg")
    files, errors = active_config_files(root, restrict_to_root=True)
    probe = probe_endpoint(moonraker, opener=urllib.request.build_opener(_NoRedirect))
    if not probe:
        raise ValueError("Moonraker discovery failed")
    if probe["config_dir"] != str(root):
        raise ValueError("Moonraker serves a different config directory")
    identity = ""
    for path in files:
        match = re.search(r'^\s*variable_printer_name:\s*["\']?([^"\'\n]+)', path.read_text(encoding="utf-8"), re.M)
        if match:
            identity = match[1].strip()
    try:
        definitions, all_definitions = effective_macro_sources(root, restrict_to_root=True) if not errors else ({}, [])
    except configparser.Error:
        raise ValueError("Active macro configuration cannot be parsed. Check the active include chain syntax.") from None
    direct, calls = {}, {}
    for name, definition in definitions.items():
        direct[name] = Counter()
        calls[name] = []
        for line in definition["body"].splitlines():
            command = line.strip().split(maxsplit=1)[0].upper() if line.strip() else ""
            if command in _HOOKS:
                direct[name][_HOOKS[command]] += 1
            elif command == "RUN_SHELL_COMMAND" and re.search(r'\bCMD\s*=\s*send_print_cost\b', line, re.I):
                direct[name]["end"] += 1
            elif command in definitions:
                calls[name].append(command)

    cached = {}

    def event_counts(name, chain=()):
        if name in cached:
            return cached[name]
        if name in chain:
            if "Cyclic macro delegation requires manual review." not in errors:
                errors.append("Cyclic macro delegation requires manual review.")
            return Counter()
        if len(chain) >= 64:
            raise ValueError("Macro delegation exceeds the inspection limit")
        counts = direct[name].copy()
        for called in calls[name]:
            counts.update(event_counts(called, (*chain, name)))
        cached[name] = counts
        return counts

    macros = []
    for definition in all_definitions:
        name = definition["name"].upper()
        effective = definitions[name] is definition
        counts = event_counts(name) if effective else Counter()
        macros.append({
            "name": definition["name"], "file": str(Path(definition["file"]).relative_to(root)),
            "effective": effective, "hooks": sorted(counts),
            "possible_duplicates": sorted(event for event, count in counts.items() if count > 1),
        })
    installed = [name for name in (
        "print_cost.cfg", "kcd_job_start.sh", "kcd_job_cancel.sh", "kcd_job_pause.sh",
        "kcd_job_resume.sh", "send_print_cost.sh",
    ) if (root / name).is_file()]
    active_process = any(
        instance["kind"] == "klipper" and instance["config"]
        and Path(instance["config"]).resolve().parent == root for instance in process_instances()
    )
    return {
        "config_dir": str(root), "printer_name": identity, "moonraker": moonraker,
        "port": urlsplit(moonraker).port or (443 if moonraker.startswith("https://") else 80),
        "connected": probe["connected"], "active_process": active_process,
        "active_files": [str(path.relative_to(root)) for path in files],
        "unused_files": sorted(path.name for path in root.glob("*.cfg") if path not in files),
        "installed_files": installed, "errors": errors, "macros": macros,
    }
