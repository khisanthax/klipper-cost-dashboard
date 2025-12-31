"""
Backfill SQLite job rows from CSV history.

This is a one-time repair tool to sync existing DB rows with CSV data.
"""
from __future__ import annotations

import logging
from typing import Dict, Optional

from core import db as db_module
from core.config import CSV_FILE
from core.storage import load_rows_raw

logger = logging.getLogger(__name__)


def _needs_backfill(conn, job_uid: str, csv_row: dict) -> bool:
    row = conn.execute(
        "SELECT duration_seconds, rate_per_hour, time_cost, total_cost FROM jobs WHERE job_uid = ?",
        (job_uid,),
    ).fetchone()
    if not row:
        return True

    def _num(val) -> float:
        try:
            if val is None or (isinstance(val, str) and not val.strip()):
                return 0.0
            return float(val)
        except Exception:
            return 0.0

    db_duration = _num(row["duration_seconds"])
    db_rate = _num(row["rate_per_hour"])
    db_time = _num(row["time_cost"])
    db_total = _num(row["total_cost"])

    csv_duration = _num(csv_row.get("duration_seconds"))
    csv_rate = _num(csv_row.get("rate_per_hour"))
    csv_time = _num(csv_row.get("time_cost"))
    csv_total = _num(csv_row.get("total_cost"))

    if db_duration <= 0 and csv_duration > 0:
        return True
    if db_rate <= 0 and csv_rate > 0:
        return True
    if db_time <= 0 and csv_time > 0:
        return True
    if db_total <= 0 and csv_total > 0:
        return True
    return False


def run_backfill() -> Dict[str, int]:
    """
    Upsert CSV rows into SQLite only when DB fields are missing/zero.

    Returns a summary dict with counts.
    """
    rows, _err = load_rows_raw(CSV_FILE)
    if not rows:
        return {"rows_seen": 0, "rows_upserted": 0}

    rows_upserted = 0
    with db_module.connect_db() as conn:
        db_module.apply_migrations(conn)
        for row in rows:
            job_uid = str(row.get("job_uid") or "").strip()
            if not job_uid:
                continue
            if not _needs_backfill(conn, job_uid, row):
                continue

            # Ensure the DB sees a numeric timestamp (epoch) if available.
            ts_epoch = row.get("timestamp_epoch")
            if ts_epoch is None or ts_epoch == "":
                ts_epoch = row.get("timestamp_raw")
            if ts_epoch is not None and ts_epoch != "":
                row = dict(row)
                row["timestamp"] = ts_epoch
            try:
                db_module.upsert_job(conn, row)
                rows_upserted += 1
            except Exception as exc:
                logger.warning("Backfill upsert failed for job_uid=%s: %s", job_uid, exc)
        conn.commit()

    return {"rows_seen": len(rows), "rows_upserted": rows_upserted}
