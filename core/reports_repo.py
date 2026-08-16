"""
Reports read repository (CSV or SQLite).
"""
from __future__ import annotations

import logging
import os
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from core import db as db_module
from core import reports_cache
from core import profiles
from core.config import CSV_FILE
from core.sql_only import is_sql_only
from core.reports import (
    aggregate_by_material,
    aggregate_by_profile,
    compute_monthly_breakdown,
    compute_pause_analytics,
    compute_summary,
    compute_top_printers,
    get_date_range_from_params,
)
from core.storage import load_rows_raw, ts_to_local_dt

logger = logging.getLogger(__name__)
_AUTO_WARNED = False


@dataclass(frozen=True)
class ReportsRange:
    start_dt: Optional[datetime]
    end_dt: Optional[datetime]
    range_label: str
    quick_range: str


def _reports_backend() -> Tuple[str, Optional[str]]:
    if is_sql_only():
        return "sql", None
    # CSV remains the default compatibility read path outside strict SQL-only mode.
    raw_mode = str(os.getenv("KCD_REPORTS_BACKEND", "csv")).strip().lower()
    mode = raw_mode
    if raw_mode == "auto":
        mode = "sql"
    if mode == "sql":
        try:
            with closing(db_module.connect_db()) as conn:
                version = db_module.current_schema_version(conn)
                if not version:
                    return "csv", "SQL reports backend not initialized; falling back to CSV."
                if raw_mode == "auto":
                    _warn_if_sql_ahead_of_csv(conn)
                return "sql", None
        except Exception:
            return "csv", "SQL reports backend not available; falling back to CSV."
    return "csv", None


def get_reports_data(args: Any, backend_override: Optional[str] = None) -> Dict[str, Any]:
    start_dt, end_dt, range_label, quick_range = get_date_range_from_params(args)
    return get_reports_data_range(
        start_dt=start_dt,
        end_dt=end_dt,
        range_label=range_label,
        quick_range=quick_range,
        backend_override=backend_override,
        args=args,
    )


def get_reports_data_range(
    *,
    start_dt: Optional[datetime],
    end_dt: Optional[datetime],
    range_label: str,
    quick_range: str,
    backend_override: Optional[str] = None,
    args: Any = None,
) -> Dict[str, Any]:
    if backend_override:
        backend = backend_override
        backend_error = None
    else:
        backend, backend_error = _reports_backend()

    if backend == "sql":
        data = _reports_from_sql(start_dt, end_dt)
        data["backend"] = "sql"
        data["error"] = backend_error
    else:
        data = _reports_from_csv(start_dt, end_dt)
        data["backend"] = "csv"
        data["error"] = data.get("error") or backend_error

    data["range_label"] = range_label
    data["quick_range"] = quick_range
    data["start_date"] = start_dt.strftime("%Y-%m-%d") if start_dt else ""
    data["end_date"] = end_dt.strftime("%Y-%m-%d") if end_dt else ""
    data["args"] = args
    return data


def _reports_from_csv(start_dt: Optional[datetime], end_dt: Optional[datetime]) -> Dict[str, Any]:
    rows, error = load_rows_raw(CSV_FILE)

    if start_dt or end_dt:
        filtered = []
        for r in rows:
            ts_raw = r.get("timestamp_raw")
            if not ts_raw:
                continue
            try:
                row_dt = ts_to_local_dt(float(ts_raw))
                if start_dt and row_dt < start_dt:
                    continue
                if end_dt and row_dt > end_dt:
                    continue
                filtered.append(r)
            except Exception:
                continue
        rows = filtered

    monthly = compute_monthly_breakdown(rows)
    top_printers = compute_top_printers(rows, limit=5)
    summary = compute_summary(rows) or {}
    summary.setdefault("total_prints", 0)
    summary.setdefault("total_hours", 0.0)
    summary.setdefault("total_meters", 0.0)
    summary.setdefault("total_cost", 0.0)
    summary.setdefault("per_day", {})
    summary.setdefault("per_printer", {})

    pause_analytics = compute_pause_analytics(rows)
    material_summary = aggregate_by_material(rows)
    all_profiles = profiles.get_all_profiles()
    profile_summary = aggregate_by_profile(rows, all_profiles)

    return {
        "monthly_breakdown": monthly,
        "top_printers": top_printers,
        "summary": summary,
        "pause_analytics": pause_analytics,
        "material_summary": material_summary,
        "profile_summary": profile_summary,
        "error": error,
    }


def _reports_from_sql(start_dt: Optional[datetime], end_dt: Optional[datetime]) -> Dict[str, Any]:
    with closing(db_module.connect_db()) as conn:
        db_module.apply_migrations(conn)

        where_sql, params = _build_date_filter(start_dt, end_dt)

        cache_ttl = _cache_ttl_seconds()
        range_key = _range_key(start_dt, end_dt)
        fingerprint = _reports_jobs_fingerprint(conn, where_sql, params)
        cached = reports_cache.get_cached_payload(
            conn,
            key="reports:full",
            range_key=range_key,
            fingerprint=fingerprint,
            ttl_seconds=cache_ttl,
        )
        if cached is not None:
            return cached

        summary = _sql_summary(conn, where_sql, params)
        monthly = _sql_monthly_breakdown(conn, where_sql, params)
        top_printers = _sql_top_printers(conn, where_sql, params)
        pause_analytics = _sql_pause_analytics(conn, where_sql, params, summary.get("total_prints", 0))
        material_summary = _sql_material_summary(conn, where_sql, params)
        profile_summary = _sql_profile_summary(conn, where_sql, params)

        payload = {
            "monthly_breakdown": monthly,
            "top_printers": top_printers,
            "summary": summary,
            "pause_analytics": pause_analytics,
            "material_summary": material_summary,
            "profile_summary": profile_summary,
        }

        if cache_ttl > 0 and fingerprint:
            try:
                reports_cache.set_cached_payload(
                    conn,
                    key="reports:full",
                    range_key=range_key,
                    fingerprint=fingerprint,
                    payload=payload,
                )
                conn.commit()
            except Exception:
                pass

        return payload


def _build_date_filter(start_dt: Optional[datetime], end_dt: Optional[datetime]) -> Tuple[str, list]:
    where = []
    params: list = []

    ts_expr = "CAST(strftime('%s', COALESCE(j.ended_at, j.created_at)) AS INTEGER)"
    start_epoch = None
    end_epoch = None
    if start_dt:
        try:
            start_epoch = int(start_dt.timestamp())
        except Exception:
            start_epoch = None
    if end_dt:
        try:
            end_epoch = int(end_dt.timestamp())
        except Exception:
            end_epoch = None

    if start_epoch is not None:
        where.append(f"{ts_expr} >= ?")
        params.append(start_epoch)
    if end_epoch is not None:
        where.append(f"{ts_expr} <= ?")
        params.append(end_epoch)

    where_sql = ""
    if where:
        where_sql = "WHERE " + " AND ".join(where)
    return where_sql, params


def _range_key(start_dt: Optional[datetime], end_dt: Optional[datetime]) -> str:
    if not start_dt and not end_dt:
        return "all"
    start = start_dt.isoformat() if start_dt else ""
    end = end_dt.isoformat() if end_dt else ""
    return f"{start}..{end}"


def _cache_ttl_seconds() -> int:
    raw = os.getenv("KCD_REPORTS_CACHE_TTL_SECONDS", "300")
    try:
        return max(0, int(raw))
    except Exception:
        return 300


def _reports_jobs_fingerprint(conn, where_sql: str, params: list) -> str:
    ts_expr = "CAST(strftime('%s', COALESCE(j.ended_at, j.created_at)) AS INTEGER)"
    row = conn.execute(
        f"""
        SELECT
            COALESCE(MAX({ts_expr}), 0) AS max_ts,
            COUNT(*) AS row_count,
            COALESCE(MAX(rowid), 0) AS max_rowid
        FROM jobs j
        {where_sql}
        """,
        params,
    ).fetchone()
    if not row:
        return "0:0:0"
    return f"{row['max_ts']}:{row['row_count']}:{row['max_rowid']}"


def _warn_if_sql_ahead_of_csv(conn) -> None:
    global _AUTO_WARNED
    if _AUTO_WARNED:
        return
    try:
        row = conn.execute(
            "SELECT COALESCE(MAX(CAST(strftime('%s', COALESCE(ended_at, created_at)) AS INTEGER)), 0) AS max_ts FROM jobs"
        ).fetchone()
        sql_max = float(row["max_ts"] or 0.0) if row else 0.0
    except Exception:
        return

    try:
        from core.config import CSV_FILE
        from core.storage import load_rows_raw

        rows, _err = load_rows_raw(CSV_FILE)
        csv_max = 0.0
        for r in rows:
            try:
                ts = float(r.get("timestamp_raw") or 0.0)
            except Exception:
                ts = 0.0
            if ts > csv_max:
                csv_max = ts
    except Exception:
        return

    if sql_max > csv_max and csv_max > 0:
        logger.warning(
            "CSV appears behind SQL; run python -m kcd export csv --from sql --overwrite to sync."
        )
        _AUTO_WARNED = True


def _sql_summary(conn, where_sql: str, params: list) -> Dict[str, Any]:
    row = conn.execute(
        f"""
        SELECT
            COUNT(*) AS total_prints,
            COALESCE(SUM(j.duration_hours), 0) AS total_hours,
            COALESCE(SUM(j.filament_meters), 0) AS total_meters,
            COALESCE(SUM(j.total_cost), 0) AS total_cost
        FROM jobs j
        {where_sql}
        """,
        params,
    ).fetchone()
    summary = {
        "total_prints": int(row["total_prints"]) if row else 0,
        "total_hours": float(row["total_hours"]) if row and row["total_hours"] is not None else 0.0,
        "total_meters": float(row["total_meters"]) if row and row["total_meters"] is not None else 0.0,
        "total_cost": float(row["total_cost"]) if row and row["total_cost"] is not None else 0.0,
        "per_day": {},
        "per_printer": {},
    }
    return summary


def _sql_monthly_breakdown(conn, where_sql: str, params: list) -> list:
    rows = conn.execute(
        f"""
        SELECT
            strftime('%Y-%m', COALESCE(j.ended_at, j.created_at), 'localtime') AS label,
            COUNT(*) AS count,
            COALESCE(SUM(j.duration_hours), 0) AS hours,
            COALESCE(SUM(j.total_cost), 0) AS cost
        FROM jobs j
        {where_sql}
        GROUP BY label
        ORDER BY label DESC
        """,
        params,
    ).fetchall()
    return [
        {
            "label": row["label"],
            "count": int(row["count"]),
            "hours": float(row["hours"] or 0.0),
            "cost": float(row["cost"] or 0.0),
        }
        for row in rows
        if row["label"]
    ]


def _sql_top_printers(conn, where_sql: str, params: list) -> list:
    rows = conn.execute(
        f"""
        SELECT
            p.name AS name,
            COUNT(*) AS count,
            COALESCE(SUM(j.total_cost), 0) AS cost
        FROM jobs j
        JOIN printers p ON j.printer_id = p.id
        {where_sql}
        GROUP BY p.name
        ORDER BY cost DESC
        LIMIT 5
        """,
        params,
    ).fetchall()
    return [
        {"name": row["name"], "count": int(row["count"]), "cost": float(row["cost"] or 0.0)}
        for row in rows
    ]


def _sql_pause_analytics(conn, where_sql: str, params: list, total_prints: int) -> Dict[str, Any]:
    row = conn.execute(
        f"""
        SELECT
            COALESCE(SUM(j.paused_seconds_total), 0) AS total_paused
        FROM jobs j
        {where_sql}
        """,
        params,
    ).fetchone()
    total_paused = float(row["total_paused"] or 0.0) if row else 0.0
    avg_paused = (total_paused / total_prints) if total_prints else 0.0

    runout_rows = conn.execute(
        f"""
        SELECT
            p.name AS printer,
            COALESCE(SUM(j.runout_count), 0) AS runouts
        FROM jobs j
        JOIN printers p ON j.printer_id = p.id
        {where_sql}
        AND COALESCE(j.runout_count, 0) > 0
        GROUP BY p.name
        ORDER BY runouts DESC
        """,
        params,
    ).fetchall()
    runouts_by_printer = {
        str(r["printer"]): int(r["runouts"])
        for r in runout_rows
        if r["printer"] and int(r["runouts"] or 0) > 0
    }

    return {
        "total_paused_seconds": total_paused,
        "average_paused_seconds": avg_paused,
        "runouts_by_printer": runouts_by_printer,
    }


def _sql_material_summary(conn, where_sql: str, params: list) -> list:
    rows = conn.execute(
        f"""
        SELECT
            CASE
                WHEN j.filament_material IS NULL OR j.filament_material = '' THEN 'Unknown'
                ELSE j.filament_material
            END AS material,
            COUNT(*) AS count,
            COALESCE(SUM(j.duration_hours), 0) AS hours,
            COALESCE(SUM(j.total_cost), 0) AS cost
        FROM jobs j
        {where_sql}
        GROUP BY material
        ORDER BY cost DESC
        """,
        params,
    ).fetchall()
    return [
        {
            "material": row["material"],
            "count": int(row["count"]),
            "hours": float(row["hours"] or 0.0),
            "cost": float(row["cost"] or 0.0),
        }
        for row in rows
    ]


def _sql_profile_summary(conn, where_sql: str, params: list) -> list:
    rows = conn.execute(
        f"""
        SELECT
            COALESCE(NULLIF(j.filament_profile_id, ''), 'none') AS profile_id,
            COUNT(*) AS count,
            COALESCE(SUM(j.duration_hours), 0) AS hours,
            COALESCE(SUM(j.total_cost), 0) AS cost
        FROM jobs j
        {where_sql}
        GROUP BY profile_id
        ORDER BY cost DESC
        """,
        params,
    ).fetchall()

    profiles_dict = profiles.get_all_profiles()
    result = []
    for row in rows:
        profile_id = row["profile_id"]
        if profile_id == "none":
            profile_name = "No Profile (Defaults)"
        else:
            profile = profiles_dict.get(profile_id, {})
            profile_name = profile.get("name", f"Unknown ({str(profile_id)[:8]})")
        result.append(
            {
                "profile_id": profile_id,
                "profile_name": profile_name,
                "count": int(row["count"]),
                "hours": float(row["hours"] or 0.0),
                "cost": float(row["cost"] or 0.0),
            }
        )
    return result
