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


def infer_cancelled_effective_duration_seconds(
    *,
    estimated_seconds: float,
    elapsed_seconds: float,
    cancelled_attempt_index: int,
) -> float:
    """
    Infer an "effective" duration for cancelled/failed print attempts.

    Preference order:
      1) Use elapsed_seconds when available (capped by estimated_seconds if present)
      2) Otherwise, use a ramping fraction of estimated_seconds by attempt index:
         1st=0.10, 2nd=0.20, 3rd=0.30 ... cap 0.60
      3) Fallback fraction when ramping isn't possible: 0.15
    """
    estimated_seconds = max(0.0, float(estimated_seconds or 0.0))
    elapsed_seconds = max(0.0, float(elapsed_seconds or 0.0))

    if elapsed_seconds > 0:
        if estimated_seconds > 0:
            return min(elapsed_seconds, estimated_seconds)
        return elapsed_seconds

    if estimated_seconds <= 0:
        return 0.0

    try:
        idx = int(cancelled_attempt_index or 0)
    except Exception:
        idx = 0

    if idx <= 0:
        frac = 0.15
    else:
        frac = min(0.60, 0.10 * idx)
    return estimated_seconds * frac


def infer_cancelled_effective_filament_mm(
    *,
    filament_mm_raw: float,
    filament_mm_est: float,
    duration_seconds_effective: float,
    duration_seconds_est: float,
) -> float:
    """
    Infer effective filament usage for cancelled/failed print attempts.

    Preference order:
      1) If filament_mm_raw > 0, use it
      2) Else if filament_mm_est > 0, scale by progress ratio:
         duration_effective / max(duration_est, 1), clamped to [0..filament_mm_est]
      3) Else 0
    """
    filament_mm_raw = max(0.0, float(filament_mm_raw or 0.0))
    filament_mm_est = max(0.0, float(filament_mm_est or 0.0))
    duration_seconds_effective = max(0.0, float(duration_seconds_effective or 0.0))
    duration_seconds_est = max(0.0, float(duration_seconds_est or 0.0))

    if filament_mm_raw > 0:
        return filament_mm_raw

    if filament_mm_est <= 0:
        return 0.0

    denom = max(duration_seconds_est, 1.0)
    ratio = min(1.0, max(0.0, duration_seconds_effective / denom))
    return min(filament_mm_est, filament_mm_est * ratio)


def _extract_elapsed_seconds(job: Dict[str, Any], start_ts: float) -> float:
    end_ts = _as_float(_get_first(job, ("end_time",)))
    if start_ts > 0 and end_ts > 0 and end_ts >= start_ts:
        return max(0.0, end_ts - start_ts)

    # Various Moonraker fields observed in the wild.
    return _as_float(_get_first(job, ("total_duration", "elapsed", "total_time", "duration", "print_duration", "print_time")))


def _build_row_from_history_job(printer_name: str, job: Dict[str, Any], *, cancelled_attempt_index: int = 0) -> Dict[str, Any]:
    filename = str(_get_first(job, ("filename", "name", "file")) or "").strip()
    start_ts = _as_float(_get_first(job, ("start_time", "timestamp", "end_time")))
    if start_ts <= 0:
        start_ts = 0.0

    # Moonraker's "print_time"/"print_duration" represent actual printed time (completed or partial).
    # Do NOT fall back to "total_duration" here, as it may be present for cancelled jobs as well.
    print_time = _as_float(_get_first(job, ("print_time", "print_duration")))
    estimated_time = _as_float(_get_first(job, ("estimated_time", "estimated_duration")))

    filament_raw = _as_float(_get_first(job, ("filament_used", "filament", "filament_mm")))
    filament_est = _as_float(_get_first(job, ("filament_total", "filament_est", "filament_est_mm")))

    outcome = _infer_outcome(job, print_time=print_time, estimated_time=estimated_time)

    if outcome == "completed":
        duration_effective = print_time
        filament_effective = filament_raw
    else:
        elapsed = _extract_elapsed_seconds(job, start_ts)
        duration_effective = infer_cancelled_effective_duration_seconds(
            estimated_seconds=estimated_time,
            elapsed_seconds=elapsed,
            cancelled_attempt_index=cancelled_attempt_index,
        )
        filament_effective = infer_cancelled_effective_filament_mm(
            filament_mm_raw=filament_raw,
            filament_mm_est=filament_est,
            duration_seconds_effective=duration_effective,
            duration_seconds_est=estimated_time,
        )

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
    cancelled_counts: Dict[Tuple[str, str], int] = {}

    # Deterministic-ish processing order.
    try:
        jobs = sorted(jobs, key=lambda j: _as_float(_get_first(j, ("start_time", "timestamp", "end_time"))))
    except Exception:
        pass

    for job in jobs:
        try:
            filename = str(_get_first(job, ("filename", "name", "file")) or "").strip()
            # Ramping inference requires attempt order per (printer, filename).
            cancelled_attempt_index = 0
            try:
                print_time = _as_float(_get_first(job, ("print_time", "print_duration")))
                estimated_time = _as_float(_get_first(job, ("estimated_time", "estimated_duration")))
                outcome = _infer_outcome(job, print_time=print_time, estimated_time=estimated_time)
                if outcome in ("cancelled", "failed"):
                    key2 = (printer_name, filename)
                    cancelled_counts[key2] = cancelled_counts.get(key2, 0) + 1
                    cancelled_attempt_index = cancelled_counts[key2]
            except Exception:
                cancelled_attempt_index = 0

            row = _build_row_from_history_job(printer_name, job, cancelled_attempt_index=cancelled_attempt_index)
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
