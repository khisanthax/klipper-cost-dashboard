"""
Curated system events / audit trail for Klipper Cost Dashboard.

This is intentionally not a raw log viewer. Events are human-readable summaries of
meaningful actions and warnings (deletes, failures, manual actions required, etc.).

SQL-only note:
  The event store is file-backed and is blocked in SQL-only mode to avoid runtime
  JSON/JSONL reads/writes.
"""

from __future__ import annotations

import json
import os
from contextlib import closing
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from core.config import DATA_DIR
from core.sql_only import require_file_reads_allowed, require_file_writes_allowed, is_sql_only
from core import db as db_module

EVENTS_FILE = os.path.join(DATA_DIR, "system_events.jsonl")
MAX_EVENTS = 1000

VALID_CATEGORIES = {"activity", "deleted", "warning", "failure"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_meta(meta: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(meta, dict):
        return {}
    out: Dict[str, Any] = {}
    for k, v in meta.items():
        key = str(k).strip()
        if not key:
            continue
        if v is None:
            out[key] = None
        elif isinstance(v, (str, int, float, bool)):
            out[key] = v
        else:
            out[key] = str(v)
    return out


def _enforce_retention(max_events: int = MAX_EVENTS) -> None:
    try:
        require_file_writes_allowed("system_events.jsonl", caller_hint="core.system_events._enforce_retention")
        if not os.path.exists(EVENTS_FILE):
            return
        with open(EVENTS_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) <= max_events:
            return
        keep = lines[-max_events:]
        tmp = f"{EVENTS_FILE}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.writelines(keep)
        os.replace(tmp, EVENTS_FILE)
    except Exception:
        return


def _enforce_retention_sql(max_events: int = MAX_EVENTS) -> None:
    try:
        with closing(db_module.connect_db()) as conn:
            db_module.apply_migrations(conn)
            conn.execute(
                """
                DELETE FROM system_events
                 WHERE id NOT IN (
                     SELECT id FROM system_events ORDER BY ts DESC LIMIT ?
                 )
                """,
                (max_events,),
            )
            conn.commit()
    except Exception:
        return


def _emit_event_sql(event: Dict[str, Any]) -> None:
    with closing(db_module.connect_db()) as conn:
        db_module.apply_migrations(conn)
        conn.execute(
            """
            INSERT INTO system_events (ts, category, title, message, severity, meta_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                event.get("ts"),
                event.get("category"),
                event.get("title"),
                event.get("message"),
                event.get("category"),
                json.dumps(event.get("meta") or {}, ensure_ascii=False),
            ),
        )
        conn.commit()
    try:
        _enforce_retention_sql()
    except Exception:
        return


def emit_event(category: str, title: str, message: str, meta: Optional[Dict[str, Any]] = None) -> None:
    """
    Append a single curated event to the event store.

    Retention is enforced as a best-effort ring buffer.
    """
    cat = str(category or "").strip().lower()
    if cat not in VALID_CATEGORIES:
        cat = "activity"

    event = {
        "ts": _now_iso(),
        "category": cat,
        "title": str(title or "").strip()[:200],
        "message": str(message or "").strip()[:2000],
        "meta": _safe_meta(meta),
    }

    if is_sql_only():
        _emit_event_sql(event)
        return

    require_file_writes_allowed("system_events.jsonl", caller_hint="core.system_events.emit_event")

    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(EVENTS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
        _enforce_retention()
    except Exception:
        return


def _iter_events_newest_first() -> Iterable[Dict[str, Any]]:
    if is_sql_only():
        with closing(db_module.connect_db()) as conn:
            db_module.apply_migrations(conn)
            rows = conn.execute(
                "SELECT ts, category, title, message, meta_json FROM system_events ORDER BY ts DESC"
            ).fetchall()
        out: List[Dict[str, Any]] = []
        for r in rows:
            meta_raw = r["meta_json"] if isinstance(r, dict) or hasattr(r, "__getitem__") else None
            meta = {}
            if meta_raw:
                try:
                    meta = json.loads(meta_raw)
                except Exception:
                    meta = {}
            out.append(
                {
                    "ts": r["ts"] if hasattr(r, "__getitem__") else None,
                    "category": r["category"] if hasattr(r, "__getitem__") else None,
                    "title": r["title"] if hasattr(r, "__getitem__") else None,
                    "message": r["message"] if hasattr(r, "__getitem__") else None,
                    "meta": meta,
                }
            )
        return out

    require_file_reads_allowed("system_events.jsonl", caller_hint="core.system_events._iter_events_newest_first")
    if not os.path.exists(EVENTS_FILE):
        return []
    try:
        with open(EVENTS_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception:
        return []

    out: List[Dict[str, Any]] = []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if not isinstance(obj, dict):
            continue
        out.append(obj)
    return out


def list_events(filter_name: str = "all", limit: int = 200) -> List[Dict[str, Any]]:
    """
    Return recent events (newest first).

    filter_name:
      - all
      - failures  (warning + failure)
      - deleted
    """
    try:
        lim = int(limit)
    except Exception:
        lim = 200
    lim = max(1, min(2000, lim))

    f = str(filter_name or "all").strip().lower()
    if f in ("failures", "warnings", "failures_warnings", "failures-and-warnings"):
        allowed = {"warning", "failure"}
    elif f == "deleted":
        allowed = {"deleted"}
    else:
        allowed = None

    results: List[Dict[str, Any]] = []
    for ev in _iter_events_newest_first():
        try:
            cat = str(ev.get("category") or "").strip().lower()
        except Exception:
            cat = ""
        if allowed is not None and cat not in allowed:
            continue
        results.append(ev)
        if len(results) >= lim:
            break
    return results
