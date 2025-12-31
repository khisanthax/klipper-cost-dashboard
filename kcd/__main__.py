"""KCD CLI entrypoint."""
from __future__ import annotations

import argparse

from core import db as db_module
from core import db_import
from core import db_verify
from core import db_backfill
from core import history_parity


def _cmd_db_init(_args: argparse.Namespace) -> int:
    version = db_module.init_db()
    print(f"DB initialized. Current schema: {version}")
    return 0


def _cmd_db_import(args: argparse.Namespace) -> int:
    report = db_import.run_import(skip_existing=not args.overwrite, overwrite=args.overwrite)
    print(db_import.render_import_summary(report))
    return 0


def _cmd_db_verify(_args: argparse.Namespace) -> int:
    report = db_verify.run_verify()
    print(db_verify.render_verify_summary(report))
    return 0


def _cmd_db_backfill(_args: argparse.Namespace) -> int:
    report = db_backfill.run_backfill()
    print(f"DB backfill complete. Rows seen: {report.get('rows_seen', 0)}, upserted: {report.get('rows_upserted', 0)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Klipper Cost Dashboard utilities")
    sub = parser.add_subparsers(dest="command")

    db_parser = sub.add_parser("db", help="Database utilities")
    db_sub = db_parser.add_subparsers(dest="db_command")

    db_init = db_sub.add_parser("init", help="Initialize the SQLite database")
    db_init.set_defaults(func=_cmd_db_init)

    db_import_cmd = db_sub.add_parser("import", help="Import CSV/JSON into SQLite")
    db_import_cmd.add_argument("--overwrite", action="store_true", help="Overwrite existing imported rows")
    db_import_cmd.set_defaults(func=_cmd_db_import)

    db_verify_cmd = db_sub.add_parser("verify", help="Verify CSV/DB parity")
    db_verify_cmd.set_defaults(func=_cmd_db_verify)

    db_backfill_cmd = db_sub.add_parser("backfill", help="Backfill DB rows from CSV history")
    db_backfill_cmd.set_defaults(func=_cmd_db_backfill)

    history_parser = sub.add_parser("history", help="History utilities")
    history_sub = history_parser.add_subparsers(dest="history_command")

    history_parity_cmd = history_sub.add_parser("parity", help="Compare CSV and SQL history rows")
    history_parity_cmd.add_argument("--limit", type=int, default=200)
    history_parity_cmd.set_defaults(func=lambda args: _cmd_history_parity(args))
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 1
    return int(args.func(args))


def _cmd_history_parity(args: argparse.Namespace) -> int:
    report = history_parity.run_parity(limit=args.limit)
    print(history_parity.render_parity_summary(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
