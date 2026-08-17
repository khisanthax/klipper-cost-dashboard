#!/usr/bin/env python
"""
SQL-only startup and representative runtime filesystem validation.

Observation starts before app import. Known legacy business-state paths are
blocked at Python's file-open layer, including direct accesses that bypass KCD's
centralized guard helpers. Deliberate credential/cache/export/backup exceptions
remain allowed by policy.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from typing import Dict, Tuple


FILES_TO_WATCH = [
    "data/settings.json",
    "data/display.json",
    "data/app_settings.json",
    "data/print_costs.csv",
    "data/system_events.jsonl",
    "data/live_jobs.json",
]


# Script execution otherwise allows an inherited PYTHONPATH to resolve another
# project's ``app`` module before this repository's app.py.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)


def _snapshot_files() -> Dict[str, Tuple[bool, float]]:
    out: Dict[str, Tuple[bool, float]] = {}
    for path in FILES_TO_WATCH:
        try:
            exists = os.path.exists(path)
            mtime = os.path.getmtime(path) if exists else 0.0
            out[path] = (exists, mtime)
        except Exception:
            out[path] = (False, 0.0)
    return out


def _detect_changes(before: Dict[str, Tuple[bool, float]]) -> Dict[str, str]:
    changes: Dict[str, str] = {}
    for path, (existed, mtime) in before.items():
        try:
            now_exists = os.path.exists(path)
            now_mtime = os.path.getmtime(path) if now_exists else 0.0
            if not existed and now_exists:
                changes[path] = "created"
            elif existed and now_exists and now_mtime > mtime + 1e-6:
                changes[path] = "modified"
        except Exception:
            continue
    return changes


def _prepare_isolated_sql_state() -> None:
    from core import db

    with db.connect_db() as conn:
        db.apply_migrations(conn)
        conn.execute(
            """
            INSERT INTO user_settings (key, value_json, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value_json = excluded.value_json,
                updated_at = excluded.updated_at
            """,
            (
                "display_settings",
                json.dumps({"pause_include_paused_time_default": False}),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()


def _run_validation() -> int:
    before = _snapshot_files()
    from core.sql_only import SqlOnlyFilesystemMonitor, SqlOnlyViolationError

    try:
        with SqlOnlyFilesystemMonitor() as monitor:
            # Import only after observation begins so startup is part of the contract.
            import app as kcd_app  # noqa: WPS433

            client = kcd_app.app.test_client()
            endpoints = [
                "/health",
                "/",
                "/reports",
                "/projects",
                "/settings/printers",
                "/settings/other",
                "/system-events",
            ]

            for ep in endpoints:
                resp = client.get(ep)
                if resp.status_code >= 400:
                    print(f"[sql-only] endpoint failed: {ep} status={resp.status_code}")
                    return 2
    except SqlOnlyViolationError as exc:
        print(f"[sql-only] forbidden filesystem access: {exc}")
        return 4

    # allow any async disk flush
    time.sleep(0.1)
    changes = _detect_changes(before)
    if changes:
        for path, reason in changes.items():
            print(f"[sql-only] file change detected: {path} ({reason})")
        return 3

    allowed = [access for access in monitor.accesses if access["classification"].startswith("allowed_")]
    print(
        "[sql-only] validation OK: startup/routes used no legacy runtime files "
        f"({len(allowed)} allowed filesystem access(es) observed)."
    )
    return 0


def main() -> int:
    os.environ["KCD_STORAGE_BACKEND"] = "sql"
    os.environ.setdefault("KCD_API_KEY", "sql-only-validator")

    original_cwd = os.getcwd()
    with tempfile.TemporaryDirectory(prefix="kcd-sql-only-validation-") as tmpdir:
        try:
            os.chdir(tmpdir)
            _prepare_isolated_sql_state()
            return _run_validation()
        finally:
            os.chdir(original_cwd)


if __name__ == "__main__":
    raise SystemExit(main())
