#!/usr/bin/env python
"""
Lightweight SQL-only validation helper.

Runs a small set of endpoints under KCD_STORAGE_BACKEND=sql and checks that
runtime file-backed state is not modified.
"""
from __future__ import annotations

import os
import sys
import time
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


def main() -> int:
    os.environ["KCD_STORAGE_BACKEND"] = "sql"
    # Import app after setting env.
    import app as kcd_app  # noqa: WPS433

    client = kcd_app.app.test_client()
    before = _snapshot_files()

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

    # allow any async disk flush
    time.sleep(0.1)
    changes = _detect_changes(before)
    if changes:
        for path, reason in changes.items():
            print(f"[sql-only] file change detected: {path} ({reason})")
        return 3

    print("[sql-only] validation OK: no runtime file writes detected.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
