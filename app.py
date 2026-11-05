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
import hmac
import threading
from functools import wraps
from contextlib import closing
from pathlib import Path
from datetime import datetime, timedelta, timezone
import flask
from importlib.metadata import version
from flask import Flask, abort, request, jsonify, render_template, redirect, url_for, send_file
from werkzeug.utils import secure_filename
from core.application_mode import get_application_mode, set_application_mode
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
from core import thumbnail_archive as archived_thumbs
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
from core.import_moonraker import import_moonraker_history_to_csv, import_moonraker_history_to_sql
from core.gcode_metadata import extract_gcode_metadata
from core.printers import (
    get_canonical_printer_names,
    normalize_incoming_printer_and_filename,
    looks_like_gcode_filename,
)
from core.backup import load_backup_settings, save_backup_settings, create_backup_archive, maybe_run_auto_backup
from core.readiness import (
    check_sql_only_readiness,
    enforce_sql_only_startup_readiness,
    SqlOnlyStartupReadinessError,
)
from core.numeric import finite_float, optional_finite_float, NumericValidationError
from core.demo.flask_integration import install_workspace_runtime
from core.runtime import RuntimeConfigurationError, is_demo_runtime

app = Flask(__name__)
install_workspace_runtime(app)
app.logger.info("Flask version: %s", version("flask"))

if str(os.getenv("KCD_STORAGE_BACKEND", "csv")).strip().lower() == "sql":
    app.logger.warning("SQL-only mode active (KCD_STORAGE_BACKEND=sql)")
if not is_demo_runtime():
    try:
        enforce_sql_only_startup_readiness()
    except SqlOnlyStartupReadinessError as exc:
        app.logger.error(str(exc))
        raise

_ALLOWED_PER_PAGE = (10, 25, 50, 100)
RECALC_CONFIRM_THRESHOLD = 50
PROJECTS_UNASSIGNED_COLUMNS = ["thumbnail", "date", "printer", "filename", "status", "hours", "filament", "cost"]
PROJECTS_TRACKED_COLUMNS = [
    "date",
    "thumbnail",
    "printer",
    "filename",
    "status",
    "hours",
    "filament",
    "time_cost",
    "material_cost",
    "adjustment",
    "total_cost",
]
PROJECTS_MANUAL_COLUMNS = [
    "description",
    "hours",
    "filament_g",
    "time_cost",
    "material_cost",
    "adjustment",
    "total_cost",
    "created",
]
PROJECTS_PLANNED_COLUMNS = [
    "filename",
    "estimated_time",
    "filament_g",
    "estimated_time_cost",
    "estimated_material_cost",
    "estimated_adjustment",
    "estimated_total_cost",
    "status",
    "created",
]


def _get_projects_visible_columns(display_settings, table_id, allowed_columns):
    tables = display_settings.get("tables") if isinstance(display_settings, dict) else None
    config = tables.get(table_id) if isinstance(tables, dict) else None
    saved = config.get("visible_columns") if isinstance(config, dict) else None
    if isinstance(saved, list):
        normalized = ["total_cost" if column == "cost" else column for column in saved]
        selected = [column for column in normalized if column in allowed_columns]
        if selected:
            return selected
    return list(allowed_columns)


def _safe_thumb_dir(name: str) -> str:
    s = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in str(name or "").strip())
    return s or "unknown"


def build_thumb_url_from_token(printer_name: str, token: str, size_hint: str, job_uid=None) -> str | None:
    if job_uid:
        asset = archived_thumbs.get_job_asset(job_uid)
        if asset:
            return url_for('archived_thumbnail', digest=asset)
        if _is_sql_only():
            return url_for('job_thumbnail',job_uid=job_uid)
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
    if is_demo_runtime():
        return None
    if job_uid:
        asset = archived_thumbs.get_job_asset(job_uid)
        if asset:
            return url_for('archived_thumbnail', digest=asset)
        if _is_sql_only():
            return url_for('job_thumbnail',job_uid=job_uid)
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


_thumbnail_recovery_slots=threading.BoundedSemaphore(2)


@app.get('/jobs/<job_uid>/thumbnail')
def job_thumbnail(job_uid):
    """Lazy retrieval keeps remote printer failures out of HTML rendering."""
    asset=archived_thumbs.get_job_asset(job_uid)
    if asset:return archived_thumbnail(asset)
    status=archived_thumbs.get_job_status(job_uid)
    if request.method=='HEAD':
        state=status['status'] if status.get('checked_at') else 'recovery_deferred'
        return ('',404,{'X-KCD-Thumbnail-Status':state,'Cache-Control':'no-store'})
    checked=status.get('checked_at')
    if checked:
        try:
            if time.time()-datetime.fromisoformat(checked.replace('Z','+00:00')).timestamp()<120:
                return ('',404,{'X-KCD-Thumbnail-Status':status['status'],'Cache-Control':'no-store'})
        except ValueError:pass
    with db_module.connect_db() as conn:
        row=conn.execute('SELECT j.*,p.name AS printer FROM jobs j JOIN printers p ON p.id=j.printer_id WHERE j.job_uid=?',(job_uid,)).fetchone()
    if not row:return ('',404)
    ended=None
    try:ended=datetime.fromisoformat(row['ended_at'].replace('Z','+00:00')).timestamp()
    except (AttributeError,ValueError):pass
    asset=archived_thumbs.recover_cached_job(job_uid,row['printer'],row['filename'],row['thumbnail'],ended)
    if not asset:
        if not _thumbnail_recovery_slots.acquire(blocking=False):
            return ('',404,{'X-KCD-Thumbnail-Status':'recovery_deferred','Cache-Control':'no-store'})
        try:
            result=archived_thumbs.capture_job(job_uid,row['printer'],row['filename'],ended_at=ended,historical=True)
            asset=result['asset'];status=result
        finally:_thumbnail_recovery_slots.release()
    if asset:return archived_thumbnail(asset)
    return ('',404,{'X-KCD-Thumbnail-Status':status['status'],'Cache-Control':'no-store'})


@app.get('/thumbnails/<digest>')
def archived_thumbnail(digest):
    result=archived_thumbs.asset_path(digest)
    if not result:
        return ('',404)
    path,mime=result
    response=send_file(path,mimetype=mime,conditional=True)
    response.headers['Cache-Control']='public, max-age=31536000, immutable'
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['X-KCD-Thumbnail-Status']='archived'
    return response


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
def _reject_cross_origin_mutations():
    """Reject browser cross-site writes to the trusted-LAN dashboard."""
    if request.method in {'GET','HEAD','OPTIONS'}:
        return
    from urllib.parse import urlsplit
    origin=request.headers.get('Origin')
    if origin:
        try:parsed=urlsplit(origin)
        except ValueError:return jsonify(error='Invalid request origin'),403
        if parsed.scheme not in {'http','https'} or parsed.netloc.lower()!=request.host.lower():
            return jsonify(error='Cross-origin changes are not allowed'),403
    elif request.headers.get('Sec-Fetch-Site')=='cross-site':
        return jsonify(error='Cross-site changes are not allowed'),403


@app.before_request
def _kcd_auto_backup_hook():
    # Best-effort auto backup. This is intentionally lightweight: it only runs when due.
    if is_demo_runtime() or request.method != "GET":
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


def require_printer_api_key(view_func):
    """Fail closed unless a printer-client request supplies the configured key."""
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        server_key = str(API_KEY or "").strip()
        if not server_key:
            app.logger.error("Printer-client API unavailable: server API key is not configured")
            return jsonify(
                {"status": "error", "error": "Printer-client API key is not configured"}
            ), 503

        supplied_key = str(request.headers.get("X-API-Key", "") or "")
        from core.auth import valid_api_key
        if not valid_api_key(supplied_key, server_key):
            return jsonify({"status": "error", "error": "Unauthorized"}), 403
        if request.method in {"POST", "PUT", "PATCH"} and request.is_json and not isinstance(request.get_json(silent=True), dict):
            return jsonify(error="Expected a JSON object"), 400
        return view_func(*args, **kwargs)

    return wrapped


@app.route("/api/installer/registration", methods=["GET", "POST"])
@require_printer_api_key
def installer_registration():
    from core.registration import register_printer, registered_clients, RegistrationConflict
    if request.method == "GET":
        return jsonify(backend=os.getenv("KCD_STORAGE_BACKEND", "csv"), clients=registered_clients(),
                       readiness=check_sql_only_readiness())
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify(error="Expected a JSON object"), 400
    try:
        if payload.get("action") == "retire":
            name = str(payload.get("printer_name") or "").strip()
            if live.get_job(name):
                return jsonify(error="Printer has an active job; retire only while idle"), 409
            from core.printer_lifecycle import delete_printer
            if _is_sql_only():
                delete_printer(name)
            else:
                pricing.delete_printer(name, delete_csv=False)
            return jsonify(retired=True, printer_name=name, files_retained=True)
        metadata = payload.get("metadata") or {}
        if not isinstance(metadata, dict):
            raise ValueError("Invalid installer metadata")
        config_dir = metadata.get("cfg_dir") or metadata.get("config_dir")
        if config_dir:
            from installer.discovery import probe_endpoint
            probe = probe_endpoint(str(payload.get("moonraker_url") or ""))
            if not probe or probe["config_dir"] != config_dir:
                raise ValueError("Master cannot verify the selected Moonraker config root; check network route and instance port")
        result = register_printer(payload.get("printer_name"), payload.get("moonraker_url"),
                                  metadata=payload.get("metadata"),
                                  relocate=payload.get("relocate") is True,
                                  reactivate=payload.get("reactivate") is True)
        if not _is_sql_only():
            settings = load_settings(SETTINGS_FILE)
            prices = settings.setdefault(result["printer_name"], {})
            for key, value in DEFAULT_PRICING.items():
                prices.setdefault(key, value)
            prices["moonraker_url"] = payload["moonraker_url"]
            save_settings(SETTINGS_FILE, DATA_DIR, settings)
        return jsonify(**result, readiness=check_sql_only_readiness())
    except RegistrationConflict as exc:
        return jsonify(error=str(exc)), 409
    except ValueError as exc:
        return jsonify(error=str(exc)), 400


@app.post("/log-print")
@require_printer_api_key
@live.synchronized
def log_print():
    """API endpoint to log print data from 3D printers."""
    payload = request.get_json(force=True, silent=True)
    if not payload:
        return jsonify({"status": "error", "error": "Invalid or missing JSON"}), 400

    missing = [k for k in ["timestamp", "printer", "filename", "duration_seconds", "filament_mm"] if k not in payload]
    if missing:
        return jsonify({"status": "error", "error": f"Missing fields: {', '.join(missing)}"}), 400

    try:
        ts = finite_float(payload["timestamp"], label="timestamp")
        duration_seconds = finite_float(
            payload["duration_seconds"], label="duration_seconds", nonnegative=True
        )
        filament_mm = finite_float(payload["filament_mm"], label="filament_mm", nonnegative=True)
    except NumericValidationError as exc:
        return jsonify({"status": "error", "error": str(exc)}), 400

    printer_name_raw = str(payload["printer"])
    filename_raw = str(payload["filename"])

    def _choose_duration_seconds(payload_dur, live_dur, mr_print, mr_total, mr_calc):
        def _pos(v):
            try:
                v = finite_float(v, label="duration candidate", nonnegative=True)
            except NumericValidationError:
                return 0.0
            return v if v > 0 else 0.0

        payload_dur = _pos(payload_dur)
        live_dur = _pos(live_dur)
        mr_print = _pos(mr_print)
        mr_total = _pos(mr_total)
        mr_calc = _pos(mr_calc)

        if payload_dur > 0:
            return payload_dur
        if mr_print > 0:
            return mr_print
        if mr_total > 0:
            return mr_total
        if mr_calc > 0:
            return mr_calc
        if live_dur > 0:
            return live_dur
        return None

    payload_duration_seconds = duration_seconds
    live_duration_seconds = None
    mr_print_duration = 0.0
    mr_total_duration = 0.0
    mr_calc_duration = 0.0

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
            live_duration_seconds = max(0.0, elapsed_excluding_pauses + float(paused_seconds_total))
        except Exception:
            live_duration_seconds = None

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
                        return finite_float(v, label="Moonraker numeric value")
                    except NumericValidationError:
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

                if print_time > 0:
                    mr_print_duration = print_time
                if total_duration > 0:
                    mr_total_duration = total_duration
                if start_ts > 0 and end_ts > 0 and end_ts >= start_ts:
                    mr_calc_duration = end_ts - start_ts

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

    duration_seconds = _choose_duration_seconds(
        payload_duration_seconds,
        live_duration_seconds,
        mr_print_duration,
        mr_total_duration,
        mr_calc_duration,
    )
    # Klipper print_duration excludes pauses. New clients label that basis so
    # the stored duration is wall time and compute_costs subtracts pauses once.
    if duration_seconds is not None and payload.get("duration_basis") == "active":
        duration_seconds += paused_seconds_total

    app.logger.info(
        "job-finalize: printer=%s file=%s payload_dur=%s live_dur=%s mr_print=%s mr_total=%s mr_calc=%s chosen=%s",
        printer_name,
        filename,
        payload_duration_seconds,
        live_duration_seconds,
        mr_print_duration,
        mr_total_duration,
        mr_calc_duration,
        duration_seconds,
    )

    cost_data = {}
    if duration_seconds is not None:
        cost_data = compute_costs(printer_name, duration_seconds, filament_mm, paused_seconds_total=paused_seconds_total)

    row_duration = duration_seconds if duration_seconds is not None else ""
    event_id = payload.get("event_id")
    if event_id is not None:
        try:
            event_id = str(uuid.UUID(str(event_id)))
        except ValueError:
            return jsonify(error="event_id must be a UUID"), 400
    identity = ("history:"+str(history_job_id)) if history_job_id is not None else ("event:"+event_id) if event_id else None
    job_uid = str(uuid.uuid5(uuid.NAMESPACE_URL, "kcd:"+printer_name+":"+identity)) if identity else str(uuid.uuid4())
    if _is_sql_only():
        with db_module.connect_db() as conn:
            if conn.execute("SELECT 1 FROM jobs WHERE job_uid=?", (job_uid,)).fetchone():
                if live_metadata and live_metadata.get("filename") == filename and float(live_metadata.get("actual_start_time") or 0) <= float(payload["timestamp"]):
                    live.end_job(printer_name)
                return jsonify(status="ok", duplicate=True)
    row = {
        "timestamp": ts,
        "job_uid": job_uid,
        "printer": printer_name,
        "filename": filename,
        "duration_seconds": row_duration,
        "paused_seconds_total": paused_seconds_total,
        "pause_count": pause_count,
        "runout_count": runout_count,
        "filament_mm": filament_mm,
    }
    row.update(cost_data)
    if live_metadata and live_metadata.get('filename')==filename:
        row['thumbnail_asset']=live_metadata.get('thumbnail_asset')

    # Add status and failure_reason
    row["status"] = status
    row["failure_reason"] = failure_reason
    if row_started_at:
        row["started_at"] = row_started_at
    if row_ended_at:
        row["ended_at"] = row_ended_at

    storage_backend.write_job(row)

    try:
        archived_thumbs.capture_job(row['job_uid'],printer_name,filename,
            live_asset=(live_metadata or {}).get('thumbnail_asset') if (live_metadata or {}).get('filename')==filename else None,
            ended_at=ts)
    except Exception:
        app.logger.warning('Thumbnail retention deferred for job %s',row['job_uid'])

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
    from core.sql_only import SqlOnlyViolationError

    try:
        if _is_sql_only():
            readiness = check_sql_only_readiness()
            db_ok = any(
                check.get("name") == "db_connection" and bool(check.get("ok"))
                for check in readiness.get("checks", [])
            )
            ready = bool(readiness.get("ready"))
            return (
                jsonify(
                    {
                        "status": "ok" if ready else "error",
                        "backend": "sql",
                        "ready": ready,
                        "db_ok": db_ok,
                        "schema_version": readiness.get("schema_version"),
                        "printers_count": readiness.get("printers_count", 0),
                        "checks": readiness.get("checks", []),
                        "errors": readiness.get("errors", []),
                        "timestamp": datetime.now(TIMEZONE_OBJ).isoformat(),
                    }
                ),
                200 if ready else 503,
            )

        status = {
            "status": "healthy",
            "timestamp": datetime.now(TIMEZONE_OBJ).isoformat(),
            "api_key_configured": bool(API_KEY),
            "csv_exists": os.path.exists(CSV_FILE),
        }

        if os.path.exists(CSV_FILE):
            rows, _ = load_rows_raw(CSV_FILE)
            status["total_prints"] = len(rows)
        else:
            status["total_prints"] = 0

        status["known_printers"] = get_known_printers()
        return jsonify(status), 200
    except SqlOnlyViolationError as exc:
        return jsonify({"status": "error", "backend": "sql", "error": str(exc)}), 503
    except Exception as exc:
        return jsonify({"status": "error", "error": str(exc)}), 500


# ============================================================================
# LIVE JOB CONTROL ENDPOINTS
# ============================================================================

@app.post("/job-start")
@require_printer_api_key
@live.synchronized
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
    try:
        start_time = optional_finite_float(data.get("start_time"), label="start_time")
        estimated_duration = optional_finite_float(
            data.get("estimated_duration"),
            label="estimated_duration",
            nonnegative=True,
        )
        estimated_filament = optional_finite_float(
            data.get("estimated_filament_mm"),
            label="estimated_filament_mm",
            nonnegative=True,
        )
    except NumericValidationError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    
    profile_id = data.get("profile_id")
    
    attempt_token = str(data.get("attempt_token") or "").strip()
    if len(attempt_token) > 128:
        return jsonify({"success": False, "error": "attempt_token exceeds 128 characters"}), 400
    live.start_job(printer_name, filename, start_time, estimated_duration, estimated_filament, profile_id,
                   attempt_token=attempt_token)
    try:
        asset,_status,_modified=archived_thumbs.fetch_image(printer_name,filename)
        if asset:
            live.update_job(printer_name,thumbnail_asset=asset)
    except Exception:
        app.logger.warning('Thumbnail unavailable at job start for %s',printer_name)
    job = live.get_job(printer_name)
    
    return jsonify({"success": True, "job": job})


@app.post("/job-update")
@require_printer_api_key
@live.synchronized
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
                updates[field] = finite_float(updates[field], label=field, nonnegative=True)
            except NumericValidationError as exc:
                return jsonify({"success": False, "error": str(exc)}), 400
    
    result = live.update_job(printer_name, **updates)
    
    if result is None:
        return jsonify({"success": False, "error": "Job not found"}), 404
    
    job = live.get_job(printer_name)
    return jsonify({"success": True, "job": job})


@app.post("/job-pause")
@require_printer_api_key
@live.synchronized
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
@require_printer_api_key
@live.synchronized
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
@require_printer_api_key
@live.synchronized
def job_cancel():
    """Cancel an active print job."""
    from core import live
    
    data = request.get_json() or request.form.to_dict()
    printer_name_raw = data.get("printer_name")
    filename_raw = data.get("filename")
    reason = data.get("reason")  # Optional failure/cancel reason
    elapsed_raw = data.get("elapsed_seconds")
    filament_mm = None
    if data.get("filament_mm") not in (None, ""):
        try:
            filament_mm = finite_float(data["filament_mm"], label="filament_mm", nonnegative=True)
        except NumericValidationError as exc:
            return jsonify({"success": False, "error": str(exc)}), 400
    
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
            elapsed_seconds = finite_float(elapsed_raw, label="elapsed_seconds", nonnegative=True)
        except NumericValidationError as exc:
            return jsonify({"success": False, "error": str(exc)}), 400

    active_before = live.get_job(printer_name)
    event_time = data.get("event_time")
    if event_time is not None:
        try:
            event_time = finite_float(event_time, label="event_time", nonnegative=True)
        except NumericValidationError as exc:
            return jsonify({"success": False, "error": str(exc)}), 400
    if active_before:
        active_filename = str(active_before.get("filename") or "").strip()
        attempt_token = str(data.get("attempt_token") or "").strip()
        if (filename and filename != active_filename) or (
            (attempt_token or active_before.get("attempt_token"))
            and attempt_token != active_before.get("attempt_token")
        ) or (
            event_time is not None and event_time < active_before["start_time"]
        ):
            return jsonify({"success": True, "job": None, "history": "stale_cancel"}), 200
        filename = active_filename

    # Mark canceled in live state (this also removes it from active jobs).
    live_result = live.cancel_job(printer_name, reason, remove=False)
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
            if _is_sql_only():
                with db_module.connect_db() as conn:
                    recent = conn.execute("SELECT j.status,j.ended_at FROM jobs j JOIN printers p ON j.printer_id=p.id WHERE p.name=? AND (?='' OR j.filename=?) ORDER BY j.ended_at DESC LIMIT 1", (printer_name, str(filename or ""), str(filename or ""))).fetchone()
                if not recent:
                    return None
                try:
                    timestamp = datetime.fromisoformat(str(recent["ended_at"]).replace("Z", "+00:00")).timestamp()
                except ValueError:
                    timestamp = 0
                return {"status": recent["status"], "timestamp": timestamp}
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
        filament_mm if filament_mm is not None else 0.0,
        paused_seconds_total=paused_seconds_total,
    )
    if filament_mm is None:
        cost_data.update(material_cost=None, total_cost=None, filament_meters=None,
                         filament_grams=None, filament_weight_kind="unknown")

    row = {
        "timestamp": ts,
        "job_uid": str(uuid.uuid4()),
        "printer": printer_name,
        "filename": filename,
        "started_at": active_before.get("start_time") if active_before else None,
        "duration_seconds": float(duration_seconds_total),
        "paused_seconds_total": paused_seconds_total,
        "pause_count": pause_count,
        "runout_count": runout_count,
        "filament_mm": filament_mm,
    }
    row.update(cost_data)
    row["status"] = "canceled"
    row["failure_reason"] = str(reason or "").strip()
    row["thumbnail_asset"]=live_result.get("thumbnail_asset")
    audit = {"printer": printer_name, "job_uid": row["job_uid"], "before": active_before,
             "after": {"status": "canceled", "duration_seconds": duration_seconds_total,
                       "filament_mm": filament_mm, "time_cost": row.get("time_cost"),
                       "material_cost": row.get("material_cost"), "total_cost": row.get("total_cost")}}
    if _is_sql_only():
        with closing(db_module.connect_db()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            storage_backend.write_job(row, connection=conn)
            live.end_job(printer_name, connection=conn)
            system_events.emit_event("activity", "Print canceled", "Saved cancellation and cleared live tracking.",
                                     meta=audit, connection=conn)
    else:
        storage_backend.write_job(row)
        live.end_job(printer_name)
        system_events.emit_event("activity", "Print canceled", "Saved cancellation and cleared live tracking.", meta=audit)
    try:
        archived_thumbs.capture_job(row['job_uid'],printer_name,filename,
            live_asset=live_result.get('thumbnail_asset'),ended_at=ts)
    except Exception:
        app.logger.warning('Canceled job saved; thumbnail archival deferred for %s',row['job_uid'])

    app.logger.info(
        "job-cancel logged: printer=%s filename=%s elapsed_seconds=%s",
        printer_name,
        filename,
        elapsed_seconds,
    )

    return jsonify({"success": True, "job": live_result, "history_job_uid": row["job_uid"]})


@app.post("/job-end")
@require_printer_api_key
@live.synchronized
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

@app.get('/api/printer-status')
def printer_status():
    """Read telemetry without rewriting history or clearing retained live state."""
    from core.filament import conversion,material_cost,price_spec
    if is_demo_runtime():
        from flask import g
        from core.demo.simulation import simulated_printer_status

        observations = simulated_printer_status(
            get_canonical_printer_names(), g.demo_workspace_session.record.created_at.timestamp(),
        )
    else:
        from core import monitoring
        endpoints={name:thumbs.resolve_moonraker_base_url(name) or '' for name in get_canonical_printer_names()}
        observations=monitoring.statuses(endpoints)
    for name,item in observations.items():
        stored=live.get_job(name)
        item['tracking_warning']=None
        if stored and (not item['connected'] or item['state'] not in ('printing','paused') or item.get('filename')!=stored.get('filename')):
            item['tracking_warning']='Stored job differs from current Moonraker state. Review tracking; no job was automatically cleared or finalized.'
        if item['connected'] and item['state'] in ('printing','paused'):
            item.update(grams=None, time_cost=None, material_cost=None, total_cost=None)
            try:
                cfg=pricing._get_effective_filament_pricing(name)
                seconds=item['total_seconds'] if pricing._include_paused_time_for_printer(name) else item['print_seconds']
                if seconds is not None:
                    item['time_cost']=pricing.compute_live_time_cost(name,seconds)
                if item['filament_mm'] is not None:
                    measure=conversion({'filament_mm':item['filament_mm'],'grams_per_meter':cfg['grams_per_meter']})
                    item['grams']=float(measure['grams'])
                    item['material_cost']=float(material_cost(measure,price_spec(cfg['filament_mode'],cfg['filament_rate'])))
                    item['conversion_source']=str(cfg['grams_per_meter'])+' g/m; current printer pricing'
                if item['time_cost'] is not None and item['material_cost'] is not None:
                    item['total_cost']=item['time_cost']+item['material_cost']
                else:
                    item['pricing_error']='Live estimate incomplete; Moonraker measurements are unavailable'
            except (ValueError,RuntimeError,TypeError):
                item['pricing_error']='Live price unavailable; repair pricing/conversion in Printer Settings'
    return jsonify({'printers':observations}),200,{'Cache-Control':'no-store'}


@app.get("/printers/<path:printer_name>")
def printer_detail(printer_name):
    from core import printer_reports

    if printer_name not in get_canonical_printer_names():
        abort(404)
    now = datetime.now(TIMEZONE_OBJ)
    start_value = request.args.get("start", "")
    end_value = request.args.get("end", "")
    compare = request.args.get("compare") == "1"
    view = dict(printer_name=printer_name, start_value=start_value, end_value=end_value, compare=compare)
    try:
        period, previous = printer_reports.report_periods(start_value, end_value, now)
    except ValueError as exc:
        return render_template("printer_detail.html", error=str(exc), **view), 400
    start, end = period
    filters = dict(printer=printer_name, end_exclusive=True, recorded_costs_only=True)
    history = history_repo.list_history_rows(
        history_repo.HistoryQuery(start_dt=start, end_dt=end, **filters),
        page=request.args.get("page", 1), per_page=25,
    )
    view.update(
        start_value=start.date().isoformat(),
        end_value=(end.date() - timedelta(days=1)).isoformat(),
        summary=printer_reports.summarize(history.rows_all, period, now),
        rows=[printer_reports.display_job(row) for row in history.rows_page],
        error=history.error,
        backend=history.backend,
    )
    view["pager"] = _pager_links(
        "printer_detail",
        dict(printer_name=printer_name, start=view["start_value"], end=view["end_value"], compare="1" if compare else "0"),
        "page", "per_page", history.pager,
    )
    if compare:
        prior = history_repo.list_history_rows(
            history_repo.HistoryQuery(start_dt=previous[0], end_dt=previous[1], **filters),
            page=1, per_page=1,
        )
        view.update(
            prior=printer_reports.summarize(prior.rows_all, previous, now),
            prior_start=previous[0].date(),
            prior_end=previous[1].date() - timedelta(days=1),
            comparison_error=prior.error,
        )
    return render_template("printer_detail.html", **view)


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
                    if not _is_sql_only() and request.form.get("selection_kind") != "job_uid" and all(str(v).strip().isdigit() for v in selected):
                        indices = [int(i) for i in selected if str(i).strip().isdigit()]
                        rows_for_map, _ = _load_history_rows_for_recalc()
                        job_uids = [r.get("job_uid") for r in rows_for_map if r.get("row_index") in indices and r.get("job_uid")]
                    else:
                        job_uids = [str(v).strip() for v in selected if str(v).strip()]
                    try:
                        _mark_completed_jobs_sql(job_uids)
                    except Exception as exc:
                        return redirect(url_for("index", error=f"Failed to mark jobs completed: {exc}"))
                else:
                    if not _is_sql_only() and request.form.get("selection_kind") != "job_uid" and all(str(v).strip().isdigit() for v in selected):
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
                if not _is_sql_only() and request.form.get("selection_kind") != "job_uid" and all(str(v).strip().isdigit() for v in selected):
                    indices = {int(i) for i in selected if str(i).strip().isdigit()}
                    rows_for_map, _ = load_rows_raw(CSV_FILE)
                    job_uids = [r.get("job_uid") for r in rows_for_map if r.get("row_index") in indices and r.get("job_uid")]
                else:
                    job_uids = [str(v).strip() for v in selected if str(v).strip()]

            if not _is_sql_only() and request.form.get('selection_kind')!='job_uid':
                return redirect(url_for('recalculate_page',job_uids=job_uids))
            return redirect(url_for('recalculate_preview'),code=307)

        # Handle row deletion
        if action in ("delete_rows", "delete"):
            selected = request.form.getlist("delete_rows")
            if selected:
                if _is_sql_only():
                    if not _is_sql_only() and request.form.get("selection_kind") != "job_uid" and all(str(v).strip().isdigit() for v in selected):
                        indices = [int(i) for i in selected if str(i).strip().isdigit()]
                        rows_for_map, _ = _load_history_rows_for_recalc()
                        job_uids = [r.get("job_uid") for r in rows_for_map if r.get("row_index") in indices and r.get("job_uid")]
                    else:
                        job_uids = [str(v).strip() for v in selected if str(v).strip()]
                    try:
                        deleted = _delete_jobs_sql(job_uids)
                    except Exception as exc:
                        return redirect(url_for("index", error=f"Failed to delete jobs: {exc}"))
                    system_events.emit_event(
                        "deleted",
                        "Deleted history jobs",
                        f"Deleted {deleted} job(s) from Print History (SQL-only).",
                        meta={"action": "delete_history_rows", "count": deleted},
                    )
                else:
                    if not _is_sql_only() and request.form.get("selection_kind") != "job_uid" and all(str(v).strip().isdigit() for v in selected):
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

    error = request.args.get("error", "").strip() or None
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
    error = history_result.error or error

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
    now_for_cards=datetime.now(TIMEZONE_OBJ)
    today_for_cards=now_for_cards.replace(hour=0,minute=0,second=0,microsecond=0)
    card_rows=history_repo.printer_card_rows(today_for_cards,now_for_cards)
    printer_summaries = compute_printer_summaries(card_rows, active_jobs)

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
            ps["_thumb_card"] = None
            if job_for_card.get("thumbnail_asset"):
                ps["_thumb_card"] = url_for('archived_thumbnail',digest=job_for_card['thumbnail_asset'])
            if not ps["_thumb_card"]:
                try:
                    with db_module.connect_db() as conn:
                        latest=conn.execute("SELECT job_uid FROM jobs j JOIN printers p ON p.id=j.printer_id WHERE p.name=? AND j.filename=? ORDER BY j.ended_at DESC LIMIT 1",(pname,card_filename)).fetchone()
                    ps["_thumb_card"] = get_job_thumbnail_url(pname,card_filename,size_hint="card",job_uid=latest[0]) if latest else None
                except Exception:
                    ps["_thumb_card"] = None
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
            job["_thumb_small"] = url_for("archived_thumbnail",digest=job["thumbnail_asset"]) if job.get("thumbnail_asset") else None
        else:
            job["_thumb_small"] = None

    from core.filament import describe_job,summarize_weights
    all_filament_profiles=profiles.get_all_profiles()
    summary.update(summarize_weights(rows,all_filament_profiles))
    for row in history_rows_page:
        describe_job(row,all_filament_profiles.get(row.get('filament_profile_id')))

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
                row["_thumb_small"] = build_thumb_url_from_token(pname, token, size_hint="small", job_uid=row.get("job_uid"))
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
      history_source="SQL" if _is_sql_only() else "CSV",
        active_jobs=active_jobs,
        printer_summaries=printer_summaries,
    )


@app.context_processor
def inventory_navigation():
    return {"inventory_available": _is_sql_only()}


@app.route("/inventory", methods=["GET", "POST"])
def inventory_page():
    from core import inventory

    if not _is_sql_only():
        return "Filament inventory requires SQL-only storage.", 404
    error = None
    if request.method == "POST":
        form = request.form
        action = form.get("action")
        spool = form.get("spool_uid")
        operation = form.get("operation_uid")
        try:
            changed = True
            if action == "create":
                _spool, changed = inventory.create_spool(operation, **{
                    field: form.get(field) for field in (
                        "manufacturer", "material", "color", "net_grams", "remaining_grams",
                        "low_stock_grams", "purchase_price", "purchased_on", "grams_per_meter",
                    )
                })
            elif action == "assign":
                inventory.assign_spool(spool, form.get("printer_id"))
            elif action == "consume":
                changed = inventory.record_consumption(
                    spool, operation, form.get("job_uid"), form.get("grams"),
                    verified=form.get("verified") == "1",
                )
            elif action == "adjust":
                changed = inventory.adjust_stock(spool, operation, form.get("grams"), form.get("reason"))
            elif action == "correct":
                changed = inventory.correct_consumption(
                    spool, operation, form.get("original_event_uid"), form.get("grams"),
                    expected_grams=form.get("expected_grams"), reason=form.get("reason"),
                )
            elif action == "estimate":
                changed = inventory.record_estimate(
                    spool, operation, form.get("job_uid"), form.get("grams"), form.get("source"),
                )
            elif action in {"archive", "reactivate"}:
                changed = inventory.set_spool_archived(
                    spool, operation, archived=action == "archive", reason=form.get("reason"),
                )
            else:
                raise ValueError("Unknown inventory operation.")
        except ValueError as exc:
            error = str(exc)
        else:
            return redirect(url_for("inventory_page", saved="1" if changed else "0"), code=303)
    spools, printers, events, jobs = inventory.inventory_view()
    return render_template(
        "inventory.html", spools=spools, printers=printers, events=events, jobs=jobs,
        error=error, new_operation=lambda: uuid.uuid4().hex,
    ), 400 if error else 200


@app.route("/estimates", methods=["GET", "POST"])
@app.post("/api/estimates")
def estimate_page():
    from core.estimates import estimate_print
    from itsdangerous import URLSafeTimedSerializer

    api = request.path == "/api/estimates"
    request.max_content_length = 16 * 1024 * 1024 if request.mimetype == "multipart/form-data" else 16 * 1024
    result, error, values = None, None, {}
    review_token = None
    if request.method == "POST":
        try:
            values = request.get_json(silent=True) if request.is_json else request.form
            if not isinstance(values, dict) and not hasattr(values, "get"):
                raise ValueError("Expected an estimate input object.")
            result = estimate_print(values, request.files.get("gcode"))
            if result["total_cost"] is not None and _is_sql_only():
                review_token = URLSafeTimedSerializer(API_KEY, salt="kcd-planned-estimate").dumps({
                    "estimate": result, "plan_id": uuid.uuid4().hex,
                })
        except ValueError as exc:
            error = str(exc)
            if not hasattr(values, "get"):
                values = {}
    if api:
        return (jsonify(error=error), 400) if error else jsonify(**result, review_token=review_token)
    from core.profiles import get_all_profiles
    return render_template(
        "estimates.html", result=result, error=error, values=values, profiles=get_all_profiles(),
        review_token=review_token, project_candidates=projects.load_projects() if _is_sql_only() else {},
    ), 400 if error else 200


@app.post("/estimates/plan")
def save_estimate_plan():
    from itsdangerous import BadSignature, URLSafeTimedSerializer
    import sqlite3

    if not _is_sql_only():
        return jsonify(error="Reviewed estimate promotion requires SQL-only storage"), 409
    request.max_content_length = 16 * 1024
    try:
        reviewed = URLSafeTimedSerializer(API_KEY, salt="kcd-planned-estimate").loads(
            request.form.get("review_token", ""), max_age=900,
        )
        estimate = reviewed["estimate"]
        title = request.form.get("title", "").strip()
        if not title or len(title) > 200:
            raise ValueError("Enter a planned-item title of at most 200 characters")
        import math
        item = projects.create_plan_item(
            request.form.get("project_id", ""), title, math.ceil(estimate["duration_seconds"]),
            estimate["filament_grams"], source="reviewed_estimate",
            notes=json.dumps({"estimate": estimate}, sort_keys=True),
            est_cost_override=float(estimate["display_total"]), plan_id=reviewed["plan_id"],
        )
    except BadSignature:
        return jsonify(error="The estimate review is invalid or expired. Calculate a fresh estimate."), 409
    except ValueError as exc:
        return jsonify(error=str(exc)), 409
    except sqlite3.DatabaseError:
        return jsonify(error="The planned item could not be saved. Check database health, then retry this review."), 503
    from core.profiles import get_all_profiles
    return render_template(
        "estimates.html", result=estimate, values={}, error=None,
        profiles=get_all_profiles(), saved_plan=item.plan_id,
    )


@app.route("/reports")
def reports_page():
    """Reports page with monthly breakdown and top printers."""
    data = reports_repo.get_reports_data(request.args)
    recent=data.get("recent_jobs", [])
    printer_settings=load_settings(SETTINGS_FILE)
    for row in recent:
        enabled=printer_settings.get(row['printer'],{}).get('thumbnails_enabled',True)
        row['_thumb_small']=get_job_thumbnail_url(row['printer'],row['filename'],'small',job_uid=row.get('job_uid')) if enabled else None

    return render_template(
        "reports.html",
        recent_jobs=recent,
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
        selected_printer=data["selected_printer"],
        printer_choices=data.get("printer_choices", []),
        report_timezone=data["timezone"],
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
            if _is_sql_only():
                raise
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


def _parse_required_nonneg_float(raw, field_label):
    raw = "" if raw is None else str(raw).strip()
    if not raw:
        return None, f"Missing {field_label}."
    try:
        value = finite_float(raw, label=field_label, nonnegative=True)
    except NumericValidationError:
        return None, f"Invalid {field_label} (must be a non-negative number)."
    return value, None


def _parse_required_positive_float(raw, field_label):
    raw = "" if raw is None else str(raw).strip()
    if not raw:
        return None, f"Missing {field_label}."
    try:
        value = finite_float(raw, label=field_label, positive=True)
    except NumericValidationError:
        return None, f"Invalid {field_label} (must be a finite number greater than zero)."
    return value, None


def _is_sql_only() -> bool:
    return str(os.getenv("KCD_STORAGE_BACKEND", "csv")).strip().lower() == "sql"


def _sql_only_printer_exists(printer: str) -> bool:
    if not _is_sql_only():
        return True
    return str(printer or "").strip() in get_canonical_printer_names()


def _load_history_rows_for_recalc() -> tuple[list, str | None]:
    if _is_sql_only():
        try:
            history_query = history_repo.HistoryQuery()
            history_result = history_repo.list_history_rows_sql(history_query, page=1, per_page=25, error=None)
            return [r for r in history_result.rows_all if str(r.get("status") or "").lower() not in {"printing","paused"}], history_result.error
        except Exception as exc:
            return [], f"Error loading history from SQL: {exc}"
    rows,error=load_rows_raw(CSV_FILE)
    return [r for r in rows if str(r.get("status") or "").lower() not in {"printing","paused"}],error


def _sum_total_cost_sql(job_uids: list[str]) -> float:
    if not job_uids:
        return 0.0
    placeholders = ",".join(["?"] * len(job_uids))
    with closing(db_module.connect_db()) as conn:
        db_module.apply_migrations(conn)
        row = conn.execute(
            f"SELECT SUM(COALESCE(total_cost, 0)) AS total FROM jobs WHERE job_uid IN ({placeholders})",
            job_uids,
        ).fetchone()
        if not row:
            return 0.0
        return finite_float(row["total"] or 0.0, label="total_cost")


def _recalc_jobs_sql(job_uids: list[str], compute_fn) -> int:
    if not job_uids:
        return 0
    fields=("duration_hours","filament_meters","rate_per_hour","filament_mode","filament_rate","grams_per_meter","time_cost","material_cost","total_cost","filament_profile_id","filament_material","hourly_rate_profile_id","filament_grams","billable_hours","filament_weight_kind","override_rate_per_hour","override_material_cost","override_total_cost")
    mandatory={"rate_per_hour","filament_mode","filament_rate","grams_per_meter","time_cost","material_cost","total_cost"}
    outcomes=[]
    with db_module.connect_db() as conn:
        db_module.apply_migrations(conn)
        conn.execute("BEGIN IMMEDIATE")
        rows=conn.execute("SELECT j.*,p.name AS printer FROM jobs j JOIN printers p ON p.id=j.printer_id WHERE j.job_uid IN ("+",".join("?" for _ in job_uids)+")",job_uids).fetchall()
        if getattr(compute_fn,'expected_preview_hash',None):
            from core.recalculation import preview_fingerprint
            actual_hash=preview_fingerprint([dict(r) for r in rows],compute_fn,compute_fn.kcd_plan)
            if actual_hash!=compute_fn.expected_preview_hash:
                raise ValueError('Jobs or requested profile prices changed; review a fresh preview')
        for source in rows:
            row=dict(source)
            computed=(compute_fn.for_row(row) if hasattr(compute_fn,'for_row') else compute_fn(row['printer'],row.get('duration_seconds') or 0,row.get('filament_mm') or 0,row.get('paused_seconds_total') or 0)) or {}
            if not mandatory.issubset(computed):
                raise ValueError("Recalculation did not produce complete pricing; no success was recorded")
            expected={k:computed[k] for k in fields if k in computed}
            for key in mandatory-{'filament_mode'}:
                if key=='grams_per_meter' and expected[key] is None:
                    continue
                expected[key]=finite_float(expected[key],label=key,nonnegative=key!='total_cost')
            if expected['filament_mode'] not in {'per_meter','per_gram','per_kg'}:
                raise ValueError('Recalculation produced an invalid filament unit')
            before={k:row.get(k) for k in expected}
            def equivalent(a,b):
                if a in (None,'') and b in (None,''):return True
                if isinstance(a,(int,float)) and isinstance(b,(int,float)):
                    return math.isclose(a,b,rel_tol=1e-10,abs_tol=1e-10)
                return a==b
            changed=any(not equivalent(before[k],v) for k,v in expected.items())
            if changed:
                conn.execute("INSERT OR IGNORE INTO job_cost_originals VALUES (?,?,?)",(row['job_uid'],json.dumps(row),db_module._utc_now_iso()))
                conn.execute("UPDATE jobs SET "+",".join(k+"=?" for k in expected)+",updated_at=? WHERE job_uid=?",[*expected.values(),db_module._utc_now_iso(),row['job_uid']])
            actual=dict(conn.execute("SELECT * FROM jobs WHERE job_uid=?",(row['job_uid'],)).fetchone())
            if any(not equivalent(actual[k],value) for k,value in expected.items()):
                raise ValueError("Stored pricing failed post-update verification; transaction rolled back")
            outcomes.append({'job_uid':row['job_uid'],'changed':changed,'validated':True,'before':before,'after':{k:actual[k] for k in expected},'conversion_source':computed.get('conversion_source'),'pricing_source':computed.get('pricing_source')})
        changed_count=sum(item['changed'] for item in outcomes)
        audit_meta={'count_requested':len(job_uids),'count_skipped_missing':0,**getattr(compute_fn,'kcd_request_meta',{}),'action':'recalculate',
            'count_updated':changed_count,
            'count_validated':len(outcomes),'count_unchanged':len(outcomes)-changed_count,
            'plan':getattr(compute_fn,'kcd_plan',{}),'comparisons':outcomes,
            'totals':{side:sum(item[side]['total_cost'] or 0 for item in outcomes) for side in ('before','after')}}
        system_events.emit_event('activity','Pricing recalculation completed',
            f'Verified {len(outcomes)} job(s): {changed_count} changed, {len(outcomes)-changed_count} already matched.',
            meta=audit_meta,connection=conn)
    compute_fn.kcd_audit_recorded=True
    compute_fn.kcd_outcomes=outcomes
    return sum(1 for r in outcomes if r['changed'])


def _mark_completed_jobs_sql(job_uids: list[str]) -> int:
    if not job_uids:
        return 0
    placeholders = ",".join(["?"] * len(job_uids))
    with closing(db_module.connect_db()) as conn:
        db_module.apply_migrations(conn)
        now_iso = datetime.now(timezone.utc).isoformat()
        cur = conn.execute(
            f"UPDATE jobs SET status = 'completed', failure_reason = NULL, updated_at = ? "
            f"WHERE job_uid IN ({placeholders}) AND status = 'printing'",
            [now_iso, *job_uids],
        )
        conn.commit()
        return int(cur.rowcount or 0)


def _delete_jobs_sql(job_uids: list[str]) -> int:
    if not job_uids:
        return 0
    placeholders = ",".join(["?"] * len(job_uids))
    with closing(db_module.connect_db()) as conn:
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


@app.route("/recalculate", methods=["GET"], endpoint="recalculate_page")
def recalculate_page():
    """
    Recalculate Center: select historical jobs by job_uid and rerun pricing.

    Data safety:
      - Never deletes rows
      - Never changes job_uid / printer / filename / timestamps
      - Only rewrites computed pricing fields (same behavior as existing bulk recalc)
    """
    rows, error = _load_history_rows_for_recalc()
    error=request.args.get("error") or error
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
    from core.filament import describe_job
    for row in rows_page:describe_job(row,filament_profiles.get(row.get('filament_profile_id')))
    rate_profiles = rates.list_rate_profiles()
    display_settings = load_display_settings(DISPLAY_FILE, HEADERS)
    recalc_visible_cols = get_visible_columns_for_table(
        display_settings,
        "recalc_jobs",
        ["printer", "filename", "status", "hours", "filament", "time_cost", "material_cost", "total", "job_uid"],
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
        recompute_mode="pricing_only",
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
        selected_job_uids=set(request.args.getlist("job_uids")),
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
    """Run pricing recalculation for selected job_uids."""
    def _parse_optional_nonneg_float(raw):
        raw = (raw or "").strip()
        if not raw:
            return None, None
        try:
            value = finite_float(raw, label="override", nonnegative=True)
            return value, None
        except NumericValidationError:
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
        if _is_sql_only():
            system_events.emit_event(
                "activity",
                "Pricing recalculation completed",
                f"Recalculated pricing for {int(record.get('count_updated') or 0)} job(s).",
                meta={
                    "action": "recalculate",
                    "count_requested": int(record.get("count_requested") or 0),
                    "count_updated": int(record.get("count_updated") or 0),
                    "count_validated": int(record.get("count_validated") or 0),
                    "count_unchanged": int(record.get("count_unchanged") or 0),
                    "plan": record.get("plan"),
                    "comparisons": record.get("comparisons", []),
                    "totals": record.get("totals"),
                    "count_skipped_missing": int(record.get("count_skipped_missing") or 0),
                    "job_uids_hash": str(record.get("job_uids_hash") or ""),
                },
            )
            return
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
        return redirect(url_for("recalculate_page", error=f"Error loading history: {error}"))

    if recompute_mode not in ("pricing_only", "full"):
        recompute_mode = "pricing_only"

    if recompute_mode == "full":
        return redirect(url_for("recalculate_page", msg="Only pricing recalculation is supported in this release."))

    compute_fn, plan_err, plan = _build_compute_fn(
        apply_rate_profile,
        apply_filament_profile,
        rate_profile_id,
        filament_profile_id,
        rate_per_hour_override_raw,
        filament_rate_per_meter_override_raw,
    )
    if plan_err:
        return redirect(url_for("recalculate_page", error=plan_err))
    from core.recalculation import enrich_compute,preview_fingerprint
    try:
        compute_fn,plan=enrich_compute(compute_fn,plan,request.form)
    except ValueError as exc:
        return redirect(url_for('recalculate_page',error=str(exc)))
    compute_fn.kcd_outcomes=[]


    existing_uids = {str(r.get("job_uid") or "").strip() for r in (rows or []) if str(r.get("job_uid") or "").strip()}

    if select_filtered:
        filtered, _start_dt, _end_dt = _filter_history_rows_for_recalc(rows, request.form)
        requested_uids = {str(r.get("job_uid") or "").strip() for r in filtered if str(r.get("job_uid") or "").strip()}
    else:
        requested_uids = {str(u or "").strip() for u in (request.form.getlist("job_uids") or request.form.getlist("delete_rows") or request.form.getlist("job_uid")) if str(u or "").strip()}

    missing = {u for u in requested_uids if u not in existing_uids}
    to_update = [u for u in requested_uids if u in existing_uids]

    if not to_update:
        return redirect(url_for('recalculate_page',error='No selected historical jobs were found; no changes were written.'))

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
        "pricing_mode", "pricing_amount", "net_spool_grams", "conversion_factor_override",
        ):
            v = (request.form.get(key) or "").strip()
            if v:
                redirect_params[key] = v
        redirect_params["msg"] = f"Confirm large run: type RECALC to recalculate {len(to_update)} jobs."
        return redirect(url_for("recalculate_page", **redirect_params))

    if _is_sql_only():
        if not request.form.get('preview_hash'):
            return recalculate_preview()
        compute_fn.expected_preview_hash=request.form.get('preview_hash')
        compute_fn.kcd_plan=plan
        compute_fn.kcd_request_meta={"count_skipped_missing":len(missing),"count_requested":len(requested_uids)}
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
            try:
                updated = _recalc_jobs_sql(to_update, compute_fn)
                after_total = _sum_total_cost_sql(to_update)
            except Exception as exc:
                return redirect(url_for("recalculate_page", error=f"Pricing recalculation failed: {exc}"))
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
            "count_validated": len(getattr(compute_fn,"kcd_outcomes",[])),
            "count_unchanged": sum(1 for r in getattr(compute_fn,"kcd_outcomes",[]) if not r["changed"]),
            "comparisons": getattr(compute_fn,"kcd_outcomes",[]),
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
        if not getattr(compute_fn,"kcd_audit_recorded",False):
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
        "pricing_mode", "pricing_amount", "net_spool_grams", "conversion_factor_override",
    ):
        v = (request.form.get(key) or "").strip()
        if v:
            redirect_params[key] = v

    unchanged_count=sum(1 for outcome in getattr(compute_fn,"kcd_outcomes",[]) if not outcome["changed"])
    msg = f"Verified pricing; changed {updated} job(s). {unchanged_count} already matched."
    if skipped:
        msg += f" Skipped {skipped} missing job(s)."
    redirect_params["msg"] = msg

    return redirect(url_for("recalculate_page", **redirect_params))


@app.route("/recalculate/preview", methods=["POST"], endpoint="recalculate_preview")
def recalculate_preview():
    """Preview pricing recalculation without writing history."""

    def _parse_optional_nonneg_float(raw):
        raw = (raw or "").strip()
        if not raw:
            return None, None
        try:
            value = finite_float(raw, label="override", nonnegative=True)
            return value, None
        except NumericValidationError:
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
        return redirect(url_for("recalculate_page", error=f"Error loading history: {error}"))

    if recompute_mode not in ("pricing_only", "full"):
        recompute_mode = "pricing_only"
    if recompute_mode == "full":
        return redirect(url_for("recalculate_page", msg="Only pricing recalculation is supported in this release."))

    compute_fn, plan_or_err = _build_compute_fn(
        apply_rate_profile,
        apply_filament_profile,
        rate_profile_id,
        filament_profile_id,
        rate_per_hour_override_raw,
        filament_rate_per_meter_override_raw,
    )
    if compute_fn is None:
        return redirect(url_for("recalculate_page", error=plan_or_err))
    plan = plan_or_err
    from core.recalculation import enrich_compute,preview_fingerprint
    try:
        compute_fn,plan=enrich_compute(compute_fn,plan,request.form)
    except ValueError as exc:
        return redirect(url_for('recalculate_page',error=str(exc)))

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
        requested_uids = {str(u or "").strip() for u in (request.form.getlist("job_uids") or request.form.getlist("delete_rows") or request.form.getlist("job_uid")) if str(u or "").strip()}

    missing = {u for u in requested_uids if u not in existing_uids}
    to_preview = {u for u in requested_uids if u in existing_uids}

    raw_preview={}
    if _is_sql_only() and to_preview:
        with db_module.connect_db() as conn:
            raw_preview={r['job_uid']:dict(r) for r in conn.execute('SELECT j.*,p.name AS printer FROM jobs j JOIN printers p ON p.id=j.printer_id WHERE j.job_uid IN ('+','.join('?' for _ in to_preview)+')',list(to_preview))}
    preview_rows = []
    preview_errors=[]
    before_total = 0.0
    after_total = 0.0
    for r in rows:
        uid = str(r.get("job_uid") or "").strip()
        if not uid or uid not in to_preview:
            continue

        if uid in raw_preview:r={**r,**raw_preview[uid]}
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
            paused_seconds_total = float(r.get("paused_seconds_total") or 0.0)
        except Exception:
            paused_seconds_total = 0.0
        try:
            old_total = float(r.get("total_cost") or 0.0)
        except Exception:
            old_total = 0.0

        try:
            computed = (compute_fn.for_row(r) if hasattr(compute_fn,"for_row") else compute_fn(printer_name, duration_seconds, filament_mm, paused_seconds_total)) or {}
        except ValueError as exc:
            preview_errors.append(str(r.get('filename') or uid)+': '+str(exc))
            continue
        try:
            new_total = float(computed.get("total_cost") or 0.0)
        except Exception:
            new_total = 0.0
        try:
            duration_hours = float(computed.get("duration_hours", duration_seconds / 3600.0) or 0.0)
        except Exception:
            duration_hours = duration_seconds / 3600.0
        try:
            filament_meters = float(computed.get("filament_meters", filament_mm / 1000.0) or 0.0)
        except Exception:
            filament_meters = filament_mm / 1000.0
        try:
            time_cost = float(computed.get("time_cost") or 0.0)
        except Exception:
            time_cost = 0.0
        try:
            material_cost = float(computed.get("material_cost") or 0.0)
        except Exception:
            material_cost = 0.0

        before_total += old_total
        after_total += new_total
        preview_rows.append(
            {
                "job_uid": uid,
                "printer": printer_name,
                "filename": str(r.get("filename") or ""),
                "duration_hours": duration_hours,
                "filament_meters": filament_meters,
                "time_cost": time_cost,
                "material_cost": material_cost,
                "old_time_cost": r.get("time_cost") or 0,
                "old_material_cost": r.get("material_cost") or 0,
                "old_adjustment": float(r.get("total_cost") or 0)-float(r.get("time_cost") or 0)-float(r.get("material_cost") or 0),
                "adjustment": computed.get("adjustment",0),
                "filament_grams": computed.get("filament_grams"),
                "grams_per_meter": computed.get("grams_per_meter"),
                "conversion_source": computed.get("conversion_source"),
                "pricing_source": computed.get("pricing_source"),
                "filament_mode": computed.get("filament_mode"),
                "filament_rate": computed.get("filament_rate"),
                "old_total": old_total,
                "new_total": new_total,
                "delta": new_total - old_total,
            }
        )

    from core.recalculation import reconcile_preview
    display_before,display_after=reconcile_preview(preview_rows)
    before_total=float(display_before['total_cost'])
    after_total=float(display_after['total_cost'])

    canonical_printers = sorted(get_canonical_printer_names(include_hidden=True))
    filament_profiles = profiles.get_all_profiles()
    rate_profiles = rates.list_rate_profiles()
    display_settings = load_display_settings(DISPLAY_FILE, HEADERS)
    recalc_visible_cols = get_visible_columns_for_table(
        display_settings,
        "recalc_jobs",
        ["printer", "filename", "status", "hours", "filament", "time_cost", "material_cost", "total", "job_uid"],
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
        error="; ".join(preview_errors) or error,
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
        preview_hash=preview_fingerprint(list(raw_preview.values()),compute_fn,plan) if _is_sql_only() and not preview_errors else '',
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


@app.route('/projects/<project_uid>/pricing',methods=['GET','POST'])
def project_pricing_page(project_uid):
    from core import project_pricing
    if not _is_sql_only():
        return redirect(url_for('projects_page',error='Project-only pricing snapshots require SQL storage; compatibility history is unchanged'))
    error=None
    try:
        if request.method=='POST':
            if request.form.get('action')=='clear':
                project_pricing.clear(project_uid)
            elif request.form.get('action')=='apply':
                project_pricing.apply(project_uid,request.form.get('fingerprint',''))
            return redirect(url_for('projects_page',msg='Project price view updated. Historical job prices were retained.'))
        plan=project_pricing.preview(project_uid)
    except ValueError as exc:
        error=str(exc)
        if error=='Project not found':
            return ('Project not found',404)
        plan=project_pricing.preview(project_uid)
    return render_template('project_pricing.html',plan=plan,error=error),409 if error else 200


@app.get("/projects/<project_uid>/export.json")
def export_project(project_uid):
    if not _is_sql_only():
        return jsonify(error="Project export requires SQL-only storage"), 404
    from core.project_export import project_document

    with closing(db_module.connect_db()) as connection:
        connection.execute("BEGIN")
        document = project_document(connection, project_uid)
    if document is None:
        return jsonify(error="Project not found"), 404
    response = jsonify(document)
    response.headers["Content-Disposition"] = 'attachment; filename="kcd-project.json"'
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/projects", methods=["GET", "POST"])
def projects_page():
    """
    Projects page:
    - SQL-only uses canonical SQL project and assignment state.
    - Compatibility modes retain JSON project and assignment state.
    - Deleting a project unassigns jobs without deleting history rows.
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
                from core.filament import project_price_from_form
                modern_kg,modern_spec=project_price_from_form(request.form)
                if modern_kg is not None:filament_cost_per_kg_override=modern_kg
                projects.create_project(
                    name=name,
                    notes=notes,
                    hourly_rate_override=hourly_rate_override,
                    filament_cost_per_kg_override=filament_cost_per_kg_override,
                    filament_price=modern_spec,
                    labor_only=labor_only,
                )

            elif action == "update_project":
                project_id = request.form.get("project_id", "").strip()
                name = request.form.get("name", "").strip()
                notes = request.form.get("notes", "").strip()
                hourly_rate_override = request.form.get("hourly_rate_override", "").strip()
                filament_cost_per_kg_override = request.form.get("filament_cost_per_kg_override", "").strip()
                labor_only = bool(request.form.get("labor_only"))
                from core.filament import project_price_from_form
                modern_kg,modern_spec=project_price_from_form(request.form)
                if modern_kg is not None:filament_cost_per_kg_override=modern_kg
                projects.update_project(
                    project_id=project_id,
                    name=name,
                    notes=notes,
                    hourly_rate_override=hourly_rate_override,
                    filament_cost_per_kg_override=filament_cost_per_kg_override,
                    filament_price=modern_spec,
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
                return redirect(url_for('recalculate_preview'),code=307)

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
                            est_time_s = int(
                                finite_float(
                                    manual_time_hours,
                                    label="Estimated time hours",
                                    positive=True,
                                )
                                * 3600.0
                            )
                            source = "Manual"
                        if not est_time_s:
                            raise ValueError(meta.error or "No slicer metadata found (missing estimated time).")

                    if est_filament_g is None and manual_filament_g:
                        est_filament_g = finite_float(
                            manual_filament_g,
                            label="Estimated filament grams",
                            nonnegative=True,
                        )

                    est_cost_override = optional_finite_float(
                        manual_cost,
                        label="Estimated total cost override",
                        nonnegative=True,
                    )

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

                est_time_s = int(
                    finite_float(
                        time_hours,
                        label="Estimated time hours",
                        positive=True,
                    )
                    * 3600.0
                )
                filament_val = optional_finite_float(
                    filament_g,
                    label="Estimated filament grams",
                    nonnegative=True,
                )
                cost_val = optional_finite_float(
                    est_cost,
                    label="Estimated total cost override",
                    nonnegative=True,
                )

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
    project_filament_profiles=profiles.get_all_profiles()
    for pid, p in projects_map.items():
        jobs = project_jobs.get(pid, [])
        manual_jobs = manual_jobs_by_project.get(pid, [])
        from core import project_pricing
        from core.filament import describe_job,reconcile_display
        overlays=project_pricing.saved_overlays(pid) if _is_sql_only() else {}
        for job in jobs:
            components=projects.compute_tracked_job_cost_components(job)
            job['historical_costs']=dict(components)
            overlay=overlays.get(job['job_uid'])
            if overlay:
                components={key:float(overlay['after'][key]) for key in ('time_cost','material_cost','adjustment','total_cost')}
            job.update(components)
            describe_job(job,project_filament_profiles.get(job.get('filament_profile_id')))
            measurement={'grams':job['_weight_grams'],'grams_per_meter':job['_weight_factor'],'conversion_source':job['_weight_source']}
            if overlay:
                measurement={**measurement,**{k:overlay[k] for k in ('grams','grams_per_meter','conversion_source')}}
            job['_weight_grams']=float(measurement['grams']) if measurement['grams'] is not None else None
            job['_weight_factor']=measurement['grams_per_meter']
            job['_weight_source']=measurement['conversion_source']
            job['pricing_view']='Project-adjusted snapshot' if overlay else 'Saved historical'

        totals = projects.compute_project_totals(jobs, manual_jobs=manual_jobs, project=p)
        totals["tracked_filament_g"]=sum(job.get("_weight_grams") or 0 for job in jobs)
        totals["unknown_weights"]=sum(job.get("_weight_grams") is None for job in jobs)
        plans = plans_by_project.get(pid, [])
        projection = projects.compute_project_projection(plans, project=p)

        manual_jobs_view = []
        for mj in manual_jobs:
            components = projects.compute_manual_job_cost_components(mj, project=p)
            manual_jobs_view.append(
                {
                    "manual_job_id": mj.manual_job_id,
                    "title": mj.title,
                    "hours": mj.hours,
                    "filament_g": mj.filament_g,
                    "cost_override": mj.cost_override,
                    "computed_cost": components["total_cost"],
                    **components,
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
            components = projects.compute_planned_item_cost_components(pl, p)
            plans_view.append(
                {
                    "plan_id": pl.plan_id,
                    "filename": pl.filename,
                    "created_at": pl.created_at,
                    "est_time_s": pl.est_time_s,
                    "est_filament_g": pl.est_filament_g,
                    "est_cost": components["total_cost"],
                    "estimated_time_cost": components["time_cost"],
                    "estimated_material_cost": components["material_cost"],
                    "estimated_adjustment": components["adjustment"],
                    "estimated_total_cost": components["total_cost"],
                    "status": pl.status,
                    "source": pl.source,
                    "converted_to_manual_job_id": pl.converted_to_manual_job_id,
                    "est_cost_is_override": pl.est_cost_is_override,
                    "notes": pl.notes,
                }
            )

        # Reconcile the actual view at cent precision across tracked and manual
        # components, keeping stored history and overlay full precision intact.
        actual_views=jobs+manual_jobs_view
        displayed=reconcile_display(actual_views)
        for item in actual_views:
            for key in ('time_cost','material_cost','adjustment','total_cost'):
                item[key]=float(item['display_'+key])
        for key,value in displayed.items():totals[key]=float(value)
        totals['cost']=totals['total_cost']
        tracked_cost=sum(item['total_cost'] for item in jobs)
        manual_cost=sum(item['total_cost'] for item in manual_jobs_view)
        for item in manual_jobs_view:item['computed_cost']=item['total_cost']
        active_plans=[item for item in plans_view if item['status']=='active' and not item['converted_to_manual_job_id']]
        plan_components=[{key:item['estimated_'+key] for key in ('time_cost','material_cost','adjustment','total_cost')} for item in active_plans]
        plan_display=reconcile_display(plan_components)
        for item,component in zip(active_plans,plan_components):
            for key in ('time_cost','material_cost','adjustment','total_cost'):item['estimated_'+key]=float(component['display_'+key])
            item['est_cost']=item['estimated_total_cost']
        for key,value in plan_display.items():projection[key]=float(value)
        projection['cost']=projection['total_cost']

        project_rows.append(
            {
                "id": pid,
                "name": p.name,
                "notes": p.notes,
                "hourly_rate_override": p.hourly_rate_override,
                "price_record": {"filament_mode":p.filament_price.get("mode"),"price_input_mode":p.filament_price.get("input_mode"),"filament_rate":p.filament_price.get("rate"),"spool_price":p.filament_price.get("spool_price"),"net_spool_grams":p.filament_price.get("net_spool_grams"),"grams_per_meter":p.filament_price.get("grams_per_meter"),"profile_id":p.filament_price.get("profile_id")} if p.filament_price else {"filament_mode":"per_kg","filament_rate":p.filament_cost_per_kg_override},
                "adjusted_jobs": len(overlays),
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
            j["_thumb_small"] = build_thumb_url_from_token(pname, token, size_hint="small", job_uid=j.get("job_uid"))
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
                j["_thumb_small"] = build_thumb_url_from_token(pname, token, size_hint="small", job_uid=j.get("job_uid"))
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
        PROJECTS_UNASSIGNED_COLUMNS,
    )
    projects_project_jobs_visible_cols = _get_projects_visible_columns(
        display_settings,
        "projects_project_jobs",
        PROJECTS_TRACKED_COLUMNS,
    )
    projects_manual_jobs_visible_cols = _get_projects_visible_columns(
        display_settings,
        "projects_manual_jobs",
        PROJECTS_MANUAL_COLUMNS,
    )
    projects_planned_items_visible_cols = _get_projects_visible_columns(
        display_settings,
        "projects_planned_items",
        PROJECTS_PLANNED_COLUMNS,
    )

    return render_template(
        "projects.html",
        error=error,
        message=message,
        project_export_enabled=_is_sql_only(),
        edit_project=edit_project,
        edit_manual_job_id=edit_manual_job_id,
        display_settings=display_settings,
        projects_unassigned_visible_cols=projects_unassigned_visible_cols,
        projects_project_jobs_visible_cols=projects_project_jobs_visible_cols,
        projects_manual_jobs_visible_cols=projects_manual_jobs_visible_cols,
        projects_planned_items_visible_cols=projects_planned_items_visible_cols,
        projects=project_rows,
        unassigned_jobs=unassigned_jobs_sorted,
        unassigned_jobs_page=unassigned_jobs_page,
        unassigned_pager=unassigned_pager,
        projects_by_id={p["id"]: p for p in project_rows},
        filament_profiles=project_filament_profiles,
    )


@app.route("/orders", methods=["GET", "POST"])
def orders_page():
    """Manage project-backed order metadata without repricing tracked jobs."""
    from core import orders

    if not _is_sql_only():
        abort(404)
    if get_application_mode() != "business":
        return render_template("orders.html", unavailable=True), 403
    catalog = projects.load_projects()
    values = request.form if request.method == "POST" else request.args
    project_uid = values.get("project", "")
    project = catalog.get(project_uid)
    if project_uid and project is None:
        abort(404)
    error = None
    if request.method == "POST":
        if project is None:
            abort(400)
        try:
            orders.save_order(project_uid, values)
        except ValueError as exc:
            error = str(exc)
        else:
            return redirect(url_for("orders_page", project=project_uid), code=303)
    order = orders.get_order(project_uid) if project else None
    form = values if error else order or {"status": "draft", "priority": "normal"}
    financial = orders.financial_view(project, order) if order else None
    return render_template(
        "orders.html", orders=orders.list_orders(), projects=catalog.values(), project=project,
        order=order, form=form, financial=financial, amounts=orders.AMOUNTS,
        statuses=orders.STATUSES, priorities=orders.PRIORITIES, error=error,
        revision=values.get("revision", "") if error else order["updated_at"] if order else "",
    ), 400 if error else 200


@app.route("/settings", methods=["GET", "POST"])
def settings_page():
    """Back-compat settings entrypoint (redirects to /settings/printers)."""
    if request.method == "GET":
        return redirect(url_for("settings_printers_page"))
    # For legacy POSTs, process the action but redirect to the appropriate sub-page.
    return _settings_view(tab="printers")


def _settings_endpoint_for_action(action: str) -> str:
    other_actions = {"update_columns", "backup_now", "update_backup_settings", "update_application_mode"}
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

        if action == "update_application_mode":
            try:
                set_application_mode(request.form.get("mode", ""))
            except ValueError as exc:
                return redirect(url_for("settings_other_page", error=str(exc)))
            return redirect(url_for("settings_other_page", msg="Application mode updated for this installation."))

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
            if _is_sql_only() and enabled:
                return redirect(
                    url_for(
                        _settings_endpoint_for_action(action),
                        error="Automatic backups are unavailable in SQL-only mode; use Backup now.",
                    )
                )
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
                if _is_sql_only():
                    from core import printer_lifecycle

                    printer_lifecycle.delete_printer(printer)
                else:
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
            # The global default is stored with display settings in the active backend.
            global_include = bool(request.form.get("pause_include_paused_time_default"))
            display_settings = load_display_settings(DISPLAY_FILE, HEADERS)
            display_settings["pause_include_paused_time_default"] = bool(global_include)
            save_display_settings(DISPLAY_FILE, DATA_DIR, display_settings)

            # Per-printer overrides are stored with printer settings in the active backend.
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
            printer = (request.form.get("printer") or "").strip()
            if printer:
                if _is_sql_only() and not _sql_only_printer_exists(printer):
                    return redirect(url_for(_settings_endpoint_for_action(action), error=f"Printer not found: {printer}"))
                rate_raw = request.form.get("rate_per_hour")
                mode = (request.form.get("filament_mode") or "").strip()
                filament_rate_raw = request.form.get("filament_rate")
                grams_raw = request.form.get("grams_per_meter")
                from core.filament import configuration_from_form
                try:
                    modern=configuration_from_form(request.form)
                except ValueError as exc:
                    return redirect(url_for(_settings_endpoint_for_action(action),error=str(exc)))
                mode=modern.get('filament_mode',mode)
                filament_rate_raw=modern.get('filament_rate',filament_rate_raw)
                grams_raw=modern.get('grams_per_meter',grams_raw)

                if _is_sql_only():
                    rate_per_hour, err = _parse_required_nonneg_float(rate_raw, "hourly rate")
                    if err:
                        return redirect(url_for(_settings_endpoint_for_action(action), error=err))
                    if mode not in {"per_meter", "per_gram", "per_kg"}:
                        return redirect(url_for(_settings_endpoint_for_action(action), error="Missing filament mode."))
                    filament_rate, err = _parse_required_nonneg_float(filament_rate_raw, "filament rate")
                    if err:
                        return redirect(url_for(_settings_endpoint_for_action(action), error=err))
                    grams_per_meter, err = _parse_required_positive_float(grams_raw, "grams per meter")
                    if err:
                        return redirect(url_for(_settings_endpoint_for_action(action), error=err))
                else:
                    rate_per_hour = float(request.form.get("rate_per_hour", 1.0))
                    if not mode:
                        mode = "per_meter"
                    filament_rate = float(request.form.get("filament_rate", 0.25))
                    grams_per_meter = float(request.form.get("grams_per_meter", 3.0))

                filament_rate=modern.get("filament_rate",filament_rate)
                grams_per_meter=modern.get("grams_per_meter",grams_per_meter)
                settings = load_settings(SETTINGS_FILE)
                if printer not in settings:
                    settings[printer] = {}

                try:
                    settings[printer].update(modern)
                    settings[printer]["rate_per_hour"] = rate_per_hour
                    settings[printer]["filament_mode"] = mode
                    settings[printer]["filament_rate"] = filament_rate
                    settings[printer]["grams_per_meter"] = grams_per_meter
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
                              "Set a Moonraker URL for this printer, then retry."
                        ),
                    )
                )

            ok, detail, _payload = test_moonraker_url(base_url)
            if ok:
                return redirect(url_for(_settings_endpoint_for_action(action), msg=f"Moonraker OK for {printer}: {base_url}"))
            return redirect(url_for(_settings_endpoint_for_action(action), error=f"Moonraker test failed for {printer}: {detail} ({base_url})"))

        if action == "save_moonraker_url":
            printer = (request.form.get("printer") or "").strip()
            url_raw = (request.form.get("moonraker_url") or "").strip()
            if not printer:
                return redirect(url_for(_settings_endpoint_for_action(action), error="Missing printer name."))
            if _is_sql_only() and not _sql_only_printer_exists(printer):
                return redirect(url_for(_settings_endpoint_for_action(action), error=f"Printer not found: {printer}"))

            url = url_raw.strip()
            if url and not (url.startswith("http://") or url.startswith("https://")):
                return redirect(url_for(_settings_endpoint_for_action(action), error="Moonraker URL must start with http:// or https://"))

            try:
                if _is_sql_only():
                    with closing(db_module.connect_db()) as conn:
                        db_module.apply_migrations(conn)
                        db_module.upsert_printer(conn, printer, url)
                        conn.commit()
                else:
                    settings = load_settings(SETTINGS_FILE)
                    if printer not in settings:
                        settings[printer] = {}
                    settings[printer]["moonraker_url"] = url or ""
                    save_settings(SETTINGS_FILE, DATA_DIR, settings)
            except Exception as exc:
                return redirect(url_for(_settings_endpoint_for_action(action), error=f"Failed to save Moonraker URL: {exc}"))

            return redirect(url_for(_settings_endpoint_for_action(action), msg=f"Moonraker URL saved for {printer}."))

        if action == "test_moonraker_url":
            printer = (request.form.get("printer") or "").strip()
            url_raw = (request.form.get("moonraker_url") or "").strip()
            if not printer:
                return redirect(url_for(_settings_endpoint_for_action(action), error="Missing printer name."))

            url = url_raw.strip()
            if not url:
                url = thumbs.resolve_moonraker_base_url(printer) or ""
            if not url:
                return redirect(url_for(_settings_endpoint_for_action(action), error=f"No Moonraker URL configured for {printer}."))
            if url and not (url.startswith("http://") or url.startswith("https://")):
                return redirect(url_for(_settings_endpoint_for_action(action), error="Moonraker URL must start with http:// or https://"))

            probe = probe_moonraker_server_info(url)
            if probe.get("ok"):
                return redirect(
                    url_for(
                        _settings_endpoint_for_action(action),
                        msg=f"Moonraker OK for {printer}: {url}",
                        **{f"moonraker_url_{printer}": url},
                    )
                )
            detail = probe.get("error") or "Probe failed"
            code = probe.get("status_code")
            return redirect(
                url_for(
                    _settings_endpoint_for_action(action),
                    error=f"Moonraker test failed for {printer}: {detail} ({code}) {url}",
                    **{f"moonraker_url_{printer}": url},
                )
            )

        if action == "import_moonraker_history":
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
                              "Set a Moonraker URL for this printer, then retry."
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

            import_mode = (request.form.get("import_mode") or "").strip().lower()
            if import_mode not in ("skip", "overwrite"):
                import_mode = "overwrite" if request.form.get("import_overwrite_existing") else "skip"
            skip_existing = import_mode != "overwrite"
            overwrite_existing = import_mode == "overwrite"

            if _is_sql_only():
                summary = import_moonraker_history_to_sql(
                    printer_name=printer,
                    base_url=base_url,
                    limit=limit,
                    skip_existing=skip_existing,
                    overwrite_existing=overwrite_existing,
                )
            else:
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
                allowed = [
                    "printer",
                    "filename",
                    "status",
                    "hours",
                    "filament",
                    "time_cost",
                    "material_cost",
                    "total",
                    "job_uid",
                ]
            elif table_id == "projects_unassigned":
                allowed = PROJECTS_UNASSIGNED_COLUMNS
            elif table_id == "projects_project_jobs":
                allowed = PROJECTS_TRACKED_COLUMNS
                cols = ["total_cost" if column == "cost" else column for column in cols]
            elif table_id == "projects_manual_jobs":
                allowed = PROJECTS_MANUAL_COLUMNS
            elif table_id == "projects_planned_items":
                allowed = PROJECTS_PLANNED_COLUMNS
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
                if _is_sql_only():
                    from core import printer_lifecycle

                    printer_lifecycle.rename_printer(old, new)
                else:
                    rename_printer(old, new)
            return redirect(url_for(_settings_endpoint_for_action(action)))

        if action == "merge_printers":
            primary = request.form.get("primary", "").strip()
            secondary = request.form.get("secondary", "").strip()
            if primary and secondary:
                if _is_sql_only():
                    from core import printer_lifecycle

                    printer_lifecycle.merge_printers(primary, secondary)
                else:
                    merge_printers([secondary], primary)
            return redirect(url_for(_settings_endpoint_for_action(action)))

        if action == "add_filament_profile":
            name = request.form.get("profile_name", "").strip()
            material = request.form.get("material", "").strip()
            brand = request.form.get("brand", "").strip()
            color = request.form.get("color", "").strip()
            mode = request.form.get("filament_mode", "").strip()
            description = request.form.get("description", "").strip()
            from core.filament import configuration_from_form
            try:
                modern=configuration_from_form(request.form)
            except ValueError as exc:
                return redirect(url_for(_settings_endpoint_for_action(action),error=str(exc)))
            mode=modern.get('filament_mode',mode)
            if _is_sql_only():
                if mode not in {"per_meter", "per_gram", "per_kg"}:
                    return redirect(url_for(_settings_endpoint_for_action(action), error="Missing filament mode."))
                rate, err = _parse_required_nonneg_float(modern.get("filament_rate",request.form.get("filament_rate")), "filament rate")
                if err:
                    return redirect(url_for(_settings_endpoint_for_action(action), error=err))
                gpm, err = _parse_required_positive_float(modern.get("grams_per_meter",request.form.get("grams_per_meter")), "grams per meter")
                if err:
                    return redirect(url_for(_settings_endpoint_for_action(action), error=err))
            else:
                if not mode:
                    mode = "per_meter"
                try:
                    rate = float(request.form.get("filament_rate", 0.25))
                except (TypeError, ValueError):
                    rate = None
                try:
                    gpm = float(request.form.get("grams_per_meter", 3.0))
                except (TypeError, ValueError):
                    gpm = None
            rate=modern.get("filament_rate",rate)
            gpm=modern.get("grams_per_meter",gpm)
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
                profile.update(modern)
                try:
                    profiles.add_profile(profile)
                except ValueError as exc:
                    return redirect(url_for(_settings_endpoint_for_action(action),error=str(exc)))
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
                from core.filament import configuration_from_form
                try:
                    modern=configuration_from_form(request.form)
                except ValueError as exc:
                    return redirect(url_for(_settings_endpoint_for_action(action),error=str(exc)))
                mode=modern.get('filament_mode',mode)
                if _is_sql_only():
                    if mode not in {"per_meter", "per_gram", "per_kg"}:
                        return redirect(url_for(_settings_endpoint_for_action(action), error="Missing filament mode."))
                    filament_rate, err = _parse_required_nonneg_float(
                        modern.get("filament_rate",request.form.get("filament_rate")),
                        "filament rate",
                    )
                    if err:
                        return redirect(url_for(_settings_endpoint_for_action(action), error=err))
                    grams_per_meter, err = _parse_required_positive_float(
                        modern.get("grams_per_meter",request.form.get("grams_per_meter")),
                        "grams per meter",
                    )
                    if err:
                        return redirect(url_for(_settings_endpoint_for_action(action), error=err))
                if name:
                    updates["name"] = name
                updates["material"] = material
                updates["brand"] = brand
                updates["color"] = color
                if mode:
                    updates["filament_mode"] = mode
                if _is_sql_only():
                    updates["filament_rate"] = filament_rate
                    updates["grams_per_meter"] = grams_per_meter
                else:
                    try:
                        updates["filament_rate"] = float(request.form.get("filament_rate"))
                    except (TypeError, ValueError):
                        updates["filament_rate"] = None
                    try:
                        updates["grams_per_meter"] = float(request.form.get("grams_per_meter"))
                    except (TypeError, ValueError):
                        updates["grams_per_meter"] = None
                updates.update(modern)
                if "price_input_mode" not in updates:
                    updates["price_input_mode"]=mode
                try:
                    profiles.update_profile(profile_id, updates)
                except ValueError as exc:
                    return redirect(url_for(_settings_endpoint_for_action(action),error=str(exc)))
            return redirect(url_for(_settings_endpoint_for_action(action)))

        if action == "delete_filament_profile":
            profile_id = request.form.get("profile_id", "").strip()
            if profile_id:
                profile_obj = None
                try:
                    profile_obj = profiles.get_profile(profile_id)
                except Exception:
                    profile_obj = None
                try:
                    deleted=profiles.delete_profile(profile_id)
                except ValueError as exc:
                    return redirect(url_for(_settings_endpoint_for_action(action),error=str(exc)))
                if not deleted:return redirect(url_for(_settings_endpoint_for_action(action),error='Filament profile not found; nothing was deleted'))
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
                if _is_sql_only() and not _sql_only_printer_exists(printer):
                    return redirect(url_for(_settings_endpoint_for_action(action), error=f"Printer not found: {printer}"))
                normalized_profile_id = None if profile_id in {"", "none"} else profile_id
                saved = profiles.set_printer_mapping(printer, normalized_profile_id)
                if _is_sql_only() and normalized_profile_id and not saved:
                    return redirect(
                        url_for(
                            _settings_endpoint_for_action(action),
                            error=f"Filament profile not found: {normalized_profile_id}",
                        )
                    )
            return redirect(url_for(_settings_endpoint_for_action(action)))

        if action == "add_rate_profile":
            name = request.form.get("rate_profile_name", "").strip()
            description = request.form.get("rate_profile_description", "").strip()
            if _is_sql_only():
                rate_per_hour, err = _parse_required_nonneg_float(
                    request.form.get("rate_profile_rate"),
                    "hourly rate",
                )
                if err:
                    return redirect(url_for(_settings_endpoint_for_action(action), error=err))
            else:
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
                try:
                    rates.upsert_rate_profile(rate_profile)
                except ValueError as exc:
                    return redirect(url_for(_settings_endpoint_for_action(action),error=str(exc)))
            return redirect(url_for(_settings_endpoint_for_action(action)))

        if action == "update_rate_profile":
            profile_id = request.form.get("rate_profile_id", "").strip()
            if profile_id:
                updates = {}
                name = request.form.get("rate_profile_name", "").strip()
                description = request.form.get("rate_profile_description", "").strip()
                if _is_sql_only():
                    rate_per_hour, err = _parse_required_nonneg_float(
                        request.form.get("rate_profile_rate"),
                        "hourly rate",
                    )
                    if err:
                        return redirect(url_for(_settings_endpoint_for_action(action), error=err))
                if name:
                    updates["name"] = name
                updates["description"] = description
                if _is_sql_only():
                    updates["rate_per_hour"] = rate_per_hour
                else:
                    try:
                        updates["rate_per_hour"] = float(request.form.get("rate_profile_rate"))
                    except (TypeError, ValueError):
                        updates["rate_per_hour"] = None
                try:
                    rates.update_rate_profile(profile_id, updates)
                except ValueError as exc:
                    return redirect(url_for(_settings_endpoint_for_action(action),error=str(exc)))
            return redirect(url_for(_settings_endpoint_for_action(action)))

        if action == "delete_rate_profile":
            profile_id = request.form.get("rate_profile_id", "").strip()
            if profile_id:
                rate_obj = None
                try:
                    rate_obj = rates.get_rate_profile(profile_id)
                except Exception:
                    rate_obj = None
                try:
                    deleted=rates.delete_rate_profile(profile_id)
                except ValueError as exc:
                    return redirect(url_for(_settings_endpoint_for_action(action),error=str(exc)))
                if not deleted:return redirect(url_for(_settings_endpoint_for_action(action),error='Hourly profile not found; nothing was deleted'))
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
                if _is_sql_only() and not _sql_only_printer_exists(printer):
                    return redirect(url_for(_settings_endpoint_for_action(action), error=f"Printer not found: {printer}"))
                if _is_sql_only():
                    if not rates.set_printer_rate_profile_sql(printer,profile_id):
                        return redirect(url_for(_settings_endpoint_for_action(action),error=f'Rate profile not found: {profile_id}'))
                    return redirect(url_for(_settings_endpoint_for_action(action)))
                settings = load_settings(SETTINGS_FILE)
                if printer not in settings:
                    settings[printer] = {}
                normalized_profile_id = None if profile_id in {"", "none"} else profile_id
                if _is_sql_only() and normalized_profile_id and not rates.get_rate_profile(normalized_profile_id):
                    return redirect(
                        url_for(
                            _settings_endpoint_for_action(action),
                            error=f"Rate profile not found: {normalized_profile_id}",
                        )
                    )
                if normalized_profile_id is None:
                    settings[printer].pop("active_rate_profile_id", None)
                else:
                    settings[printer]["active_rate_profile_id"] = normalized_profile_id
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

    moonraker_urls = {}
    try:
        with closing(db_module.connect_db()) as conn:
            db_module.apply_migrations(conn)
            for p in printers:
                moonraker_urls[p] = db_module.get_printer_moonraker_url(conn, p) or ""
    except Exception:
        for p in printers:
            moonraker_urls[p] = ""

    if not isinstance(settings, dict):
        settings = {}
    for p in printers:
        if not moonraker_urls.get(p):
            moonraker_urls[p] = str(settings.get(p, {}).get("moonraker_url") or "").strip()

    # Preserve any in-form Moonraker URL when returning from a test action.
    try:
        for p in printers:
            arg_key = f"moonraker_url_{p}"
            if arg_key in request.args:
                moonraker_urls[p] = (request.args.get(arg_key) or "").strip()
    except Exception:
        pass

    active_rate_profiles = {}
    for p in printers:
        active_rate_profiles[p] = settings.get(p, {}).get("active_rate_profile_id", "")

    display_settings = load_display_settings(DISPLAY_FILE, HEADERS)
    selected_columns = display_settings.get("visible_columns") or HEADERS

    # Settings → Other: per-table column visibility (server-backed display.json).
    # Defaults: show all allowed columns if no saved selection exists.
    recalc_allowed_cols = [
        "printer",
        "filename",
        "status",
        "hours",
        "filament",
        "time_cost",
        "material_cost",
        "total",
        "job_uid",
    ]
    projects_unassigned_allowed_cols = PROJECTS_UNASSIGNED_COLUMNS
    projects_project_jobs_allowed_cols = PROJECTS_TRACKED_COLUMNS
    projects_manual_jobs_allowed_cols = PROJECTS_MANUAL_COLUMNS
    projects_planned_items_allowed_cols = PROJECTS_PLANNED_COLUMNS

    recalc_selected_columns = get_visible_columns_for_table(display_settings, "recalc_jobs", recalc_allowed_cols)
    projects_unassigned_selected_columns = get_visible_columns_for_table(
        display_settings, "projects_unassigned", projects_unassigned_allowed_cols
    )
    projects_project_jobs_selected_columns = _get_projects_visible_columns(
        display_settings, "projects_project_jobs", projects_project_jobs_allowed_cols
    )
    projects_manual_jobs_selected_columns = _get_projects_visible_columns(
        display_settings, "projects_manual_jobs", projects_manual_jobs_allowed_cols
    )
    projects_planned_items_selected_columns = _get_projects_visible_columns(
        display_settings, "projects_planned_items", projects_planned_items_allowed_cols
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
        moonraker_urls=moonraker_urls,
        headers=[h for h in HEADERS if h != "job_uid"],
        friendly_headers=FRIENDLY_HEADERS,
        selected_columns=selected_columns,
        recalc_allowed_cols=recalc_allowed_cols,
        recalc_selected_columns=recalc_selected_columns,
        projects_unassigned_allowed_cols=projects_unassigned_allowed_cols,
        projects_unassigned_selected_columns=projects_unassigned_selected_columns,
        projects_project_jobs_allowed_cols=projects_project_jobs_allowed_cols,
        projects_project_jobs_selected_columns=projects_project_jobs_selected_columns,
        projects_manual_jobs_allowed_cols=projects_manual_jobs_allowed_cols,
        projects_manual_jobs_selected_columns=projects_manual_jobs_selected_columns,
        projects_planned_items_allowed_cols=projects_planned_items_allowed_cols,
        projects_planned_items_selected_columns=projects_planned_items_selected_columns,
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
    if _is_sql_only():
        try:
            from core.export_csv import export_csv_from_sql
            tmp_path = os.path.join(DATA_DIR, "_tmp_print_costs_from_sql.csv")
            export_csv_from_sql(out_path=tmp_path, overwrite=True)
            return send_file(tmp_path, as_attachment=True, download_name="print_costs.csv")
        except Exception as exc:
            return f"CSV export failed: {exc}", 500
    if not os.path.exists(CSV_FILE):
        return "No data available", 404
    return send_file(CSV_FILE, as_attachment=True, download_name="print_costs.csv")


@app.context_processor
def application_mode_context():
    mode = get_application_mode()
    return {
        "application_mode": mode,
        "show_advanced": mode != "personal",
        "show_orders": mode == "business" and _is_sql_only(),
    }


# ============================================================================
# APPLICATION STARTUP
# ============================================================================

if is_demo_runtime():
    _missing_demo_policies = app.extensions["kcd_demo_policy"].missing_routes(app)
    if _missing_demo_policies:
        raise RuntimeConfigurationError(
            "Demo route policy is incomplete: "
            + ", ".join(
                f"{method} {rule} ({endpoint})"
                for endpoint, method, rule in _missing_demo_policies
            )
        )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
