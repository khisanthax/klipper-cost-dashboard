"""Bounded Klipper discovery and traversal of the actual configuration include graph."""
from __future__ import annotations

import configparser
import glob
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import urllib.request
from concurrent.futures import ThreadPoolExecutor


def active_config_files(config_dir, max_files=256, *, restrict_to_root=False):
    root = Path(config_dir).resolve()
    files, errors, visiting = [], [], set()
    def visit(path, depth=0):
        # Klipper resolves includes relative to the include path, including
        # symlink names, rather than the symlink target's parent directory.
        path = Path(os.path.abspath(path))
        if restrict_to_root and not path.resolve().is_relative_to(root):
            raise ValueError("An active Klipper configuration escapes the selected directory")
        if depth > 24 or len(files) >= max_files:
            raise ValueError("Klipper include graph exceeds safe discovery limits")
        if path in visiting:
            errors.append(f"Include cycle: {path}")
            return
        if path in files:
            return
        if not path.is_file():
            errors.append(f"Missing config: {path}")
            return
        visiting.add(path)
        files.append(path)
        for line in path.read_text(errors="replace").splitlines():
            match = re.match(r"^\s*\[include\s+(.+?)\]\s*(?:[#;].*)?$", line, re.I)
            if match:
                pattern = str(path.parent / match[1])
                if restrict_to_root and not Path(pattern).resolve().is_relative_to(root):
                    raise ValueError("An active Klipper include escapes the selected directory")
                matches = sorted(glob.glob(pattern))
                if not matches and not glob.has_magic(pattern):
                    errors.append(f"Missing include: {pattern}")
                for child in matches:
                    visit(Path(child), depth+1)
        visiting.remove(path)
    visit(root / "printer.cfg")
    return files, errors


def effective_macro_sources(config_dir, *, restrict_to_root=False):
    """Follow Klipper's linear include/override order for each macro's gcode."""
    active, definitions, stack = {}, [], set()
    root = Path(config_dir).resolve()
    def visit(path, depth=0):
        path = Path(os.path.abspath(path))
        if restrict_to_root and not path.resolve().is_relative_to(root):
            raise ValueError("An active Klipper configuration escapes the selected directory")
        if path in stack or depth > 24 or len(definitions) > 2048:
            raise ValueError("Recursive or excessive macro include graph")
        stack.add(path)
        buffer = []
        def flush():
            if not any(line.strip() for line in buffer):
                buffer.clear(); return
            parser = configparser.RawConfigParser(strict=False, interpolation=None, inline_comment_prefixes=(';', '#'))
            parser.read_string('\n'.join(buffer))
            for section in parser.sections():
                if section.lower().startswith('gcode_macro ') and parser.has_option(section,'gcode'):
                    name=section[len('gcode_macro '):]
                    item={'file':str(path),'name':name,'body':parser.get(section,'gcode')}
                    definitions.append(item);active[name.upper()]=item
            buffer.clear()
        for line in path.read_text(errors='replace').splitlines():
            match=re.match(r'^\s*\[include\s+(.+?)\]\s*(?:[#;].*)?$',line,re.I)
            if match:
                flush()
                if restrict_to_root and not (path.parent / match[1]).resolve().is_relative_to(root):
                    raise ValueError("An active Klipper include escapes the selected directory")
                for child in sorted(glob.glob(str(path.parent/match[1]))):visit(child,depth+1)
            else:buffer.append(line)
        flush();stack.remove(path)
    visit(Path(config_dir)/'printer.cfg')
    return active, definitions


def process_instances(proc_root="/proc"):
    result = []
    for entry in Path(proc_root).glob("[0-9]*/cmdline"):
        try:
            args = entry.read_bytes().decode().rstrip("\0").split("\0")
            if not any(a.endswith(("klippy.py", "moonraker.py")) for a in args):
                continue
            def option(flag):
                return args[args.index(flag)+1] if flag in args and args.index(flag)+1 < len(args) else None
            klipper = any(a.endswith("klippy.py") for a in args)
            cfg = next((a for a in args if a.endswith(".cfg")), None) if klipper else option("-c")
            if not klipper and not cfg and option("-d"):
                cfg = str(Path(option("-d")) / "config/moonraker.conf")
            result.append({"kind": "klipper" if klipper else "moonraker", "config": cfg, "socket": option("-a")})
        except (OSError, UnicodeError):
            continue
    return result


def discover_local(home=None, extra_dirs=(), instances=None):
    home = Path(home or Path.home())
    instances = process_instances() if instances is None else instances
    candidates = set()
    # Fixed depth; no recursive home or root filesystem scan.
    for pattern in ("printer_data/config/printer.cfg", "*data/config/printer.cfg", "*/printer.cfg"):
        candidates.update(p.parent.resolve() for p in home.glob(pattern))
    candidates.update(Path(p).resolve() for p in extra_dirs)
    active = {Path(p["config"]).resolve().parent for p in instances if p["kind"] == "klipper" and p["config"]}
    candidates.update(active)
    moonrakers = []
    for process in instances:
        if process["kind"] != "moonraker" or not process["config"]:
            continue
        cfg = Path(process["config"])
        parser = configparser.ConfigParser(strict=False, interpolation=None)
        try:
            parser.read(cfg)
            port = parser.getint("server", "port", fallback=None)
            uds = parser.get("server", "klippy_uds_address", fallback="")
            moonrakers.append((cfg.parent.resolve(), port, os.path.expanduser(uds)))
        except (OSError, configparser.Error, ValueError):
            continue
    results = []
    for directory in sorted(candidates):
        files, errors = active_config_files(directory)
        identity = ""
        for file in files:
            text = file.read_text(errors="replace")
            match = re.search(r'^\s*variable_printer_name:\s*["\']?([^"\'\n]+)', text, re.M)
            if match:
                identity = match[1].strip()
        port = None
        for cfg_dir, candidate_port, uds in moonrakers:
            sockets = [p["socket"] for p in instances if p["kind"] == "klipper" and p["config"] and Path(p["config"]).resolve().parent == directory]
            if cfg_dir == directory or (uds and uds in sockets):
                port = candidate_port
        results.append({"config_dir": str(directory), "active_process": directory in active,
                        "classification": "active" if directory in active else "unverified/obsolete candidate",
                        "printer_name": identity, "moonraker_port": port,
                        "scripts": [p.name for p in directory.glob("kcd_job_*.sh")] + (["send_print_cost.sh"] if (directory/"send_print_cost.sh").exists() else []),
                        "errors": errors, "active_files": [str(p) for p in files]})
    return results


def local_lan_addresses():
    try:
        data = json.loads(subprocess.check_output(["ip", "-j", "addr"], timeout=3))
        return sorted({a["local"] for interface in data for a in interface.get("addr_info", [])
                       if a.get("family") == "inet" and a.get("scope") == "global"
                       and not interface.get("ifname", "").startswith(("docker", "br-", "veth"))})
    except (OSError, ValueError, subprocess.SubprocessError):
        return sorted({a[4][0] for a in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
                       if not ipaddress.ip_address(a[4][0]).is_loopback})


def probe_endpoint(url, timeout=2, *, opener=None):
    def get(path):
        request = opener.open if opener is not None else urllib.request.urlopen
        with request(url.rstrip("/")+path, timeout=timeout) as response:
            return json.load(response)["result"]
    try:
        info = get("/server/info")
        if not isinstance(info, dict) or "klippy_connected" not in info:
            return None
        system = get("/machine/system_info").get("system_info", {})
        roots = get("/server/files/roots")
        config = next((r["path"] for r in roots if r["name"] == "config"), None)
        # Instance IDs alone repeat on different hosts. Bind to host identity too.
        network = system.get("network", {})
        macs = sorted(v.get("mac_address", "") for v in network.values() if v.get("mac_address") and v.get("mac_address") != "00:00:00:00:00:00")
        identity = (tuple(macs), system.get("instance_ids", {}).get("moonraker"), config)
        return {"url": url, "config_dir": config, "identity": identity if macs and identity[1] and config else (url,),
                "connected": info["klippy_connected"]}
    except (OSError, ValueError, KeyError, TypeError):
        return None


def discover_endpoints(ports, hosts=None):
    hosts = hosts or [*local_lan_addresses(), "127.0.0.1", "localhost"]
    ports = sorted({p for p in ports if isinstance(p, int) and 0 < p < 65536})
    urls = [f"http://{h}:{p}" for h in hosts for p in ports]
    groups = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        for probe in pool.map(probe_endpoint, urls):
            if probe:
                key = probe["identity"]
                if key not in groups:
                    groups[key] = {**probe, "aliases": []}
                groups[key]["aliases"].append(probe["url"])
    return list(groups.values())
