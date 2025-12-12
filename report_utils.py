from datetime import datetime, timedelta
from core.storage import ts_to_local_dt

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
        
        if quick_range == "last7":
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

def compute_monthly_breakdown(rows):
    """
    Aggregate stats by (year, month).
    Returns list of dicts: {label: "YYYY-MM", count: N, hours: H, cost: C}
    """
    groups = {}
    for r in rows:
        ts_raw = r.get("timestamp_raw")
        if not ts_raw:
            continue
        try:
            dt = ts_to_local_dt(float(ts_raw))
        except Exception:
            continue
            
        key = dt.strftime("%Y-%m")
        if key not in groups:
            groups[key] = {"count": 0, "hours": 0.0, "cost": 0.0}
            
        groups[key]["count"] += 1
        groups[key]["hours"] += float(r.get("duration_hours") or 0.0)
        groups[key]["cost"] += float(r.get("total_cost") or 0.0)
        
    # Sort by month descending
    sorted_keys = sorted(groups.keys(), reverse=True)
    result = []
    for k in sorted_keys:
        d = groups[k]
        result.append({
            "label": k,
            "count": d["count"],
            "hours": d["hours"],
            "cost": d["cost"]
        })
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
