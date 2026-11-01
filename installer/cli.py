"""Noninteractive discovery, dry-run repair and explicit macro patching."""
import argparse
import json
import os
from pathlib import Path
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description="KCD installer diagnostics and recoverable repair")
    sub = parser.add_subparsers(dest="command", required=True)
    discovery = sub.add_parser("discover")
    discovery.add_argument("--config-dir", action="append", default=[])
    repair = sub.add_parser("repair-client")
    for option in ("config-dir", "printer", "moonraker", "master"):
        repair.add_argument("--"+option, required=True)
    repair.add_argument("--key-file", help="Private JSON credential file; otherwise KCD_API_KEY or hidden prompt")
    repair.add_argument("--apply", action="store_true", help="Write changes; default is a dry run")
    repair.add_argument("--relocate", action="store_true", help="Preserve identity when changing host or endpoint")
    repair.add_argument("--reactivate", action="store_true")
    macro = sub.add_parser("macro-plan")
    macro.add_argument("--config-dir", required=True)
    macro.add_argument("--file", help="Active config file relative to config-dir")
    macro.add_argument("--macro")
    macro.add_argument("--event", choices=["start", "end", "cancel", "pause", "resume"])
    macro.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "discover":
            from .discovery import discover_local, discover_endpoints
            clients = discover_local(extra_dirs=args.config_dir)
            result = {"candidates": clients, "endpoints": discover_endpoints([c["moonraker_port"] for c in clients])}
        elif args.command == "repair-client":
            from .workflow import repair_client
            import getpass
            key = os.environ.get("KCD_API_KEY", "")
            if args.key_file:
                key = json.loads(Path(args.key_file).read_text())["api_key"]
            if not key:
                key = getpass.getpass("Master API key: ")
            result = repair_client(args.config_dir, args.printer, args.moonraker, args.master, key,
                                   apply=args.apply, relocate=args.relocate, reactivate=args.reactivate)
        else:
            from .macro_plan import macro_plan
            result = macro_plan(args.config_dir, args.file, args.macro, args.event, apply=args.apply)
        print(json.dumps(result, indent=2))
        return 0
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"Installer failed: {exc}", file=sys.stderr)
        return 2
