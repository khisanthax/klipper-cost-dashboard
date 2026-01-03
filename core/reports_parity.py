"""
Parity checks for reports aggregates (CSV vs SQL).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from core import db as db_module
from core.config import DATA_DIR
from core.export_csv import export_csv_from_sql
from core.reports import (
    aggregate_by_material,
    aggregate_by_profile,
    compute_monthly_breakdown,
    compute_pause_analytics,
    compute_summary,
    compute_top_printers,
)
from core.reports_repo import get_reports_data_range
from core.storage import load_rows_raw, ts_to_local_dt

REPORT_PATH = os.path.join(DATA_DIR, "reports_parity_report.json")
SQL_ONLY_PATH = os.path.join(DATA_DIR, "parity_sql_only.json")
CSV_ONLY_PATH = os.path.join(DATA_DIR, "parity_csv_only.json")
TEMP_CSV_PATH = os.path.join(DATA_DIR, "_tmp_print_costs_from_sql.csv")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_ts_epoch(value: object) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except Exception:
        pass
    try:
        raw = str(value).strip()
        if not raw:
            return None
        cleaned = raw.replace("Z", "+00:00").replace("T", " ")
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except Exception:
        return None


def _parse_range(range_str: str) -> int:
    raw = str(range_str or "").strip().lower()
    if not raw:
        return 30
    if raw.endswith("d"):
        try:
            return max(1, int(raw[:-1]))
        except Exception:
            return 30
    try:
        return max(1, int(raw))
    except Exception:
        return 30


def _now_local() -> datetime:
    return ts_to_local_dt(datetime.now().timestamp())


def _float(val: Any) -> float:
    try:
        if val is None:
            return 0.0
        return float(val)
    except Exception:
        return 0.0


def _diff(a: float, b: float, tol: float = 0.01) -> bool:
    return abs(a - b) > tol


def _index_by_key(rows: List[dict], key: str) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    for r in rows or []:
        k = str(r.get(key) or "").strip()
        if not k:
            continue
        out[k] = r
    return out


def run_parity(
    *,
    range_str: str = "30d",
    dump_job_diff: bool = False,
    regen_csv_from_sql: bool = False,
    overwrite_csv: bool = False,
) -> Dict[str, Any]:
    days = _parse_range(range_str)
    now = _now_local()
    start_dt = now - timedelta(days=days)

    csv_path = None
    if regen_csv_from_sql:
        if overwrite_csv:
            from core.config import CSV_FILE
            csv_path = CSV_FILE
            export_csv_from_sql(out_path=csv_path, overwrite=True)
        else:
            csv_path = TEMP_CSV_PATH
            export_csv_from_sql(out_path=csv_path, overwrite=True)

    csv_data = _reports_from_csv_path(
        csv_path=csv_path,
        start_dt=start_dt,
        end_dt=now,
    )
    sql_data = get_reports_data_range(
        start_dt=start_dt,
        end_dt=now,
        range_label=f"Last {days} days",
        quick_range=f"last{days}",
        backend_override="sql",
    )

    mismatches: List[dict] = []

    csv_summary = csv_data.get("summary", {})
    sql_summary = sql_data.get("summary", {})
    for key in ("total_prints", "total_hours", "total_meters", "total_cost"):
        csv_val = _float(csv_summary.get(key))
        sql_val = _float(sql_summary.get(key))
        if key == "total_prints":
            if int(csv_val) != int(sql_val):
                mismatches.append({"field": key, "csv": int(csv_val), "sql": int(sql_val)})
        elif _diff(csv_val, sql_val):
            mismatches.append({"field": key, "csv": csv_val, "sql": sql_val})

    csv_printers = _index_by_key(csv_data.get("top_printers", []), "name")
    sql_printers = _index_by_key(sql_data.get("top_printers", []), "name")
    for name, row in csv_printers.items():
        other = sql_printers.get(name)
        if not other:
            mismatches.append({"field": "printer_missing_sql", "printer": name})
            continue
        if int(row.get("count", 0) or 0) != int(other.get("count", 0) or 0):
            mismatches.append(
                {
                    "field": "printer_count",
                    "printer": name,
                    "csv": int(row.get("count", 0) or 0),
                    "sql": int(other.get("count", 0) or 0),
                }
            )
        if _diff(_float(row.get("cost")), _float(other.get("cost"))):
            mismatches.append(
                {
                    "field": "printer_cost",
                    "printer": name,
                    "csv": _float(row.get("cost")),
                    "sql": _float(other.get("cost")),
                }
            )

    csv_months = _index_by_key(csv_data.get("monthly_breakdown", []), "label")
    sql_months = _index_by_key(sql_data.get("monthly_breakdown", []), "label")
    for label, row in csv_months.items():
        other = sql_months.get(label)
        if not other:
            mismatches.append({"field": "month_missing_sql", "label": label})
            continue
        if int(row.get("count", 0) or 0) != int(other.get("count", 0) or 0):
            mismatches.append(
                {
                    "field": "month_count",
                    "label": label,
                    "csv": int(row.get("count", 0) or 0),
                    "sql": int(other.get("count", 0) or 0),
                }
            )
        if _diff(_float(row.get("cost")), _float(other.get("cost"))):
            mismatches.append(
                {
                    "field": "month_cost",
                    "label": label,
                    "csv": _float(row.get("cost")),
                    "sql": _float(other.get("cost")),
                }
            )

    report = {
        "range": range_str,
        "started_at": _utc_now_iso(),
        "finished_at": _utc_now_iso(),
        "csv_path": csv_path or "data/print_costs.csv",
        "regen_csv_from_sql": bool(regen_csv_from_sql),
        "csv_summary": csv_summary,
        "sql_summary": sql_summary,
        "mismatches": mismatches,
    }

    if dump_job_diff:
        sql_jobs = _load_sql_jobs(start_dt, now)
        csv_jobs = _load_csv_jobs(csv_path or None, start_dt, now)
        sql_keys, sql_only = _diff_job_sets(sql_jobs, csv_jobs)
        csv_keys, csv_only = _diff_job_sets(csv_jobs, sql_jobs)
        report["job_diff"] = {
            "sql_only_count": len(sql_only),
            "csv_only_count": len(csv_only),
        }
        _write_json(SQL_ONLY_PATH, sql_only)
        _write_json(CSV_ONLY_PATH, csv_only)

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    return report


def render_parity_summary(report: Dict[str, Any]) -> str:
    mismatches = report.get("mismatches", [])
    lines = [
        "Reports parity check (CSV vs SQL)",
        f"  Range: {report.get('range', '')}",
        f"  Mismatches: {len(mismatches)}",
        f"  Report: {REPORT_PATH}",
    ]
    if mismatches:
        lines.append("  Top mismatches:")
        for item in mismatches[:10]:
            lines.append(f"    - {item}")
    return "\n".join(lines)


def _reports_from_csv_path(csv_path: Optional[str], start_dt: Optional[datetime], end_dt: Optional[datetime]) -> Dict[str, Any]:
    if csv_path:
        rows, error = load_rows_raw(csv_path)
    else:
        from core.config import CSV_FILE
        rows, error = load_rows_raw(CSV_FILE)

    if start_dt or end_dt:
        filtered = []
        start_ts = int(start_dt.timestamp()) if start_dt else None
        end_ts = int(end_dt.timestamp()) if end_dt else None
        for r in rows:
            ts_epoch = _normalize_ts_epoch(r.get("timestamp_epoch")) or _normalize_ts_epoch(
                r.get("timestamp_raw")
            ) or _normalize_ts_epoch(r.get("timestamp"))
            if ts_epoch is None:
                continue
            if start_ts is not None and ts_epoch < start_ts:
                continue
            if end_ts is not None and ts_epoch > end_ts:
                continue
            filtered.append(r)
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
    try:
        from core import profiles as profiles_module
        profiles_dict = profiles_module.get_all_profiles()
    except Exception:
        profiles_dict = {}
    profile_summary = aggregate_by_profile(rows, profiles_dict)

    return {
        "monthly_breakdown": monthly,
        "top_printers": top_printers,
        "summary": summary,
        "pause_analytics": pause_analytics,
        "material_summary": material_summary,
        "profile_summary": profile_summary,
        "error": error,
    }


def _job_identity(row: dict) -> str:
    uid = str(row.get("job_uid") or "").strip()
    if uid:
        return f"uid:{uid}"
    printer = str(row.get("printer") or "").strip()
    filename = str(row.get("filename") or "").strip()
    ts = _normalize_ts_epoch(row.get("timestamp_epoch")) or _normalize_ts_epoch(
        row.get("timestamp_raw")
    ) or _normalize_ts_epoch(row.get("timestamp"))
    ts_val = str(ts or 0)
    return f"composite:{printer}|{filename}|{ts_val}"


def _load_csv_jobs(csv_path: Optional[str], start_dt: Optional[datetime], end_dt: Optional[datetime]) -> List[dict]:
    if csv_path:
        rows, _err = load_rows_raw(csv_path)
    else:
        from core.config import CSV_FILE
        rows, _err = load_rows_raw(CSV_FILE)

    if start_dt or end_dt:
        filtered = []
        start_ts = int(start_dt.timestamp()) if start_dt else None
        end_ts = int(end_dt.timestamp()) if end_dt else None
        for r in rows:
            ts_epoch = _normalize_ts_epoch(r.get("timestamp_epoch")) or _normalize_ts_epoch(
                r.get("timestamp_raw")
            ) or _normalize_ts_epoch(r.get("timestamp"))
            if ts_epoch is None:
                continue
            if start_ts is not None and ts_epoch < start_ts:
                continue
            if end_ts is not None and ts_epoch > end_ts:
                continue
            filtered.append(r)
        rows = filtered

    out = []
    for r in rows:
        ts_epoch = _normalize_ts_epoch(r.get("timestamp_epoch")) or _normalize_ts_epoch(
            r.get("timestamp_raw")
        ) or _normalize_ts_epoch(r.get("timestamp"))
        out.append(
            {
                "job_uid": r.get("job_uid"),
                "printer": r.get("printer"),
                "filename": r.get("filename"),
                "timestamp_epoch": ts_epoch,
                "status": r.get("status"),
                "duration_seconds": r.get("duration_seconds"),
                "filament_mm": r.get("filament_mm"),
                "total_cost": r.get("total_cost"),
            }
        )
    return out


def _load_sql_jobs(start_dt: Optional[datetime], end_dt: Optional[datetime]) -> List[dict]:
    conn = db_module.connect_db()
    db_module.apply_migrations(conn)

    where = []
    params: list = []
    ts_expr = "CAST(strftime('%s', COALESCE(j.ended_at, j.started_at, j.created_at)) AS INTEGER)"

    if start_dt:
        try:
            where.append(f"{ts_expr} >= ?")
            params.append(int(start_dt.timestamp()))
        except Exception:
            pass
    if end_dt:
        try:
            where.append(f"{ts_expr} <= ?")
            params.append(int(end_dt.timestamp()))
        except Exception:
            pass

    where_sql = ""
    if where:
        where_sql = "WHERE " + " AND ".join(where)

      rows = conn.execute(
        f"""
        SELECT
            j.job_uid,
            p.name AS printer,
            j.filename,
            j.status,
            j.duration_seconds,
            j.filament_mm,
            j.total_cost,
            {ts_expr} AS timestamp_epoch
        FROM jobs j
        JOIN printers p ON j.printer_id = p.id
        {where_sql}
          """,
          params,
      ).fetchall()
      out = []
      for r in rows:
          row = dict(r)
          row["timestamp_epoch"] = _normalize_ts_epoch(row.get("timestamp_epoch"))
          out.append(row)
      return out


def _diff_job_sets(primary: List[dict], other: List[dict]) -> Tuple[set, List[dict]]:
    primary_map = {_job_identity(r): r for r in primary}
    other_keys = {_job_identity(r) for r in other}
    only_keys = [k for k in primary_map.keys() if k not in other_keys]
    only_rows = []
    for key in only_keys:
        row = primary_map.get(key, {})
        only_rows.append(
            {
                "key": key,
                "job_uid": row.get("job_uid"),
                "printer": row.get("printer"),
                "filename": row.get("filename"),
                "timestamp_epoch": row.get("timestamp_epoch"),
                "status": row.get("status"),
                "duration_seconds": row.get("duration_seconds"),
                "filament_mm": row.get("filament_mm"),
                "total_cost": row.get("total_cost"),
            }
        )
    return set(primary_map.keys()), only_rows


def _write_json(path: str, payload: Any) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
