"""Printer history presentation using recorded measurements and prices."""

from collections import defaultdict
from datetime import date, datetime, time, timedelta

from core.filament import describe_job
from core.numeric import NumericValidationError, finite_float


def report_periods(start_value, end_value, now):
    """Use inclusive local dates and an optional equal-length prior period."""
    today = now.date()
    try:
        end_day = date.fromisoformat(end_value) if end_value else today
        start_day = date.fromisoformat(start_value) if start_value else end_day - timedelta(days=29)
        days = (end_day - start_day).days + 1
        if not 1 <= days <= 366:
            raise ValueError("Choose a date range of 1 to 366 days.")
        if end_day > today:
            raise ValueError("The end date cannot be in the future.")
        prior_day = start_day - timedelta(days=days)
    except OverflowError as exc:
        raise ValueError("Choose dates within the supported calendar.") from exc

    def midnight(day):
        return datetime.combine(day, time.min, tzinfo=now.tzinfo)

    start = midnight(start_day)
    end = midnight(end_day + timedelta(days=1))
    return (start, end), (midnight(prior_day), start)


def _known(value, *, nonnegative=False):
    try:
        return finite_float(value, label="Recorded value", nonnegative=nonnegative)
    except NumericValidationError:
        return None


def display_job(row):
    """Build a view row and keep unknown cost and measurement values explicit."""
    item = describe_job(dict(row))
    status = str(row.get("job_outcome") or row.get("status") or "").strip().lower()
    item["outcome"] = {
        "complete": "Completed", "completed": "Completed", "success": "Completed",
        "failed": "Failed", "error": "Failed",
        "canceled": "Canceled", "cancelled": "Canceled",
    }.get(status, "Other or unknown")
    seconds = _known(row.get("duration_seconds"), nonnegative=True)
    item["hours"] = seconds / 3600 if seconds is not None else _known(row.get("duration_hours"), nonnegative=True)
    for key in ("time_cost", "material_cost", "total_cost"):
        item[key] = _known(row.get(key))
    return item


def summarize(rows, period, now):
    start, end = period
    totals = {key: None for key in ("hours", "_weight_grams", "_length_meters", "time_cost", "material_cost", "total_cost")}
    unknown = dict.fromkeys(totals, 0)
    counts = dict.fromkeys(("Completed", "Failed", "Canceled", "Other or unknown"), 0)
    # Short ranges show daily activity. Longer ranges use local calendar weeks.
    weekly = (end.date() - start.date()).days > 14
    trend = defaultdict(float)
    for raw in rows:
        row = display_job(raw)
        counts[row["outcome"]] += 1
        for key in totals:
            if row[key] is None:
                unknown[key] += 1
            else:
                totals[key] = (totals[key] or 0.0) + row[key]
        stamp = _known(row.get("timestamp_raw"))
        if stamp is not None and row["hours"] is not None:
            try:
                day = datetime.fromtimestamp(stamp, tz=now.tzinfo).date()
            except (OverflowError, OSError, ValueError):
                continue
            if weekly:
                day -= timedelta(days=day.weekday())
            trend[day] += row["hours"]
    elapsed_hours = (min(end, now).timestamp() - start.timestamp()) / 3600
    utilization = None
    if elapsed_hours > 0 and totals["hours"] is not None:
        utilization = 100 * totals["hours"] / elapsed_hours
    return {
        "job_count": len(rows), "counts": counts, "totals": totals, "unknown": unknown,
        "utilization": utilization, "trend": sorted(trend.items()), "weekly": weekly,
    }
