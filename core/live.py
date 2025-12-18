"""
Live Job State Management

Tracks active print jobs per printer with status, timing, and cost estimation support.
"""
import time
import os
from core.config import DATA_DIR
from core.storage import load_json_file, save_json_file

# File for persisting live job state
LIVE_JOBS_FILE = os.path.join(DATA_DIR, "live_jobs.json")

# In-memory job state: printer_name -> job_dict
_jobs = {}


def _load_state():
    """Load live job state from disk."""
    global _jobs
    data = load_json_file(LIVE_JOBS_FILE)
    _jobs = data.get("jobs", {}) if data else {}


def _save_state():
    """Save live job state to disk."""
    save_json_file(LIVE_JOBS_FILE, DATA_DIR, {"jobs": _jobs})


def start_job(printer_name, filename, start_time=None, estimated_duration=None, 
              estimated_filament=None, profile_id=None):
    """
    Start a new print job for a printer.
    
    Args:
        printer_name: Name of the printer
        filename: Name of the file being printed
        start_time: Unix timestamp (uses current time if None)
        estimated_duration: Estimated duration in seconds (optional)
        estimated_filament: Estimated filament in mm (optional)
        profile_id: Filament profile ID (optional)
    
    Returns:
        dict: The created job state
    """
    if start_time is None:
        start_time = time.time()
    
    job = {
        "printer_name": printer_name,
        "filename": filename,
        "status": "printing",
        "start_time": float(start_time),
        "total_paused_duration": 0.0,
        "pause_count": 0,
        "runout_count": 0,
    }
    
    # Add optional fields if provided
    if estimated_duration is not None:
        job["estimated_duration"] = float(estimated_duration)
    if estimated_filament is not None:
        job["estimated_filament_mm"] = float(estimated_filament)
    if profile_id:
        job["profile_id"] = str(profile_id)
    
    _jobs[printer_name] = job
    _save_state()
    return job


def update_job(printer_name, **kwargs):
    """
    Update fields of an existing job.
    
    Args:
        printer_name: Name of the printer
        **kwargs: Fields to update
    
    Returns:
        dict: Updated job state or None if job doesn't exist
    """
    if printer_name not in _jobs:
        return None
    
    job = _jobs[printer_name]
    
    # Update allowed fields
    allowed_fields = {
        "filename", "estimated_duration", "estimated_filament_mm", "profile_id"
    }
    for key, value in kwargs.items():
        if key in allowed_fields:
            job[key] = value
    
    _save_state()
    return job


def pause_job(printer_name, reason=None):
    """
    Pause an active job.
    
    Args:
        printer_name: Name of the printer
        reason: Optional pause reason (e.g. user_pause, filament_runout, filament_change)
    
    Returns:
        dict: Updated job state or None if job doesn't exist
    """
    if printer_name not in _jobs:
        return None
    
    job = _jobs[printer_name]
    incoming_reason = str(reason or "").strip()
    if job.get("status") == "printing":
        job["status"] = "paused"
        job["pause_time"] = time.time()
        job["pause_count"] = int(job.get("pause_count") or 0) + 1
        if incoming_reason:
            job["pause_reason"] = incoming_reason
            if incoming_reason == "filament_runout":
                job["runout_count"] = int(job.get("runout_count") or 0) + 1
        _save_state()
    elif job.get("status") == "paused":
        # Idempotent: don't reset pause_time, but allow reason to be updated.
        if incoming_reason and incoming_reason != str(job.get("pause_reason") or "").strip():
            job["pause_reason"] = incoming_reason
            _save_state()
    
    return job


def resume_job(printer_name):
    """
    Resume a paused job.
    
    Args:
        printer_name: Name of the printer
    
    Returns:
        dict: Updated job state or None if job doesn't exist
    """
    if printer_name not in _jobs:
        return None
    
    job = _jobs[printer_name]
    if job.get("status") == "paused":
        pause_time = job.get("pause_time", time.time())
        pause_duration = time.time() - pause_time
        job["total_paused_duration"] = job.get("total_paused_duration", 0) + pause_duration
        job["status"] = "printing"
        if "pause_time" in job:
            del job["pause_time"]
        if "pause_reason" in job:
            del job["pause_reason"]
        _save_state()
    
    return job


def cancel_job(printer_name, reason=None):
    """
    Cancel an active job.
    
    Args:
        printer_name: Name of the printer
        reason: Optional failure/cancel reason
    
    Returns:
        dict: Updated job state or None if job doesn't exist
    """
    if printer_name not in _jobs:
        return None

    now = time.time()
    job = _jobs[printer_name].copy()

    # Compute elapsed seconds (pause-aware; excludes paused duration).
    start_time = float(job.get("start_time") or now)
    total_paused = float(job.get("total_paused_duration") or 0.0)
    status = (job.get("status") or "").lower()

    if status == "paused":
        pause_time = float(job.get("pause_time") or now)
        pause_duration = max(0.0, now - pause_time)
        job["total_paused_duration"] = total_paused + pause_duration
        elapsed = pause_time - start_time - total_paused
    else:
        elapsed = now - start_time - total_paused

    job["elapsed_seconds"] = max(0.0, float(elapsed))
    job["status"] = "canceled"
    job["failure_reason"] = str(reason or "").strip()
    job["end_time"] = float(now)
    if "pause_time" in job:
        del job["pause_time"]

    # Remove from active jobs so "Now Printing" clears immediately.
    del _jobs[printer_name]
    _save_state()
    return job


def end_job(printer_name):
    """
    Mark a job as completed and remove from active jobs.
    
    Args:
        printer_name: Name of the printer
    
    Returns:
        dict: Final job state or None if job doesn't exist
    """
    if printer_name not in _jobs:
        return None
    
    job = _jobs[printer_name].copy()
    job["status"] = "completed"
    job["failure_reason"] = ""
    job["end_time"] = time.time()
    
    # Remove from active jobs
    del _jobs[printer_name]
    _save_state()
    
    return job


def get_job(printer_name):
    """
    Get the current job for a printer.
    
    Args:
        printer_name: Name of the printer
    
    Returns:
        dict: Job state with enriched cost data, or None if no active job
    """
    if printer_name not in _jobs:
        return None
    
    job = _jobs[printer_name].copy()
    return _enrich_job_with_costs(job)


def list_active_jobs():
    """
    Get all active jobs (printing or paused).
    
    Returns:
        list: List of job dicts with enriched cost data
    """
    active = []
    for printer_name, job in _jobs.items():
        if job.get("status") in ["printing", "paused"]:
            enriched = _enrich_job_with_costs(job.copy())
            active.append(enriched)
    
    return active


def _enrich_job_with_costs(job):
    """
    Add live cost fields to a job dict.
    
    Args:
        job: Job dict
    
    Returns:
        dict: Job with added cost fields
    """
    if not job or job.get("status") not in ["printing", "paused"]:
        return job
    
    # Import here to avoid circular dependency
    from core import pricing
    
    start_time = job.get("start_time", time.time())
    total_paused = job.get("total_paused_duration", 0)

    # Calculate elapsed time (excluding pauses)
    if job.get("status") == "paused":
        pause_time = job.get("pause_time", time.time())
        pause_duration = max(0.0, time.time() - pause_time)
        elapsed = pause_time - start_time - total_paused
    else:
        pause_duration = 0.0
        elapsed = time.time() - start_time - total_paused

    # Live paused duration: persisted pauses + current pause segment (if any).
    job["paused_seconds"] = max(0.0, float(total_paused) + float(pause_duration))
    
    job["elapsed_seconds"] = max(0, elapsed)
    
    # Compute current time cost
    try:
        job["current_time_cost"] = pricing.compute_live_time_cost(
            job["printer_name"], 
            job["elapsed_seconds"]
        )
    except Exception:
        job["current_time_cost"] = 0.0
    
    # Compute estimated final cost if we have estimates
    if job.get("estimated_duration") and job.get("estimated_filament_mm"):
        try:
            estimates = pricing.compute_estimated_final_cost(
                job["printer_name"],
                job["estimated_duration"],
                job["estimated_filament_mm"],
                job.get("profile_id")
            )
            job.update(estimates)
        except Exception:
            pass
    
    return job


def get_job_metadata_for_logging(printer_name):
    """
    Get metadata from live job for CSV logging.
    
    Args:
        printer_name: Name of the printer
    
    Returns:
        dict with metadata or None if no job exists:
        - actual_start_time: When job really started (Unix timestamp)
        - total_paused_duration: Total pause time in seconds
        - profile_id: Filament profile used
        - filename: Job filename
    """
    if printer_name not in _jobs:
        return None
    
    job = _jobs[printer_name]

    total_paused = float(job.get("total_paused_duration") or 0.0)
    if str(job.get("status") or "").lower() == "paused":
        pause_time = float(job.get("pause_time") or time.time())
        total_paused += max(0.0, time.time() - pause_time)

    return {
        "actual_start_time": job.get("start_time"),
        "total_paused_duration": total_paused,
        "profile_id": job.get("profile_id"),
        "filename": job.get("filename"),
        "pause_reason": job.get("pause_reason"),
        "pause_count": int(job.get("pause_count") or 0),
        "runout_count": int(job.get("runout_count") or 0),
    }


# Load state on module import
_load_state()
