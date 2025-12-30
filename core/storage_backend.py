"""
Storage backend abstraction for job writes.

Phase 2: dual-write (CSV + SQLite) for job append/update.
"""
from __future__ import annotations

import logging
import os
from typing import Iterable

from core import db as db_module
from core.config import CSV_FILE, HEADERS
from core.storage import append_row, load_rows_raw, rewrite_csv_recalculate_costs_job_uids

logger = logging.getLogger(__name__)


def _mode() -> str:
    return str(os.getenv("KCD_STORAGE_BACKEND", "csv")).strip().lower()


def write_job(row: dict) -> None:
    mode = _mode()
    if mode in ("csv", "dual"):
        append_row(CSV_FILE, HEADERS, row)
    if mode in ("sql", "dual"):
        try:
            with db_module.connect_db() as conn:
                db_module.apply_migrations(conn)
                db_module.upsert_job(conn, row)
                conn.commit()
        except Exception as exc:
            if mode == "dual":
                logger.error("SQL write failed in dual mode: %s", exc)
            else:
                raise


def recalc_jobs(job_uids: Iterable[str], compute_costs_fn) -> int:
    updated = rewrite_csv_recalculate_costs_job_uids(CSV_FILE, HEADERS, job_uids, compute_costs_fn)
    if updated <= 0:
        return updated

    mode = _mode()
    if mode not in ("sql", "dual"):
        return updated

    try:
        rows, _ = load_rows_raw(CSV_FILE)
        uid_set = {str(u or "").strip() for u in (job_uids or []) if str(u or "").strip()}
        with db_module.connect_db() as conn:
            db_module.apply_migrations(conn)
            for row in rows:
                if str(row.get("job_uid") or "").strip() in uid_set:
                    db_module.upsert_job(conn, row)
            conn.commit()
    except Exception as exc:
        if mode == "dual":
            logger.error("SQL recalc sync failed in dual mode: %s", exc)
        else:
            raise

    return updated
