"""
Print Cost Dashboard - Flask Application

Refactored to use modular core package.
"""
import os
import tempfile
import uuid
from flask import Flask, request, jsonify, render_template, redirect, url_for, send_file
from werkzeug.utils import secure_filename
from core.config import (
    API_KEY, CSV_FILE, HEADERS, FRIENDLY_HEADERS, PRINTER_COLORS,
    DEFAULT_PRICING, SETTINGS_FILE, DISPLAY_FILE, DATA_DIR, TIMEZONE_OBJ
)
from core.storage import (
    load_settings, save_settings, load_display_settings, save_display_settings,
    append_row, load_rows_raw,
    rewrite_csv_without_indices, rewrite_csv_mark_completed,
    rewrite_csv_without_job_uids, rewrite_csv_mark_completed_job_uids,
    rewrite_csv_recalculate_costs_job_uids,
    ts_to_local_dt
)
from core.pricing import (
    compute_costs, get_known_printers, rename_printer, merge_printers,
    get_pricing_for_printer_raw
)
from core.reports import (
    get_date_range_from_params, compute_monthly_breakdown,
    compute_top_printers, compute_summary,
    aggregate_by_material, aggregate_by_profile
)
from core import profiles
from core import rates
from core import pricing
from core import live
from core import projects
from core.gcode_metadata import extract_gcode_metadata
from core.printers import (
    get_canonical_printer_names,
    normalize_incoming_printer_and_filename,
    looks_like_gcode_filename,
)
from core.backup import load_backup_settings, save_backup_settings, create_backup_archive, maybe_run_auto_backup

app = Flask(__name__)


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
    if live_metadata and live_metadata.get("filename") == filename:
        live_job = live.get_job(printer_name)
        if live_job:
            live_status = str(live_job.get("status") or "").strip().lower()
            if live_status in ("canceled", "cancelled"):
                status = "canceled"
            elif live_status == "failed":
                status = "failed"
            failure_reason = str(live_job.get("failure_reason") or "").strip()

    cost_data = compute_costs(printer_name, duration_seconds, filament_mm)

    row = {
        "timestamp": ts,
        "job_uid": str(uuid.uuid4()),
        "printer": printer_name,
        "filename": filename,
        "duration_seconds": duration_seconds,
        "filament_mm": filament_mm,
    }
    row.update(cost_data)
    
    # Add status and failure_reason
    row["status"] = status
    row["failure_reason"] = failure_reason

    append_row(CSV_FILE, HEADERS, row)

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
        return jsonify({"success": False, "error": norm.reason}), 400

    printer_name = norm.printer_name
    filename = norm.filename

    if not printer_name or not filename:
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
        return jsonify({"success": False, "error": reason}), 400

    if printer_name not in canonical:
        reason = f"Unknown printer_name received: {printer_name!r}"
        app.logger.warning(reason)
        app.logger.warning("Allowed printers: %s", sorted(canonical))
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
    
    result = live.pause_job(printer_name)
    
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
        return jsonify({"success": False, "error": reason}), 400

    if printer_name not in canonical:
        reason = f"Unknown printer_name received: {printer_name!r}"
        app.logger.warning(reason)
        app.logger.warning("Allowed printers: %s", sorted(canonical))
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
    printer_name = data.get("printer_name")
    reason = data.get("reason")  # Optional failure/cancel reason
    
    if not printer_name:
        return jsonify({"success": False, "error": "Missing required field: printer_name"}), 400

    canonical = get_canonical_printer_names()
    if looks_like_gcode_filename(printer_name):
        error = f"Rejected printer_name because it looks like a gcode filename: {printer_name!r}"
        app.logger.warning(error)
        return jsonify({"success": False, "error": error}), 400

    if printer_name not in canonical:
        error = f"Unknown printer_name received: {printer_name!r}"
        app.logger.warning(error)
        app.logger.warning("Allowed printers: %s", sorted(canonical))
        return jsonify({"success": False, "error": error}), 400
    
    result = live.cancel_job(printer_name, reason)
    
    if result is None:
        return jsonify({"success": False, "error": "Job not found"}), 404
    
    job = live.get_job(printer_name)
    return jsonify({"success": True, "job": job})


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

            updated = rewrite_csv_recalculate_costs_job_uids(CSV_FILE, HEADERS, job_uids, compute_costs)
            return redirect(url_for("index", msg=f"Recalculated costs for {updated} job(s)."))

        # Handle row deletion
        if action in ("delete_rows", "delete"):
            selected = request.form.getlist("delete_rows")
            if selected:
                if all(str(v).strip().isdigit() for v in selected):
                    indices = [int(i) for i in selected if str(i).strip().isdigit()]
                    rewrite_csv_without_indices(CSV_FILE, HEADERS, indices)
                else:
                    job_uids = [str(v).strip() for v in selected if str(v).strip()]
                    rewrite_csv_without_job_uids(CSV_FILE, HEADERS, job_uids)
            return redirect(url_for("index"))

    rows, error = load_rows_raw(CSV_FILE)
    message = request.args.get("msg", "").strip()
    
    # Apply date filtering
    start_dt, end_dt, range_label, quick_range = get_date_range_from_params(request.args)
    
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

    return render_template(
        "index.html",
        rows=rows,
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
        printers=get_known_printers(),
        selected_printer=request.args.get("printer", "All"),
        start_date=start_dt.strftime("%Y-%m-%d") if start_dt else "",
        end_date=end_dt.strftime("%Y-%m-%d") if end_dt else "",
        csv_file=CSV_FILE,
        active_jobs=active_jobs,
        printer_summaries=printer_summaries,
    )


@app.route("/reports")
def reports_page():
    """Reports page with monthly breakdown and top printers."""
    rows, error = load_rows_raw(CSV_FILE)
    
    # Apply date filtering
    start_dt, end_dt, range_label, quick_range = get_date_range_from_params(request.args)
    
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
    
    # Aggregate by material and profile
    material_summary = aggregate_by_material(rows)
    
    # Load profiles for profile aggregation
    all_profiles = profiles.get_all_profiles()
    profile_summary = aggregate_by_profile(rows, all_profiles)

    return render_template(
        "reports.html",
        monthly_breakdown=monthly,
        top_printers=top_printers,
        summary=summary,
        material_summary=material_summary,
        profile_summary=profile_summary,
        range_label=range_label,
        quick_range=quick_range,
        start_date=request.args.get("start_date", ""),
        end_date=request.args.get("end_date", ""),
        error=error,
    )


def _filter_history_rows_for_recalc(rows, args):
    printer = (args.get("printer") or "").strip()
    q = (args.get("q") or "").strip().lower()
    status = (args.get("status") or "").strip().lower()

    start_dt, end_dt, _range_label, _quick_range = get_date_range_from_params(args)

    filtered = []
    for r in rows:
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


@app.route("/recalculate", methods=["GET"], endpoint="recalculate_page")
def recalculate_page():
    """
    Recalculate Center (Phase 1): select historical jobs by job_uid and rerun pricing.

    Data safety:
      - Never deletes rows
      - Never changes job_uid / printer / filename / timestamps
      - Only rewrites computed pricing fields (same behavior as existing bulk recalc)
    """
    rows, error = load_rows_raw(CSV_FILE)
    message = request.args.get("msg", "").strip()

    filtered, start_dt, end_dt = _filter_history_rows_for_recalc(rows, request.args)
    filtered_total = len(filtered)

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
    return render_template(
        "recalculate.html",
        error=error,
        message=message,
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
        quick_range=request.args.get("quick_range", "").strip(),
        start_date=start_dt.strftime("%Y-%m-%d") if start_dt else "",
        end_date=end_dt.strftime("%Y-%m-%d") if end_dt else "",
        rows_page=rows_page,
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
    select_filtered = (request.form.get("select_filtered") or "").strip() == "1"
    recompute_mode = (request.form.get("recompute_mode") or "pricing_only").strip()
    apply_rate_profile = (request.form.get("apply_rate_profile") or "").strip() == "1"
    apply_filament_profile = (request.form.get("apply_filament_profile") or "").strip() == "1"
    rate_profile_id = (request.form.get("rate_profile_id") or "").strip()
    filament_profile_id = (request.form.get("filament_profile_id") or "").strip()

    rows, error = load_rows_raw(CSV_FILE)
    if error:
        return redirect(url_for("recalculate_page", msg=f"Error loading history: {error}"))

    if recompute_mode not in ("pricing_only", "full"):
        recompute_mode = "pricing_only"

    if recompute_mode == "full":
        return redirect(url_for("recalculate_page", msg="Full recompute is not supported yet; use pricing-only."))

    # Validate plan inputs up-front so we don't partially mutate CSV.
    if apply_rate_profile:
        if not rate_profile_id:
            return redirect(url_for("recalculate_page", msg="Select a rate profile (or uncheck Apply hourly rate profile)."))
        if not rates.get_rate_profile(rate_profile_id):
            return redirect(url_for("recalculate_page", msg=f"Rate profile not found: {rate_profile_id}"))

    if apply_filament_profile:
        if not filament_profile_id:
            return redirect(url_for("recalculate_page", msg="Select a filament profile (or uncheck Apply filament profile)."))
        if not profiles.get_profile(filament_profile_id):
            return redirect(url_for("recalculate_page", msg=f"Filament profile not found: {filament_profile_id}"))

    existing_uids = {str(r.get("job_uid") or "").strip() for r in (rows or []) if str(r.get("job_uid") or "").strip()}

    if select_filtered:
        filtered, _start_dt, _end_dt = _filter_history_rows_for_recalc(rows, request.form)
        requested_uids = {str(r.get("job_uid") or "").strip() for r in filtered if str(r.get("job_uid") or "").strip()}
    else:
        requested_uids = {str(u or "").strip() for u in request.form.getlist("job_uids") if str(u or "").strip()}

    missing = {u for u in requested_uids if u not in existing_uids}
    to_update = [u for u in requested_uids if u in existing_uids]

    updated = 0
    if to_update:
        if apply_rate_profile or apply_filament_profile:
            from core.pricing import compute_costs_with_overrides

            def compute_fn(p, d, f):
                return compute_costs_with_overrides(
                    p,
                    d,
                    f,
                    filament_profile_id=filament_profile_id if apply_filament_profile else None,
                    rate_profile_id=rate_profile_id if apply_rate_profile else None,
                )

            updated = rewrite_csv_recalculate_costs_job_uids(CSV_FILE, HEADERS, to_update, compute_fn)
        else:
            updated = rewrite_csv_recalculate_costs_job_uids(CSV_FILE, HEADERS, to_update, compute_costs)

    skipped = len(missing)

    # Preserve current filters on redirect.
    redirect_params = {}
    for key in (
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
    ):
        v = (request.form.get(key) or "").strip()
        if v:
            redirect_params[key] = v

    msg = f"Recalculated costs for {updated} job(s)."
    if skipped:
        msg += f" Skipped {skipped} missing job(s)."
    redirect_params["msg"] = msg

    return redirect(url_for("recalculate_page", **redirect_params))


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
                projects.delete_project(project_id)

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
                updated = rewrite_csv_recalculate_costs_job_uids(CSV_FILE, HEADERS, job_ids, compute_costs)
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
    rows, rows_error = load_rows_raw(CSV_FILE)
    if rows_error:
        error = rows_error

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

    return render_template(
        "projects.html",
        error=error,
        message=message,
        edit_project=edit_project,
        edit_manual_job_id=edit_manual_job_id,
        projects=project_rows,
        unassigned_jobs=unassigned_jobs_sorted,
        projects_by_id={p["id"]: p for p in project_rows},
    )


@app.route("/settings", methods=["GET", "POST"])
def settings_page():
    """Settings page for printer pricing configuration."""
    if request.method == "POST":
        action = (request.form.get("action") or "").strip()

        if action == "backup_now":
            try:
                bs = load_backup_settings()
                path = create_backup_archive(keep=bs.auto_backup_keep)
                return redirect(
                    url_for(
                        "settings_page",
                        msg=f"Backup created: data/backups/{os.path.basename(path)}",
                    )
                )
            except Exception as e:
                return redirect(url_for("settings_page", error=f"Backup failed: {e}"))

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
            return redirect(url_for("settings_page", msg="Backup settings saved."))

        if action == "delete_printer":
            printer = request.form.get("printer", "").strip()
            if printer:
                pricing.delete_printer(printer, delete_csv=False)
                # Soft-delete: hide from Settings/dashboard lists even if CSV history exists.
                pricing.hide_printer(printer)
            return redirect(url_for("settings_page"))

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
                    save_settings(SETTINGS_FILE, DATA_DIR, settings)
                except ValueError:
                    pass

        elif action == "update_columns":
            selected = request.form.getlist("columns")
            save_display_settings(DISPLAY_FILE, HEADERS, selected)

        elif action == "rename_printer":
            old_name = request.form.get("old_name")
            new_name = request.form.get("new_name")
            if old_name and new_name:
                rename_printer(old_name, new_name, update_csv=True)

        elif action == "merge_printers":
            names_str = request.form.get("printer_names", "")
            merged_name = request.form.get("merged_name", "")
            if names_str and merged_name:
                names = [n.strip() for n in names_str.split(",") if n.strip()]
                if names:
                    merge_printers(names, merged_name)

        elif action == "add_filament_profile":
            name = request.form.get("profile_name", "").strip()
            material = request.form.get("material", "").strip()
            filament_mode = request.form.get("filament_mode", "per_meter")
            brand = request.form.get("brand", "").strip()
            color = request.form.get("color", "").strip()
            
            try:
                filament_rate = float(request.form.get("filament_rate", 0.25))
                grams_per_meter = float(request.form.get("grams_per_meter", 3.0))
                
                if name:  # Only create if name is provided
                    profile_data = {
                        "name": name,
                        "material": material,
                        "brand": brand,
                        "color": color,
                        "filament_mode": filament_mode,
                        "filament_rate": filament_rate,
                        "grams_per_meter": grams_per_meter,
                    }
                    profiles.upsert_profile(profile_data)
            except ValueError:
                pass  # Invalid numeric input, skip

        elif action == "update_filament_profile":
            profile_id = request.form.get("profile_id", "").strip()
            if profile_id:
                existing = profiles.get_profile(profile_id)
                if existing:
                    updates = {}
                    name = request.form.get("profile_name", "").strip()
                    material = request.form.get("material", "").strip()
                    brand = request.form.get("brand", "").strip()
                    color = request.form.get("color", "").strip()
                    filament_mode = request.form.get("filament_mode", "").strip()

                    if name:
                        updates["name"] = name
                    updates["material"] = material
                    updates["brand"] = brand
                    updates["color"] = color
                    if filament_mode:
                        updates["filament_mode"] = filament_mode

                    try:
                        updates["filament_rate"] = float(request.form.get("filament_rate"))
                    except (TypeError, ValueError):
                        updates["filament_rate"] = existing.get("filament_rate")

                    try:
                        updates["grams_per_meter"] = float(request.form.get("grams_per_meter"))
                    except (TypeError, ValueError):
                        updates["grams_per_meter"] = existing.get("grams_per_meter")

                    profiles.update_profile(profile_id, updates)

        elif action == "delete_filament_profile":
            profile_id = request.form.get("profile_id", "").strip()
            if profile_id:
                # Safety check: verify profile exists before deleting
                profile = profiles.get_profile(profile_id)
                if profile:
                    # Check if profile is in use
                    mappings = profiles.get_all_printer_mappings()
                    in_use = any(pid == profile_id for pid in mappings.values())
                    
                    if not in_use:
                        profiles.delete_profile(profile_id)
                    # If in use, silently fail (could add flash message in future)

        elif action == "set_active_filament_profile":
            printer = request.form.get("printer", "").strip()
            profile_id = request.form.get("profile_id", "").strip()
            
            if printer:
                if profile_id == "" or profile_id == "none":
                    # Clear the mapping
                    profiles.set_printer_mapping(printer, None)
                else:
                    # Set the mapping
                    profiles.set_printer_mapping(printer, profile_id)

        elif action == "add_rate_profile":
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

        elif action == "update_rate_profile":
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

        elif action == "delete_rate_profile":
            profile_id = request.form.get("rate_profile_id", "").strip()
            if profile_id:
                rates.delete_rate_profile(profile_id)

        elif action == "set_active_rate_profile":
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

        return redirect(url_for("settings_page"))

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

    # Load profile data
    all_profiles = profiles.get_all_profiles()
    printer_mappings = profiles.get_all_printer_mappings()
    rate_profiles = rates.list_rate_profiles()
    backup_settings = load_backup_settings()

    return render_template(
        "settings.html",
        message=message,
        error=error,
        printers=printers,
        discovered_printers=discovered_printers,
        configs=printer_configs,
        headers=[h for h in HEADERS if h != "job_uid"],
        friendly_headers=FRIENDLY_HEADERS,
        selected_columns=selected_columns,
        profiles=all_profiles,
        printer_mappings=printer_mappings,
        rate_profiles=rate_profiles,
        active_rate_profiles=active_rate_profiles,
        backup_settings=backup_settings,
    )


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
