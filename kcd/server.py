"""Single-process production server; initialize defaults only for a brand-new SQL DB."""
import argparse
import os


def initialize_fresh_master():
    from core import db
    from core.sql_only import is_sql_only
    if is_sql_only() and not os.path.exists(db._db_path()):
        with db.connect_db() as conn:
            db.apply_migrations(conn)
            from core.printer_lifecycle import _save_setting
            _save_setting(conn, "display_settings", {"pause_include_paused_time_default": False, "hidden_printers": []})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=5000)
    args = parser.parse_args()
    initialize_fresh_master()
    from waitress import serve
    from app import app
    # In-memory live state is serialized and persisted in SQL. Multiple worker
    # processes are intentionally unsupported; use one process with threads.
    serve(app, host="0.0.0.0", port=args.port, threads=4, max_request_body_size=1024*1024)


if __name__ == "__main__":
    main()
