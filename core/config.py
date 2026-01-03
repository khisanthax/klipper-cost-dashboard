"""
Configuration and constants for Print Cost Dashboard.
"""
import os
from datetime import timezone
try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None
from core.storage import ensure_api_key

# Directories and files
DATA_DIR = "data"
CSV_FILE = os.path.join(DATA_DIR, "print_costs.csv")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
CSV_EXAMPLE_FILE = os.path.join(DATA_DIR, "print_costs.example.csv")
SETTINGS_EXAMPLE_FILE = os.path.join(DATA_DIR, "settings.example.json")
CSV_LOCAL_FILE = os.path.join(DATA_DIR, "print_costs.local.csv")
SETTINGS_LOCAL_FILE = os.path.join(DATA_DIR, "settings.local.json")
DISPLAY_FILE = os.path.join(DATA_DIR, "display.json")
SECRET_FILE = os.path.join(DATA_DIR, "secret.json")
PROFILES_FILE = os.path.join(DATA_DIR, "profiles.json")

# Default pricing (fallback)
DEFAULT_PRICING = {
    "rate_per_hour": 1.0,          # USD per hour of machine time
    "filament_mode": "per_meter",  # "per_meter", "per_gram", or "per_kg"
    "filament_rate": 0.25,         # USD per unit of the chosen mode
    "grams_per_meter": 3.0,        # approx grams per meter
}

# CSV columns (raw stats + pricing + computed costs)
HEADERS = [
    "timestamp",
    "job_uid",
    "printer",
    "filename",
    "thumbnail",
    "duration_seconds",
    "paused_seconds_total",
    "pause_count",
    "runout_count",
    "duration_hours",
    "filament_mm",
    "filament_meters",
    "rate_per_hour",
    "filament_mode",
    "filament_rate",
    "grams_per_meter",
    "time_cost",
    "material_cost",
    "total_cost",
    "filament_profile_id",
    "filament_material",
    "status",
    "failure_reason",
    # Import metadata (Moonraker history backfill)
    "import_source",
    "import_id",
    "job_outcome",
    "duration_seconds_raw",
    "duration_seconds_est",
    "duration_seconds_effective",
    "filament_mm_raw",
    "filament_mm_est",
    "filament_mm_effective",
]

# User-friendly column names
FRIENDLY_HEADERS = {
    "timestamp": "Date & Time",
    "job_uid": "Job UID",
    "printer": "Printer",
    "filename": "File Name",
    "thumbnail": "Thumbnail",
    "duration_seconds": "Duration (sec)",
    "paused_seconds_total": "Paused (sec)",
    "pause_count": "Pauses",
    "runout_count": "Runouts",
    "duration_hours": "Duration (hrs)",
    "filament_mm": "Filament (mm)",
    "filament_meters": "Filament (m)",
    "rate_per_hour": "Rate ($/hr)",
    "filament_mode": "Filament Mode",
    "filament_rate": "Filament Rate ($)",
    "grams_per_meter": "Grams/meter",
    "time_cost": "Time Cost ($)",
    "material_cost": "Material Cost ($)",
    "total_cost": "Total Cost ($)",
    "filament_profile_id": "Profile ID",
    "filament_material": "Material",
    "status": "Status",
    "failure_reason": "Failure Reason",
    "import_source": "Import Source",
    "import_id": "Import ID",
    "job_outcome": "Outcome",
    "duration_seconds_raw": "Duration Raw (sec)",
    "duration_seconds_est": "Duration Est (sec)",
    "duration_seconds_effective": "Duration Effective (sec)",
    "filament_mm_raw": "Filament Raw (mm)",
    "filament_mm_est": "Filament Est (mm)",
    "filament_mm_effective": "Filament Effective (mm)",
}

# Colors per printer for tags
PRINTER_COLORS = {
    "SV08": "#cce5ff",    # light blue
    "SV07": "#d4edda",    # light green
    "Ender5P": "#ffeeba", # light yellow
}

# Timezone configuration
DEFAULT_TIMEZONE = "America/New_York"
_TZ_ENV = os.getenv("TZ", DEFAULT_TIMEZONE)
if ZoneInfo:
    try:
        TIMEZONE_OBJ = ZoneInfo(_TZ_ENV)
    except Exception:
        TIMEZONE_OBJ = timezone.utc
else:
    TIMEZONE_OBJ = timezone.utc

# Ensure data directory exists
os.makedirs(DATA_DIR, exist_ok=True)

# Initialize API key
API_KEY = ensure_api_key()
