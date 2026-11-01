"""Inspect active macro hooks; preview targeted changes before explicit apply."""
import difflib
from pathlib import Path
import re
import tempfile

from .discovery import active_config_files, effective_macro_sources
from core.file_io import atomic_write


def macro_plan(config_dir, filename=None, macro=None, event=None, *, apply=False):
    import installer_macro as im
    root = Path(config_dir).resolve()
    files, errors = active_config_files(root)
    if errors:
        if filename:
            raise ValueError("Resolve missing includes/cycles before patching")
        return {"active_files":[str(p) for p in files],"errors":errors}
    effective, all_definitions = effective_macro_sources(root)
    definitions = {}
    for definition in all_definitions:
        definitions.setdefault(definition['name'].upper(),[]).append(definition)
    duplicates = [name for name, defs in definitions.items() if len(defs) > 1]
    summary = [{"macro": name, "file": d["file"], "active":effective[name] is d, "hooks": sorted(set(re.findall(r'\bKCD_JOB_(?:START|CANCEL|PAUSE|RESUME)\b|CMD=send_print_cost', d["body"])))} for name, defs in definitions.items() for d in defs]
    if not filename:
        return {"active_files": [str(p) for p in files], "errors": errors, "duplicate_macros": duplicates, "macros": summary}
    path = Path(__import__('os').path.abspath(root / filename))
    if path not in files or path.name == "print_cost.cfg":
        raise ValueError("Choose an active user macro file; inactive alternatives are never patched")
    defs = [effective[str(macro).upper()]] if str(macro).upper() in effective else []
    if len(defs) != 1 or defs[0]["file"] != str(path) or not event:
        raise ValueError("Select one existing active macro and event; custom routines are never created automatically")
    body = defs[0]["body"]
    hook = {"start":"KCD_JOB_START", "end":"CMD=send_print_cost", "cancel":"KCD_JOB_CANCEL", "pause":"KCD_JOB_PAUSE", "resume":"KCD_JOB_RESUME"}[event]
    if hook in body:
        return {"applied": False, "diff": "", "status": "Hook already present; no duplicate inserted"}
    # A wrapper that delegates to another hooked macro must not emit the same event.
    for name, definition in effective.items():
        if re.search(r'^\s*'+re.escape(name)+r'(?:\s|$)', body, re.M | re.I) and hook in definition["body"]:
            raise ValueError("Delegated macro already emits this event; refusing a duplicate hook")
    before = path.read_text()
    with tempfile.TemporaryDirectory() as tmp:
        candidate = Path(tmp)/path.name
        candidate.write_text(before)
        if event == "end":
            ok, message = im.insert_run_shell_command(macro, str(candidate), "")
        else:
            function = {"start":im.insert_macro_call, "cancel":im.insert_cancel_macro_call,
                        "pause":im.insert_pause_macro_call, "resume":im.insert_resume_macro_call}[event]
            ok, message = function(macro, str(candidate), hook)
        if not ok:
            raise ValueError(message)
        after = candidate.read_text()
        if event in {"cancel", "pause", "resume"}:
            # mainsail.cfg is often a symlink shared by several printer
            # instances. Unconfigured instances must not call missing KCD macros.
            after = re.sub(r'^([ \t]*)'+re.escape(hook)+r'[ \t]*$',
                lambda m: m[1]+"{% if 'gcode_macro "+hook+"' in printer %}\n"+m[1]+"    "+hook+"\n"+m[1]+"{% endif %}",after,flags=re.M)
    diff = "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True), fromfile=str(path), tofile=str(path)))
    if apply and diff:
        atomic_write(path, after, path.stat().st_mode & 0o777)
    return {"applied": bool(apply and diff), "diff": diff, "status": message}
