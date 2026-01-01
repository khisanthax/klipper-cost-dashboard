"""
History read repository (CSV or SQLite).
"""
from __future__ import annotations

import os
import sqlite3
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Tuple

from core import db as db_module
from core import pricing
from core.config import CSV_FILE, HEADERS, TIMEZONE_OBJ
from core.storage import load_rows_raw, ts_to_local_dt

logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class HistoryQuery:
    printer: Optional[str] = None
    start_dt: Optional[datetime] = None
    end_dt: Optional[datetime] = None
    end_exclusive: bool = False
    paused_min: int = 0
    has_runout: bool = False


@dataclass(frozen=True)
class HistoryResult:
    rows_page: List[dict]
    rows_all: List[dict]
    total: int
    pager: dict
    backend: str
    error: Optional[str] = None


def _history_sort_key(row: dict) -> float:
    try:
        ts_raw = row.get("timestamp_raw")
        if ts_raw is not None and ts_raw != "":
            return float(ts_raw)
    except Exception:
        pass

    try:
        ts_epoch = row.get("timestamp_epoch")
        if ts_epoch is not None and str(ts_epoch).strip() != "":
            return float(ts_epoch)
    except Exception:
        pass

    try:
        ts_text = str(row.get("timestamp") or "").strip().replace("\n", " ").strip()
        if not ts_text:
            return float("-inf")
        dt = datetime.strptime(ts_text[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=TIMEZONE_OBJ)
        return float(dt.timestamp())
    except Exception:
        return float("-inf")


def _sort_history_rows(rows: List[dict]) -> List[dict]:
    return sorted(rows, key=_history_sort_key, reverse=True)


def _pager_meta(total: int, page: int, per_page: int) -> dict:
    pages = max(1, int((total + per_page - 1) / float(per_page))) if per_page else 1
    try:
        page = int(page)
    except Exception:
        page = 1
    page = max(1, min(pages, page))
    return {
        "page": page,
        "per_page": per_page,
        "total": total,
        "pages": pages,
        "has_prev": page > 1,
        "has_next": page < pages,
    }


def _read_backend() -> Tuple[str, Optional[str]]:
    mode = str(os.getenv("KCD_READ_BACKEND", "csv")).strip().lower()
    if mode == "auto":
        mode = "sql"
    if mode == "sql":
        try:
            conn = db_module.connect_db()
            version = db_module.current_schema_version(conn)
            if not version:
                return "csv", "SQL backend not initialized; falling back to CSV."
            return "sql", None
        except Exception:
            return "csv", "SQL backend not available; falling back to CSV."
    return "csv", None


def list_history_rows(query: HistoryQuery, page: int, per_page: int) -> HistoryResult:
    backend, error = _read_backend()
    if backend == "sql":
        return list_history_rows_sql(query, page, per_page, error)
    return list_history_rows_csv(query, page, per_page, error)


def list_history_rows_csv(query: HistoryQuery, page: int, per_page: int, error: Optional[str]) -> HistoryResult:
    rows, _err = load_rows_raw(CSV_FILE)

    if query.start_dt or query.end_dt:
        filtered = []
        for r in rows:
            ts_raw = r.get("timestamp_raw")
            if not ts_raw:
                continue
            try:
                row_dt = ts_to_local_dt(float(ts_raw))
                if query.start_dt and row_dt < query.start_dt:
                    continue
                if query.end_dt:
                    if query.end_exclusive and row_dt >= query.end_dt:
                        continue
                    if (not query.end_exclusive) and row_dt > query.end_dt:
                        continue
                filtered.append(r)
            except Exception:
                continue
        rows = filtered

    if query.printer:
        rows = [r for r in rows if (r.get("printer") or "") == query.printer]

    if query.paused_min > 0:
        threshold = float(query.paused_min) * 60.0
        filtered = []
        for r in rows:
            try:
                paused_s = float(r.get("paused_seconds_total") or 0.0)
            except (TypeError, ValueError):
                paused_s = 0.0
            if paused_s >= threshold:
                filtered.append(r)
        rows = filtered

    if query.has_runout:
        filtered = []
        for r in rows:
            try:
                rc = int(float(r.get("runout_count") or 0))
            except Exception:
                rc = 0
            if rc > 0:
                filtered.append(r)
        rows = filtered

    # Ensure any missing derived cost fields are computed consistently.
    for row in rows:
        compute_job_cost_fields(row)
    rows = _sort_history_rows(rows)
    total = len(rows)
    pager = _pager_meta(total, page, per_page)
    start = (pager["page"] - 1) * per_page
    end = start + per_page
    rows_page = rows[start:end]
    return HistoryResult(rows_page=rows_page, rows_all=rows, total=total, pager=pager, backend="csv", error=error)


def list_history_rows_sql(query: HistoryQuery, page: int, per_page: int, error: Optional[str]) -> HistoryResult:
    conn = db_module.connect_db()
    db_module.apply_migrations(conn)

    where = []
    params: list = []

    if query.printer:
        where.append("p.name = ?")
        params.append(query.printer)

    if query.paused_min > 0:
        where.append("COALESCE(j.paused_seconds_total, 0) >= ?")
        params.append(float(query.paused_min) * 60.0)

    if query.has_runout:
        where.append("COALESCE(j.runout_count, 0) > 0")

    start_epoch = None
    end_epoch = None
    if query.start_dt:
        try:
            start_epoch = int(query.start_dt.timestamp())
        except Exception:
            start_epoch = None
    if query.end_dt:
        try:
            end_epoch = int(query.end_dt.timestamp())
        except Exception:
            end_epoch = None

    ts_expr = "CAST(strftime('%s', COALESCE(j.ended_at, j.created_at)) AS INTEGER)"
    if start_epoch is not None:
        where.append(f"{ts_expr} >= ?")
        params.append(start_epoch)
    if end_epoch is not None:
        op = "<" if query.end_exclusive else "<="
        where.append(f"{ts_expr} {op} ?")
        params.append(end_epoch)

    where_sql = " AND ".join(where)
    if where_sql:
        where_sql = "WHERE " + where_sql

    total_row = conn.execute(
        f"""
        SELECT COUNT(*)
          FROM jobs j
          JOIN printers p ON j.printer_id = p.id
          {where_sql}
        """,
        params,
    ).fetchone()
    total = int(total_row[0]) if total_row else 0

    pager = _pager_meta(total, page, per_page)
    limit = pager["per_page"]
    offset = (pager["page"] - 1) * pager["per_page"]

    rows_page = _fetch_sql_rows(conn, where_sql, params, limit=limit, offset=offset)
    rows_all = _fetch_sql_rows(conn, where_sql, params, limit=None, offset=None)

    for idx, row in enumerate(rows_page[:3]):
        logger.debug(
            "history-sql row[%s] job_uid=%s duration_seconds=%s duration_hours=%s time_cost=%s",
            idx,
            row.get("job_uid"),
            row.get("duration_seconds"),
            row.get("duration_hours"),
            row.get("time_cost"),
        )

    return HistoryResult(
        rows_page=rows_page,
        rows_all=rows_all,
        total=total,
        pager=pager,
        backend="sql",
        error=error,
    )


def _fetch_sql_rows(conn: sqlite3.Connection, where_sql: str, params: list, limit: Optional[int], offset: Optional[int]) -> List[dict]:
    ts_expr = "CAST(strftime('%s', COALESCE(j.ended_at, j.created_at)) AS INTEGER)"
    sql = f"""
        SELECT
            j.job_uid,
            p.name AS printer,
            j.filename,
            j.thumbnail,
            j.duration_seconds,
            j.paused_seconds_total,
            j.pause_count,
            j.runout_count,
            j.duration_hours,
            j.filament_mm,
            j.filament_meters,
            j.rate_per_hour,
            j.filament_mode,
            j.filament_rate,
            j.grams_per_meter,
            j.time_cost,
            j.material_cost,
            j.total_cost,
            j.filament_profile_id,
            j.filament_material,
            j.status,
            j.failure_reason,
            j.import_source,
            j.import_id,
            j.job_outcome,
            j.duration_seconds_raw,
            j.duration_seconds_est,
            j.duration_seconds_effective,
            j.filament_mm_raw,
            j.filament_mm_est,
            j.filament_mm_effective,
            j.override_rate_per_hour,
            j.override_material_cost,
            j.override_total_cost,
            j.hourly_rate_profile_id,
            j.started_at,
            j.ended_at,
            j.created_at,
            {ts_expr} AS timestamp_epoch
        FROM jobs j
        JOIN printers p ON j.printer_id = p.id
        {where_sql}
        ORDER BY timestamp_epoch DESC
    """
    if limit is not None:
        sql += " LIMIT ?"
    if offset is not None and limit is not None:
        sql += " OFFSET ?"

    bind = list(params)
    if limit is not None:
        bind.append(int(limit))
        if offset is not None:
            bind.append(int(offset))

    rows = []
    for idx, row in enumerate(conn.execute(sql, bind)):
        record = dict(row)
        ts_epoch = record.get("timestamp_epoch")
        if ts_epoch is not None:
            try:
                ts_float = float(ts_epoch)
                record["timestamp_raw"] = ts_float
                record["timestamp"] = ts_to_local_dt(ts_float).strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                record["timestamp_raw"] = None
        else:
            record["timestamp_raw"] = None
        record["row_index"] = idx

        # Normalize missing fields to match CSV behavior.
        for key in (
            "filament_profile_id",
            "filament_material",
            "paused_seconds_total",
            "pause_count",
            "runout_count",
            "failure_reason",
            "import_source",
            "import_id",
            "job_outcome",
            "duration_seconds_raw",
            "duration_seconds_est",
            "duration_seconds_effective",
            "filament_mm_raw",
            "filament_mm_est",
            "filament_mm_effective",
        ):
            if record.get(key) is None:
                record[key] = "" if key not in ("paused_seconds_total", "pause_count", "runout_count") else 0

        # If duration_seconds is missing but we have both started_at and ended_at, derive it.
        if not _as_float(record.get("duration_seconds")):
            started_at = record.get("started_at")
            ended_at = record.get("ended_at")
            if started_at and ended_at:
                start_epoch = _parse_iso_to_epoch(started_at)
                end_epoch = _parse_iso_to_epoch(ended_at)
                if start_epoch is not None and end_epoch is not None:
                    record["duration_seconds"] = max(0.0, end_epoch - start_epoch)

        # Compute derived fields if missing/zero.
        compute_job_cost_fields(record)

        rows.append(record)
    return rows


def _as_float(value: object) -> Optional[float]:
    try:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return float(value)
    except Exception:
        return None


def _parse_iso_to_epoch(value: object) -> Optional[float]:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        cleaned = raw.replace("Z", "+00:00")
        return datetime.fromisoformat(cleaned).timestamp()
    except Exception:
        return None


def compute_job_cost_fields(row: dict) -> dict:
    """
    Compute missing/zero cost fields for a job row using the same pricing rules
    as CSV reads. This is used to fill gaps in SQL rows that lack derived fields.
    """
    printer_name = str(row.get("printer") or "").strip()
    if not printer_name:
        return row

    duration_seconds = _as_float(row.get("duration_seconds")) or 0.0
    filament_mm = _as_float(row.get("filament_mm")) or 0.0
    paused_seconds_total = _as_float(row.get("paused_seconds_total")) or 0.0

    # If duration_seconds is missing but duration_hours is present, derive it.
    if duration_seconds <= 0:
        dur_hours = _as_float(row.get("duration_hours"))
        if dur_hours and dur_hours > 0:
            duration_seconds = dur_hours * 3600.0

    # If there's still no signal, don't overwrite existing zeros.
    if duration_seconds <= 0 and filament_mm <= 0:
        return row

    filament_profile_id = str(row.get("filament_profile_id") or "").strip() or None
    rate_profile_id = str(row.get("hourly_rate_profile_id") or "").strip() or None

    rate_override = _as_float(row.get("override_rate_per_hour"))
    material_override = _as_float(row.get("override_material_cost"))
    total_override = _as_float(row.get("override_total_cost"))

    cost_data = pricing.compute_costs_with_overrides(
        printer_name,
        duration_seconds,
        filament_mm,
        paused_seconds_total=paused_seconds_total,
        filament_profile_id=filament_profile_id,
        rate_profile_id=rate_profile_id,
        rate_per_hour_override=rate_override,
    )

    if material_override is not None:
        cost_data["material_cost"] = material_override
        cost_data["total_cost"] = cost_data["time_cost"] + material_override
    if total_override is not None:
        cost_data["total_cost"] = total_override

    def _should_set(key: str, value: float) -> bool:
        existing = _as_float(row.get(key))
        if existing is None:
            return True
        if existing <= 0 and value > 0:
            return True
        return False

    for key in (
        "duration_hours",
        "filament_meters",
        "rate_per_hour",
        "filament_mode",
        "filament_rate",
        "grams_per_meter",
        "time_cost",
        "material_cost",
        "total_cost",
        "filament_profile_id",
        "filament_material",
    ):
        val = cost_data.get(key)
        if key in ("filament_mode", "filament_profile_id", "filament_material"):
            if row.get(key) in (None, "") and val:
                row[key] = val
            continue
        if isinstance(val, (int, float)) and _should_set(key, float(val)):
            row[key] = val

    return row
