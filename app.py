"""
Print Cost Dashboard - Flask Application

Refactored to use modular core package.
"""
import os
import json
import tempfile
import uuid
import math
import hashlib
import time
import re
from pathlib import Path
from datetime import datetime, timedelta, timezone
import flask
from flask import Flask, request, jsonify, render_template, redirect, url_for, send_file
from werkzeug.utils import secure_filename
from core.config import (
    API_KEY, CSV_FILE, HEADERS, FRIENDLY_HEADERS, PRINTER_COLORS,
    DEFAULT_PRICING, SETTINGS_FILE, DISPLAY_FILE, DATA_DIR, TIMEZONE_OBJ
)
from core.storage import (
    load_settings, save_settings, load_display_settings, save_display_settings,
    load_rows_raw,
    rewrite_csv_without_indices, rewrite_csv_mark_completed,
    rewrite_csv_without_job_uids, rewrite_csv_mark_completed_job_uids,
    get_visible_columns_for_table, set_visible_columns_for_table,
    ts_to_local_dt
)
from core.pricing import (
    compute_costs, get_known_printers, rename_printer, merge_printers,
    get_pricing_for_printer_raw
)
from core.reports import (
    get_date_range_from_params, compute_monthly_breakdown,
    compute_top_printers, compute_summary,
    compute_pause_analytics,
    aggregate_by_material, aggregate_by_profile
)
from core import profiles
from core import rates
from core import pricing
from core import live
from core import projects
from core import thumbnails as thumbs
from core import system_events
from core import storage_backend
from core import history_repo
from core import reports_repo
from core import db as db_module
from core.moonraker import (
    probe_moonraker_server_info,
    test_moonraker_url,
    find_history_job_for_completion,
)
from core.import_moonraker import import_moonraker_history_to_csv
from core.gcode_metadata import extract_gcode_metadata
from core.printers import (
    get_canonical_printer_names,
    normalize_incoming_printer_and_filename,
    looks_like_gcode_filename,
)
from core.backup import load_backup_settings, save_backup_settings, create_backup_archive, maybe_run_auto_backup

app = Flask(__name__)
app.logger.info("Flask version: %s", getattr(flask, "__version__", "unknown"))

if str(os.getenv("KCD_STORAGE_BACKEND", "csv")).strip().lower() == "sql":
    app.logger.warning("SQL-only mode active (KCD_STORAGE_BACKEND=sql)")

_ALLOWED_PER_PAGE = (10, 25, 50, 100)
RECALC_CONFIRM_THRESHOLD = 50


def _safe_thumb_dir(name: str) -> str:
    s = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in str(name or "").strip())
    return s or "unknown"


def build_thumb_url_from_token(printer_name: str, token: str, size_hint: str) -> str | None:
    token = str(token or "").strip()
    if not token:
        return None
    size = "card" if str(size_hint or "").strip().lower() == "card" else "small"
    return url_for(
        "thumb_cache",
        printer_name=_safe_thumb_dir(printer_name),
        cache_file=f"{token}_{size}.png",
    )


def get_job_thumbnail_url(
    printer_name: str,
    filename: str,
    size_hint: str,
    *,
    job_uid: str | None = None,
) -> str | None:
    cache_path = thumbs.get_cached_thumbnail_path(printer_name, filename, size_hint=size_hint)
    if not cache_path:
        return None
    try:
        url = url_for(
            "thumb_cache",
            printer_name=_safe_thumb_dir(printer_name),
            cache_file=os.path.basename(cache_path),
        )
    except Exception:
        return None

    if job_uid:
        try:
            base = thumbs.resolve_moonraker_base_url(printer_name)
            token = thumbs.compute_thumbnail_token(printer_name, filename, base_url=base)
            history_repo.set_job_thumbnail(job_uid, token)
        except Exception:
            pass

    return url


@app.route("/thumb/<printer_name>/<cache_file>", methods=["GET"], endpoint="thumb_cache")
def thumb_cache(printer_name: str, cache_file: str):
    # Path safety:
    # - serve only cached files for known printers (slugged)
    # - serve only files matching our generated cache name pattern
    # - enforce resolved-path containment using Path.resolve()
    if not printer_name or not cache_file:
        return ("", 404)

    safe_printer = _safe_thumb_dir(printer_name)
    if safe_printer != printer_name:
        return ("", 404)

    # Cache files are generated as: <sha1>_<size>.png
    if not re.fullmatch(r"[a-f0-9]{40}_(small|card)\.png", cache_file):
        return ("", 404)

    # Only allow printers that exist in the canonical configured printer list.
    try:
        allowed_slugs = {_safe_thumb_dir(p) for p in get_canonical_printer_names()}
    except Exception:
        allowed_slugs = set()
    if safe_printer not in allowed_slugs:
        return ("", 404)

    base_dir = Path(DATA_DIR) / "thumb_cache" / safe_printer
    file_path = base_dir / cache_file
    try:
        base_real = base_dir.resolve(strict=False)
        file_real = file_path.resolve(strict=False)
        if not file_real.is_relative_to(base_real):
            return ("", 404)
    except Exception:
        return ("", 404)

    if not file_path.exists():
        return ("", 404)

    resp = send_file(str(file_path), mimetype="image/png")
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp


@app.route("/system-events", methods=["GET"])
def system_events_page():
    filter_name = (request.args.get("filter") or "all").strip().lower()
    if filter_name not in {"all", "failures", "deleted"}:
        filter_name = "all"
    events = system_events.list_events(filter_name=filter_name, limit=500)
    for ev in events:
        ts = str(ev.get("ts") or "").strip()
        if not ts:
            ev["_ts_display"] = ""
            continue
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(TIMEZONE_OBJ)
            ev["_ts_display"] = dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            ev["_ts_display"] = ts
    return render_template(
        "system_events.html",
        events=events,
        filter_name=filter_name,
    )

def _parse_per_page(raw, default=25):
    try:
        v = int(raw)
    except Exception:
        v = default
    return v if v in _ALLOWED_PER_PAGE else default


def _paginate(items, page, per_page):
    total = len(items)
    pages = max(1, int(math.ceil(total / float(per_page))) if per_page else 1)
    try:
        page = int(page)
    except Exception:
        page = 1
    page = max(1, min(pages, page))
    start = (page - 1) * per_page
    end = start + per_page
    return items[start:end], {
        "page": page,
        "per_page": per_page,
        "total": total,
        "pages": pages,
        "has_prev": page > 1,
        "has_next": page < pages,
    }


def _pager_links(endpoint, args_dict, page_key, per_page_key, pager_meta):
    def _url_for_page(p):
        params = dict(args_dict)
        params[page_key] = str(p)
        params[per_page_key] = str(pager_meta["per_page"])
        return url_for(endpoint, **params)

    pages = pager_meta["pages"]
    page = pager_meta["page"]

    if pages <= 7:
        page_numbers = list(range(1, pages + 1))
    else:
        page_numbers = sorted(set([1, pages, page - 2, page - 1, page, page + 1, page + 2]))
        page_numbers = [p for p in page_numbers if 1 <= p <= pages]

    links = []
    last = None
    for p in page_numbers:
        if last is not None and p - last > 1:
            links.append({"page": None, "url": None, "current": False})
        links.append({"page": p, "url": _url_for_page(p), "current": p == page})
        last = p

    pager_meta = dict(pager_meta)
    pager_meta["links"] = links
    pager_meta["prev_url"] = _url_for_page(page - 1) if pager_meta["has_prev"] else None
    pager_meta["next_url"] = _url_for_page(page + 1) if pager_meta["has_next"] else None
    pager_meta["base_query"] = {k: v for k, v in args_dict.items() if k not in (page_key, per_page_key)}
    return pager_meta


def _history_sort_key(row: dict) -> float:
    """
    Return a stable numeric sort key for history rows (newest first).

    Prefers epoch seconds (timestamp_raw / timestamp_epoch). Falls back to parsing
    a rendered timestamp string (including newline-separated date/time).
    Rows with missing/invalid timestamps sort last consistently.
    """
    try:
        ts_raw = row.get("timestamp_raw")
        if ts_raw is not None and ts_raw != "":
            return float(ts_raw)
    except Exception:
        pass

    # Prefer the original epoch string stored in CSV.
    try:
        ts_epoch = row.get("timestamp_epoch")
        if ts_epoch is not None and str(ts_epoch).strip() != "":
            return float(ts_epoch)
    except Exception:
        pass

    # Fallback: parse display timestamp (YYYY-MM-DD HH:MM:SS), tolerate newline split.
    try:
        ts_text = str(row.get("timestamp") or "").strip().replace("\n", " ").strip()
        if not ts_text:
            return float("-inf")
        dt = datetime.strptime(ts_text[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=TIMEZONE_OBJ)
        return float(dt.timestamp())
    except Exception:
        return float("-inf")


def _sort_history_rows(rows: list[dict]) -> list[dict]:
    return sorted(rows, key=_history_sort_key, reverse=True)


@app.before_request
def _kcd_auto_backup_hook():
    # Best-effort auto backup. This is intentionally lightweight: it only runs when due.
    if request.method != "GET":
        return
    if request.path.startswith("/static"):
        return
    try:
        maybe_run_auto_backup()
    except Exception:
        pass


# ============================================================================
# API ENDPOINTS
# ============================================================================

@app.post("/log-print")
def log_print():
    """API endpoint to log print data from 3D printers."""
    # Simple API key check
    auth = request.headers.get("X-API-Key", "")
    if API_KEY and auth != API_KEY:
        return jsonify({"status": "error", "error": "Unauthorized"}), 403

    payload = request.get_json(force=True, silent=True)
    if not payload:
        return jsonify({"status": "error", "error": "Invalid or missing JSON"}), 400

    missing = [k for k in ["timestamp", "printer", "filename", "duration_seconds", "filament_mm"] if k not in payload]
    if missing:
        return jsonify({"status": "error", "error": f"Missing fields: {', '.join(missing)}"}), 400

    try:
        ts = float(payload["timestamp"])
        duration_seconds = float(payload["duration_seconds"])
        filament_mm = float(payload["filament_mm"])
    except ValueError:
        return jsonify({"status": "error", "error": "Numeric fields must be numbers"}), 400

    printer_name_raw = str(payload["printer"])
    filename_raw = str(payload["filename"])

    canonical = get_canonical_printer_names()
    norm = normalize_incoming_printer_and_filename(printer_name_raw, filename_raw, canonical_printers=canonical)
    if not norm.valid_printer:
        app.logger.warning(norm.reason)
        app.logger.warning("Allowed printers: %s", sorted(canonical))
        system_events.emit_event(
            "warning",
            "Rejected incoming print log",
            f"{norm.reason} Next: check Settings → Printers for the correct printer name and re-run the client installer if needed.",
            meta={"action": "log_print", "printer": str(printer_name_raw or ""), "filename": str(filename_raw or "")},
        )
        return jsonify({"status": "error", "error": norm.reason}), 400

    printer_name = norm.printer_name
    filename = norm.filename

    # Check for live job metadata
    from core import live
    live_metadata = live.get_job_metadata_for_logging(printer_name)
    
    # Determine status and failure_reason
    status = "completed"
    failure_reason = ""
    
    # History rows represent completed/canceled/failed jobs only. If /log-print is called
    # at end-of-print, we should not persist transient live states like "printing".
    incoming_status = str(payload.get("status") or "").strip().lower()
    if incoming_status:
        if incoming_status in ("completed", "complete", "success", "succeeded"):
            status = "completed"
        elif incoming_status in ("canceled", "cancelled", "canceled_print", "cancelled_print"):
            status = "canceled"
        elif incoming_status in ("failed", "error"):
            status = "failed"

    # If live job exists and matches filename, optionally capture failure/cancel reason.
    # Only allow cancel/failed to override completed; never write "printing"/"paused" to history.
    paused_seconds_total = 0.0
    pause_count = 0
    runout_count = 0
    if live_metadata and live_metadata.get("filename") == filename:
        live_job = live.get_job(printer_name)
        if live_job:
            live_status = str(live_job.get("status") or "").strip().lower()
            if live_status in ("canceled", "cancelled"):
                status = "canceled"
            elif live_status == "failed":
                status = "failed"
            failure_reason = str(live_job.get("failure_reason") or "").strip()
        try:
            paused_seconds_total = float(live_metadata.get("total_paused_duration") or 0.0)
        except Exception:
            paused_seconds_total = 0.0
        try:
            pause_count = int(live_metadata.get("pause_count") or 0)
        except Exception:
            pause_count = 0
        try:
            runout_count = int(live_metadata.get("runout_count") or 0)
        except Exception:
            runout_count = 0
        # Persist wall-clock duration (elapsed excluding pauses + paused total) so that pause accounting
        # can reliably exclude paused time later.
        try:
            elapsed_excluding_pauses = float(live_metadata.get("elapsed_seconds") or 0.0)
            duration_seconds = max(0.0, elapsed_excluding_pauses + float(paused_seconds_total))
        except Exception:
            pass

    # For completed jobs, attempt to finalize duration/filament using Moonraker history.
    history_job_id = None
    history_unavailable = False
    history_detail = ""
    if status == "completed":
        base_url = thumbs.resolve_moonraker_base_url(printer_name)
        app.logger.info("job-finalize: printer=%s moonraker_url=%s", printer_name, base_url or "none")
        if base_url:
            ok, detail, job = find_history_job_for_completion(
                base_url,
                filename=filename,
                end_timestamp=ts,
                window_seconds=600.0,
                limit=200,
            )
            app.logger.info(
                "job-finalize: history_lookup ok=%s detail=%s matched=%s",
                ok,
                detail,
                bool(job),
            )
            if not ok:
                history_unavailable = True
                history_detail = detail
            if ok and job:
                def _as_float(v):
                    try:
                        return float(v)
                    except Exception:
                        return 0.0

                def _get_first(j, keys):
                    for k in keys:
                        if k in j and j.get(k) is not None:
                            return j.get(k)
                    return None

                start_ts = _as_float(_get_first(job, ("start_time", "timestamp")))
                end_ts = _as_float(_get_first(job, ("end_time", "timestamp")))
                print_time = _as_float(_get_first(job, ("print_time", "print_duration")))
                total_duration = _as_float(_get_first(job, ("total_duration", "elapsed", "duration", "total_time")))
                filament_used = _as_float(_get_first(job, ("filament_used", "filament", "filament_mm")))

                duration_candidate = None
                if print_time > 0:
                    duration_candidate = print_time
                elif total_duration > 0:
                    duration_candidate = total_duration
                elif start_ts > 0 and end_ts > 0 and end_ts >= start_ts:
                    duration_candidate = end_ts - start_ts

                if duration_candidate is not None:
                    duration_seconds = duration_candidate
                else:
                    duration_seconds = None
                    app.logger.warning(
                        "job-finalize: %s history missing duration fields; leaving duration unknown",
                        printer_name,
                    )

                if filament_used > 0:
                    filament_mm = filament_used

                if end_ts > 0:
                    ts = end_ts

                history_job_id = _get_first(job, ("job_id", "uid", "id", "history_id"))
                # Carry start/end for DB storage (CSV will ignore).
                row_started_at = start_ts if start_ts > 0 else None
                row_ended_at = end_ts if end_ts > 0 else None
            else:
                if ok and not job:
                    history_unavailable = True
                    history_detail = detail or "No matching history entry found"
                row_started_at = None
                row_ended_at = None
        else:
            history_unavailable = True
            history_detail = "Missing Moonraker URL"
            row_started_at = None
            row_ended_at = None
    else:
        row_started_at = None
        row_ended_at = None

    if history_unavailable:
        app.logger.warning(
            "%s: moonraker history unavailable (%s). duration/thumbnail not updated.",
            printer_name,
            history_detail or "unknown",
        )
        try:
            if duration_seconds is None or float(duration_seconds) <= 0:
                duration_seconds = None
        except Exception:
            duration_seconds = None

    cost_data = {}
    if duration_seconds is not None:
        cost_data = compute_costs(printer_name, duration_seconds, filament_mm, paused_seconds_total=paused_seconds_total)

    row_duration = duration_seconds if duration_seconds is not None else ""
    row = {
        "timestamp": ts,
        "job_uid": str(uuid.uuid4()),
        "printer": printer_name,
        "filename": filename,
        "duration_seconds": row_duration,
        "paused_seconds_total": paused_seconds_total,
        "pause_count": pause_count,
        "runout_count": runout_count,
        "filament_mm": filament_mm,
    }
    row.update(cost_data)
    
    # Add status and failure_reason
    row["status"] = status
    row["failure_reason"] = failure_reason
    if row_started_at:
        row["started_at"] = row_started_at
    if row_ended_at:
        row["ended_at"] = row_ended_at

    storage_backend.write_job(row)

    if status == "completed":
        app.logger.info(
            "job-finalize: job_uid=%s filename=%s history_id=%s duration_seconds=%s",
            row["job_uid"],
            filename,
            history_job_id,
            duration_seconds,
        )

    # Clear live job after successful logging
    if live_metadata and live_metadata.get("filename") == filename:
        live.end_job(printer_name)

    return jsonify({"status": "ok"})



@app.route("/health")
def health():
    """
    Simple health check endpoint for monitoring and diagnostics.
    Returns 200 OK with basic status information.
    """
    import os
    from datetime import datetime
    
    status = {
        "status": "healthy",
        "timestamp": datetime.now(TIMEZONE_OBJ).isoformat(),
        "api_key_configured": bool(API_KEY),
        "csv_exists": os.path.exists(CSV_FILE),
    }
    
    # Count rows if CSV exists
    if os.path.exists(CSV_FILE):
        rows, _ = load_rows_raw(CSV_FILE)
        status["total_prints"] = len(rows)
    else:
        status["total_prints"] = 0
    
    # List known printers
    status["known_printers"] = get_known_printers()
    
    return jsonify(status), 200


# ============================================================================
# LIVE JOB CONTROL ENDPOINTS
# ============================================================================

@app.post("/job-start")
def job_start():
    """Start tracking a new print job."""
    from core import live
    
    data = request.get_json() or request.form.to_dict()
    printer_name_raw = data.get("printer_name")
    filename_raw = data.get("filename")

    canonical = get_canonical_printer_names()
    norm = normalize_incoming_printer_and_filename(printer_name_raw, filename_raw, canonical_printers=canonical)
    if not norm.valid_printer:
        app.logger.warning(norm.reason)
        app.logger.warning("Allowed printers: %s", sorted(canonical))
        system_events.emit_event(
            "warning",
            "Rejected incoming job start",
            f"{norm.reason} Next: check Settings → Printers for the correct printer name and re-run the client installer if needed.",
            meta={"action": "job_start", "printer": str(printer_name_raw or ""), "filename": str(filename_raw or "")},
        )
        return jsonify({"success": False, "error": norm.reason}), 400

    printer_name = norm.printer_name
    filename = norm.filename

    if not printer_name or not filename:
        system_events.emit_event(
            "warning",
            "Rejected incoming job start",
            "Missing required fields (printer_name or filename). Next: verify the Klipper start macro sends both fields.",
            meta={"action": "job_start", "printer": str(printer_name or ""), "filename": str(filename or "")},
        )
        return jsonify({"success": False, "error": "Missing required fields: printer_name, filename"}), 400
    
    # Optional fields
    start_time = data.get("start_time")
    if start_time:
        try:
            start_time = float(start_time)
        except ValueError:
            start_time = None
    
    estimated_duration = data.get("estimated_duration")
    if estimated_duration:
        try:
            estimated_duration = float(estimated_duration)
        except ValueError:
            estimated_duration = None
    
    estimated_filament = data.get("estimated_filament_mm")
    if estimated_filament:
        try:
            estimated_filament = float(estimated_filament)
        except ValueError:
            estimated_filament = None
    
    profile_id = data.get("profile_id")
    
    live.start_job(printer_name, filename, start_time, estimated_duration, estimated_filament, profile_id)
    job = live.get_job(printer_name)
    
    return jsonify({"success": True, "job": job})


@app.post("/job-update")
def job_update():
    """Update an active print job."""
    from core import live
    
    data = request.get_json() or request.form.to_dict()
    printer_name = data.get("printer_name")
    
    if not printer_name:
        return jsonify({"success": False, "error": "Missing required field: printer_name"}), 400

    canonical = get_canonical_printer_names()
    if looks_like_gcode_filename(printer_name):
        reason = f"Rejected printer_name because it looks like a gcode filename: {printer_name!r}"
        app.logger.warning(reason)
        system_events.emit_event(
            "warning",
            "Rejected incoming job update",
            f"{reason} Next: confirm your Klipper macro sends the printer name first, not the filename.",
            meta={"action": "job_update", "printer": str(printer_name or "")},
        )
        return jsonify({"success": False, "error": reason}), 400

    if printer_name not in canonical:
        reason = f"Unknown printer_name received: {printer_name!r}"
        app.logger.warning(reason)
        app.logger.warning("Allowed printers: %s", sorted(canonical))
        system_events.emit_event(
            "warning",
            "Ignored update for unknown printer",
            f"{reason} Next: add the printer in Settings → Printers and reinstall the client.",
            meta={"action": "job_update", "printer": str(printer_name or "")},
        )
        return jsonify({"success": False, "error": reason}), 400
    
    # Extract update fields (exclude printer_name from updates)
    updates = {k: v for k, v in data.items() if k != "printer_name"}
    
    # Convert numeric fields
    for field in ["estimated_duration", "estimated_filament_mm"]:
        if field in updates:
            try:
                updates[field] = float(updates[field])
            except (ValueError, TypeError):
                pass
    
    result = live.update_job(printer_name, **updates)
    
    if result is None:
        return jsonify({"success": False, "error": "Job not found"}), 404
    
    job = live.get_job(printer_name)
    return jsonify({"success": True, "job": job})


@app.post("/job-pause")
def job_pause():
    """Pause an active print job."""
    from core import live
    
    data = request.get_json() or request.form.to_dict()
    printer_name = data.get("printer_name")
    reason = data.get("reason") or data.get("pause_reason")
    
    if not printer_name:
        return jsonify({"success": False, "error": "Missing required field: printer_name"}), 400

    canonical = get_canonical_printer_names()
    if looks_like_gcode_filename(printer_name):
        reason = f"Rejected printer_name because it looks like a gcode filename: {printer_name!r}"
        app.logger.warning(reason)
        system_events.emit_event(
            "warning",
            "Rejected incoming job pause",
            f"{reason} Next: confirm your Klipper macro sends the printer name first, not the filename.",
            meta={"action": "job_pause", "printer": str(printer_name or "")},
        )
        return jsonify({"success": False, "error": reason}), 400

    if printer_name not in canonical:
        reason = f"Unknown printer_name received: {printer_name!r}"
        app.logger.warning(reason)
        app.logger.warning("Allowed printers: %s", sorted(canonical))
        system_events.emit_event(
            "warning",
            "Ignored pause for unknown printer",
            f"{reason} Next: add the printer in Settings → Printers and reinstall the client.",
            meta={"action": "job_pause", "printer": str(printer_name or "")},
        )
        return jsonify({"success": False, "error": reason}), 400
    
    pause_reason = str(reason or "").strip().lower()
    if pause_reason and pause_reason not in ("user_pause", "filament_runout", "filament_change"):
        # Be permissive: accept unknown reasons but normalize to a safe value.
        app.logger.warning("Unknown pause reason received for %s: %r", printer_name, pause_reason)
        pause_reason = ""

    result = live.pause_job(printer_name, reason=pause_reason)
    
    if result is None:
        return jsonify({"success": False, "error": "Job not found"}), 404
    
    job = live.get_job(printer_name)
    return jsonify({"success": True, "job": job})


@app.post("/job-resume")
def job_resume():
    """Resume a paused print job."""
    from core import live
    
    data = request.get_json() or request.form.to_dict()
    printer_name = data.get("printer_name")
    
    if not printer_name:
        return jsonify({"success": False, "error": "Missing required field: printer_name"}), 400

    canonical = get_canonical_printer_names()
    if looks_like_gcode_filename(printer_name):
        reason = f"Rejected printer_name because it looks like a gcode filename: {printer_name!r}"
        app.logger.warning(reason)
        system_events.emit_event(
            "warning",
            "Rejected incoming job resume",
            f"{reason} Next: confirm your Klipper macro sends the printer name first, not the filename.",
            meta={"action": "job_resume", "printer": str(printer_name or "")},
        )
        return jsonify({"success": False, "error": reason}), 400

    if printer_name not in canonical:
        reason = f"Unknown printer_name received: {printer_name!r}"
        app.logger.warning(reason)
        app.logger.warning("Allowed printers: %s", sorted(canonical))
        system_events.emit_event(
            "warning",
            "Ignored resume for unknown printer",
            f"{reason} Next: add the printer in Settings → Printers and reinstall the client.",
            meta={"action": "job_resume", "printer": str(printer_name or "")},
        )
        return jsonify({"success": False, "error": reason}), 400
    
    result = live.resume_job(printer_name)
    
    if result is None:
        return jsonify({"success": False, "error": "Job not found"}), 404
    
    job = live.get_job(printer_name)
    return jsonify({"success": True, "job": job})


@app.post("/job-cancel")
def job_cancel():
    """Cancel an active print job."""
    from core import live
    
    data = request.get_json() or request.form.to_dict()
    printer_name_raw = data.get("printer_name")
    filename_raw = data.get("filename")
    reason = data.get("reason")  # Optional failure/cancel reason
    elapsed_raw = data.get("elapsed_seconds")
    
    canonical = get_canonical_printer_names()
    norm = normalize_incoming_printer_and_filename(printer_name_raw, filename_raw, canonical_printers=canonical)
    if not norm.valid_printer:
        app.logger.warning(norm.reason)
        app.logger.warning("Allowed printers: %s", sorted(canonical))
        system_events.emit_event(
            "warning",
            "Rejected incoming job cancel",
            f"{norm.reason} Next: check Settings → Printers for the correct printer name and re-run the client installer if needed.",
            meta={"action": "job_cancel", "printer": str(printer_name_raw or ""), "filename": str(filename_raw or "")},
        )
        return jsonify({"success": False, "error": norm.reason}), 400

    printer_name = norm.printer_name
    filename = norm.filename

    elapsed_seconds = None
    if elapsed_raw is not None and str(elapsed_raw).strip() != "":
        try:
            elapsed_seconds = float(elapsed_raw)
        except Exception:
            return jsonify({"success": False, "error": "elapsed_seconds must be a number"}), 400

    # Prefer the currently active job for this printer (more reliable than inbound payload).
    active_before = live.get_job(printer_name)
    if active_before and not filename:
        filename = str(active_before.get("filename") or "").strip()

    # Mark canceled in live state (this also removes it from active jobs).
    live_result = live.cancel_job(printer_name, reason)
    if live_result and (elapsed_seconds is None or elapsed_seconds <= 0):
        elapsed_seconds = float(live_result.get("elapsed_seconds") or 0.0)

    if elapsed_seconds is None:
        elapsed_seconds = 0.0

    # Idempotency + safety:
    # - If there's no active job to cancel, treat as a no-op and avoid duplicating history rows.
    # - If the most recent history row for this printer/filename is already completed/canceled,
    #   don't append a new canceled row.
    if live_result is None:
        def _find_recent_status():
            rows, _err = load_rows_raw(CSV_FILE)
            if not rows:
                return None
            target_printer = printer_name
            target_filename = str(filename or "").strip()
            for r in reversed(rows):
                if (r.get("printer") or "") != target_printer:
                    continue
                if target_filename and (r.get("filename") or "") != target_filename:
                    continue
                status = (r.get("status") or "").strip().lower()
                try:
                    ts = float(r.get("timestamp") or 0.0)
                except Exception:
                    ts = 0.0
                return {"status": status, "timestamp": ts, "row": r}
            return None

        recent = _find_recent_status()
        if recent:
            age = max(0.0, time.time() - float(recent.get("timestamp") or 0.0))
            recent_status = (recent.get("status") or "").lower()
            if recent_status in ("canceled", "cancelled") and age < 6 * 3600:
                return jsonify({"success": True, "job": None, "history": "already_canceled"}), 200
            if recent_status in ("completed", "complete") and age < 6 * 3600:
                return jsonify({"success": True, "job": None, "history": "already_completed"}), 200
        return jsonify({"success": True, "job": None, "history": "no_active_job"}), 200

    # Record a canceled job in history so it appears in Print History.
    ts = time.time()
    filament_mm = 0.0
    try:
        paused_seconds_total = float(live_result.get("total_paused_duration") or 0.0)
    except Exception:
        paused_seconds_total = 0.0
    try:
        pause_count = int(live_result.get("pause_count") or 0)
    except Exception:
        pause_count = 0
    try:
        runout_count = int(live_result.get("runout_count") or 0)
    except Exception:
        runout_count = 0.0

    # Store duration_seconds as total wall time, with paused time tracked separately.
    # This keeps cost calculation consistent when excluding paused time is enabled.
    duration_seconds_total = float(elapsed_seconds) + float(paused_seconds_total)

    cost_data = compute_costs(
        printer_name,
        float(duration_seconds_total),
        filament_mm,
        paused_seconds_total=paused_seconds_total,
    )

    row = {
        "timestamp": ts,
        "job_uid": str(uuid.uuid4()),
        "printer": printer_name,
        "filename": filename,
        "duration_seconds": float(duration_seconds_total),
        "paused_seconds_total": paused_seconds_total,
        "pause_count": pause_count,
        "runout_count": runout_count,
        "filament_mm": filament_mm,
    }
    row.update(cost_data)
    row["status"] = "canceled"
    row["failure_reason"] = str(reason or "").strip()
    storage_backend.write_job(row)

    app.logger.info(
        "job-cancel logged: printer=%s filename=%s elapsed_seconds=%s",
        printer_name,
        filename,
        elapsed_seconds,
    )

    return jsonify({"success": True, "job": live_result, "history_job_uid": row["job_uid"]})


@app.post("/job-end")
def job_end():
    """Mark a print job as completed."""
    from core import live
    
    data = request.get_json() or request.form.to_dict()
    printer_name = data.get("printer_name")
    
    if not printer_name:
        return jsonify({"success": False, "error": "Missing required field: printer_name"}), 400

    canonical = get_canonical_printer_names()
    if looks_like_gcode_filename(printer_name):
        reason = f"Rejected printer_name because it looks like a gcode filename: {printer_name!r}"
        app.logger.warning(reason)
        return jsonify({"success": False, "error": reason}), 400

    if printer_name not in canonical:
        reason = f"Unknown printer_name received: {printer_name!r}"
        app.logger.warning(reason)
        app.logger.warning("Allowed printers: %s", sorted(canonical))
        return jsonify({"success": False, "error": reason}), 400
    
    result = live.end_job(printer_name)
    
    if result is None:
        return jsonify({"success": False, "error": "Job not found"}), 404
    
    return jsonify({"success": True, "job": result})


# ============================================================================
# WEB PAGES
# ============================================================================

@app.route("/", methods=["GET", "POST"])
def index():
    """Main dashboard page."""
    if request.method == "POST":
        action = request.form.get("action")

        # Clear a stuck / wrong live job without logging history
        if action == "clear_live_job":
            printer_name = request.form.get("printer_name", "").strip()
            if printer_name:
                live.end_job(printer_name)
            return redirect(url_for("index"))

        if action == "complete_rows":
            selected = request.form.getlist("delete_rows")
            if selected:
                if _is_sql_only():
                    if all(str(v).strip().isdigit() for v in selected):
                        indices = [int(i) for i in selected if str(i).strip().isdigit()]
                        rows_for_map, _ = _load_history_rows_for_recalc()
                        job_uids = [r.get("job_uid") for r in rows_for_map if r.get("row_index") in indices and r.get("job_uid")]
                    else:
                        job_uids = [str(v).strip() for v in selected if str(v).strip()]
                    _mark_completed_jobs_sql(job_uids)
                else:
                    if all(str(v).strip().isdigit() for v in selected):
                        indices = [int(i) for i in selected if str(i).strip().isdigit()]
                        rewrite_csv_mark_completed(CSV_FILE, HEADERS, indices)
                    else:
                        job_uids = [str(v).strip() for v in selected if str(v).strip()]
                        rewrite_csv_mark_completed_job_uids(CSV_FILE, HEADERS, job_uids)
            return redirect(url_for("index"))

        if action == "recalc_costs":
            selected = request.form.getlist("delete_rows")
            job_uids = []
            if selected:
                if all(str(v).strip().isdigit() for v in selected):
                    indices = {int(i) for i in selected if str(i).strip().isdigit()}
                    rows_for_map, _ = load_rows_raw(CSV_FILE)
                    job_uids = [r.get("job_uid") for r in rows_for_map if r.get("row_index") in indices and r.get("job_uid")]
                else:
                    job_uids = [str(v).strip() for v in selected if str(v).strip()]

            updated = storage_backend.recalc_jobs(job_uids, compute_costs)
            return redirect(url_for("index", msg=f"Recalculated costs for {updated} job(s)."))

        # Handle row deletion
        if action in ("delete_rows", "delete"):
            selected = request.form.getlist("delete_rows")
            if selected:
                if _is_sql_only():
                    if all(str(v).strip().isdigit() for v in selected):
                        indices = [int(i) for i in selected if str(i).strip().isdigit()]
                        rows_for_map, _ = _load_history_rows_for_recalc()
                        job_uids = [r.get("job_uid") for r in rows_for_map if r.get("row_index") in indices and r.get("job_uid")]
                    else:
                        job_uids = [str(v).strip() for v in selected if str(v).strip()]
                    deleted = _delete_jobs_sql(job_uids)
                    system_events.emit_event(
                        "deleted",
                        "Deleted history jobs",
                        f"Deleted {deleted} job(s) from Print History (SQL-only).",
                        meta={"action": "delete_history_rows", "count": deleted},
                    )
                else:
                    if all(str(v).strip().isdigit() for v in selected):
                        indices = [int(i) for i in selected if str(i).strip().isdigit()]
                        rewrite_csv_without_indices(CSV_FILE, HEADERS, indices)
                        system_events.emit_event(
                            "deleted",
                            "Deleted history jobs",
                            f"Deleted {len(indices)} job(s) from Print History.",
                            meta={"action": "delete_history_rows", "count": len(indices)},
                        )
                    else:
                        job_uids = [str(v).strip() for v in selected if str(v).strip()]
                        rewrite_csv_without_job_uids(CSV_FILE, HEADERS, job_uids)
                        system_events.emit_event(
                            "deleted",
                            "Deleted history jobs",
                            f"Deleted {len(job_uids)} job(s) from Print History.",
                            meta={"action": "delete_history_rows", "count": len(job_uids)},
                        )
            return redirect(url_for("index"))

    error = None
    message = request.args.get("msg", "").strip()
    
    # Apply date filtering (printer + range + legacy date range inputs)
    selected_printer = (request.args.get("printer") or "All").strip()
    selected_range = (request.args.get("range") or "all").strip().lower()
    paused_min_raw = (request.args.get("paused_min") or "").strip()
    has_runout = (request.args.get("has_runout") or "").strip() in {"1", "true", "yes", "on"}

    start_dt, end_dt, range_label, quick_range = get_date_range_from_params(request.args)
    if selected_range in {"today", "yesterday", "week", "month"}:
        now = datetime.now(TIMEZONE_OBJ)
        start_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if selected_range == "today":
            start_dt = start_today
            end_dt = now
            range_label = "Today"
        elif selected_range == "yesterday":
            start_dt = start_today - timedelta(days=1)
            end_dt = start_today
            range_label = "Yesterday"
        elif selected_range == "week":
            start_dt = (start_today - timedelta(days=start_today.weekday()))
            end_dt = now
            range_label = "This week"
        elif selected_range == "month":
            start_dt = start_today.replace(day=1)
            end_dt = now
            range_label = "This month"
        quick_range = ""
    
    end_exclusive = selected_range == "yesterday"
    canonical_printers = get_canonical_printer_names()
    if selected_printer and selected_printer != "All":
        if selected_printer in canonical_printers:
            selected_printer = selected_printer
        else:
            selected_printer = "All"

    paused_min = 0
    if paused_min_raw:
        try:
            paused_min = int(float(paused_min_raw))
        except Exception:
            paused_min = 0
    paused_min = max(0, min(24 * 60, paused_min))

    history_per_page = _parse_per_page(request.args.get("history_per_page"), default=25)
    history_query = history_repo.HistoryQuery(
        printer=selected_printer if selected_printer != "All" else None,
        start_dt=start_dt,
        end_dt=end_dt,
        end_exclusive=end_exclusive,
        paused_min=paused_min,
        has_runout=has_runout,
    )
    history_result = history_repo.list_history_rows(
        history_query,
        page=request.args.get("history_page", 1),
        per_page=history_per_page,
    )
    history_rows_page = history_result.rows_page
    history_pager = history_result.pager
    error = history_result.error

    history_pager = _pager_links(
        endpoint="index",
        args_dict=request.args.to_dict(flat=True),
        page_key="history_page",
        per_page_key="history_per_page",
        pager_meta=history_pager,
    )

    rows = history_result.rows_all

    summary = compute_summary(rows) or {}
    # Ensure expected keys exist to avoid template errors
    summary.setdefault("total_prints", 0)
    summary.setdefault("total_hours", 0.0)
    summary.setdefault("total_meters", 0.0)
    summary.setdefault("total_cost", 0.0)
    summary.setdefault("per_day", {})
    summary.setdefault("per_printer", {})
    display_settings = load_display_settings(DISPLAY_FILE, HEADERS)
    selected_columns = display_settings.get("visible_columns") or HEADERS
    visible_cols = selected_columns

    # Prepare chart data
    chart_cost_per_day = {"labels": [], "values": []}
    chart_hours_per_printer = {"labels": [], "values": []}
    
    if summary.get("per_day"):
        sorted_days = sorted(summary["per_day"].keys())
        chart_cost_per_day["labels"] = sorted_days
        chart_cost_per_day["values"] = [summary["per_day"][d]["cost"] for d in sorted_days]
        
    if summary.get("per_printer"):
        sorted_printers = sorted(summary["per_printer"].keys())
        chart_hours_per_printer["labels"] = sorted_printers
        chart_hours_per_printer["values"] = [summary["per_printer"][p]["hours"] for p in sorted_printers]

    active_jobs = live.list_active_jobs()
    active_by_printer = {j.get("printer_name"): j for j in active_jobs if j.get("printer_name")}
    
    # Compute printer summaries for status cards
    from core.reports import compute_printer_summaries
    printer_summaries = compute_printer_summaries(rows, active_jobs)

    # Enrich printer summaries with active filament/rate profile names
    all_profiles = profiles.get_all_profiles()
    printer_mappings = profiles.get_all_printer_mappings()
    rate_profiles = rates.list_rate_profiles()
    settings = load_settings(SETTINGS_FILE)
    for pname, ps in printer_summaries.items():
        pid = printer_mappings.get(pname)
        ps["active_filament_name"] = all_profiles.get(pid, {}).get("name") if pid else None
        rate_id = settings.get(pname, {}).get("active_rate_profile_id")
        ps["active_rate_name"] = rate_profiles.get(rate_id, {}).get("name") if rate_id else None

        # Card thumbnails (per-printer settings: thumbnails_enabled && thumbnails_on_cards).
        printer_cfg = settings.get(pname, {}) if isinstance(settings, dict) else {}
        thumbs_enabled = printer_cfg.get("thumbnails_enabled", True) is not False
        thumbs_on_cards = printer_cfg.get("thumbnails_on_cards", True) is not False
        ps["_thumb_cards_enabled"] = bool(thumbs_enabled and thumbs_on_cards)
        if thumbs_enabled and thumbs_on_cards:
            job_for_card = active_by_printer.get(pname, {})
            card_filename = str(job_for_card.get("filename") or ps.get("last_job_name") or "").strip()
            ps["_thumb_card"] = get_job_thumbnail_url(pname, card_filename, size_hint="card") if card_filename else None
        else:
            ps["_thumb_card"] = None

    # Now Printing thumbnails (same rule as cards).
    for job in active_jobs:
        pname = str(job.get("printer_name") or "").strip()
        if not pname:
            continue
        printer_cfg = settings.get(pname, {}) if isinstance(settings, dict) else {}
        thumbs_enabled = printer_cfg.get("thumbnails_enabled", True) is not False
        thumbs_on_cards = printer_cfg.get("thumbnails_on_cards", True) is not False
        job["_thumb_cards_enabled"] = bool(thumbs_enabled and thumbs_on_cards)
        if thumbs_enabled and thumbs_on_cards:
            fname = str(job.get("filename") or "").strip()
            job["_thumb_small"] = get_job_thumbnail_url(pname, fname, size_hint="small") if fname else None
        else:
            job["_thumb_small"] = None

    # Print History thumbnails (independent toggle: visible column; respects thumbnails_enabled only).
    if "thumbnail" in (visible_cols or []):
        for row in history_rows_page:
            pname = str(row.get("printer") or "").strip()
            fname = str(row.get("filename") or "").strip()
            if not pname:
                row["_thumbs_enabled"] = False
                row["_thumb_small"] = None
                row["_thumb_unavailable"] = False
                continue
            printer_cfg = settings.get(pname, {}) if isinstance(settings, dict) else {}
            thumbs_enabled = printer_cfg.get("thumbnails_enabled", True) is not False
            row["_thumbs_enabled"] = bool(thumbs_enabled)
            token = str(row.get("thumbnail") or "").strip()
            if thumbs_enabled and token:
                row["_thumb_small"] = build_thumb_url_from_token(pname, token, size_hint="small")
                row["_thumb_unavailable"] = False
            elif thumbs_enabled and fname:
                thumb_url = get_job_thumbnail_url(
                    pname,
                    fname,
                    size_hint="small",
                    job_uid=str(row.get("job_uid") or "").strip() or None,
                )
                row["_thumb_small"] = thumb_url
                row["_thumb_unavailable"] = bool(not thumb_url)
            else:
                row["_thumb_small"] = None
                row["_thumb_unavailable"] = False

    return render_template(
        "index.html",
        rows=rows,
      history_rows_page=history_rows_page,
        history_pager=history_pager,
        error=error,
        message=message,
        headers=HEADERS,
        friendly_headers=FRIENDLY_HEADERS,
        visible_cols=visible_cols,
        printer_colors=PRINTER_COLORS,
        summary=summary,
        range_label=range_label,
        quick_range=quick_range,
        chart_cost_per_day=chart_cost_per_day,
        chart_hours_per_printer=chart_hours_per_printer,
        printers=canonical_printers,
        selected_printer=selected_printer,
        selected_range=selected_range,
        paused_min=str(paused_min) if paused_min else "",
        has_runout=has_runout,
        start_date=start_dt.strftime("%Y-%m-%d") if start_dt else "",
        end_date=end_dt.strftime("%Y-%m-%d") if end_dt else "",
        csv_file=CSV_FILE,
        active_jobs=active_jobs,
        printer_summaries=printer_summaries,
    )


@app.route("/reports")
def reports_page():
    """Reports page with monthly breakdown and top printers."""
    data = reports_repo.get_reports_data(request.args)

    return render_template(
        "reports.html",
        monthly_breakdown=data.get("monthly_breakdown", []),
        top_printers=data.get("top_printers", []),
        summary=data.get("summary", {}),
        pause_analytics=data.get("pause_analytics", {}),
        material_summary=data.get("material_summary", []),
        profile_summary=data.get("profile_summary", []),
        range_label=data.get("range_label", ""),
        quick_range=data.get("quick_range", ""),
        start_date=data.get("start_date", ""),
        end_date=data.get("end_date", ""),
        error=data.get("error"),
    )


def _filter_history_rows_for_recalc(rows, args):
    project_id = (args.get("project") or "").strip()
    printer = (args.get("printer") or "").strip()
    q = (args.get("q") or "").strip().lower()
    status = (args.get("status") or "").strip().lower()

    start_dt, end_dt, _range_label, _quick_range = get_date_range_from_params(args)

    assignments = None
    if project_id:
        try:
            assignments = projects.load_assignments()
        except Exception:
            assignments = {}

    filtered = []
    for r in rows:
        if project_id:
            uid = str(r.get("job_uid") or "").strip()
            if not uid:
                continue
            if assignments is None:
                continue
            if assignments.get(uid) != project_id and assignments.get(projects.job_key(r)) != project_id:
                continue

        if printer and printer.lower() != "all":
            if str(r.get("printer") or "").strip() != printer:
                continue

        if status and status != "all":
            if str(r.get("status") or "").strip().lower() != status:
                continue

        if q:
            fname = str(r.get("filename") or "").lower()
            if q not in fname:
                continue

        if start_dt or end_dt:
            ts_raw = r.get("timestamp_raw")
            if not ts_raw:
                continue
            try:
                row_dt = ts_to_local_dt(float(ts_raw))
            except Exception:
                continue
            if start_dt and row_dt < start_dt:
                continue
            if end_dt and row_dt > end_dt:
                continue

        filtered.append(r)

    return filtered, start_dt, end_dt


def _parse_int(value, default):
    try:
        return int(value)
    except Exception:
        return default


def _is_sql_only() -> bool:
    return str(os.getenv("KCD_STORAGE_BACKEND", "csv")).strip().lower() == "sql"


def _load_history_rows_for_recalc() -> tuple[list, str | None]:
    if _is_sql_only():
        try:
            history_query = history_repo.HistoryQuery()
            history_result = history_repo.list_history_rows_sql(history_query, page=1, per_page=25, error=None)
            return history_result.rows_all, history_result.error
        except Exception as exc:
            return [], f"Error loading history from SQL: {exc}"
    return load_rows_raw(CSV_FILE)


def _sum_total_cost_sql(job_uids: list[str]) -> float:
    if not job_uids:
        return 0.0
    placeholders = ",".join(["?"] * len(job_uids))
    try:
        conn = db_module.connect_db()
        db_module.apply_migrations(conn)
        row = conn.execute(
            f"SELECT SUM(COALESCE(total_cost, 0)) AS total FROM jobs WHERE job_uid IN ({placeholders})",
            job_uids,
        ).fetchone()
        if not row:
            return 0.0
        return float(row["total"] or 0.0)
    except Exception:
        return 0.0


def _recalc_jobs_sql(job_uids: list[str], compute_fn) -> int:
    if not job_uids:
        return 0
    placeholders = ",".join(["?"] * len(job_uids))
    updated = 0
    try:
        conn = db_module.connect_db()
        db_module.apply_migrations(conn)
        rows = conn.execute(
            f"""
            SELECT
                j.job_uid,
                p.name AS printer,
                j.duration_seconds,
                j.filament_mm,
                j.paused_seconds_total
            FROM jobs j
            JOIN printers p ON j.printer_id = p.id
            WHERE j.job_uid IN ({placeholders})
            """,
            job_uids,
        ).fetchall()

        now_iso = datetime.now(timezone.utc).isoformat()
        for row in rows:
            job_uid = str(row["job_uid"] or "").strip()
            if not job_uid:
                continue
            printer = str(row["printer"] or "").strip()
            try:
                duration_seconds = float(row["duration_seconds"] or 0.0)
            except Exception:
                duration_seconds = 0.0
            try:
                filament_mm = float(row["filament_mm"] or 0.0)
            except Exception:
                filament_mm = 0.0
            try:
                paused_seconds_total = float(row["paused_seconds_total"] or 0.0)
            except Exception:
                paused_seconds_total = 0.0

            computed = compute_fn(printer, duration_seconds, filament_mm, paused_seconds_total) or {}
            if not computed:
                continue

            conn.execute(
                """
                UPDATE jobs
                   SET duration_hours = ?,
                       filament_meters = ?,
                       rate_per_hour = ?,
                       filament_mode = ?,
                       filament_rate = ?,
                       grams_per_meter = ?,
                       time_cost = ?,
                       material_cost = ?,
                       total_cost = ?,
                       filament_profile_id = ?,
                       filament_material = ?,
                       updated_at = ?
                 WHERE job_uid = ?
                """,
                (
                    computed.get("duration_hours"),
                    computed.get("filament_meters"),
                    computed.get("rate_per_hour"),
                    computed.get("filament_mode"),
                    computed.get("filament_rate"),
                    computed.get("grams_per_meter"),
                    computed.get("time_cost"),
                    computed.get("material_cost"),
                    computed.get("total_cost"),
                    computed.get("filament_profile_id"),
                    computed.get("filament_material"),
                    now_iso,
                    job_uid,
                ),
            )
            updated += 1

        conn.commit()
    except Exception as exc:
        app.logger.warning("SQL recalc failed: %s", exc)
    if updated:
        app.logger.info("SQL recalc updated %s job(s).", updated)
    return updated


def _mark_completed_jobs_sql(job_uids: list[str]) -> int:
    if not job_uids:
        return 0
    placeholders = ",".join(["?"] * len(job_uids))
    try:
        conn = db_module.connect_db()
        db_module.apply_migrations(conn)
        now_iso = datetime.now(timezone.utc).isoformat()
        cur = conn.execute(
            f"UPDATE jobs SET status = 'completed', failure_reason = NULL, updated_at = ? "
            f"WHERE job_uid IN ({placeholders}) AND status = 'printing'",
            [now_iso, *job_uids],
        )
        conn.commit()
        return int(cur.rowcount or 0)
    except Exception:
        return 0


def _delete_jobs_sql(job_uids: list[str]) -> int:
    if not job_uids:
        return 0
    placeholders = ",".join(["?"] * len(job_uids))
    try:
        conn = db_module.connect_db()
        db_module.apply_migrations(conn)
        conn.execute(
            f"DELETE FROM project_assignments WHERE job_uid IN ({placeholders})",
            job_uids,
        )
        cur = conn.execute(
            f"DELETE FROM jobs WHERE job_uid IN ({placeholders})",
            job_uids,
        )
        conn.commit()
        return int(cur.rowcount or 0)
    except Exception:
        return 0


@app.route("/recalculate", methods=["GET"], endpoint="recalculate_page")
def recalculate_page():
    """
    Recalculate Center (Phase 1): select historical jobs by job_uid and rerun pricing.

    Data safety:
      - Never deletes rows
      - Never changes job_uid / printer / filename / timestamps
      - Only rewrites computed pricing fields (same behavior as existing bulk recalc)
    """
    rows, error = _load_history_rows_for_recalc()
    message = request.args.get("msg", "").strip()

    filtered, start_dt, end_dt = _filter_history_rows_for_recalc(rows, request.args)
    filtered_total = len(filtered)

    project_id = (request.args.get("project") or "").strip()
    project_name = ""
    project_options = []
    try:
        projects_map = projects.load_projects()
        project_options = [{"id": pid, "name": p.name} for pid, p in projects_map.items()]
        project_options.sort(key=lambda x: (x.get("name") or "").lower())
        if project_id:
            project = projects_map.get(project_id)
            project_name = project.name if project else ""
    except Exception:
        project_options = []
        project_name = ""

    per_page = _parse_int(request.args.get("per_page"), 25)
    if per_page not in (10, 25, 50, 100):
        per_page = 25
    page = max(1, _parse_int(request.args.get("page"), 1))

    pages = max(1, (filtered_total + per_page - 1) // per_page)
    page = min(page, pages)

    start_idx = (page - 1) * per_page
    end_idx = start_idx + per_page
    rows_page = filtered[start_idx:end_idx]

    canonical_printers = sorted(get_canonical_printer_names(include_hidden=True))
    filament_profiles = profiles.get_all_profiles()
    rate_profiles = rates.list_rate_profiles()
    display_settings = load_display_settings(DISPLAY_FILE, HEADERS)
    recalc_visible_cols = get_visible_columns_for_table(
        display_settings,
        "recalc_jobs",
        ["printer", "filename", "status", "hours", "total", "job_uid"],
    )
    return render_template(
        "recalculate.html",
        error=error,
        message=message,
        project_id=project_id,
        project_name=project_name,
        project_options=project_options,
        printers=canonical_printers,
        filament_profiles=filament_profiles,
        rate_profiles=rate_profiles,
        selected_printer=request.args.get("printer", "All"),
        q=request.args.get("q", "").strip(),
        status=request.args.get("status", "All"),
        recompute_mode=request.args.get("recompute_mode", "pricing_only"),
        apply_filament_profile=request.args.get("apply_filament_profile", "") == "1",
        apply_rate_profile=request.args.get("apply_rate_profile", "") == "1",
        filament_profile_id=request.args.get("filament_profile_id", "").strip(),
        rate_profile_id=request.args.get("rate_profile_id", "").strip(),
        rate_per_hour_override=request.args.get("rate_per_hour_override", "").strip(),
        filament_rate_per_meter_override=request.args.get("filament_rate_per_meter_override", "").strip(),
        quick_range=request.args.get("quick_range", "").strip(),
        start_date=start_dt.strftime("%Y-%m-%d") if start_dt else "",
        end_date=end_dt.strftime("%Y-%m-%d") if end_dt else "",
        rows_page=rows_page,
        selected_job_uids=set(),
        select_filtered=False,
        preview=None,
        recalc_visible_cols=recalc_visible_cols,
        pager={
            "page": page,
            "per_page": per_page,
            "total": filtered_total,
            "pages": pages,
            "has_prev": page > 1,
            "has_next": page < pages,
        },
    )


@app.route("/recalculate/run", methods=["POST"], endpoint="recalculate_run")
def recalculate_run():
    """Run a bulk recalc for selected job_uids (Phase 1)."""
    def _parse_optional_nonneg_float(raw):
        raw = (raw or "").strip()
        if not raw:
            return None, None
        try:
            value = float(raw)
            if value < 0:
                raise ValueError()
            return value, None
        except Exception:
            return None, "Invalid value (must be a non-negative number)."

    def _build_compute_fn(
        apply_rate_profile,
        apply_filament_profile,
        rate_profile_id,
        filament_profile_id,
        rate_per_hour_override_raw,
        filament_rate_per_meter_override_raw,
    ):
        if apply_rate_profile:
            if not rate_profile_id:
                return None, "Select a rate profile (or uncheck Apply hourly rate profile).", None
            if not rates.get_rate_profile(rate_profile_id):
                return None, f"Rate profile not found: {rate_profile_id}", None

        if apply_filament_profile:
            if not filament_profile_id:
                return None, "Select a filament profile (or uncheck Apply filament profile).", None
            if not profiles.get_profile(filament_profile_id):
                return None, f"Filament profile not found: {filament_profile_id}", None

        rate_per_hour_override, err = _parse_optional_nonneg_float(rate_per_hour_override_raw)
        if err:
            return None, "Invalid hourly rate override (must be a non-negative number).", None

        filament_rate_per_meter_override, err = _parse_optional_nonneg_float(filament_rate_per_meter_override_raw)
        if err:
            return None, "Invalid filament $/meter override (must be a non-negative number).", None

        plan = {
            "apply_rate_profile": bool(apply_rate_profile),
            "apply_filament_profile": bool(apply_filament_profile),
            "rate_profile_id": rate_profile_id if apply_rate_profile else None,
            "filament_profile_id": filament_profile_id if apply_filament_profile else None,
            "rate_per_hour_override": rate_per_hour_override,
            "filament_rate_per_meter_override": filament_rate_per_meter_override,
        }

        if apply_rate_profile or apply_filament_profile or rate_per_hour_override is not None or filament_rate_per_meter_override is not None:
            from core.pricing import compute_costs_with_overrides

            def compute_fn(p, d, f, paused_seconds_total=0.0):
                return compute_costs_with_overrides(
                    p,
                    d,
                    f,
                    paused_seconds_total,
                    filament_profile_id=filament_profile_id if apply_filament_profile else None,
                    rate_profile_id=rate_profile_id if apply_rate_profile else None,
                    rate_per_hour_override=rate_per_hour_override,
                    filament_rate_per_meter_override=filament_rate_per_meter_override,
                )

            return compute_fn, None, plan

        return compute_costs, None, plan

    def _append_recalc_audit_log(record):
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            path = os.path.join(DATA_DIR, "recalc_runs.jsonl")
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as e:
            app.logger.warning("Failed to write recalc audit log: %s", e)

    select_filtered = (request.form.get("select_filtered") or "").strip() == "1"
    recompute_mode = (request.form.get("recompute_mode") or "pricing_only").strip()
    apply_rate_profile = (request.form.get("apply_rate_profile") or "").strip() == "1"
    apply_filament_profile = (request.form.get("apply_filament_profile") or "").strip() == "1"
    rate_profile_id = (request.form.get("rate_profile_id") or "").strip()
    filament_profile_id = (request.form.get("filament_profile_id") or "").strip()
    rate_per_hour_override_raw = (request.form.get("rate_per_hour_override") or "").strip()
    filament_rate_per_meter_override_raw = (request.form.get("filament_rate_per_meter_override") or "").strip()

    rows, error = _load_history_rows_for_recalc()
    if error:
        return redirect(url_for("recalculate_page", msg=f"Error loading history: {error}"))

    if recompute_mode not in ("pricing_only", "full"):
        recompute_mode = "pricing_only"

    if recompute_mode == "full":
        return redirect(url_for("recalculate_page", msg="Full recompute is not supported yet; use pricing-only."))

    compute_fn, plan_err, plan = _build_compute_fn(
        apply_rate_profile,
        apply_filament_profile,
        rate_profile_id,
        filament_profile_id,
        rate_per_hour_override_raw,
        filament_rate_per_meter_override_raw,
    )
    if plan_err:
        return redirect(url_for("recalculate_page", msg=plan_err))

    existing_uids = {str(r.get("job_uid") or "").strip() for r in (rows or []) if str(r.get("job_uid") or "").strip()}

    if select_filtered:
        filtered, _start_dt, _end_dt = _filter_history_rows_for_recalc(rows, request.form)
        requested_uids = {str(r.get("job_uid") or "").strip() for r in filtered if str(r.get("job_uid") or "").strip()}
    else:
        requested_uids = {str(u or "").strip() for u in request.form.getlist("job_uids") if str(u or "").strip()}

    missing = {u for u in requested_uids if u not in existing_uids}
    to_update = [u for u in requested_uids if u in existing_uids]

    confirm = (request.form.get("confirm") or "").strip().upper()
    if len(to_update) > RECALC_CONFIRM_THRESHOLD and confirm != "RECALC":
        redirect_params = {}
        for key in (
            "project",
            "printer",
            "q",
            "status",
            "start_date",
            "end_date",
            "quick_range",
            "per_page",
            "page",
            "recompute_mode",
            "apply_filament_profile",
            "apply_rate_profile",
            "filament_profile_id",
            "rate_profile_id",
            "rate_per_hour_override",
            "filament_rate_per_meter_override",
        ):
            v = (request.form.get(key) or "").strip()
            if v:
                redirect_params[key] = v
        redirect_params["msg"] = f"Confirm large run: type RECALC to recalculate {len(to_update)} jobs."
        return redirect(url_for("recalculate_page", **redirect_params))

    updated = 0
    if to_update:
        before_total = 0.0
        for r in rows:
            uid = str(r.get("job_uid") or "").strip()
            if uid and uid in to_update:
                try:
                    before_total += float(r.get("total_cost") or 0.0)
                except Exception:
                    continue

        if _is_sql_only():
            updated = _recalc_jobs_sql(to_update, compute_fn)
            after_total = _sum_total_cost_sql(to_update)
        else:
            updated = storage_backend.recalc_jobs(to_update, compute_fn)

            after_rows, after_error = load_rows_raw(CSV_FILE)
            after_total = 0.0
            if not after_error:
                for r in (after_rows or []):
                    uid = str(r.get("job_uid") or "").strip()
                    if uid and uid in to_update:
                        try:
                            after_total += float(r.get("total_cost") or 0.0)
                        except Exception:
                            continue

        uids_sorted = sorted(to_update)
        uids_hash = hashlib.sha256(("|".join(uids_sorted)).encode("utf-8")).hexdigest()
        record = {
            "timestamp": datetime.now(TIMEZONE_OBJ).isoformat(),
            "count_requested": len(requested_uids),
            "count_updated": int(updated),
            "count_skipped_missing": int(len(missing)),
            "select_filtered": bool(select_filtered),
            "recompute_mode": "pricing_only",
            "plan": plan,
            "job_uids_count": len(to_update),
            "job_uids_hash": uids_hash,
            "job_uids_sample": uids_sorted[:20],
            "totals": {
                "before": before_total,
                "after": after_total,
                "delta": after_total - before_total,
            },
        }
        _append_recalc_audit_log(record)

    skipped = len(missing)

    # Preserve current filters on redirect.
    redirect_params = {}
    for key in (
        "project",
        "printer",
        "q",
        "status",
        "start_date",
        "end_date",
        "quick_range",
        "per_page",
        "page",
        "recompute_mode",
        "apply_filament_profile",
        "apply_rate_profile",
        "filament_profile_id",
        "rate_profile_id",
        "rate_per_hour_override",
        "filament_rate_per_meter_override",
    ):
        v = (request.form.get(key) or "").strip()
        if v:
            redirect_params[key] = v

    msg = f"Recalculated costs for {updated} job(s)."
    if skipped:
        msg += f" Skipped {skipped} missing job(s)."
    redirect_params["msg"] = msg

    return redirect(url_for("recalculate_page", **redirect_params))


@app.route("/recalculate/preview", methods=["POST"], endpoint="recalculate_preview")
def recalculate_preview():
    """Preview a bulk recalc without writing any history (Phase 3)."""

    def _parse_optional_nonneg_float(raw):
        raw = (raw or "").strip()
        if not raw:
            return None, None
        try:
            value = float(raw)
            if value < 0:
                raise ValueError()
            return value, None
        except Exception:
            return None, "Invalid value (must be a non-negative number)."

    def _build_compute_fn(
        apply_rate_profile,
        apply_filament_profile,
        rate_profile_id,
        filament_profile_id,
        rate_per_hour_override_raw,
        filament_rate_per_meter_override_raw,
    ):
        if apply_rate_profile:
            if not rate_profile_id:
                return None, "Select a rate profile (or uncheck Apply hourly rate profile)."
            if not rates.get_rate_profile(rate_profile_id):
                return None, f"Rate profile not found: {rate_profile_id}"

        if apply_filament_profile:
            if not filament_profile_id:
                return None, "Select a filament profile (or uncheck Apply filament profile)."
            if not profiles.get_profile(filament_profile_id):
                return None, f"Filament profile not found: {filament_profile_id}"

        rate_per_hour_override, err = _parse_optional_nonneg_float(rate_per_hour_override_raw)
        if err:
            return None, "Invalid hourly rate override (must be a non-negative number)."

        filament_rate_per_meter_override, err = _parse_optional_nonneg_float(filament_rate_per_meter_override_raw)
        if err:
            return None, "Invalid filament $/meter override (must be a non-negative number)."

        plan = {
            "apply_rate_profile": bool(apply_rate_profile),
            "apply_filament_profile": bool(apply_filament_profile),
            "rate_profile_id": rate_profile_id if apply_rate_profile else None,
            "filament_profile_id": filament_profile_id if apply_filament_profile else None,
            "rate_per_hour_override": rate_per_hour_override,
            "filament_rate_per_meter_override": filament_rate_per_meter_override,
        }

        if apply_rate_profile or apply_filament_profile or rate_per_hour_override is not None or filament_rate_per_meter_override is not None:
            from core.pricing import compute_costs_with_overrides

            def compute_fn(p, d, f, paused_seconds_total=0.0):
                return compute_costs_with_overrides(
                    p,
                    d,
                    f,
                    paused_seconds_total,
                    filament_profile_id=filament_profile_id if apply_filament_profile else None,
                    rate_profile_id=rate_profile_id if apply_rate_profile else None,
                    rate_per_hour_override=rate_per_hour_override,
                    filament_rate_per_meter_override=filament_rate_per_meter_override,
                )

            return compute_fn, plan

        return compute_costs, plan

    select_filtered = (request.form.get("select_filtered") or "").strip() == "1"
    recompute_mode = (request.form.get("recompute_mode") or "pricing_only").strip()
    apply_rate_profile = (request.form.get("apply_rate_profile") or "").strip() == "1"
    apply_filament_profile = (request.form.get("apply_filament_profile") or "").strip() == "1"
    rate_profile_id = (request.form.get("rate_profile_id") or "").strip()
    filament_profile_id = (request.form.get("filament_profile_id") or "").strip()
    rate_per_hour_override_raw = (request.form.get("rate_per_hour_override") or "").strip()
    filament_rate_per_meter_override_raw = (request.form.get("filament_rate_per_meter_override") or "").strip()

    rows, error = _load_history_rows_for_recalc()
    if error:
        return redirect(url_for("recalculate_page", msg=f"Error loading history: {error}"))

    if recompute_mode not in ("pricing_only", "full"):
        recompute_mode = "pricing_only"
    if recompute_mode == "full":
        return redirect(url_for("recalculate_page", msg="Full recompute is not supported yet; use pricing-only."))

    compute_fn, plan_or_err = _build_compute_fn(
        apply_rate_profile,
        apply_filament_profile,
        rate_profile_id,
        filament_profile_id,
        rate_per_hour_override_raw,
        filament_rate_per_meter_override_raw,
    )
    if compute_fn is None:
        return redirect(url_for("recalculate_page", msg=plan_or_err))
    plan = plan_or_err

    filtered, start_dt, end_dt = _filter_history_rows_for_recalc(rows, request.form)
    filtered_total = len(filtered)

    per_page = _parse_int(request.form.get("per_page"), 25)
    if per_page not in (10, 25, 50, 100):
        per_page = 25
    page = max(1, _parse_int(request.form.get("page"), 1))

    pages = max(1, (filtered_total + per_page - 1) // per_page)
    page = min(page, pages)

    start_idx = (page - 1) * per_page
    end_idx = start_idx + per_page
    rows_page = filtered[start_idx:end_idx]

    existing_uids = {str(r.get("job_uid") or "").strip() for r in (rows or []) if str(r.get("job_uid") or "").strip()}
    if select_filtered:
        requested_uids = {str(r.get("job_uid") or "").strip() for r in filtered if str(r.get("job_uid") or "").strip()}
    else:
        requested_uids = {str(u or "").strip() for u in request.form.getlist("job_uids") if str(u or "").strip()}

    missing = {u for u in requested_uids if u not in existing_uids}
    to_preview = {u for u in requested_uids if u in existing_uids}

    preview_rows = []
    before_total = 0.0
    after_total = 0.0
    for r in filtered:
        uid = str(r.get("job_uid") or "").strip()
        if not uid or uid not in to_preview:
            continue

        printer_name = str(r.get("printer") or "")
        try:
            duration_seconds = float(r.get("duration_seconds") or 0.0)
        except Exception:
            duration_seconds = 0.0
        try:
            filament_mm = float(r.get("filament_mm") or 0.0)
        except Exception:
            filament_mm = 0.0
        try:
            old_total = float(r.get("total_cost") or 0.0)
        except Exception:
            old_total = 0.0

        computed = compute_fn(printer_name, duration_seconds, filament_mm) or {}
        try:
            new_total = float(computed.get("total_cost") or 0.0)
        except Exception:
            new_total = 0.0

        before_total += old_total
        after_total += new_total
        preview_rows.append(
            {
                "job_uid": uid,
                "printer": printer_name,
                "filename": str(r.get("filename") or ""),
                "old_total": old_total,
                "new_total": new_total,
                "delta": new_total - old_total,
            }
        )

    canonical_printers = sorted(get_canonical_printer_names(include_hidden=True))
    filament_profiles = profiles.get_all_profiles()
    rate_profiles = rates.list_rate_profiles()
    display_settings = load_display_settings(DISPLAY_FILE, HEADERS)
    recalc_visible_cols = get_visible_columns_for_table(
        display_settings,
        "recalc_jobs",
        ["printer", "filename", "status", "hours", "total", "job_uid"],
    )

    msg = f"Previewing {len(preview_rows)} job(s)."
    if missing:
        msg += f" {len(missing)} selected job(s) were missing from history."

    project_id = (request.form.get("project") or "").strip()
    project_name = ""
    project_options = []
    try:
        projects_map = projects.load_projects()
        project_options = [{"id": pid, "name": p.name} for pid, p in projects_map.items()]
        project_options.sort(key=lambda x: (x.get("name") or "").lower())
        if project_id:
            project = projects_map.get(project_id)
            project_name = project.name if project else ""
    except Exception:
        project_options = []
        project_name = ""

    return render_template(
        "recalculate.html",
        error=error,
        message=msg,
        project_id=project_id,
        project_name=project_name,
        project_options=project_options,
        printers=canonical_printers,
        filament_profiles=filament_profiles,
        rate_profiles=rate_profiles,
        selected_printer=request.form.get("printer", "All"),
        q=request.form.get("q", "").strip(),
        status=request.form.get("status", "All"),
        recompute_mode="pricing_only",
        apply_filament_profile=apply_filament_profile,
        apply_rate_profile=apply_rate_profile,
        filament_profile_id=filament_profile_id,
        rate_profile_id=rate_profile_id,
        rate_per_hour_override=rate_per_hour_override_raw,
        filament_rate_per_meter_override=filament_rate_per_meter_override_raw,
        quick_range=request.form.get("quick_range", "").strip(),
        start_date=start_dt.strftime("%Y-%m-%d") if start_dt else "",
        end_date=end_dt.strftime("%Y-%m-%d") if end_dt else "",
        rows_page=rows_page,
        selected_job_uids=to_preview,
        select_filtered=select_filtered,
        recalc_visible_cols=recalc_visible_cols,
        preview={
            "rows": preview_rows,
            "totals": {"before": before_total, "after": after_total, "delta": after_total - before_total},
            "plan": plan,
        },
        pager={
            "page": page,
            "per_page": per_page,
            "total": filtered_total,
            "pages": pages,
            "has_prev": page > 1,
            "has_next": page < pages,
        },
    )


@app.route("/projects", methods=["GET", "POST"])
def projects_page():
    """
    Projects page:
    - Projects are stored in data/projects.json
    - Job membership is stored in data/project_assignments.json (job_uid -> project_id)
    - Deleting a project unassigns jobs (no CSV rows are deleted)
    """
    error = None

    if request.method == "POST":
        action = (request.form.get("action") or "").strip()
        redirect_args = {}
        try:
            if action == "update_projects_display":
                display_settings = load_display_settings(DISPLAY_FILE, HEADERS)
                display_settings["projects_show_cost_totals"] = bool(
                    request.form.get("projects_show_cost_totals")
                )
                save_display_settings(DISPLAY_FILE, DATA_DIR, display_settings)
                redirect_args = {"msg": "Projects display preferences updated."}

            if action == "create_project":
                name = request.form.get("name", "").strip()
                notes = request.form.get("notes", "").strip()
                hourly_rate_override = request.form.get("hourly_rate_override", "").strip()
                filament_cost_per_kg_override = request.form.get("filament_cost_per_kg_override", "").strip()
                labor_only = bool(request.form.get("labor_only"))
                projects.create_project(
                    name=name,
                    notes=notes,
                    hourly_rate_override=hourly_rate_override,
                    filament_cost_per_kg_override=filament_cost_per_kg_override,
                    labor_only=labor_only,
                )

            elif action == "update_project":
                project_id = request.form.get("project_id", "").strip()
                name = request.form.get("name", "").strip()
                notes = request.form.get("notes", "").strip()
                hourly_rate_override = request.form.get("hourly_rate_override", "").strip()
                filament_cost_per_kg_override = request.form.get("filament_cost_per_kg_override", "").strip()
                labor_only = bool(request.form.get("labor_only"))
                projects.update_project(
                    project_id=project_id,
                    name=name,
                    notes=notes,
                    hourly_rate_override=hourly_rate_override,
                    filament_cost_per_kg_override=filament_cost_per_kg_override,
                    labor_only=labor_only,
                )

            elif action == "delete_project":
                project_id = request.form.get("project_id", "").strip()
                project_name = ""
                try:
                    project_obj = projects.load_projects().get(project_id)
                    project_name = project_obj.name if project_obj else ""
                except Exception:
                    project_name = ""
                projects.delete_project(project_id)
                system_events.emit_event(
                    "deleted",
                    "Project deleted",
                    f"Project{(' ' + repr(project_name)) if project_name else ''} was deleted. Tracked jobs were unassigned (history was not deleted).",
                    meta={"action": "delete_project", "project_id": project_id, "project_name": project_name},
                )

            elif action == "assign_jobs":
                project_id = request.form.get("project_id", "").strip()
                job_ids = request.form.getlist("job_uid") or request.form.getlist("job_key")
                if project_id and job_ids:
                    projects.assign_jobs(job_ids, project_id=project_id)

            elif action == "unassign_job":
                job_id = (request.form.get("job_uid") or request.form.get("job_key") or "").strip()
                if job_id:
                    projects.unassign_jobs([job_id])

            elif action == "unassign_selected":
                job_ids = request.form.getlist("job_uid") or request.form.getlist("job_key")
                if job_ids:
                    projects.unassign_jobs(job_ids)

            elif action == "recalc_costs":
                job_ids = request.form.getlist("job_uid") or request.form.getlist("job_key")
                if _is_sql_only():
                    updated = _recalc_jobs_sql(job_ids, compute_costs)
                else:
                    updated = storage_backend.recalc_jobs(job_ids, compute_costs)
                redirect_args = {"msg": f"Recalculated costs for {updated} job(s)."}

            elif action == "create_manual_job":
                project_id = request.form.get("project_id", "").strip()
                title = request.form.get("title", "").strip()
                hours = request.form.get("hours", "").strip()
                filament_g = request.form.get("filament_g", "").strip()
                cost_override = request.form.get("cost_override", "").strip()
                notes = request.form.get("notes", "").strip()
                mj = projects.create_manual_job(
                    project_id=project_id,
                    title=title,
                    hours=hours,
                    filament_g=filament_g,
                    cost_override=cost_override,
                    notes=notes,
                )
                redirect_args = {"msg": "Manual job added.", "edit_project": project_id}

            elif action == "update_manual_job":
                manual_job_id = request.form.get("manual_job_id", "").strip()
                project_id = request.form.get("project_id", "").strip()
                title = request.form.get("title", "").strip()
                hours = request.form.get("hours", "").strip()
                filament_g = request.form.get("filament_g", "").strip()
                cost_override = request.form.get("cost_override", "").strip()
                notes = request.form.get("notes", "").strip()
                projects.update_manual_job(
                    manual_job_id=manual_job_id,
                    title=title,
                    hours=hours,
                    filament_g=filament_g,
                    cost_override=cost_override,
                    notes=notes,
                )
                redirect_args = {"msg": "Manual job updated.", "edit_project": project_id, "edit_manual_job_id": manual_job_id}

            elif action == "delete_manual_job":
                manual_job_id = request.form.get("manual_job_id", "").strip()
                projects.delete_manual_job(manual_job_id)
                redirect_args = {"msg": "Manual job deleted."}

            elif action == "upload_plan_gcode":
                project_id = request.form.get("project_id", "").strip()
                manual_time_hours = request.form.get("manual_time_hours", "").strip()
                manual_filament_g = request.form.get("manual_filament_g", "").strip()
                manual_cost = request.form.get("manual_cost", "").strip()
                file = request.files.get("gcode_file")
                if not project_id:
                    raise ValueError("Project is required")
                if not file or not file.filename:
                    raise ValueError("Please choose a .gcode file to upload.")

                original_name = os.path.basename(file.filename)
                if not original_name.lower().endswith(".gcode"):
                    raise ValueError("Only .gcode files are supported.")

                # Basic guardrail (request size)
                max_bytes = 150 * 1024 * 1024  # 150MB
                if request.content_length and request.content_length > max_bytes:
                    raise ValueError("Upload too large. Please upload a smaller .gcode file (max 150MB).")

                tmp_path = None
                try:
                    safe_name = secure_filename(original_name) or "upload.gcode"
                    with tempfile.NamedTemporaryFile(prefix="kcd_plan_", suffix="_" + safe_name, delete=False) as tf:
                        tmp_path = tf.name
                        total = 0
                        while True:
                            chunk = file.stream.read(1024 * 1024)
                            if not chunk:
                                break
                            total += len(chunk)
                            if total > max_bytes:
                                raise ValueError("Upload too large. Please upload a smaller .gcode file (max 150MB).")
                            tf.write(chunk)

                    meta = extract_gcode_metadata(tmp_path)
                    est_time_s = int(meta.time_s or 0)
                    est_filament_g = meta.filament_g
                    source = meta.slicer or ""

                    # If slicer metadata is missing/incomplete, allow manual overrides.
                    if not meta.found or not est_time_s:
                        if manual_time_hours:
                            try:
                                est_time_s = int(float(manual_time_hours) * 3600.0)
                            except (TypeError, ValueError):
                                est_time_s = 0
                            source = "Manual"
                        if not est_time_s:
                            raise ValueError(meta.error or "No slicer metadata found (missing estimated time).")

                    if est_filament_g is None and manual_filament_g:
                        try:
                            est_filament_g = float(manual_filament_g)
                        except (TypeError, ValueError):
                            est_filament_g = None

                    est_cost_override = None
                    if manual_cost:
                        try:
                            est_cost_override = float(manual_cost)
                        except (TypeError, ValueError):
                            est_cost_override = None

                    projects.create_plan_item(
                        project_id=project_id,
                        filename=original_name,
                        est_time_s=est_time_s,
                        est_filament_g=est_filament_g,
                        source=source,
                        est_cost_override=est_cost_override,
                    )
                    msg = "Planned item added."
                    if meta.found and meta.filament_g is None and not manual_filament_g:
                        msg = "Planned item added. Filament grams not found; use Edit to fill it in."
                    if not meta.found:
                        msg = "Planned item added using manual estimates."
                    redirect_args = {"msg": msg, "edit_project": project_id}
                finally:
                    if tmp_path and os.path.exists(tmp_path):
                        try:
                            os.remove(tmp_path)
                        except Exception:
                            pass

            elif action == "update_plan_item":
                plan_id = request.form.get("plan_id", "").strip()
                project_id = request.form.get("project_id", "").strip()
                filename = request.form.get("filename", "").strip()
                time_hours = request.form.get("time_hours", "").strip()
                filament_g = request.form.get("filament_g", "").strip()
                est_cost = request.form.get("est_cost", "").strip()
                source = request.form.get("source", "").strip()
                notes = request.form.get("notes", "").strip()

                try:
                    est_time_s = int(float(time_hours) * 3600.0)
                except (TypeError, ValueError):
                    est_time_s = 0

                filament_val = None
                if filament_g != "":
                    try:
                        filament_val = float(filament_g)
                    except (TypeError, ValueError):
                        filament_val = None

                cost_val = None
                if est_cost != "":
                    try:
                        cost_val = float(est_cost)
                    except (TypeError, ValueError):
                        cost_val = None

                projects.update_plan_item(
                    plan_id,
                    filename=filename,
                    est_time_s=est_time_s,
                    est_filament_g=filament_val,
                    est_cost=cost_val,
                    source=source,
                    notes=notes,
                )
                redirect_args = {"msg": "Planned item updated.", "edit_project": project_id}

            elif action == "fulfill_plan_item":
                plan_id = request.form.get("plan_id", "").strip()
                if plan_id:
                    projects.set_plan_status(plan_id, "fulfilled")

            elif action == "delete_plan_item":
                plan_id = request.form.get("plan_id", "").strip()
                if plan_id:
                    projects.delete_plan_item(plan_id)

            elif action == "convert_plan_to_manual":
                project_id = request.form.get("project_id", "").strip()
                plan_id = request.form.get("plan_id", "").strip()
                mj = projects.convert_plan_item_to_manual(project_id=project_id, plan_id=plan_id)
                redirect_args = {
                    "msg": "Converted planned item to manual job.",
                    "edit_project": project_id,
                    "edit_manual_job_id": mj.manual_job_id,
                }

            # Always cleanup after any mutation
            projects.recalculate_all()
        except projects.ProjectsDataError as e:
            error = str(e)
        except ValueError as e:
            error = str(e)
        except Exception as e:
            error = f"Unexpected error: {e}"

        if error:
            return redirect(url_for("projects_page", error=error))
        return redirect(url_for("projects_page", **redirect_args))

    # GET
    history_query = history_repo.HistoryQuery()
    history_result = history_repo.list_history_rows(history_query, page=1, per_page=1)
    rows = history_result.rows_all
    if history_result.error:
        error = history_result.error

    error = request.args.get("error") or error
    message = request.args.get("msg", "").strip()
    edit_project = request.args.get("edit_project", "").strip()
    edit_manual_job_id = request.args.get("edit_manual_job_id", "").strip() or request.args.get("edit_manual", "").strip()
    orphans_added = 0

    try:
        # Ensure legacy assignment keys are migrated to stable job_uids.
        _, orphans_added = projects.migrate_assignments_to_job_uid(rows)

        projects_map, assignments, manual_jobs_by_project, plans_by_project = projects.recalculate_all()
        project_jobs, unassigned_jobs = projects.group_rows_by_project(rows)
    except projects.ProjectsDataError as e:
        return render_template(
            "projects.html",
            error=str(e),
            projects=[],
            unassigned_jobs=[],
            projects_by_id={},
        )

    display_settings = load_display_settings(DISPLAY_FILE, HEADERS)

    if orphans_added:
        orphan_msg = (
            f"Cleaned up {orphans_added} orphaned legacy assignment entr"
            f"{'y' if orphans_added == 1 else 'ies'} (saved to project_assignments_orphans.json)."
        )
        message = f"{message} {orphan_msg}".strip() if message else orphan_msg

    # Build view models (derived totals always computed from current membership)
    project_rows = []
    for pid, p in projects_map.items():
        jobs = project_jobs.get(pid, [])
        manual_jobs = manual_jobs_by_project.get(pid, [])
        totals = projects.compute_project_totals(jobs, manual_jobs=manual_jobs, project=p)
        plans = plans_by_project.get(pid, [])
        projection = projects.compute_project_projection(plans, project=p)

        manual_jobs_view = []
        for mj in manual_jobs:
            manual_jobs_view.append(
                {
                    "manual_job_id": mj.manual_job_id,
                    "title": mj.title,
                    "hours": mj.hours,
                    "filament_g": mj.filament_g,
                    "cost_override": mj.cost_override,
                    "computed_cost": projects.compute_manual_job_cost(mj, project=p),
                    "created_at": mj.created_at,
                    "updated_at": mj.updated_at,
                    "notes": mj.notes,
                }
            )
        manual_jobs_view.sort(key=lambda x: x.get("created_at") or "", reverse=True)

        # Tracked (CSV) actual cost only (manual jobs are shown separately in totals).
        tracked_cost = 0.0
        for r in jobs:
            try:
                tracked_cost += float(r.get("total_cost") or 0.0)
            except (TypeError, ValueError):
                pass
        manual_cost = max(float(totals.get("cost") or 0.0) - tracked_cost, 0.0)

        plans_view = []
        for pl in sorted(plans, key=lambda x: x.created_at or "", reverse=True):
            effective_cost = projects.compute_planned_item_cost(pl, p)
            plans_view.append(
                {
                    "plan_id": pl.plan_id,
                    "filename": pl.filename,
                    "created_at": pl.created_at,
                    "est_time_s": pl.est_time_s,
                    "est_filament_g": pl.est_filament_g,
                    "est_cost": effective_cost,
                    "status": pl.status,
                    "source": pl.source,
                    "converted_to_manual_job_id": pl.converted_to_manual_job_id,
                    "est_cost_is_override": pl.est_cost_is_override,
                    "notes": pl.notes,
                }
            )

        project_rows.append(
            {
                "id": pid,
                "name": p.name,
                "notes": p.notes,
                "hourly_rate_override": p.hourly_rate_override,
                "filament_cost_per_kg_override": p.filament_cost_per_kg_override,
                "labor_only": bool(p.labor_only),
                "created_at": p.created_at,
                "updated_at": p.updated_at,
                "totals": totals,
                "jobs": sorted(jobs, key=lambda r: float(r.get("timestamp_raw") or 0), reverse=True),
                "manual_jobs": manual_jobs_view,
                "planned_items": plans_view,
                "projected": projection,
                "actual_tracked_cost": tracked_cost,
                "actual_manual_cost": manual_cost,
            }
        )

    project_rows.sort(key=lambda x: (x.get("updated_at") or 0.0, x.get("name") or ""), reverse=True)

    # Sort unassigned by timestamp, newest first
    unassigned_jobs_sorted = sorted(unassigned_jobs, key=lambda r: float(r.get("timestamp_raw") or 0), reverse=True)

    unassigned_per_page = _parse_per_page(request.args.get("unassigned_per_page"), default=25)
    unassigned_jobs_page, unassigned_pager = _paginate(
        unassigned_jobs_sorted,
        request.args.get("unassigned_page", 1),
        unassigned_per_page,
    )
    unassigned_pager = _pager_links(
        endpoint="projects_page",
        args_dict=request.args.to_dict(flat=True),
        page_key="unassigned_page",
        per_page_key="unassigned_per_page",
        pager_meta=unassigned_pager,
    )

    # Project thumbnails (respects thumbnails_enabled only).
    settings = load_settings(SETTINGS_FILE)
    for j in unassigned_jobs_page:
        pname = str(j.get("printer") or "").strip()
        fname = str(j.get("filename") or "").strip()
        if not pname:
            j["_thumbs_enabled"] = False
            j["_thumb_small"] = None
            j["_thumb_unavailable"] = False
            continue
        printer_cfg = settings.get(pname, {}) if isinstance(settings, dict) else {}
        thumbs_enabled = printer_cfg.get("thumbnails_enabled", True) is not False
        j["_thumbs_enabled"] = bool(thumbs_enabled)
        token = str(j.get("thumbnail") or "").strip()
        if thumbs_enabled and token:
            j["_thumb_small"] = build_thumb_url_from_token(pname, token, size_hint="small")
            j["_thumb_unavailable"] = False
        elif thumbs_enabled and fname:
            thumb_url = get_job_thumbnail_url(
                pname,
                fname,
                size_hint="small",
                job_uid=str(j.get("job_uid") or "").strip() or None,
            )
            j["_thumb_small"] = thumb_url
            j["_thumb_unavailable"] = bool(not thumb_url)
        else:
            j["_thumb_small"] = None
            j["_thumb_unavailable"] = False

    for p in project_rows:
        for j in p.get("jobs", []) or []:
            pname = str(j.get("printer") or "").strip()
            fname = str(j.get("filename") or "").strip()
            if not pname:
                j["_thumbs_enabled"] = False
                j["_thumb_small"] = None
                j["_thumb_unavailable"] = False
                continue
            printer_cfg = settings.get(pname, {}) if isinstance(settings, dict) else {}
            thumbs_enabled = printer_cfg.get("thumbnails_enabled", True) is not False
            j["_thumbs_enabled"] = bool(thumbs_enabled)
            token = str(j.get("thumbnail") or "").strip()
            if thumbs_enabled and token:
                j["_thumb_small"] = build_thumb_url_from_token(pname, token, size_hint="small")
                j["_thumb_unavailable"] = False
            elif thumbs_enabled and fname:
                thumb_url = get_job_thumbnail_url(
                    pname,
                    fname,
                    size_hint="small",
                    job_uid=str(j.get("job_uid") or "").strip() or None,
                )
                j["_thumb_small"] = thumb_url
                j["_thumb_unavailable"] = bool(not thumb_url)
            else:
                j["_thumb_small"] = None
                j["_thumb_unavailable"] = False

    # Server-backed per-table column visibility (Settings → Other).
    projects_unassigned_visible_cols = get_visible_columns_for_table(
        display_settings,
        "projects_unassigned",
        ["thumbnail", "date", "printer", "filename", "status", "hours", "filament", "cost"],
    )
    projects_project_jobs_visible_cols = get_visible_columns_for_table(
        display_settings,
        "projects_project_jobs",
        ["date", "thumbnail", "printer", "filename", "status", "hours", "filament", "cost"],
    )

    return render_template(
        "projects.html",
        error=error,
        message=message,
        edit_project=edit_project,
        edit_manual_job_id=edit_manual_job_id,
        display_settings=display_settings,
        projects_unassigned_visible_cols=projects_unassigned_visible_cols,
        projects_project_jobs_visible_cols=projects_project_jobs_visible_cols,
        projects=project_rows,
        unassigned_jobs=unassigned_jobs_sorted,
        unassigned_jobs_page=unassigned_jobs_page,
        unassigned_pager=unassigned_pager,
        projects_by_id={p["id"]: p for p in project_rows},
    )


@app.route("/settings", methods=["GET", "POST"])
def settings_page():
    """Back-compat settings entrypoint (redirects to /settings/printers)."""
    if request.method == "GET":
        return redirect(url_for("settings_printers_page"))
    # For legacy POSTs, process the action but redirect to the appropriate sub-page.
    return _settings_view(tab="printers")


def _settings_endpoint_for_action(action: str) -> str:
    other_actions = {"update_columns", "backup_now", "update_backup_settings"}
    pause_actions = {"update_pause_settings"}
    profiles_actions = {
        "add_filament_profile",
        "update_filament_profile",
        "delete_filament_profile",
        "add_rate_profile",
        "update_rate_profile",
        "delete_rate_profile",
    }
    if action in other_actions:
        return "settings_other_page"
    if action in pause_actions:
        return "settings_pause_page"
    if action in profiles_actions:
        return "settings_profiles_page"
    return "settings_printers_page"


def _settings_view(tab: str):
    tab = (tab or "printers").strip().lower()
    if tab not in {"printers", "profiles", "other", "pause"}:
        tab = "printers"

    if request.method == "POST":
        action = (request.form.get("action") or "").strip()

        if action == "backup_now":
            try:
                bs = load_backup_settings()
                path = create_backup_archive(keep=bs.auto_backup_keep)
                return redirect(
                    url_for(
                        _settings_endpoint_for_action(action),
                        msg=f"Backup created: data/backups/{os.path.basename(path)}",
                    )
                )
            except Exception as e:
                return redirect(url_for(_settings_endpoint_for_action(action), error=f"Backup failed: {e}"))

        if action == "update_backup_settings":
            enabled = bool(request.form.get("auto_backup_enabled"))
            freq = (request.form.get("auto_backup_frequency") or "daily").strip().lower()
            keep_raw = (request.form.get("auto_backup_keep") or "7").strip()
            try:
                keep = int(keep_raw)
            except Exception:
                keep = 7
            keep = max(1, min(100, keep))
            save_backup_settings(
                auto_backup_enabled=enabled,
                auto_backup_frequency=freq,
                auto_backup_keep=keep,
            )
            return redirect(url_for(_settings_endpoint_for_action(action), msg="Backup settings saved."))

        if action == "delete_printer":
            printer = request.form.get("printer", "").strip()
            if printer:
                pricing.delete_printer(printer, delete_csv=False)
                # Soft-delete: hide from Settings/dashboard lists even if CSV history exists.
                pricing.hide_printer(printer)
                system_events.emit_event(
                    "deleted",
                    "Printer removed",
                    f"Printer {printer!r} was removed from Settings. History rows were kept.",
                    meta={"action": "delete_printer", "printer": printer},
                )
            return redirect(url_for(_settings_endpoint_for_action(action)))

        if action == "update_pause_settings":
            # Global default is stored in display.json (display settings).
            global_include = bool(request.form.get("pause_include_paused_time_default"))
            display_settings = load_display_settings(DISPLAY_FILE, HEADERS)
            display_settings["pause_include_paused_time_default"] = bool(global_include)
            save_display_settings(DISPLAY_FILE, DATA_DIR, display_settings)

            # Per-printer overrides are stored alongside printer settings in settings.json.
            printers = pricing.get_configured_printers()
            settings = load_settings(SETTINGS_FILE)
            if not isinstance(settings, dict):
                settings = {}

            for p in printers:
                if p not in settings or not isinstance(settings.get(p), dict):
                    settings[p] = {}
                use_global = bool(request.form.get(f"pause_use_global_{p}"))
                if use_global:
                    settings[p].pop("pause_include_paused_time_override_enabled", None)
                    settings[p].pop("pause_include_paused_time_override_value", None)
                    # Backwards compat cleanup
                    settings[p].pop("pause_exclude_paused_time_override_enabled", None)
                    settings[p].pop("pause_exclude_paused_time_override_value", None)
                else:
                    settings[p]["pause_include_paused_time_override_enabled"] = True
                    settings[p]["pause_include_paused_time_override_value"] = bool(
                        request.form.get(f"pause_include_paused_time_{p}")
                    )
                    # Backwards compat cleanup
                    settings[p].pop("pause_exclude_paused_time_override_enabled", None)
                    settings[p].pop("pause_exclude_paused_time_override_value", None)

            save_settings(SETTINGS_FILE, DATA_DIR, settings)
            return redirect(url_for(_settings_endpoint_for_action(action), msg="Pause accounting settings saved."))

        if action == "save_printer_defaults":
            printer = request.form.get("printer")
            if printer:
                settings = load_settings(SETTINGS_FILE)
                if printer not in settings:
                    settings[printer] = {}

                try:
                    settings[printer]["rate_per_hour"] = float(request.form.get("rate_per_hour", 1.0))
                    settings[printer]["filament_mode"] = request.form.get("filament_mode", "per_meter")
                    settings[printer]["filament_rate"] = float(request.form.get("filament_rate", 0.25))
                    settings[printer]["grams_per_meter"] = float(request.form.get("grams_per_meter", 3.0))
                except (TypeError, ValueError):
                    pass

                # Per-printer thumbnail display settings (unchecked checkboxes are absent => False).
                enabled = bool(request.form.get(f"thumbnails_enabled_{printer}"))
                on_cards = bool(request.form.get(f"thumbnails_on_cards_{printer}"))
                settings[printer]["thumbnails_enabled"] = bool(enabled)
                settings[printer]["thumbnails_on_cards"] = bool(on_cards and enabled)

                save_settings(SETTINGS_FILE, DATA_DIR, settings)

            return redirect(url_for(_settings_endpoint_for_action(action)))

        if action == "test_moonraker":
            printer = (request.form.get("printer") or "").strip()
            if not printer:
                return redirect(url_for(_settings_endpoint_for_action(action), error="Missing printer name."))

            base_url = thumbs.resolve_moonraker_base_url(printer)
            if not base_url:
                return redirect(
                    url_for(
                        _settings_endpoint_for_action(action),
                        error=(
                            f"No Moonraker URL configured for {printer}. "
                            "Set settings.json moonraker_url for this printer, then retry."
                        ),
                    )
                )

            ok, detail, _payload = test_moonraker_url(base_url)
            if ok:
                return redirect(url_for(_settings_endpoint_for_action(action), msg=f"Moonraker OK for {printer}: {base_url}"))
            return redirect(url_for(_settings_endpoint_for_action(action), error=f"Moonraker test failed for {printer}: {detail} ({base_url})"))

        if action == "import_moonraker_history":
            if _is_sql_only():
                return redirect(
                    url_for(
                        _settings_endpoint_for_action(action),
                        error="Moonraker history import is disabled in SQL-only mode. Use CSV/dual mode for imports.",
                    )
                )
            printer = (request.form.get("printer") or "").strip()
            if not printer:
                return redirect(url_for(_settings_endpoint_for_action(action), error="Missing printer name."))

            base_url = thumbs.resolve_moonraker_base_url(printer)
            if not base_url:
                return redirect(
                    url_for(
                        _settings_endpoint_for_action(action),
                        error=(
                            f"No Moonraker URL configured for {printer}. "
                            "Set settings.json moonraker_url for this printer, then retry."
                        ),
                    )
                )

            limit_raw = (request.form.get("import_limit") or "200").strip().lower()
            limit = None
            if limit_raw not in ("", "all"):
                try:
                    limit = int(limit_raw)
                except Exception:
                    limit = 200
                limit = max(1, min(5000, limit))

            skip_existing = bool(request.form.get("import_skip_existing"))
            overwrite_existing = bool(request.form.get("import_overwrite_existing"))
            if overwrite_existing:
                skip_existing = False

            summary = import_moonraker_history_to_csv(
                csv_file=CSV_FILE,
                headers=HEADERS,
                printer_name=printer,
                base_url=base_url,
                limit=limit,
                skip_existing=skip_existing,
                overwrite_existing=overwrite_existing,
            )

            counts = (
                f"imported={summary.get('imported', 0)}, "
                f"skipped={summary.get('skipped', 0)}, "
                f"updated={summary.get('updated', 0)}, "
                f"errors={summary.get('errors', 0)}"
            )

            if summary.get("errors"):
                err = summary.get("error") or "One or more entries failed to import."
                return redirect(url_for(_settings_endpoint_for_action(action), error=f"Moonraker import finished for {printer}: {counts}. {err}"))

            return redirect(url_for(_settings_endpoint_for_action(action), msg=f"Moonraker import complete for {printer}: {counts}"))

        if action == "update_columns":
            table_id = (request.form.get("table") or "history").strip()
            cols = request.form.getlist("columns")
            display_settings = load_display_settings(DISPLAY_FILE, HEADERS)

            if table_id == "history":
                allowed = [h for h in HEADERS if h != "job_uid"]
            elif table_id == "recalc_jobs":
                allowed = ["printer", "filename", "status", "hours", "total", "job_uid"]
            elif table_id == "projects_unassigned":
                allowed = ["thumbnail", "date", "printer", "filename", "status", "hours", "filament", "cost"]
            elif table_id == "projects_project_jobs":
                allowed = ["date", "thumbnail", "printer", "filename", "status", "hours", "filament", "cost"]
            else:
                allowed = [h for h in HEADERS if h != "job_uid"]
                table_id = "history"

            selected = [c for c in cols if c in allowed]
            if not selected:
                selected = list(allowed)

            display_settings = set_visible_columns_for_table(display_settings, table_id, selected)
            save_display_settings(DISPLAY_FILE, DATA_DIR, display_settings)
            return redirect(url_for(_settings_endpoint_for_action(action)))

        if action == "rename_printer":
            old = request.form.get("old_name", "").strip()
            new = request.form.get("new_name", "").strip()
            if old and new:
                rename_printer(old, new)
            return redirect(url_for(_settings_endpoint_for_action(action)))

        if action == "merge_printers":
            primary = request.form.get("primary", "").strip()
            secondary = request.form.get("secondary", "").strip()
            if primary and secondary:
                merge_printers(primary, secondary)
            return redirect(url_for(_settings_endpoint_for_action(action)))

        if action == "add_filament_profile":
            name = request.form.get("profile_name", "").strip()
            material = request.form.get("material", "").strip()
            brand = request.form.get("brand", "").strip()
            color = request.form.get("color", "").strip()
            mode = request.form.get("filament_mode", "per_meter").strip()
            description = request.form.get("description", "").strip()
            try:
                rate = float(request.form.get("filament_rate", 0.25))
            except (TypeError, ValueError):
                rate = None
            try:
                gpm = float(request.form.get("grams_per_meter", 3.0))
            except (TypeError, ValueError):
                gpm = None
            if name and rate is not None:
                profile = {
                    "name": name,
                    "material": material,
                    "brand": brand,
                    "color": color,
                    "filament_mode": mode,
                    "filament_rate": rate,
                    "grams_per_meter": gpm,
                    "description": description,
                }
                profiles.add_profile(profile)
            return redirect(url_for(_settings_endpoint_for_action(action)))

        if action == "update_filament_profile":
            profile_id = request.form.get("profile_id", "").strip()
            if profile_id:
                updates = {}
                name = request.form.get("profile_name", "").strip()
                material = request.form.get("material", "").strip()
                brand = request.form.get("brand", "").strip()
                color = request.form.get("color", "").strip()
                mode = request.form.get("filament_mode", "").strip()
                if name:
                    updates["name"] = name
                updates["material"] = material
                updates["brand"] = brand
                updates["color"] = color
                if mode:
                    updates["filament_mode"] = mode
                try:
                    updates["filament_rate"] = float(request.form.get("filament_rate"))
                except (TypeError, ValueError):
                    updates["filament_rate"] = None
                try:
                    updates["grams_per_meter"] = float(request.form.get("grams_per_meter"))
                except (TypeError, ValueError):
                    updates["grams_per_meter"] = None
                profiles.update_profile(profile_id, updates)
            return redirect(url_for(_settings_endpoint_for_action(action)))

        if action == "delete_filament_profile":
            profile_id = request.form.get("profile_id", "").strip()
            if profile_id:
                profile_obj = None
                try:
                    profile_obj = profiles.get_profile(profile_id)
                except Exception:
                    profile_obj = None
                profiles.delete_profile(profile_id)
                profile_name = ""
                try:
                    profile_name = str((profile_obj or {}).get("name") or "").strip()
                except Exception:
                    profile_name = ""
                system_events.emit_event(
                    "deleted",
                    "Filament profile deleted",
                    f"Filament profile{(' ' + repr(profile_name)) if profile_name else ''} was deleted.",
                    meta={"action": "delete_filament_profile", "profile_id": profile_id, "profile_name": profile_name},
                )
            return redirect(url_for(_settings_endpoint_for_action(action)))

        if action == "set_active_filament_profile":
            printer = request.form.get("printer", "").strip()
            profile_id = request.form.get("profile_id", "").strip()
            if printer:
                profiles.set_printer_active_profile(printer, profile_id)
            return redirect(url_for(_settings_endpoint_for_action(action)))

        if action == "add_rate_profile":
            name = request.form.get("rate_profile_name", "").strip()
            description = request.form.get("rate_profile_description", "").strip()
            try:
                rate_per_hour = float(request.form.get("rate_profile_rate", 1.0))
            except (TypeError, ValueError):
                rate_per_hour = None

            if name and rate_per_hour is not None:
                rate_profile = {
                    "name": name,
                    "description": description,
                    "rate_per_hour": rate_per_hour,
                }
                rates.upsert_rate_profile(rate_profile)
            return redirect(url_for(_settings_endpoint_for_action(action)))

        if action == "update_rate_profile":
            profile_id = request.form.get("rate_profile_id", "").strip()
            if profile_id:
                updates = {}
                name = request.form.get("rate_profile_name", "").strip()
                description = request.form.get("rate_profile_description", "").strip()
                if name:
                    updates["name"] = name
                updates["description"] = description
                try:
                    updates["rate_per_hour"] = float(request.form.get("rate_profile_rate"))
                except (TypeError, ValueError):
                    updates["rate_per_hour"] = None
                rates.update_rate_profile(profile_id, updates)
            return redirect(url_for(_settings_endpoint_for_action(action)))

        if action == "delete_rate_profile":
            profile_id = request.form.get("rate_profile_id", "").strip()
            if profile_id:
                rate_obj = None
                try:
                    rate_obj = rates.get_rate_profile(profile_id)
                except Exception:
                    rate_obj = None
                rates.delete_rate_profile(profile_id)
                rate_name = ""
                try:
                    rate_name = str((rate_obj or {}).get("name") or "").strip()
                except Exception:
                    rate_name = ""
                system_events.emit_event(
                    "deleted",
                    "Hourly rate profile deleted",
                    f"Hourly rate profile{(' ' + repr(rate_name)) if rate_name else ''} was deleted.",
                    meta={"action": "delete_rate_profile", "rate_profile_id": profile_id, "rate_profile_name": rate_name},
                )
            return redirect(url_for(_settings_endpoint_for_action(action)))

        if action == "set_active_rate_profile":
            printer = request.form.get("printer", "").strip()
            profile_id = request.form.get("rate_profile_id", "").strip()
            if printer:
                settings = load_settings(SETTINGS_FILE)
                if printer not in settings:
                    settings[printer] = {}
                if profile_id == "" or profile_id == "none":
                    settings[printer].pop("active_rate_profile_id", None)
                else:
                    settings[printer]["active_rate_profile_id"] = profile_id
                save_settings(SETTINGS_FILE, DATA_DIR, settings)
            return redirect(url_for(_settings_endpoint_for_action(action)))

        return redirect(url_for(_settings_endpoint_for_action(action) if action else _settings_endpoint_for_action(tab)))

    # GET request
    message = request.args.get("msg", "").strip()
    error = request.args.get("error", "").strip()

    printers = pricing.get_configured_printers()
    discovered_printers = pricing.get_discovered_printers()
    settings = load_settings(SETTINGS_FILE)

    printer_configs = {}
    for p in printers:
        printer_configs[p] = get_pricing_for_printer_raw(p)

    active_rate_profiles = {}
    for p in printers:
        active_rate_profiles[p] = settings.get(p, {}).get("active_rate_profile_id", "")

    display_settings = load_display_settings(DISPLAY_FILE, HEADERS)
    selected_columns = display_settings.get("visible_columns") or HEADERS

    # Settings → Other: per-table column visibility (server-backed display.json).
    # Defaults: show all allowed columns if no saved selection exists.
    recalc_allowed_cols = ["printer", "filename", "status", "hours", "total", "job_uid"]
    projects_unassigned_allowed_cols = ["thumbnail", "date", "printer", "filename", "status", "hours", "filament", "cost"]
    projects_project_jobs_allowed_cols = ["date", "thumbnail", "printer", "filename", "status", "hours", "filament", "cost"]

    recalc_selected_columns = get_visible_columns_for_table(display_settings, "recalc_jobs", recalc_allowed_cols)
    projects_unassigned_selected_columns = get_visible_columns_for_table(
        display_settings, "projects_unassigned", projects_unassigned_allowed_cols
    )
    projects_project_jobs_selected_columns = get_visible_columns_for_table(
        display_settings, "projects_project_jobs", projects_project_jobs_allowed_cols
    )

    # Load profile data
    all_profiles = profiles.get_all_profiles()
    printer_mappings = profiles.get_all_printer_mappings()
    rate_profiles = rates.list_rate_profiles()
    backup_settings = load_backup_settings()

    template_by_tab = {
        "printers": "settings/printers.html",
        "profiles": "settings/profiles.html",
        "other": "settings/other.html",
        "pause": "settings/pause.html",
    }
    subtitle_by_tab = {
        "printers": "Manage printers connected to KCD.",
        "profiles": "Rates and material costs used for calculations.",
        "other": "Display preferences, backups, and exports.",
        "pause": "Pause accounting preferences for hourly time cost.",
    }

    return render_template(
        template_by_tab[tab],
        page_title="Print Cost Settings",
        settings_tab=tab,
        settings_subtitle=subtitle_by_tab[tab],
        sql_only=_is_sql_only(),
        message=message,
        error=error,
        printers=printers,
        discovered_printers=discovered_printers,
        configs=printer_configs,
        printer_settings=settings,
        headers=[h for h in HEADERS if h != "job_uid"],
        friendly_headers=FRIENDLY_HEADERS,
        selected_columns=selected_columns,
        recalc_allowed_cols=recalc_allowed_cols,
        recalc_selected_columns=recalc_selected_columns,
        projects_unassigned_allowed_cols=projects_unassigned_allowed_cols,
        projects_unassigned_selected_columns=projects_unassigned_selected_columns,
        projects_project_jobs_allowed_cols=projects_project_jobs_allowed_cols,
        projects_project_jobs_selected_columns=projects_project_jobs_selected_columns,
        display_settings=display_settings,
        profiles=all_profiles,
        printer_mappings=printer_mappings,
        rate_profiles=rate_profiles,
        active_rate_profiles=active_rate_profiles,
        backup_settings=backup_settings,
    )

@app.route("/settings/printers", methods=["GET", "POST"], endpoint="settings_printers_page")
def settings_printers_page():
    return _settings_view(tab="printers")


@app.route("/settings/profiles", methods=["GET", "POST"], endpoint="settings_profiles_page")
def settings_profiles_page():
    return _settings_view(tab="profiles")


@app.route("/settings/other", methods=["GET", "POST"], endpoint="settings_other_page")
def settings_other_page():
    return _settings_view(tab="other")


@app.route("/printer-diagnostics", methods=["GET"])
def printer_diagnostics_page():
    printers = sorted(get_known_printers())
    results = []
    for pname in printers:
        base_url = thumbs.resolve_moonraker_base_url(pname)
        if base_url:
            probe = probe_moonraker_server_info(base_url)
        else:
            probe = {
                "ok": False,
                "status_code": None,
                "content_type": "",
                "body_preview": "",
                "error": "Missing Moonraker URL",
                "payload": None,
            }
        results.append(
            {
                "printer": pname,
                "moonraker_url": base_url or "",
                "ok": bool(probe.get("ok")),
                "status_code": probe.get("status_code"),
                "content_type": probe.get("content_type") or "",
                "error": probe.get("error") or "",
                "body_preview": probe.get("body_preview") or "",
                "api_key_configured": bool(API_KEY),
            }
        )

    return render_template(
        "printer_diagnostics.html",
        page_title="Printer Diagnostics",
        results=results,
    )

@app.route("/settings/pause", methods=["GET", "POST"], endpoint="settings_pause_page")
def settings_pause_page():
    return _settings_view(tab="pause")


@app.route("/download-csv")
def download_csv():
    """Download CSV file."""
    import os
    if not os.path.exists(CSV_FILE):
        return "No data available", 404
    return send_file(CSV_FILE, as_attachment=True, download_name="print_costs.csv")


# ============================================================================
# APPLICATION STARTUP
# ============================================================================

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
