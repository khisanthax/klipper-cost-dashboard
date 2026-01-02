"""KCD CLI entrypoint."""
from __future__ import annotations

import argparse
import os

from core import db as db_module
from core import db_import
from core import db_verify
from core import db_backfill
from core import history_parity
from core import reports_parity
from core import export_csv
from core import reports_cache


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


def _cmd_db_backfill(args: argparse.Namespace) -> int:
    report = db_backfill.run_backfill(source=args.source)
    if report.get("source") == "moonraker":
        print(
            "DB backfill (moonraker) complete. "
            f"Targets: {report.get('targets', 0)}, "
            f"Updated: {report.get('updated', 0)}, "
            f"Skipped: {report.get('skipped', 0)}"
        )
        if report.get("fallback") == "csv":
            print(f"Fallback CSV upserts: {report.get('csv_rows_upserted', 0)}")
    else:
        print(
            "DB backfill (csv) complete. "
            f"Rows seen: {report.get('rows_seen', 0)}, "
            f"Upserted: {report.get('rows_upserted', 0)}"
        )
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

    db_backfill_cmd = db_sub.add_parser("backfill", help="Backfill DB rows from CSV or Moonraker history")
    db_backfill_cmd.add_argument(
        "--source",
        choices=("csv", "moonraker"),
        default="csv",
        help="Backfill source (default: csv).",
    )
    db_backfill_cmd.set_defaults(func=_cmd_db_backfill)

    reports_parser = sub.add_parser("reports", help="Reports utilities")
    reports_sub = reports_parser.add_subparsers(dest="reports_command")

    reports_parity_cmd = reports_sub.add_parser("parity", help="Compare CSV and SQL reports totals")
    reports_parity_cmd.add_argument("--range", dest="range_str", default="30d")
    reports_parity_cmd.add_argument("--dump-job-diff", action="store_true")
    reports_parity_cmd.add_argument("--regen-csv-from-sql", action="store_true")
    reports_parity_cmd.add_argument("--overwrite", action="store_true")
    reports_parity_cmd.set_defaults(func=lambda args: _cmd_reports_parity(args))
    history_parser = sub.add_parser("history", help="History utilities")
    history_sub = history_parser.add_subparsers(dest="history_command")

    history_parity_cmd = history_sub.add_parser("parity", help="Compare CSV and SQL history rows")
    history_parity_cmd.add_argument("--limit", type=int, default=200)
    history_parity_cmd.set_defaults(func=lambda args: _cmd_history_parity(args))
    export_parser = sub.add_parser("export", help="Export utilities")
    export_sub = export_parser.add_subparsers(dest="export_command")

    export_csv_cmd = export_sub.add_parser("csv", help="Export CSV")
    export_csv_cmd.add_argument("--from", dest="source", choices=("sql",), default="sql")
    export_csv_cmd.add_argument("--out", dest="out_path", default=os.path.join("data", "print_costs.csv"))
    export_csv_cmd.add_argument("--overwrite", action="store_true")
    export_csv_cmd.set_defaults(func=lambda args: _cmd_export_csv(args))

    cache_parser = sub.add_parser("cache", help="Reports cache utilities")
    cache_sub = cache_parser.add_subparsers(dest="cache_command")

    cache_info_cmd = cache_sub.add_parser("info", help="Show reports cache stats")
    cache_info_cmd.set_defaults(func=lambda args: _cmd_cache_info(args))

    cache_clear_cmd = cache_sub.add_parser("clear", help="Clear reports cache")
    cache_clear_cmd.add_argument("--key", dest="key", default="")
    cache_clear_cmd.add_argument("--range", dest="range_key", default="")
    cache_clear_cmd.set_defaults(func=lambda args: _cmd_cache_clear(args))

    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 1
    return int(args.func(args))


def _cmd_history_parity(args: argparse.Namespace) -> int:
    report = history_parity.run_parity(limit=args.limit)
    print(history_parity.render_parity_summary(report))
    return 0


def _cmd_reports_parity(args: argparse.Namespace) -> int:
    report = reports_parity.run_parity(
        range_str=args.range_str,
        dump_job_diff=args.dump_job_diff,
        regen_csv_from_sql=args.regen_csv_from_sql,
        overwrite_csv=args.overwrite,
    )
    print(reports_parity.render_parity_summary(report))
    return 0


def _cmd_export_csv(args: argparse.Namespace) -> int:
    count, path = export_csv.export_csv_from_sql(out_path=args.out_path, overwrite=args.overwrite)
    print(f"Exported {count} rows to {path}")
    return 0


def _cmd_cache_info(_args: argparse.Namespace) -> int:
    info = reports_cache.cache_info()
    print(f"Cache rows: {info.get('count', 0)}")
    print(f"Oldest: {info.get('oldest', 0)}")
    print(f"Newest: {info.get('newest', 0)}")
    for group in info.get("groups", []):
        print(f"- {group}")
    return 0


def _cmd_cache_clear(args: argparse.Namespace) -> int:
    key = str(args.key or "").strip() or None
    range_key = str(args.range_key or "").strip() or None
    removed = reports_cache.clear_cache(key=key, range_key=range_key)
    print(f"Cleared cache rows: {removed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
