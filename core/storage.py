"""
File I/O and data persistence for Print Cost Dashboard.

SQL-only note:
  File-backed writes are guarded and will raise SqlOnlyViolationError when
  KCD_STORAGE_BACKEND=sql. Runtime SQL-only mode must not touch CSV/JSON files.
"""
import os
import csv
import json
import secrets
import hashlib
import logging
import shutil
import uuid
from datetime import datetime, timezone
from typing import Optional
from core.sql_only import require_file_reads_allowed, require_file_writes_allowed, is_sql_only
try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

logger = logging.getLogger(__name__)


def _is_sql_only() -> bool:
    return is_sql_only()


def _load_user_settings_sql(key: str):
    try:
        from core import db as db_module
        conn = db_module.connect_db()
        db_module.apply_migrations(conn)
        row = conn.execute("SELECT value_json FROM user_settings WHERE key = ?", (key,)).fetchone()
        if not row:
            return None
        raw = row[0] if isinstance(row, (tuple, list)) else row["value_json"]
        return json.loads(raw) if raw else None
    except Exception:
        return None


def _save_user_settings_sql(key: str, value) -> None:
    try:
        from core import db as db_module
        conn = db_module.connect_db()
        db_module.apply_migrations(conn)
        now = datetime.now(timezone.utc).isoformat()
        raw = json.dumps(value, indent=2)
        row = conn.execute("SELECT 1 FROM user_settings WHERE key = ?", (key,)).fetchone()
        if row:
            conn.execute(
                "UPDATE user_settings SET value_json = ?, updated_at = ? WHERE key = ?",
                (raw, now, key),
            )
        else:
            conn.execute(
                "INSERT INTO user_settings (key, value_json, updated_at) VALUES (?, ?, ?)",
                (key, raw, now),
            )
        conn.commit()
    except Exception:
        pass

def _copy_if_missing(src: str, dest: str) -> bool:
    if os.path.exists(dest):
        return False
    if not os.path.exists(src):
        return False
    os.makedirs(os.path.dirname(dest) or '.', exist_ok=True)
    shutil.copy2(src, dest)
    return True


def ensure_runtime_files(settings_file: str, csv_file: str) -> None:
    """
    Ensure runtime files exist, bootstrapping from examples when missing.
    """
    if _is_sql_only():
        # SQL-only mode must not auto-create runtime files.
        require_file_writes_allowed("settings.json", caller_hint="core.storage.ensure_runtime_files")
        require_file_writes_allowed("print_costs.csv", caller_hint="core.storage.ensure_runtime_files")
        return
    from core.config import (
        DATA_DIR,
        SETTINGS_EXAMPLE_FILE,
        CSV_EXAMPLE_FILE,
        DEFAULT_PRICING,
        HEADERS,
    )

    os.makedirs(DATA_DIR, exist_ok=True)

    if not os.path.exists(settings_file):
        if not _copy_if_missing(SETTINGS_EXAMPLE_FILE, settings_file):
            initial = {}
            for pname in ['SV08', 'SV07', 'Ender5P']:
                initial[pname] = dict(DEFAULT_PRICING)
            with open(settings_file, 'w') as f:
                json.dump(initial, f, indent=2)

    if not os.path.exists(csv_file):
        if not _copy_if_missing(CSV_EXAMPLE_FILE, csv_file):
            with open(csv_file, 'w', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=HEADERS)
                writer.writeheader()



def ensure_settings_exists(settings_file, default_pricing):
    """Create a default settings.json if it doesn't exist."""
    if _is_sql_only():
        require_file_writes_allowed("settings.json", caller_hint="core.storage.ensure_settings_exists")
        return
    require_file_reads_allowed("settings.json", caller_hint="core.storage.ensure_settings_exists")
    if not os.path.exists(settings_file):
        from core.config import SETTINGS_EXAMPLE_FILE
        if not _copy_if_missing(SETTINGS_EXAMPLE_FILE, settings_file):
            initial = {}
            for pname in ["SV08", "SV07", "Ender5P"]:
                initial[pname] = dict(default_pricing)
            with open(settings_file, "w") as f:
                json.dump(initial, f, indent=2)


def load_settings(settings_file):
    """Load printer settings from JSON file."""
    if _is_sql_only():
        data = _load_user_settings_sql("settings")
        return data if isinstance(data, dict) else {}
    require_file_reads_allowed("settings.json", caller_hint="core.storage.load_settings")
    from core.config import DEFAULT_PRICING, CSV_FILE
    ensure_runtime_files(settings_file, CSV_FILE)
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
    if _is_sql_only():
        _save_user_settings_sql("settings", settings)
        return
    require_file_writes_allowed("settings.json", caller_hint="core.storage.save_settings")
    os.makedirs(data_dir, exist_ok=True)
    with open(settings_file, "w") as f:
        json.dump(settings, f, indent=2)


def ensure_display_exists(display_file, headers):
    """Create a default display.json if it doesn't exist."""
    if _is_sql_only():
        require_file_writes_allowed("display.json", caller_hint="core.storage.ensure_display_exists")
        return
    if not os.path.exists(display_file):
        # Default: hide Job UID and Thumbnail (thumbnail is opt-in).
        # Keep analytics/internal columns opt-in to avoid surprising users.
        _hidden_defaults = {
            "job_uid",
            "thumbnail",
            "pause_count",
            "runout_count",
            # Moonraker import fields are opt-in (auditing/debug).
            "import_source",
            "import_id",
            "job_outcome",
            "duration_seconds_raw",
            "duration_seconds_est",
            "duration_seconds_effective",
            "filament_mm_raw",
            "filament_mm_est",
            "filament_mm_effective",
        }
        visible = [h for h in headers if h not in _hidden_defaults]
        data = {
            "visible_columns": visible,
            "tables": {
                "history": {"visible_columns": visible},
            },
            "hidden_printers": [],
            # When false, hourly cost excludes paused time by default.
            "pause_include_paused_time_default": False,
            "projects_show_cost_totals": True,
        }
        with open(display_file, "w") as f:
            json.dump(data, f, indent=2)

def _coerce_display_tables(value):
    if not isinstance(value, dict):
        return {}
    tables = {}
    for key, cfg in value.items():
        if not isinstance(key, str) or not key.strip():
            continue
        if not isinstance(cfg, dict):
            continue
        cols_raw = cfg.get("visible_columns")
        if isinstance(cols_raw, list):
            cols = [str(c).strip() for c in cols_raw if str(c).strip()]
        else:
            cols = []
        tables[key] = {"visible_columns": cols}
    return tables


def load_display_settings(display_file, headers):
    """Load display settings from JSON file."""
    _hidden_defaults = {
        "job_uid",
        "thumbnail",
        "pause_count",
        "runout_count",
        "import_source",
        "import_id",
        "job_outcome",
        "duration_seconds_raw",
        "duration_seconds_est",
        "duration_seconds_effective",
        "filament_mm_raw",
        "filament_mm_est",
        "filament_mm_effective",
    }

    def _default_settings():
        visible = [h for h in headers if h not in _hidden_defaults]
        return {
            "visible_columns": visible,
            "tables": {"history": {"visible_columns": visible}},
            "hidden_printers": [],
            "pause_include_paused_time_default": False,
            "projects_show_cost_totals": True,
        }

    if _is_sql_only():
        data = _load_user_settings_sql("display") or {}
        if not isinstance(data, dict):
            return _default_settings()
        tables = _coerce_display_tables(data.get("tables"))
        history_cols = None
        if isinstance(tables.get("history"), dict):
            history_cols = tables.get("history", {}).get("visible_columns")
        if not isinstance(history_cols, list):
            history_cols = data.get("visible_columns", headers)

        cols = [c for c in (history_cols or []) if c in headers and c != "job_uid"]
        if not cols:
            cols = [h for h in headers if h not in _hidden_defaults]
        tables.setdefault("history", {"visible_columns": cols})

        hidden = data.get("hidden_printers", [])
        if not isinstance(hidden, list):
            hidden = []
        hidden = [str(p) for p in hidden if str(p).strip()]

        if "pause_include_paused_time_default" in data:
            pause_include = bool(data.get("pause_include_paused_time_default", False))
        elif "pause_exclude_paused_time_default" in data:
            pause_include = not bool(data.get("pause_exclude_paused_time_default", False))
        else:
            pause_include = False

        show_cost_totals = data.get("projects_show_cost_totals", True)
        show_cost_totals = True if show_cost_totals is None else bool(show_cost_totals)
        return {
            "visible_columns": cols,
            "tables": tables,
            "hidden_printers": hidden,
            "pause_include_paused_time_default": pause_include,
            "projects_show_cost_totals": show_cost_totals,
        }

    require_file_reads_allowed("display.json", caller_hint="core.storage.load_display_settings")
    ensure_display_exists(display_file, headers)

    try:
        with open(display_file) as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return _default_settings()

        tables = _coerce_display_tables(data.get("tables"))

        history_cols = None
        if isinstance(tables.get("history"), dict):
            history_cols = tables.get("history", {}).get("visible_columns")
        if not isinstance(history_cols, list):
            history_cols = data.get("visible_columns", headers)

        cols = [c for c in (history_cols or []) if c in headers and c != "job_uid"]
        if not cols:
            cols = [h for h in headers if h not in _hidden_defaults]

        tables.setdefault("history", {"visible_columns": cols})

        hidden = data.get("hidden_printers", [])
        if not isinstance(hidden, list):
            hidden = []
        hidden = [str(p) for p in hidden if str(p).strip()]

        if "pause_include_paused_time_default" in data:
            pause_include = bool(data.get("pause_include_paused_time_default", False))
        elif "pause_exclude_paused_time_default" in data:
            pause_include = not bool(data.get("pause_exclude_paused_time_default", False))
        else:
            pause_include = False

        show_cost_totals = data.get("projects_show_cost_totals", True)
        show_cost_totals = True if show_cost_totals is None else bool(show_cost_totals)

        return {
            "visible_columns": cols,
            "tables": tables,
            "hidden_printers": hidden,
            "pause_include_paused_time_default": pause_include,
            "projects_show_cost_totals": show_cost_totals,
        }
    except Exception:
        return _default_settings()

def get_visible_columns_for_table(display_settings, table_id, allowed_columns):
    """
    Return visible columns for a given table_id from display settings.

    If no saved settings exist for this table, default to showing all allowed columns.
    """
    allowed = [c for c in (allowed_columns or []) if isinstance(c, str) and c.strip()]
    if not allowed:
        return []

    try:
        tables = display_settings.get("tables") if isinstance(display_settings, dict) else {}
        cfg = tables.get(table_id) if isinstance(tables, dict) else None
        cols = cfg.get("visible_columns") if isinstance(cfg, dict) else None
        if isinstance(cols, list):
            out = [c for c in cols if c in allowed]
            if out:
                return out
    except Exception:
        pass

    return list(allowed)


def set_visible_columns_for_table(display_settings, table_id, visible_columns):
    if not isinstance(display_settings, dict):
        display_settings = {}
    tables = _coerce_display_tables(display_settings.get("tables"))
    cols = [str(c).strip() for c in (visible_columns or []) if str(c).strip()]
    tables[str(table_id)] = {"visible_columns": cols}
    display_settings["tables"] = tables
    if str(table_id) == "history":
        display_settings["visible_columns"] = cols
    return display_settings


def save_display_settings(display_file, data_dir, display_settings):
    """
    Save display settings to JSON file.

    This function expects a full display settings dict, and will:
    - sanitize visible_columns against the current HEADERS
    - preserve unknown keys already present in the JSON file
    """
    if _is_sql_only():
        _save_user_settings_sql("display", display_settings)
        return

    from core.config import HEADERS

    require_file_writes_allowed("display.json", caller_hint="core.storage.save_display_settings")
    os.makedirs(data_dir, exist_ok=True)

    existing = {}
    try:
        if os.path.exists(display_file):
            with open(display_file) as f:
                existing = json.load(f)
        if not isinstance(existing, dict):
            existing = {}
    except Exception:
        existing = {}

    existing_tables = _coerce_display_tables(existing.get("tables"))
    incoming_tables = _coerce_display_tables(display_settings.get("tables") if isinstance(display_settings, dict) else {})

    visible_columns = []
    try:
        visible_columns = list(display_settings.get("visible_columns") or [])
    except Exception:
        visible_columns = []

    # History columns can come from either top-level visible_columns or tables.history.visible_columns.
    history_raw = incoming_tables.get("history", {}).get("visible_columns")
    if isinstance(history_raw, list) and history_raw:
        visible_columns = history_raw

    visible = [c for c in visible_columns if c in HEADERS and c != "job_uid"]
    if not visible:
        visible = [h for h in HEADERS if h not in ("job_uid", "thumbnail", "pause_count", "runout_count")]

    hidden = display_settings.get("hidden_printers", existing.get("hidden_printers", []))
    if not isinstance(hidden, list):
        hidden = []
    hidden = [str(p) for p in hidden if str(p).strip()]

    # Merge any non-history table settings (already validated by callers).
    for table_id, cfg in incoming_tables.items():
        if table_id == "history":
            continue
        existing_tables[table_id] = {"visible_columns": cfg.get("visible_columns", [])}

    # Always persist history visible columns under tables.history, and keep legacy visible_columns for back-compat.
    existing_tables["history"] = {"visible_columns": visible}
    existing["tables"] = existing_tables
    existing["visible_columns"] = visible
    existing["hidden_printers"] = hidden
    # Persist pause accounting global default with "include paused time" semantics.
    if "pause_include_paused_time_default" in display_settings:
        pause_include = bool(display_settings.get("pause_include_paused_time_default"))
    elif "pause_exclude_paused_time_default" in display_settings:
        pause_include = not bool(display_settings.get("pause_exclude_paused_time_default"))
    else:
        if "pause_include_paused_time_default" in existing:
            pause_include = bool(existing.get("pause_include_paused_time_default", False))
        elif "pause_exclude_paused_time_default" in existing:
            pause_include = not bool(existing.get("pause_exclude_paused_time_default", False))
        else:
            pause_include = False

    existing["pause_include_paused_time_default"] = bool(pause_include)
    if "pause_exclude_paused_time_default" in existing:
        existing.pop("pause_exclude_paused_time_default", None)

    show_cost_totals = display_settings.get("projects_show_cost_totals", existing.get("projects_show_cost_totals", True))
    existing["projects_show_cost_totals"] = True if show_cost_totals is None else bool(show_cost_totals)

    if _is_sql_only():
        _save_user_settings_sql("display", existing)
        return

    with open(display_file, "w") as f:
        json.dump(existing, f, indent=2)


def save_hidden_printers(display_file, headers, hidden_printers):
    """Persist hidden printer list while preserving visible column settings."""
    hidden = hidden_printers if isinstance(hidden_printers, list) else []
    hidden = [str(p) for p in hidden if str(p).strip()]
    settings = load_display_settings(display_file, headers)
    if not isinstance(settings, dict):
        settings = {}
    settings["hidden_printers"] = hidden
    from core.config import DATA_DIR
    save_display_settings(display_file, DATA_DIR, settings)


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
    require_file_writes_allowed("print_costs.csv", caller_hint="core.storage.append_row")
    if _is_sql_only():
        require_file_reads_allowed("print_costs.csv", caller_hint="core.storage.append_row")
        return
    try:
        from core.config import CSV_FILE, SETTINGS_FILE
        if os.path.abspath(csv_file) == os.path.abspath(CSV_FILE):
            ensure_runtime_files(SETTINGS_FILE, CSV_FILE)
    except Exception:
        pass
    if "job_uid" in headers and not str(data.get("job_uid") or "").strip():
        data["job_uid"] = str(uuid.uuid4())

    file_exists = os.path.exists(csv_file)
    if file_exists:
        # Ensure the on-disk header matches the schema we're about to append.
        # This prevents schema drift from shifting columns at read time.
        try:
            ensure_csv_schema(csv_file, headers)
        except Exception:
            # Never block appends if schema repair fails; it will be retried on read.
            pass
    with open(csv_file, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        if not file_exists:
            writer.writeheader()
        writer.writerow({h: data.get(h, "") for h in headers})


def _looks_like_gcode_filename(value: str) -> bool:
    v = str(value or "").strip().lower()
    return ".gcode" in v or v.endswith(".gco")


def _score_row_mapping(row: dict) -> int:
    """
    Heuristic scoring for choosing between possible mappings of a CSV row.

    This is used only during CSV schema migration to handle "mixed schema"
    files (old headers, but newer rows appended in a different column order).
    """
    score = 0

    printer = str(row.get("printer") or "").strip()
    filename = str(row.get("filename") or "").strip()
    filament_mode = str(row.get("filament_mode") or "").strip().lower()
    status = str(row.get("status") or "").strip().lower()

    if printer:
        score += 2
    if printer and _looks_like_gcode_filename(printer):
        score -= 25

    if filename and _looks_like_gcode_filename(filename):
        score += 8
    elif filename:
        score -= 2

    if filament_mode in ("per_meter", "per_gram", "per_kg"):
        score += 3
    elif filament_mode:
        score -= 2

    if status in ("completed", "canceled", "cancelled", "failed", "printing", "paused", "idle"):
        score += 2
    elif status:
        score -= 2

    def _num(val):
        try:
            return float(str(val).strip())
        except Exception:
            return None

    dur_sec = _num(row.get("duration_seconds"))
    dur_hr = _num(row.get("duration_hours"))
    fil_mm = _num(row.get("filament_mm"))
    fil_m = _num(row.get("filament_meters"))
    rate_hr = _num(row.get("rate_per_hour"))
    total_cost = _num(row.get("total_cost"))

    if dur_sec is not None:
        if 0 <= dur_sec <= 60 * 60 * 24 * 60:
            score += 1
        else:
            score -= 1
    if dur_hr is not None:
        if 0 <= dur_hr <= 24 * 60:
            score += 1
        else:
            score -= 1
    if fil_mm is not None:
        if 0 <= fil_mm <= 50_000_000:
            score += 1
        else:
            score -= 1
    if fil_m is not None:
        if 0 <= fil_m <= 50_000:
            score += 1
        else:
            score -= 1
    if rate_hr is not None:
        if 0 <= rate_hr <= 10_000:
            score += 1
        else:
            score -= 1
    if total_cost is not None:
        if -1 <= total_cost <= 1_000_000:
            score += 1
        else:
            score -= 1

    return score


def ensure_csv_schema(csv_path: str, expected_headers: list[str]) -> bool:
    """
    Ensure the CSV on disk uses expected_headers as its header row.

    If the file header differs (missing/extra columns or different order),
    we migrate safely:
      - Create a timestamped backup
      - Rewrite the CSV with expected_headers
      - For each row, attempt to map values from either:
          A) old header mapping, or
          B) expected header mapping (for mixed-schema appended rows)
        using a heuristic score
      - Atomic replace
    Returns True if a migration occurred, else False.
    """
    if _is_sql_only():
        require_file_reads_allowed("print_costs.csv", caller_hint="core.storage.ensure_csv_schema")
        return False
    require_file_writes_allowed("print_costs.csv", caller_hint="core.storage.ensure_csv_schema")

    if not os.path.exists(csv_path):
        return False

    try:
        with open(csv_path, newline="", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            old_header = next(reader, [])
    except Exception as e:
        logger.warning("CSV schema check failed to read header: %s (%s)", csv_path, e)
        return False

    old_header = [str(h or "").strip() for h in old_header if str(h or "").strip()]
    if old_header == list(expected_headers):
        return False
    prefix_mapping = bool(old_header) and list(expected_headers)[: len(old_header)] == old_header

    logger.warning(
        "CSV schema mismatch detected; migrating %s (old=%d cols, expected=%d cols)",
        csv_path,
        len(old_header),
        len(expected_headers),
    )

    backup_path = None
    try:
        backup_path = _backup_file(csv_path)
    except Exception as e:
        logger.warning("Failed to create CSV backup for migration: %s (%s)", csv_path, e)

    tmp_path = f"{csv_path}.tmp"
    try:
        with open(csv_path, newline="", encoding="utf-8-sig") as f_in, open(
            tmp_path, "w", newline="", encoding="utf-8"
        ) as f_out:
            reader = csv.reader(f_in)
            _ = next(reader, None)  # consume header

            writer = csv.DictWriter(f_out, fieldnames=list(expected_headers))
            writer.writeheader()

            for row_list in reader:
                if not row_list:
                    continue

                # Map by the old header (normal for legacy rows).
                old_map = {old_header[i]: row_list[i] for i in range(min(len(old_header), len(row_list)))}
                # Map by the expected header (for mixed-schema rows appended with the new order).
                exp_map = {expected_headers[i]: row_list[i] for i in range(min(len(expected_headers), len(row_list)))}

                chosen = old_map
                # Common drift case: the file header is an older prefix of HEADERS, but newer rows were
                # appended using the newer column order (so they contain extra positional fields).
                if prefix_mapping and len(row_list) > len(old_header):
                    chosen = exp_map
                elif _score_row_mapping(exp_map) > _score_row_mapping(old_map):
                    chosen = exp_map

                out_row = {h: chosen.get(h, "") for h in expected_headers}
                writer.writerow(out_row)

        os.replace(tmp_path, csv_path)
        logger.warning("CSV schema migration complete: %s (backup=%s)", csv_path, backup_path or "none")
        return True
    except Exception as e:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass
        logger.error("CSV schema migration failed: %s (%s)", csv_path, e)
        return False


def load_rows_raw(csv_file):
    """Load all rows from CSV file with timestamp parsing."""
    require_file_reads_allowed("print_costs.csv", caller_hint="core.storage.load_rows_raw")
    rows = []
    try:
        from core.config import CSV_FILE, SETTINGS_FILE
        if os.path.abspath(csv_file) == os.path.abspath(CSV_FILE):
            ensure_runtime_files(SETTINGS_FILE, CSV_FILE)
    except Exception:
        pass
    if not os.path.exists(csv_file):
        return rows, "CSV file not found yet. Send at least one print to /log-print."
    try:
        # Ensure file header/schema matches current HEADERS (prevents shifted columns).
        try:
            from core.config import HEADERS
            ensure_csv_schema(csv_file, HEADERS)
        except Exception:
            pass

        needs_writeback = False
        file_fieldnames = []
        with open(csv_file, newline="") as f:
            reader = csv.DictReader(f)
            file_fieldnames = list(reader.fieldnames or [])
            has_uid_col = "job_uid" in file_fieldnames
            for idx, r in enumerate(reader):
                r = dict(r)
                
                # Ensure new fields exist for backwards compatibility
                if "filament_profile_id" not in r:
                    r["filament_profile_id"] = ""
                if "filament_material" not in r:
                    r["filament_material"] = ""
                if "paused_seconds_total" not in r:
                    r["paused_seconds_total"] = "0"
                elif not str(r.get("paused_seconds_total") or "").strip():
                    r["paused_seconds_total"] = "0"
                if "pause_count" not in r:
                    r["pause_count"] = "0"
                elif not str(r.get("pause_count") or "").strip():
                    r["pause_count"] = "0"
                if "runout_count" not in r:
                    r["runout_count"] = "0"
                elif not str(r.get("runout_count") or "").strip():
                    r["runout_count"] = "0"
                if "status" not in r:
                    r["status"] = "completed"
                if "failure_reason" not in r:
                    r["failure_reason"] = ""

                # Moonraker import columns (safe defaults)
                if "import_source" not in r:
                    r["import_source"] = ""
                if "import_id" not in r:
                    r["import_id"] = ""
                if "job_outcome" not in r:
                    r["job_outcome"] = ""
                for k in (
                    "duration_seconds_raw",
                    "duration_seconds_est",
                    "duration_seconds_effective",
                    "filament_mm_raw",
                    "filament_mm_est",
                    "filament_mm_effective",
                ):
                    if k not in r or not str(r.get(k) or "").strip():
                        r[k] = "0"

                # History rows should represent finalized jobs only. If older rows
                # incorrectly captured transient live states, normalize them for display.
                try:
                    if str(r.get("status") or "").strip().lower() in ("printing", "paused"):
                        r["status"] = "completed"
                except Exception:
                    pass

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

                # Stable persisted ID for selection and project assignment.
                uid = str(r.get("job_uid") or "").strip() if has_uid_col else ""
                if not uid:
                    uid = str(uuid.uuid4())
                    needs_writeback = True
                r["job_uid"] = uid

                # Legacy computed ID (used only for migration of older assignment keys).
                r["legacy_job_uid"] = compute_job_uid(r)
                rows.append(r)

        if "job_uid" not in file_fieldnames:
            needs_writeback = True

        if needs_writeback:
            with open(csv_file, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=HEADERS)
                writer.writeheader()
                for row in rows:
                    writer.writerow(_row_to_csv_dict(row, HEADERS))
        return rows, None
    except Exception as e:
        return [], f"Error reading CSV: {e}"


def rewrite_csv_all_rows(csv_file: str, headers: list, rows: list[dict]) -> None:
    """
    Rewrite the entire CSV from an in-memory row list (as returned by load_rows_raw).
    """
    require_file_writes_allowed("print_costs.csv", caller_hint="core.storage.rewrite_csv_all_rows")

    Uses _row_to_csv_dict to preserve raw timestamps and other persisted fields.
    """
    tmp_path = f"{csv_file}.tmp"
    try:
        with open(tmp_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            for row in rows:
                writer.writerow(_row_to_csv_dict(row, headers))
        os.replace(tmp_path, csv_file)
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass


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
    require_file_writes_allowed("print_costs.csv", caller_hint="core.storage.rewrite_csv_without_indices")
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
    require_file_writes_allowed("print_costs.csv", caller_hint="core.storage.rewrite_csv_mark_completed")
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
    require_file_writes_allowed("print_costs.csv", caller_hint="core.storage.rewrite_csv_without_job_uids")
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
    require_file_writes_allowed("print_costs.csv", caller_hint="core.storage.rewrite_csv_mark_completed_job_uids")
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


def _backup_file(csv_file: str) -> Optional[str]:
    """
    Create a timestamped backup alongside csv_file and return the backup path.

    This is intentionally simple and always creates a new backup when called.
    """
    if not os.path.exists(csv_file):
        return None
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = f"{csv_file}.bak.{ts}"
    shutil.copy2(csv_file, backup_path)
    return backup_path


def rewrite_csv_recalculate_costs_job_uids(csv_file, headers, job_uids, compute_costs_fn) -> int:
    """
    Recalculate pricing fields for selected rows identified by job_uid.
    """
    require_file_writes_allowed("print_costs.csv", caller_hint="core.storage.rewrite_csv_recalculate_costs_job_uids")

    Returns the count of rows updated.
    """
    if not os.path.exists(csv_file):
        return 0

    uid_set = {str(u or "").strip() for u in (job_uids or []) if str(u or "").strip()}
    if not uid_set:
        return 0

    # Safety: backup before bulk mutation.
    _backup_file(csv_file)

    rows, _ = load_rows_raw(csv_file)
    updated = 0
    for row in rows:
        if str(row.get("job_uid") or "").strip() not in uid_set:
            continue

        printer_name = str(row.get("printer") or "").strip()
        try:
            duration_seconds = float(row.get("duration_seconds") or 0.0)
        except (TypeError, ValueError):
            duration_seconds = 0.0
        try:
            filament_mm = float(row.get("filament_mm") or 0.0)
        except (TypeError, ValueError):
            filament_mm = 0.0
        try:
            paused_seconds_total = float(row.get("paused_seconds_total") or 0.0)
        except (TypeError, ValueError):
            paused_seconds_total = 0.0

        try:
            # Normalize persisted pause column shape during recalc.
            row["paused_seconds_total"] = str(paused_seconds_total)
            row.update(compute_costs_fn(printer_name, duration_seconds, filament_mm, paused_seconds_total))
            updated += 1
        except Exception:
            continue

    with open(csv_file, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow(_row_to_csv_dict(row, headers))

    return updated


# State management for installer (used by both app and installer)
def load_state(state_file, key, default=""):
    """Load a value from install state."""
    require_file_reads_allowed("install_state.json", caller_hint="core.storage.load_state")
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
    require_file_writes_allowed("install_state.json", caller_hint="core.storage.save_state")
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
    if _is_sql_only():
        try:
            from core import db as db_module
            conn = db_module.connect_db()
            db_module.apply_migrations(conn)
            rows = conn.execute(
                """
                SELECT id, profile_uid, name, material, filament_mode, filament_rate, grams_per_meter
                  FROM filament_profiles
                """
            ).fetchall()
        except Exception:
            return {"profiles": {}, "mappings": {}}

        profiles = {}
        for r in rows:
            if hasattr(r, "__getitem__"):
                try:
                    pid = r["profile_uid"] if "profile_uid" in r.keys() else r[1]
                except Exception:
                    pid = r[1] if len(r) > 1 else None
                pid = pid or str(r["id"] if "id" in r.keys() else r[0])
                profiles[str(pid)] = {
                    "id": str(pid),
                    "name": r["name"] if "name" in r.keys() else r[2],
                    "material": r["material"] if "material" in r.keys() else r[3],
                    "filament_mode": r["filament_mode"] if "filament_mode" in r.keys() else r[4],
                    "filament_rate": r["filament_rate"] if "filament_rate" in r.keys() else r[5],
                    "grams_per_meter": r["grams_per_meter"] if "grams_per_meter" in r.keys() else r[6],
                }
        return {"profiles": profiles, "mappings": {}}
    require_file_reads_allowed("profiles.json", caller_hint="core.storage.load_profiles_data")
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
    require_file_writes_allowed("profiles.json", caller_hint="core.storage.save_profiles_data")
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
    require_file_reads_allowed(json_file, caller_hint="core.storage.load_json_file")
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
    """
    require_file_writes_allowed(os.path.basename(str(json_file)), caller_hint="core.storage.save_json_file")
    Creates directory if needed.
    """
    os.makedirs(data_dir, exist_ok=True)
    with open(json_file, "w") as f:
        json.dump(data, f, indent=2)
