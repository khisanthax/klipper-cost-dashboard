"""
Backfill SQLite job rows from CSV history or Moonraker history.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

from core import db as db_module
from core import pricing
from core.config import CSV_FILE, DATA_DIR, HEADERS
from core.moonraker import fetch_moonraker_history
from core.storage import load_rows_raw, rewrite_csv_all_rows
from core.thumbnails import resolve_moonraker_base_url

logger = logging.getLogger(__name__)

BACKFILL_REPORT_PATH = os.path.join(DATA_DIR, "backfill_report.json")


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


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def _as_float(value: object) -> float:
    try:
        if value is None or (isinstance(value, str) and not value.strip()):
            return 0.0
        return float(value)
    except Exception:
        return 0.0


def _normalize_filename(value: str) -> str:
    name = str(value or "").strip().replace("\\", "/").lstrip("/")
    if name.lower().startswith("gcodes/"):
        name = name[len("gcodes/") :]
    return name


def _pick_history_match(jobs, filename: str, target_ts: float, window_seconds: float) -> Optional[dict]:
    if not jobs or not filename or not target_ts:
        return None

    target_name = _normalize_filename(filename)
    best = None
    best_delta = None
    for job in jobs:
        job_name = str(job.get("filename") or job.get("name") or job.get("file") or "").strip()
        if not job_name:
            continue
        if _normalize_filename(job_name) != target_name:
            continue
        end_ts = _as_float(job.get("end_time") or job.get("timestamp") or 0.0)
        if end_ts <= 0:
            continue
        delta = abs(end_ts - target_ts)
        if delta > window_seconds:
            continue
        if best_delta is None or delta < best_delta:
            best_delta = delta
            best = job
    return best


def _compute_costs_for_row(row: dict) -> dict:
    printer_name = str(row.get("printer") or "").strip()
    duration_seconds = _as_float(row.get("duration_seconds"))
    filament_mm = _as_float(row.get("filament_mm"))
    paused_seconds_total = _as_float(row.get("paused_seconds_total"))

    filament_profile_id = str(row.get("filament_profile_id") or "").strip() or None
    rate_profile_id = str(row.get("hourly_rate_profile_id") or "").strip() or None

    rate_override = _as_float(row.get("override_rate_per_hour")) or None
    material_override = _as_float(row.get("override_material_cost")) or None
    total_override = _as_float(row.get("override_total_cost")) or None

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

    return cost_data


def _write_report(report: Dict[str, object]) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(BACKFILL_REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)


def _run_backfill_csv() -> Dict[str, int]:
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


def _run_backfill_moonraker() -> Dict[str, object]:
    report: Dict[str, object] = {
        "started_at": _utc_now_iso(),
        "finished_at": None,
        "source": "moonraker",
        "targets": 0,
        "updated": 0,
        "skipped": 0,
        "missing_history": [],
        "missing_url": [],
        "updated_job_uids": [],
    }

    rows_csv, _err = load_rows_raw(CSV_FILE)
    csv_by_uid = {str(r.get("job_uid") or "").strip(): r for r in rows_csv if str(r.get("job_uid") or "").strip()}
    csv_updates = False

    with db_module.connect_db() as conn:
        db_module.apply_migrations(conn)
        target_rows = list(
            conn.execute(
                """
                SELECT j.job_uid, j.filename, j.duration_seconds, j.filament_mm,
                       j.paused_seconds_total, j.status, j.started_at, j.ended_at,
                       j.override_rate_per_hour, j.override_material_cost, j.override_total_cost,
                       j.hourly_rate_profile_id, j.filament_profile_id, p.name AS printer
                  FROM jobs j
                  JOIN printers p ON j.printer_id = p.id
                 WHERE j.status = 'completed' AND COALESCE(j.duration_seconds, 0) <= 0
                """
            )
        )

        report["targets"] = len(target_rows)
        jobs_by_printer: Dict[str, list] = {}
        for row in target_rows:
            jobs_by_printer.setdefault(str(row["printer"]), []).append(dict(row))

        for printer, jobs in jobs_by_printer.items():
            base_url = resolve_moonraker_base_url(printer)
            if not base_url:
                report["missing_url"].append({"printer": printer})
                continue

            ok, detail, history_jobs = fetch_moonraker_history(base_url, limit=200)
            if not ok:
                report["missing_history"].append({"printer": printer, "error": detail})
                continue

            for job_row in jobs:
                job_uid = str(job_row.get("job_uid") or "").strip()
                filename = str(job_row.get("filename") or "").strip()
                end_ts = _parse_iso_to_epoch(job_row.get("ended_at")) or _parse_iso_to_epoch(job_row.get("started_at")) or 0.0
                match = _pick_history_match(history_jobs, filename, end_ts, window_seconds=1800.0)
                if not match:
                    report["missing_history"].append({"job_uid": job_uid, "printer": printer, "filename": filename})
                    report["skipped"] = int(report["skipped"]) + 1
                    continue

                print_time = _as_float(match.get("print_time") or match.get("print_duration"))
                total_duration = _as_float(match.get("total_duration") or match.get("elapsed") or match.get("duration"))
                filament_used = _as_float(match.get("filament_used") or match.get("filament") or match.get("filament_mm"))
                start_ts = _as_float(match.get("start_time") or match.get("timestamp"))
                end_ts_match = _as_float(match.get("end_time") or match.get("timestamp"))

                duration_seconds = print_time if print_time > 0 else total_duration
                if duration_seconds <= 0:
                    report["skipped"] = int(report["skipped"]) + 1
                    continue

                updated_row = dict(job_row)
                updated_row["duration_seconds"] = duration_seconds
                if filament_used > 0:
                    updated_row["filament_mm"] = filament_used
                if start_ts > 0:
                    updated_row["started_at"] = datetime.fromtimestamp(start_ts, tz=timezone.utc).isoformat()
                if end_ts_match > 0:
                    updated_row["ended_at"] = datetime.fromtimestamp(end_ts_match, tz=timezone.utc).isoformat()

                updated_row.update(_compute_costs_for_row(updated_row))

                try:
                    db_module.upsert_job(conn, updated_row)
                    report["updated"] = int(report["updated"]) + 1
                    report["updated_job_uids"].append(job_uid)
                except Exception as exc:
                    logger.warning("Moonraker backfill failed for %s: %s", job_uid, exc)
                    report["skipped"] = int(report["skipped"]) + 1
                    continue

                # Update CSV row if present.
                csv_row = csv_by_uid.get(job_uid)
                if csv_row is not None:
                    csv_row.update({
                        "duration_seconds": updated_row.get("duration_seconds"),
                        "filament_mm": updated_row.get("filament_mm"),
                        "started_at": updated_row.get("started_at"),
                        "ended_at": updated_row.get("ended_at"),
                    })
                    csv_row.update(_compute_costs_for_row(csv_row))
                    csv_updates = True

        conn.commit()

    if csv_updates and rows_csv:
        rewrite_csv_all_rows(CSV_FILE, HEADERS, rows_csv)

    report["finished_at"] = _utc_now_iso()
    _write_report(report)
    return report


def run_backfill(*, source: str = "csv") -> Dict[str, object]:
    source = str(source or "csv").strip().lower()
    if source == "moonraker":
        report = _run_backfill_moonraker()
        # Fallback to CSV if Moonraker isn't available at all.
        if report.get("targets") and not report.get("updated") and report.get("missing_url"):
            csv_report = _run_backfill_csv()
            report["fallback"] = "csv"
            report["csv_rows_upserted"] = csv_report.get("rows_upserted", 0)
        return report
    return _run_backfill_csv()
