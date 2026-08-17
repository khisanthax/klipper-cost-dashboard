"""
Storage backend abstraction for job writes.

CSV and dual mode remain supported compatibility runtime modes for this
release line. SQL-only mode is the strict/canonical path and must not write CSV.
"""
from __future__ import annotations

import logging
import os
from typing import Iterable

from core import db as db_module
from core.config import CSV_FILE, HEADERS
from core.pricing import compute_costs
from core.storage import append_row, load_rows_raw, rewrite_csv_recalculate_costs_job_uids

logger = logging.getLogger(__name__)


def _mode() -> str:
    return str(os.getenv("KCD_STORAGE_BACKEND", "csv")).strip().lower()


def _as_float(value: object) -> float:
    try:
        if value is None or (isinstance(value, str) and not value.strip()):
            return 0.0
        return float(value)
    except Exception:
        return 0.0


def _ensure_cost_fields(row: dict) -> dict:
    """
    Ensure derived cost fields are present before writing to SQL.

    This keeps SQL rows consistent with CSV behavior.
    """
    printer_name = str(row.get("printer") or "").strip()
    if not printer_name:
        return row

    duration_seconds = _as_float(row.get("duration_seconds"))
    if duration_seconds <= 0:
        dur_hours = _as_float(row.get("duration_hours"))
        if dur_hours > 0:
            duration_seconds = dur_hours * 3600.0
            row["duration_seconds"] = duration_seconds

    filament_mm = _as_float(row.get("filament_mm"))
    paused_seconds_total = _as_float(row.get("paused_seconds_total"))

    rate_per_hour = _as_float(row.get("rate_per_hour"))
    time_cost = _as_float(row.get("time_cost"))
    total_cost = _as_float(row.get("total_cost"))

    needs_calc = duration_seconds > 0 and (rate_per_hour <= 0 or time_cost <= 0 or total_cost <= 0)
    if not needs_calc:
        return row

    cost_data = compute_costs(printer_name, duration_seconds, filament_mm, paused_seconds_total=paused_seconds_total)

    # Respect explicit overrides if present.
    override_material = _as_float(row.get("override_material_cost"))
    override_total = _as_float(row.get("override_total_cost"))
    if override_material > 0:
        cost_data["material_cost"] = override_material
        cost_data["total_cost"] = cost_data.get("time_cost", 0.0) + override_material
    if override_total > 0:
        cost_data["total_cost"] = override_total

    for key, value in cost_data.items():
        if row.get(key) in (None, "", 0) and value not in (None, ""):
            row[key] = value

    return row


def write_job(row: dict) -> None:
    mode = _mode()
    row = _ensure_cost_fields(row)
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
    mode = _mode()
    uid_set = {str(u or "").strip() for u in (job_uids or []) if str(u or "").strip()}
    if not uid_set:
        return 0

    if mode == "sql":
        updated = 0
        try:
            with db_module.connect_db() as conn:
                db_module.apply_migrations(conn)
                rows = conn.execute(
                    "SELECT job_uid, printer_id, duration_seconds, filament_mm, paused_seconds_total "
                    "FROM jobs WHERE job_uid IN (%s)" % (",".join(["?"] * len(uid_set))),
                    list(uid_set),
                ).fetchall()
                for row in rows:
                    job_uid = row["job_uid"] if hasattr(row, "__getitem__") else row[0]
                    printer_id = row["printer_id"] if hasattr(row, "__getitem__") else row[1]
                    printer_row = conn.execute("SELECT name FROM printers WHERE id = ?", (printer_id,)).fetchone()
                    printer_name = printer_row["name"] if printer_row else ""
                    duration_seconds = float(row["duration_seconds"] or 0.0) if hasattr(row, "__getitem__") else float(row[2] or 0.0)
                    filament_mm = float(row["filament_mm"] or 0.0) if hasattr(row, "__getitem__") else float(row[3] or 0.0)
                    paused_seconds_total = float(row["paused_seconds_total"] or 0.0) if hasattr(row, "__getitem__") else float(row[4] or 0.0)

                    cost_data = compute_costs_fn(printer_name, duration_seconds, filament_mm, paused_seconds_total=paused_seconds_total)
                    conn.execute(
                        "UPDATE jobs SET "
                        "duration_hours = ?, filament_meters = ?, rate_per_hour = ?, filament_rate = ?, "
                        "grams_per_meter = ?, time_cost = ?, material_cost = ?, total_cost = ?, updated_at = ? "
                        "WHERE job_uid = ?",
                        (
                            cost_data.get("duration_hours"),
                            cost_data.get("filament_meters"),
                            cost_data.get("rate_per_hour"),
                            cost_data.get("filament_rate"),
                            cost_data.get("grams_per_meter"),
                            cost_data.get("time_cost"),
                            cost_data.get("material_cost"),
                            cost_data.get("total_cost"),
                            db_module._utc_now_iso(),
                            job_uid,
                        ),
                    )
                    updated += 1
                conn.commit()
        except Exception as exc:
            logger.error("SQL recalc failed: %s", exc)
            raise
        return updated

    # CSV or dual mode compatibility behavior.
    updated = rewrite_csv_recalculate_costs_job_uids(CSV_FILE, HEADERS, job_uids, compute_costs_fn)
    if updated <= 0:
        return updated

    if mode != "dual":
        return updated

    try:
        rows, _ = load_rows_raw(CSV_FILE)
        with db_module.connect_db() as conn:
            db_module.apply_migrations(conn)
            for row in rows:
                if str(row.get("job_uid") or "").strip() in uid_set:
                    db_module.upsert_job(conn, row)
            conn.commit()
    except Exception as exc:
        logger.error("SQL recalc sync failed in dual mode: %s", exc)

    return updated
