"""
File I/O and data persistence for Print Cost Dashboard.
"""
import os
import csv
import json
import secrets
import hashlib
from datetime import datetime, timezone
try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None


def ensure_settings_exists(settings_file, default_pricing):
    """Create a default settings.json if it doesn't exist."""
    if not os.path.exists(settings_file):
        initial = {}
        for pname in ["SV08", "SV07", "Ender5P"]:
            initial[pname] = dict(default_pricing)
        with open(settings_file, "w") as f:
            json.dump(initial, f, indent=2)


def load_settings(settings_file):
    """Load printer settings from JSON file."""
    from core.config import DEFAULT_PRICING
    ensure_settings_exists(settings_file, DEFAULT_PRICING)
    try:
        with open(settings_file) as f:
            data = json.load(f)
            if not isinstance(data, dict):
                return {}
            return data
    except Exception:
        return {}


def save_settings(settings_file, data_dir, settings):
    """Save printer settings to JSON file."""
    os.makedirs(data_dir, exist_ok=True)
    with open(settings_file, "w") as f:
        json.dump(settings, f, indent=2)


def ensure_display_exists(display_file, headers):
    """Create a default display.json if it doesn't exist."""
    if not os.path.exists(display_file):
        data = {"visible_columns": headers, "hidden_printers": []}
        with open(display_file, "w") as f:
            json.dump(data, f, indent=2)


def load_display_settings(display_file, headers):
    """Load display settings from JSON file."""
    ensure_display_exists(display_file, headers)
    try:
        with open(display_file) as f:
            data = json.load(f)
            if not isinstance(data, dict):
                return {"visible_columns": headers, "hidden_printers": []}
            cols = data.get("visible_columns", headers)
            cols = [c for c in cols if c in headers]
            if not cols:
                cols = headers
            hidden = data.get("hidden_printers", [])
            if not isinstance(hidden, list):
                hidden = []
            hidden = [str(p) for p in hidden if str(p).strip()]
            return {"visible_columns": cols, "hidden_printers": hidden}
    except Exception:
        return {"visible_columns": headers, "hidden_printers": []}


def save_display_settings(display_file, headers, visible_columns):
    """Save display settings to JSON file."""
    visible = [c for c in visible_columns if c in headers]
    if not visible:
        visible = headers
    # Preserve any additional display settings (e.g. hidden_printers).
    hidden = []
    try:
        with open(display_file) as f:
            existing = json.load(f)
            if isinstance(existing, dict) and isinstance(existing.get("hidden_printers"), list):
                hidden = [str(p) for p in existing.get("hidden_printers", []) if str(p).strip()]
    except Exception:
        pass

    with open(display_file, "w") as f:
        json.dump({"visible_columns": visible, "hidden_printers": hidden}, f, indent=2)


def save_hidden_printers(display_file, headers, hidden_printers):
    """Persist hidden printer list while preserving visible column settings."""
    settings = load_display_settings(display_file, headers)
    visible_cols = settings.get("visible_columns", headers)
    hidden = hidden_printers if isinstance(hidden_printers, list) else []
    hidden = [str(p) for p in hidden if str(p).strip()]
    with open(display_file, "w") as f:
        json.dump({"visible_columns": visible_cols, "hidden_printers": hidden}, f, indent=2)


def ensure_api_key(secret_file=None, data_dir=None):
    """
    Load API key from secret.json if it exists.
    Only generates a new key if the file doesn't exist (first-time setup).
    This prevents overwriting API keys set by the installer.
    Returns the API key or None if file doesn't exist and couldn't be created.
    """
    if secret_file is None:
        from core.config import SECRET_FILE, DATA_DIR
        secret_file = SECRET_FILE
        data_dir = DATA_DIR
    
    os.makedirs(data_dir, exist_ok=True)
    
    # Try to read existing key first
    if os.path.exists(secret_file):
        try:
            with open(secret_file) as f:
                data = json.load(f)
                existing_key = data.get("api_key")
                if existing_key:
                    # Key exists - use it (don't regenerate)
                    return existing_key
        except Exception:
            # File exists but is corrupted - don't overwrite, let installer fix it
            pass
    
    # File doesn't exist - only generate on first-time setup
    # This should normally be done by the installer, but we provide a fallback
    key = secrets.token_hex(16)
    try:
        with open(secret_file, "w") as f:
            json.dump({"api_key": key}, f, indent=2)
        return key
    except Exception:
        return None


def append_row(csv_file, headers, data):
    """Append a row to the CSV file."""
    file_exists = os.path.exists(csv_file)
    with open(csv_file, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        if not file_exists:
            writer.writeheader()
        writer.writerow({h: data.get(h, "") for h in headers})


def load_rows_raw(csv_file):
    """Load all rows from CSV file with timestamp parsing."""
    rows = []
    if not os.path.exists(csv_file):
        return rows, "CSV file not found yet. Send at least one print to /log-print."
    try:
        with open(csv_file, newline="") as f:
            reader = csv.DictReader(f)
            for idx, r in enumerate(reader):
                r = dict(r)
                
                # Ensure new fields exist for backwards compatibility
                if "filament_profile_id" not in r:
                    r["filament_profile_id"] = ""
                if "filament_material" not in r:
                    r["filament_material"] = ""
                if "status" not in r:
                    r["status"] = "completed"
                if "failure_reason" not in r:
                    r["failure_reason"] = ""

                r["row_index"] = idx
                ts = r.get("timestamp", "")
                # Preserve the raw timestamp as stored in CSV, even if we render a display timestamp.
                r["timestamp_epoch"] = ts
                if ts:
                    try:
                        ts_float = float(ts)
                        # Keep raw timestamp for filtering
                        r["timestamp_raw"] = ts_float
                        r["timestamp"] = ts_to_local_dt(ts_float).strftime("%Y-%m-%d %H:%M:%S")
                    except Exception:
                        r["timestamp_raw"] = None
                else:
                    r["timestamp_raw"] = None

                # Stable ID for selection and project assignment
                r["job_uid"] = compute_job_uid(r)
                rows.append(r)
        return rows, None
    except Exception as e:
        return [], f"Error reading CSV: {e}"


def _canon_float(value, *, decimals: int = 6) -> str:
    try:
        return format(float(value), f".{decimals}f")
    except Exception:
        return str(value or "").strip()


def compute_job_uid(row: dict) -> str:
    """
    Compute a stable identifier for a history row without modifying CSV schema.

    The UID is deterministic and independent of table ordering, based on:
      - timestamp (epoch if available, else raw string)
      - printer
      - filename
      - duration_seconds
      - filament_mm

    This is used for safe selection/actions (delete/complete/recalculate) and
    for project assignment mapping.
    """
    ts = row.get("timestamp_epoch")
    if not ts:
        ts = row.get("timestamp_raw")
    if ts is None or ts == "":
        ts = row.get("timestamp")

    payload = [
        _canon_float(ts),
        str(row.get("printer") or "").strip(),
        str(row.get("filename") or "").strip(),
        _canon_float(row.get("duration_seconds")),
        _canon_float(row.get("filament_mm")),
    ]

    digest = hashlib.sha1(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
    return f"job_{digest[:16]}"


def _row_to_csv_dict(row: dict, headers: list) -> dict:
    """
    Convert an in-memory row (which may include display-only fields like
    timestamp_raw / timestamp_display) into a dict suitable for writing to CSV.
    """
    out = {h: row.get(h, "") for h in headers}

    if "timestamp" in out:
        ts_epoch = row.get("timestamp_epoch")
        if ts_epoch != "" and ts_epoch is not None:
            out["timestamp"] = ts_epoch
        else:
            ts_raw = row.get("timestamp_raw")
            if ts_raw is not None and ts_raw != "":
                out["timestamp"] = ts_raw

    return out


def rewrite_csv_without_indices(csv_file, headers, indices_to_remove):
    """Rewrite CSV file without specified row indices."""
    if not os.path.exists(csv_file):
        return
    rows, _ = load_rows_raw(csv_file)
    keep = [r for r in rows if r.get("row_index") not in indices_to_remove]
    with open(csv_file, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for r in keep:
            writer.writerow(_row_to_csv_dict(r, headers))


def rewrite_csv_mark_completed(csv_file, headers, indices_to_complete):
    """Mark specified rows as completed if they are currently printing."""
    if not os.path.exists(csv_file):
        return

    rows, _ = load_rows_raw(csv_file)
    indices_set = set(indices_to_complete)

    for row in rows:
        if row.get("row_index") not in indices_set:
            continue

        status = str(row.get("status", "")).lower()
        if status == "printing":
            row["status"] = "completed"
            if "failure_reason" in row:
                row["failure_reason"] = ""

    with open(csv_file, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow(_row_to_csv_dict(row, headers))


def rewrite_csv_without_job_uids(csv_file, headers, job_uids_to_remove):
    """Rewrite CSV file without rows whose computed job_uid is in job_uids_to_remove."""
    if not os.path.exists(csv_file):
        return
    rows, _ = load_rows_raw(csv_file)
    uid_set = {str(u or "").strip() for u in (job_uids_to_remove or []) if str(u or "").strip()}
    keep = [r for r in rows if str(r.get("job_uid") or "").strip() not in uid_set]
    with open(csv_file, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for r in keep:
            writer.writerow(_row_to_csv_dict(r, headers))


def rewrite_csv_mark_completed_job_uids(csv_file, headers, job_uids_to_complete):
    """Mark specified rows as completed if they are currently printing, by job_uid."""
    if not os.path.exists(csv_file):
        return

    rows, _ = load_rows_raw(csv_file)
    uid_set = {str(u or "").strip() for u in (job_uids_to_complete or []) if str(u or "").strip()}

    for row in rows:
        if str(row.get("job_uid") or "").strip() not in uid_set:
            continue

        status = str(row.get("status", "")).lower()
        if status == "printing":
            row["status"] = "completed"
            if "failure_reason" in row:
                row["failure_reason"] = ""

    with open(csv_file, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow(_row_to_csv_dict(row, headers))


# State management for installer (used by both app and installer)
def load_state(state_file, key, default=""):
    """Load a value from install state."""
    if not os.path.exists(state_file):
        return default
    try:
        with open(state_file) as f:
            data = json.load(f)
        return data.get(key, default)
    except Exception:
        return default


def save_state(state_file, data_dir, key, value):
    """Save a value to install state."""
    os.makedirs(data_dir, exist_ok=True)
    data = {}
    if os.path.exists(state_file):
        try:
            with open(state_file) as f:
                data = json.load(f)
        except Exception:
            pass
    data[key] = value
    with open(state_file, "w") as f:
        json.dump(data, f, indent=2)


def load_profiles_data(profiles_file):
    """
    Load profiles data (profiles + mappings) from JSON file.
    Returns a dict with 'profiles' and 'mappings' keys.
    """
    if not os.path.exists(profiles_file):
        return {"profiles": {}, "mappings": {}}
    try:
        with open(profiles_file) as f:
            data = json.load(f)
            if not isinstance(data, dict):
                return {"profiles": {}, "mappings": {}}
            # Ensure keys exist
            if "profiles" not in data:
                data["profiles"] = {}
            if "mappings" not in data:
                data["mappings"] = {}
            return data
    except Exception:
        return {"profiles": {}, "mappings": {}}


def save_profiles_data(profiles_file, data_dir, data):
    """Save profiles data to JSON file."""
    os.makedirs(data_dir, exist_ok=True)
    with open(profiles_file, "w") as f:
        json.dump(data, f, indent=2)


# ----------------------------------------------------------------------
# Timezone helpers
# ----------------------------------------------------------------------

_DEFAULT_TZ_NAME = os.getenv("TZ", "America/New_York")
if ZoneInfo:
    try:
        _TIMEZONE_OBJ = ZoneInfo(_DEFAULT_TZ_NAME)
    except Exception:
        _TIMEZONE_OBJ = timezone.utc
else:  # pragma: no cover
    _TIMEZONE_OBJ = timezone.utc


def ts_to_local_dt(ts: float, tz_name: str = None) -> datetime:
    """
    Convert a POSIX timestamp to a timezone-aware datetime.
    """
    if tz_name is None:
        tz_obj = _TIMEZONE_OBJ
    else:
        if ZoneInfo:
            try:
                tz_obj = ZoneInfo(tz_name)
            except Exception:
                tz_obj = timezone.utc
        else:
            tz_obj = timezone.utc
    try:
        return datetime.fromtimestamp(float(ts), tz=tz_obj)
    except Exception:
        return datetime.fromtimestamp(float(ts))


def load_json_file(json_file):
    """
    Generic JSON file loader.
    Returns the loaded data or None if file doesn't exist or is invalid.
    """
    if not os.path.exists(json_file):
        return None
    try:
        with open(json_file) as f:
            return json.load(f)
    except Exception:
        return None


def save_json_file(json_file, data_dir, data):
    """
    Generic JSON file saver.
    Creates directory if needed.
    """
    os.makedirs(data_dir, exist_ok=True)
    with open(json_file, "w") as f:
        json.dump(data, f, indent=2)
