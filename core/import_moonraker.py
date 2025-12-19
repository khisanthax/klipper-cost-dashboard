"""
Import/Backfill helpers for bringing Moonraker job history into KCD CSV history.

Design goals:
- Idempotent: re-running should not create duplicates when skip_existing=True
- Safe: avoid CSV schema drift by relying on core.storage's schema alignment
- Conservative inference for cancelled/failed attempts (Phase 1 keeps it simple)
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any, Dict, Optional, Tuple

from core.moonraker import fetch_moonraker_history
from core.pricing import compute_costs
from core.storage import append_row, load_rows_raw, rewrite_csv_all_rows


IMPORT_SOURCE_MOONRAKER_HISTORY = "moonraker_history"


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def _as_int(value: Any) -> int:
    try:
        return int(float(value))
    except Exception:
        return 0


def _get_first(job: Dict[str, Any], keys: Tuple[str, ...]) -> Any:
    for k in keys:
        if k in job and job.get(k) is not None:
            return job.get(k)
    return None


def compute_import_id(printer_name: str, job: Dict[str, Any]) -> str:
    """
    Compute a stable import_id for a Moonraker history entry.

    Preferred: Moonraker-provided unique identifier (job_id/uid) + printer.
    Fallback: hash(printer|filename|timestamp).
    """
    printer_name = str(printer_name or "").strip()
    if not printer_name:
        return ""

    remote_id = _get_first(job, ("job_id", "uid", "id", "history_id"))
    if remote_id is not None and str(remote_id).strip() != "":
        return f"{printer_name}:{remote_id}"

    filename = str(_get_first(job, ("filename", "name", "file")) or "").strip()
    ts = _get_first(job, ("start_time", "end_time", "timestamp"))
    payload = [printer_name, filename, _as_int(ts)]
    digest = hashlib.sha1(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
    return f"{printer_name}:h{digest[:16]}"


def _infer_outcome(job: Dict[str, Any], print_time: float, estimated_time: float) -> str:
    if print_time > 0:
        return "completed"

    # Moonraker may include a status string; be conservative.
    status = str(_get_first(job, ("status", "result")) or "").strip().lower()
    if status in ("failed", "error"):
        return "failed"
    if status in ("cancelled", "canceled"):
        return "cancelled"

    if estimated_time > 0:
        return "cancelled"
    return "unknown"


def _build_row_from_history_job(printer_name: str, job: Dict[str, Any]) -> Dict[str, Any]:
    filename = str(_get_first(job, ("filename", "name", "file")) or "").strip()
    start_ts = _as_float(_get_first(job, ("start_time", "timestamp", "end_time")))
    if start_ts <= 0:
        start_ts = 0.0

    print_time = _as_float(_get_first(job, ("print_time", "print_duration", "total_duration")))
    estimated_time = _as_float(_get_first(job, ("estimated_time", "estimated_duration")))

    filament_raw = _as_float(_get_first(job, ("filament_used", "filament", "filament_mm")))
    filament_est = _as_float(_get_first(job, ("filament_total", "filament_est", "filament_est_mm")))

    outcome = _infer_outcome(job, print_time=print_time, estimated_time=estimated_time)

    if outcome == "completed":
        duration_effective = print_time
        filament_effective = filament_raw
    else:
        duration_effective = estimated_time * 0.15 if estimated_time > 0 else 0.0
        filament_effective = 0.0

    import_id = compute_import_id(printer_name, job)
    job_uid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{IMPORT_SOURCE_MOONRAKER_HISTORY}:{import_id}"))

    cost = compute_costs(printer_name, duration_effective, filament_effective)

    row: Dict[str, Any] = {
        "timestamp": str(_as_int(start_ts)),
        "job_uid": job_uid,
        "printer": printer_name,
        "filename": filename,
        "thumbnail": "",
        "duration_seconds": duration_effective,
        "filament_mm": filament_effective,
        "status": outcome,
        "failure_reason": "",
        "import_source": IMPORT_SOURCE_MOONRAKER_HISTORY,
        "import_id": import_id,
        "job_outcome": outcome,
        "duration_seconds_raw": print_time,
        "duration_seconds_est": estimated_time,
        "duration_seconds_effective": duration_effective,
        "filament_mm_raw": filament_raw,
        "filament_mm_est": filament_est,
        "filament_mm_effective": filament_effective,
    }
    row.update(cost)
    return row


def import_moonraker_history_to_csv(
    *,
    csv_file: str,
    headers: list[str],
    printer_name: str,
    base_url: str,
    limit: Optional[int] = 200,
    skip_existing: bool = True,
    overwrite_existing: bool = False,
) -> Dict[str, Any]:
    """
    Import Moonraker job history rows into KCD's CSV history.

    Returns a summary dict with counts: imported, skipped, updated, errors.
    """
    printer_name = str(printer_name or "").strip()
    base_url = str(base_url or "").strip()
    if not printer_name or not base_url:
        return {"imported": 0, "skipped": 0, "updated": 0, "errors": 1, "error": "Missing printer or Moonraker URL."}

    rows, _err = load_rows_raw(csv_file)
    existing_by_key: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for r in rows:
        if str(r.get("import_source") or "") != IMPORT_SOURCE_MOONRAKER_HISTORY:
            continue
        if str(r.get("printer") or "").strip() != printer_name:
            continue
        iid = str(r.get("import_id") or "").strip()
        if not iid:
            continue
        existing_by_key[(IMPORT_SOURCE_MOONRAKER_HISTORY, printer_name, iid)] = r

    ok, detail, jobs = fetch_moonraker_history(base_url, limit=limit)
    if not ok:
        return {"imported": 0, "skipped": 0, "updated": 0, "errors": 1, "error": f"Moonraker history fetch failed: {detail}"}

    imported = 0
    skipped = 0
    updated = 0
    errors = 0

    # Deterministic-ish processing order.
    try:
        jobs = sorted(jobs, key=lambda j: _as_float(_get_first(j, ("start_time", "timestamp", "end_time"))))
    except Exception:
        pass

    for job in jobs:
        try:
            row = _build_row_from_history_job(printer_name, job)
            import_id = str(row.get("import_id") or "").strip()
            key = (IMPORT_SOURCE_MOONRAKER_HISTORY, printer_name, import_id)

            existing = existing_by_key.get(key)
            if existing:
                if overwrite_existing:
                    # Preserve persisted identity + ordering; update only KCD fields.
                    row["job_uid"] = existing.get("job_uid", row.get("job_uid", ""))
                    existing.update(row)
                    updated += 1
                elif skip_existing:
                    skipped += 1
                else:
                    skipped += 1
                continue

            append_row(csv_file, headers, row)
            imported += 1
        except Exception:
            errors += 1

    if overwrite_existing and updated:
        try:
            rewrite_csv_all_rows(csv_file, headers, rows)
        except Exception:
            errors += 1

    out: Dict[str, Any] = {"imported": imported, "skipped": skipped, "updated": updated, "errors": errors}
    if errors:
        out["error"] = out.get("error") or "One or more rows failed to import."
    return out
