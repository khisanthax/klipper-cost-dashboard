"""
Reporting utilities for Print Cost Dashboard.

Merged from report_utils.py + additional reporting functions from app.py.
"""
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from core.config import DEFAULT_TIMEZONE, SETTINGS_FILE, DISPLAY_FILE, HEADERS
from core.storage import ts_to_local_dt, load_settings, load_display_settings
from core.printers import get_canonical_printer_names


def _parse_date(s):
    """Parse YYYY-MM-DD string to datetime object."""
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=ts_to_local_dt(0).tzinfo)
    except ValueError:
        return None


def get_date_range_from_params(args):
    """
    Extract start_date and end_date from request args.
    Also handles 'quick_range' presets.
    Returns (start_dt, end_dt, range_label, quick_range_key)
    """
    start_str = args.get("start_date", "")
    end_str = args.get("end_date", "")
    quick_range = args.get("quick_range", "")
    
    start_dt = _parse_date(start_str)
    end_dt = _parse_date(end_str)
    
    range_label = "Custom range"
    
    # If quick range is selected, it overrides manual dates
    if quick_range:
        now = ts_to_local_dt(datetime.now().timestamp())
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        
        if quick_range == "today":
            start_dt = today
            end_dt = now
            range_label = "Today"
        elif quick_range == "yesterday":
            start_dt = today - timedelta(days=1)
            end_dt = today
            range_label = "Yesterday"
        elif quick_range == "this_week":
            start_dt = today - timedelta(days=today.weekday())
            end_dt = now
            range_label = "This week"
        elif quick_range == "last7":
            start_dt = today - timedelta(days=7)
            end_dt = now
            range_label = "Last 7 days"
        elif quick_range == "last30":
            start_dt = today - timedelta(days=30)
            end_dt = now
            range_label = "Last 30 days"
        elif quick_range == "this_month":
            start_dt = today.replace(day=1)
            end_dt = now
            range_label = "This month"
        elif quick_range == "last_month":
            # First day of this month
            this_month_start = today.replace(day=1)
            # Last day of last month = this_month_start - 1 day
            end_dt = this_month_start - timedelta(days=1)
            # First day of last month
            start_dt = end_dt.replace(day=1)
            # Set end_dt to end of that day
            end_dt = end_dt.replace(hour=23, minute=59, second=59)
            range_label = "Last month"
            
    if not start_dt and not end_dt:
        range_label = "All time"
        
    return start_dt, end_dt, range_label, quick_range


_RECORDED_OUTCOMES = {
    "complete": "completed", "completed": "completed", "success": "completed", "succeeded": "completed",
    "canceled": "canceled", "cancelled": "canceled",
    "failed": "failed", "error": "failed",
}


def _recorded_number(value):
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def compute_monthly_breakdown(rows):
    """Group recorded outcomes and known values by the configured local calendar."""
    groups = {}
    for row in rows:
        try:
            label = ts_to_local_dt(float(row["timestamp_raw"])).strftime("%Y-%m")
        except (KeyError, TypeError, ValueError, OverflowError, OSError):
            label = "Unknown date"
        month = groups.setdefault(label, {
            "label": label, "count": 0, "completed": 0, "canceled": 0, "failed": 0,
            "unknown": 0, "hours": Decimal(0), "cost": Decimal(0), "missing_hours": 0, "missing_cost": 0,
        })
        month["count"] += 1
        outcome = _RECORDED_OUTCOMES.get(str(row.get("status") or "").strip().lower(), "unknown")
        month[outcome] += 1
        hours = _recorded_number(row.get("duration_hours"))
        cost = _recorded_number(row.get("total_cost"))
        if hours is None or hours < 0:
            month["missing_hours"] += 1
        else:
            month["hours"] += hours
        if cost is None:
            month["missing_cost"] += 1
        else:
            month["cost"] += cost

    result = []
    for label in sorted(groups, key=lambda name: (name != "Unknown date", name), reverse=True):
        month = groups[label]
        known = month["count"] - month["unknown"]
        month["completion_rate"] = 100 * month["completed"] / known if known else None
        for field in ("hours", "cost"):
            value = month[field]
            missing = month["missing_" + field] == month["count"]
            month[field + "_display"] = None if missing else format(value, ".2f")
            month[field] = None if missing else float(value)
        result.append(month)
    return result


def compute_top_printers(rows, limit=5):
    """
    Aggregate cost by printer and return top N.
    """
    stats = {}
    for r in rows:
        p = r.get("printer", "Unknown")
        try:
            cost = float(r.get("total_cost") or 0.0)
        except ValueError:
            cost = 0.0
        
        if p not in stats:
            stats[p] = {"name": p, "count": 0, "cost": 0.0}
        
        stats[p]["count"] += 1
        stats[p]["cost"] += cost
    
    sorted_printers = sorted(stats.values(), key=lambda x: x["cost"], reverse=True)
    return sorted_printers[:limit]


def compute_summary(rows):
    """
    Compute summary statistics for a list of print rows.
    Return totals and chart groups using local calendar dates.
    """
    total_prints = len(rows)
    total_hours = 0.0
    total_meters = 0.0
    total_cost = 0.0
    per_day = {}
    per_printer = {}

    for r in rows:
        try:
            total_hours += float(r.get("duration_hours") or 0.0)
            total_meters += float(r.get("filament_meters") or 0.0)
            cost = float(r.get("total_cost") or 0.0)
            hours = float(r.get("duration_hours") or 0.0)
            total_cost += cost
            printer = str(r.get('printer') or 'Unknown')
            group = per_printer.setdefault(printer, {'hours': 0.0, 'cost': 0.0, 'count': 0})
            group['hours'] += hours; group['cost'] += cost; group['count'] += 1
            timestamp = r.get('timestamp_raw')
            day = ts_to_local_dt(float(timestamp)).strftime('%Y-%m-%d') if timestamp is not None else str(r.get('timestamp') or '')[:10]
            if day:
                group = per_day.setdefault(day, {'hours': 0.0, 'cost': 0.0, 'count': 0})
                group['hours'] += hours; group['cost'] += cost; group['count'] += 1
        except (TypeError, ValueError):
            continue

    days=sorted(per_day)
    try:days_span=(datetime.fromisoformat(days[-1])-datetime.fromisoformat(days[0])).days+1 if days else 0
    except ValueError:days_span=0
    return {
        "total_prints": total_prints,
        "total_hours": total_hours,
        "total_meters": total_meters,
        "total_cost": total_cost,
        "per_day": per_day,
        "per_printer": per_printer,
        "days_span": days_span,
    }


def compute_pause_analytics(rows):
    """
    Compute pause/runout analytics from finalized history rows.

    Returns:
      {
        "total_paused_seconds": float,
        "average_paused_seconds": float,
        "runouts_by_printer": {printer: int},
      }
    """
    total_paused = 0.0
    total_prints = 0
    runouts_by_printer = {}

    for r in rows:
        total_prints += 1
        try:
            total_paused += float(r.get("paused_seconds_total") or 0.0)
        except (TypeError, ValueError):
            pass

        printer = str(r.get("printer") or "").strip()
        if printer:
            try:
                runouts = int(float(r.get("runout_count") or 0))
            except Exception:
                runouts = 0
            if runouts:
                runouts_by_printer[printer] = runouts_by_printer.get(printer, 0) + runouts

    avg = (total_paused / total_prints) if total_prints else 0.0
    return {
        "total_paused_seconds": float(total_paused),
        "average_paused_seconds": float(avg),
        "runouts_by_printer": runouts_by_printer,
    }


def aggregate_by_material(rows):
    """
    Aggregate print statistics by filament material.
    Returns list of dicts: {material: str, count: int, hours: float, cost: float}
    Sorted by cost descending.
    """
    stats = {}
    for r in rows:
        material = r.get("filament_material", "").strip()
        if not material:
            material = "Unknown"
        
        if material not in stats:
            stats[material] = {"count": 0, "hours": 0.0, "cost": 0.0}
        
        stats[material]["count"] += 1
        try:
            stats[material]["hours"] += float(r.get("duration_hours") or 0.0)
            stats[material]["cost"] += float(r.get("total_cost") or 0.0)
        except ValueError:
            pass
    
    # Convert to list and sort by cost descending
    result = []
    for material, data in stats.items():
        result.append({
            "material": material,
            "count": data["count"],
            "hours": data["hours"],
            "cost": data["cost"]
        })
    
    result.sort(key=lambda x: x["cost"], reverse=True)
    return result


def aggregate_by_profile(rows, profiles_dict):
    """
    Aggregate print statistics by filament profile.
    
    Args:
        rows: List of print row dicts
        profiles_dict: Dict of profile_id -> profile_data (from core.profiles)
    
    Returns list of dicts: {profile_id: str, profile_name: str, count: int, hours: float, cost: float}
    Sorted by cost descending.
    """
    stats = {}
    for r in rows:
        profile_id = r.get("filament_profile_id", "").strip()
        if not profile_id:
            profile_id = "none"
        
        if profile_id not in stats:
            stats[profile_id] = {"count": 0, "hours": 0.0, "cost": 0.0}
        
        stats[profile_id]["count"] += 1
        try:
            stats[profile_id]["hours"] += float(r.get("duration_hours") or 0.0)
            stats[profile_id]["cost"] += float(r.get("total_cost") or 0.0)
        except ValueError:
            pass
    
    # Convert to list with profile names
    result = []
    for profile_id, data in stats.items():
        if profile_id == "none":
            profile_name = "No Profile (Defaults)"
        else:
            profile = profiles_dict.get(profile_id, {})
            profile_name = profile.get("name", f"Unknown ({profile_id[:8]})")
        
        result.append({
            "profile_id": profile_id,
            "profile_name": profile_name,
            "count": data["count"],
            "hours": data["hours"],
            "cost": data["cost"]
        })
    
    result.sort(key=lambda x: x["cost"], reverse=True)
    return result


def compute_printer_summaries(rows, live_jobs_list):
    """
    Compute per-printer summary data for status cards.
    
    Args:
        rows: Historical CSV rows
        live_jobs_list: List of active jobs from core.live
    
    Returns:
        dict: {printer_name: {status, last_job_name, today_hours, today_cost, ...}}
    """
    from datetime import datetime
    
    def _norm(name) -> str:
        return str(name or "").strip()

    summaries = {}

    # Printers are registry-only: do not derive printer identities from CSV rows
    # or live job payloads (which may contain swapped printer/filename fields).
    # Respect soft-delete ("hidden_printers") so deleted/hid printers don't keep
    # reappearing in the dashboard cards due to CSV history.
    hidden = {_norm(p) for p in load_display_settings(DISPLAY_FILE, HEADERS).get("hidden_printers", [])}
    printers = {p for p in get_canonical_printer_names(include_hidden=True) if _norm(p)}
    printers = {p for p in printers if p not in hidden and p != "Unknown"}
    
    # Compute for each printer
    today_start = ts_to_local_dt(datetime.now().timestamp()).replace(hour=0, minute=0, second=0, microsecond=0)
    today_ts = today_start.timestamp()
    
    for printer in sorted(printers):
            
        summary = {
            "status": "idle",
            "last_job_name": "No jobs yet",
            "last_job_time": None,
            "today_hours": 0.0,
            "today_cost": 0.0,
            "profile_name": None,
        }
        
        # Check for active job
        for job in live_jobs_list:
            if _norm(job.get("printer_name")) == printer:
                summary["status"] = job.get("status", "printing")
                summary["last_job_name"] = job.get("filename", "Unknown")
                summary["profile_name"] = job.get("profile_id")
                break
        
        # Find most recent completed job
        printer_rows = [r for r in rows if _norm(r.get("printer")) == printer]
        if printer_rows:
            # Sort by timestamp descending
            sorted_rows = sorted(
                printer_rows,
                key=lambda x: float(x.get("timestamp_raw", 0) or 0),
                reverse=True
            )
            if sorted_rows and summary["status"] == "idle":
                latest = sorted_rows[0]
                summary["last_job_name"] = latest.get("filename", "Unknown")
                summary["last_job_time"] = latest.get("timestamp")
        
        # Compute today's totals
        for r in printer_rows:
            ts_raw = r.get("timestamp_raw")
            if ts_raw:
                try:
                    if today_ts <= float(ts_raw) <= datetime.now().timestamp():
                        summary["today_hours"] += float(r.get("duration_hours", 0) or 0)
                        summary["today_cost"] += float(r.get("total_cost", 0) or 0)
                except (ValueError, TypeError):
                    pass
        
        summaries[printer] = summary
    
    return summaries
