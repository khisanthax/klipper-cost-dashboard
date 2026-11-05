"""Single-process production server; initialize defaults only for a brand-new SQL DB."""
import argparse
import logging
import os
import signal
from threading import Event, Thread


def _shutdown(_signum, _frame):
    # Waitress catches SystemExit, waits for active worker threads, then exits.
    # An explicit handler also works when Python is Docker's PID 1.
    raise SystemExit(0)


def initialize_fresh_master():
    from core.runtime import is_demo_runtime
    if is_demo_runtime():
        return
    from core import db
    from core.sql_only import is_sql_only
    if is_sql_only() and not os.path.exists(db._db_path()):
        with db.connect_db() as conn:
            db.apply_migrations(conn)
            from core.printer_lifecycle import _save_setting
            _save_setting(conn, "display_settings", {"pause_include_paused_time_default": False, "hidden_printers": []})


def clean_demo_workspaces(manager):
    delay = Event()
    while True:
        try:
            manager.cleanup_expired_workspaces()
        except Exception:
            logging.getLogger(__name__).error("Demo workspace cleanup failed")
        delay.wait(60)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()
    initialize_fresh_master()
    from waitress import serve
    from app import app
    signal.signal(signal.SIGTERM,_shutdown)
    demo_manager = app.extensions.get("kcd_demo_manager")
    if demo_manager is not None:
        Thread(target=clean_demo_workspaces, args=(demo_manager,), daemon=True).start()
    # In-memory live state is serialized and persisted in SQL. Multiple worker
    # processes are intentionally unsupported; use one process with threads.
    options = {}
    if demo_manager is not None and demo_manager.configuration.trust_proxy:
        # Keep the socket peer in REMOTE_ADDR. The demo boundary validates that
        # peer against its explicit proxy allowlist before consulting headers.
        options["clear_untrusted_proxy_headers"] = False
    serve(app, host=args.host, port=args.port, threads=4, max_request_body_size=1024*1024, **options)


if __name__ == "__main__":
    main()
