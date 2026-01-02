"""
DB-backed cache helpers for SQL reports.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from core import db as db_module

REPORTS_CACHE_VERSION = int(os.getenv("KCD_REPORTS_CACHE_VERSION", "1"))


def _utc_now_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def get_cached_payload(
    conn,
    *,
    key: str,
    range_key: str,
    fingerprint: str,
    ttl_seconds: int,
) -> Optional[Dict[str, Any]]:
    if ttl_seconds <= 0:
        return None

    row = conn.execute(
        """
        SELECT payload_json, generated_at
          FROM report_cache
         WHERE key = ?
           AND range_key = ?
           AND backend_version = ?
           AND jobs_fingerprint = ?
         ORDER BY generated_at DESC
         LIMIT 1
        """,
        (key, range_key, REPORTS_CACHE_VERSION, fingerprint),
    ).fetchone()
    if not row:
        return None

    generated_at = int(row["generated_at"] or 0)
    if generated_at <= 0:
        return None

    if (_utc_now_ts() - generated_at) > ttl_seconds:
        return None

    try:
        return json.loads(row["payload_json"] or "{}")
    except Exception:
        return None


def set_cached_payload(
    conn,
    *,
    key: str,
    range_key: str,
    fingerprint: str,
    payload: Dict[str, Any],
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO report_cache
            (key, range_key, backend_version, jobs_fingerprint, generated_at, payload_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            key,
            range_key,
            REPORTS_CACHE_VERSION,
            fingerprint,
            _utc_now_ts(),
            json.dumps(payload),
        ),
    )


def cache_info() -> Dict[str, Any]:
    conn = db_module.connect_db()
    db_module.apply_migrations(conn)

    row = conn.execute(
        "SELECT COUNT(*) AS count, MIN(generated_at) AS oldest, MAX(generated_at) AS newest FROM report_cache"
    ).fetchone()
    count = int(row["count"] or 0) if row else 0
    oldest = int(row["oldest"] or 0) if row else 0
    newest = int(row["newest"] or 0) if row else 0

    keys = conn.execute(
        """
        SELECT key, range_key, backend_version, COUNT(*) AS entries
          FROM report_cache
         GROUP BY key, range_key, backend_version
         ORDER BY key, range_key
        """
    ).fetchall()

    return {
        "count": count,
        "oldest": oldest,
        "newest": newest,
        "groups": [dict(k) for k in keys],
    }


def clear_cache(*, key: Optional[str] = None, range_key: Optional[str] = None) -> int:
    conn = db_module.connect_db()
    db_module.apply_migrations(conn)

    where = []
    params = []
    if key:
        where.append("key = ?")
        params.append(key)
    if range_key:
        where.append("range_key = ?")
        params.append(range_key)

    where_sql = ""
    if where:
        where_sql = "WHERE " + " AND ".join(where)

    res = conn.execute(f"DELETE FROM report_cache {where_sql}", params)
    conn.commit()
    return int(res.rowcount or 0)
