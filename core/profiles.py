"""
Filament Profiles Management
"""
import os
import uuid
import sqlite3
from core import db as db_module
from core.config import PROFILES_FILE, DATA_DIR
from core.storage import load_profiles_data, save_profiles_data


def get_all_profiles():
    """
    Get all filament profiles.
    Returns a dict of profile_id -> profile_data.
    """
    if str(os.getenv("KCD_STORAGE_BACKEND", "csv")).strip().lower() == "sql":
        return _load_sql_profiles()
    data = load_profiles_data(PROFILES_FILE)
    return data.get("profiles", {})


def get_profile(profile_id):
    """
    Get a specific profile by ID.
    Returns profile dict or None if not found.
    """
    profiles = get_all_profiles()
    return profiles.get(profile_id)


def upsert_profile(profile_data):
    """
    Add or update a profile.
    If 'id' is missing, a new one is generated.
    Returns the profile_id.
    """
    data = load_profiles_data(PROFILES_FILE)
    profiles = data.get("profiles", {})
    
    profile_id = profile_data.get("id")
    if not profile_id:
        profile_id = str(uuid.uuid4())
        profile_data["id"] = profile_id
    
    # Ensure required fields have defaults if missing (though caller should provide them)
    # We don't strictly enforce schema here, but good to have some safety
    if "name" not in profile_data:
        profile_data["name"] = "Unnamed Profile"
        
    profiles[profile_id] = profile_data
    data["profiles"] = profiles
    
    save_profiles_data(PROFILES_FILE, DATA_DIR, data)
    return profile_id


def update_profile(profile_id, updates):
    """
    Update an existing profile with provided fields.
    Returns True if updated, False if profile not found.
    """
    data = load_profiles_data(PROFILES_FILE)
    profiles = data.get("profiles", {})
    
    if profile_id not in profiles:
        return False
    
    profile = profiles[profile_id]
    
    allowed_keys = {
        "name",
        "material",
        "brand",
        "color",
        "filament_mode",
        "filament_rate",
        "grams_per_meter",
    }
    
    for key, value in updates.items():
        if key in allowed_keys and value is not None:
            profile[key] = value
    
    profiles[profile_id] = profile
    data["profiles"] = profiles
    save_profiles_data(PROFILES_FILE, DATA_DIR, data)
    return True


def delete_profile(profile_id):
    """
    Delete a profile by ID.
    Also removes any printer mappings to this profile.
    """
    data = load_profiles_data(PROFILES_FILE)
    profiles = data.get("profiles", {})
    mappings = data.get("mappings", {})
    
    if profile_id in profiles:
        del profiles[profile_id]
        
        # Remove mappings pointing to this profile
        keys_to_remove = [k for k, v in mappings.items() if v == profile_id]
        for k in keys_to_remove:
            del mappings[k]
            
        data["profiles"] = profiles
        data["mappings"] = mappings
        save_profiles_data(PROFILES_FILE, DATA_DIR, data)
        return True
    return False


def get_printer_mapping(printer_name):
    """
    Get the active profile ID for a printer.
    Returns profile_id or None.
    """
    if str(os.getenv("KCD_STORAGE_BACKEND", "csv")).strip().lower() == "sql":
        try:
            conn = db_module.connect_db()
            db_module.apply_migrations(conn)
            row = conn.execute("SELECT hourly_rate_profile_id FROM printers WHERE name = ?", (printer_name,)).fetchone()
            if not row:
                return None
            if hasattr(row, "__getitem__"):
                return row["hourly_rate_profile_id"] if "hourly_rate_profile_id" in row.keys() else row[0]
        except Exception:
        return None
    data = load_profiles_data(PROFILES_FILE)
    mappings = data.get("mappings", {})
    return mappings.get(printer_name)


def set_printer_mapping(printer_name, profile_id):
    """
    Set the active profile for a printer.
    If profile_id is None, removes the mapping.
    """
    data = load_profiles_data(PROFILES_FILE)
    mappings = data.get("mappings", {})
    
    if profile_id is None:
        if printer_name in mappings:
            del mappings[printer_name]
    else:
        # Verify profile exists
        profiles = data.get("profiles", {})
        if profile_id in profiles:
            mappings[printer_name] = profile_id
        else:
            return False # Profile not found
            
    data["mappings"] = mappings
    save_profiles_data(PROFILES_FILE, DATA_DIR, data)
    return True


def get_all_printer_mappings():
    """
    Get all printer-to-profile mappings.
    Returns a dict of printer_name -> profile_id.
    """
    data = load_profiles_data(PROFILES_FILE)
    return data.get("mappings", {})


def _load_sql_profiles():
    """
    Load filament profiles from SQLite in SQL-only mode.
    Returns dict of profile_uid (or id) -> profile_data.
    """
    try:
        conn = db_module.connect_db()
        db_module.apply_migrations(conn)
        rows = conn.execute(
            """
            SELECT id, profile_uid, name, material, filament_mode, filament_rate, grams_per_meter
              FROM filament_profiles
            """
        ).fetchall()
    except Exception:
        return {}

    profiles = {}
    for r in rows:
        if isinstance(r, sqlite3.Row):
            pid = r["profile_uid"] or str(r["id"])
            rec = {
                "id": r["profile_uid"] or str(r["id"]),
                "name": r["name"],
                "material": r["material"],
                "filament_mode": r["filament_mode"],
                "filament_rate": r["filament_rate"],
                "grams_per_meter": r["grams_per_meter"],
            }
        elif isinstance(r, (tuple, list)):
            pid = r[1] or str(r[0])
            rec = {
                "id": r[1] or str(r[0]),
                "name": r[2],
                "material": r[3],
                "filament_mode": r[4],
                "filament_rate": r[5],
                "grams_per_meter": r[6],
            }
        else:
            pid = str(getattr(r, "profile_uid", "") or getattr(r, "id", "")).strip()
            rec = {}
        if pid:
            profiles[str(pid)] = rec
    return profiles
