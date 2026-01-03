"""
Export SQL jobs to legacy CSV format.
"""
from __future__ import annotations

import csv
import os
from datetime import datetime, timezone
from typing import Optional, Tuple

from core import db as db_module
from core.config import DATA_DIR, HEADERS
from core.storage import _row_to_csv_dict


def _iso_to_epoch(value: object) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        raw = str(value).strip()
        if not raw:
            return None
        cleaned = raw.replace("Z", "+00:00")
        return datetime.fromisoformat(cleaned).timestamp()
    except Exception:
        return None


def _safe_float(value: object) -> object:
    try:
        if value is None or value == "":
            return ""
        return float(value)
    except Exception:
        return ""


def _safe_int(value: object) -> object:
    try:
        if value is None or value == "":
            return ""
        return int(float(value))
    except Exception:
        return ""


def export_csv_from_sql(*, out_path: str, overwrite: bool = False) -> Tuple[int, str]:
    """
    Export SQL jobs to a CSV file using the legacy HEADERS order.

    Returns (row_count, out_path).
    """
    os.makedirs(DATA_DIR, exist_ok=True)
    out_path = str(out_path)

    if os.path.exists(out_path) and not overwrite:
        raise FileExistsError(f"CSV already exists: {out_path} (use --overwrite to replace)")

    conn = db_module.connect_db()
    db_module.apply_migrations(conn)

    rows = conn.execute(
        """
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
            j.created_at
        FROM jobs j
        JOIN printers p ON j.printer_id = p.id
        ORDER BY
            COALESCE(j.started_at, j.ended_at, j.created_at) ASC,
            p.name ASC,
            j.filename ASC,
            j.job_uid ASC
        """
    ).fetchall()

    out_rows = []
    for row in rows:
        record = dict(row)
        ts_epoch = (
            _iso_to_epoch(record.get("ended_at"))
            or _iso_to_epoch(record.get("started_at"))
            or _iso_to_epoch(record.get("created_at"))
        )
        if ts_epoch is None:
            ts_epoch = 0

        csv_row = {
            "timestamp": ts_epoch,
            "job_uid": record.get("job_uid") or "",
            "printer": record.get("printer") or "",
            "filename": record.get("filename") or "",
            "thumbnail": record.get("thumbnail") or "",
            "duration_seconds": _safe_int(record.get("duration_seconds")),
            "paused_seconds_total": _safe_float(record.get("paused_seconds_total")),
            "pause_count": _safe_int(record.get("pause_count")),
            "runout_count": _safe_int(record.get("runout_count")),
            "duration_hours": _safe_float(record.get("duration_hours")),
            "filament_mm": _safe_float(record.get("filament_mm")),
            "filament_meters": _safe_float(record.get("filament_meters")),
            "rate_per_hour": _safe_float(record.get("rate_per_hour")),
            "filament_mode": record.get("filament_mode") or "",
            "filament_rate": _safe_float(record.get("filament_rate")),
            "grams_per_meter": _safe_float(record.get("grams_per_meter")),
            "time_cost": _safe_float(record.get("time_cost")),
            "material_cost": _safe_float(record.get("material_cost")),
            "total_cost": _safe_float(record.get("total_cost")),
            "filament_profile_id": record.get("filament_profile_id") or "",
            "filament_material": record.get("filament_material") or "",
            "status": record.get("status") or "",
            "failure_reason": record.get("failure_reason") or "",
            "import_source": record.get("import_source") or "",
            "import_id": record.get("import_id") or "",
            "job_outcome": record.get("job_outcome") or "",
            "duration_seconds_raw": _safe_float(record.get("duration_seconds_raw")),
            "duration_seconds_est": _safe_float(record.get("duration_seconds_est")),
            "duration_seconds_effective": _safe_float(record.get("duration_seconds_effective")),
            "filament_mm_raw": _safe_float(record.get("filament_mm_raw")),
            "filament_mm_est": _safe_float(record.get("filament_mm_est")),
            "filament_mm_effective": _safe_float(record.get("filament_mm_effective")),
        }

        out_rows.append(csv_row)

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=HEADERS)
        writer.writeheader()
        for row in out_rows:
            writer.writerow(_row_to_csv_dict(row, HEADERS))

    return len(out_rows), out_path
