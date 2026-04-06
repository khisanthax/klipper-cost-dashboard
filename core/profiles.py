"""
Filament Profiles Management
"""
import os
import uuid
import sqlite3
from datetime import datetime, timezone
from core import db as db_module
from core.config import PROFILES_FILE, DATA_DIR
from core.storage import (
    load_profiles_data,
    save_profiles_data,
    _load_user_settings_sql,
    _save_user_settings_sql,
)
from core.sql_only import is_sql_only


def _is_sql_only() -> bool:
    return is_sql_only()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_filament_mappings_sql() -> dict:
    data = _load_user_settings_sql("filament_mappings")
    return data if isinstance(data, dict) else {}


def _save_filament_mappings_sql(mappings: dict) -> None:
    if not isinstance(mappings, dict):
        mappings = {}
    _save_user_settings_sql("filament_mappings", mappings)


def get_all_profiles():
    """
    Get all filament profiles.
    Returns a dict of profile_id -> profile_data.
    """
    if _is_sql_only():
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
    if _is_sql_only():
        return _upsert_sql_profile(profile_data)

    data = load_profiles_data(PROFILES_FILE)
    profiles = data.get("profiles", {})

    profile_id = profile_data.get("id")
    if not profile_id:
        profile_id = str(uuid.uuid4())
        profile_data["id"] = profile_id

    # Ensure required fields have defaults if missing (though caller should provide them)
    if "name" not in profile_data:
        profile_data["name"] = "Unnamed Profile"

    profiles[profile_id] = profile_data
    data["profiles"] = profiles

    save_profiles_data(PROFILES_FILE, DATA_DIR, data)
    return profile_id


def add_profile(profile_data):
    """Alias for upsert_profile for settings UI."""
    return upsert_profile(profile_data)


def update_profile(profile_id, updates):
    """
    Update an existing profile with provided fields.
    Returns True if updated, False if profile not found.
    """
    if _is_sql_only():
        return _update_sql_profile(profile_id, updates)

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
        "cost_per_kg",
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
    if _is_sql_only():
        return _delete_sql_profile(profile_id)

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
    if _is_sql_only():
        mappings = _load_filament_mappings_sql()
        return mappings.get(printer_name)

    data = load_profiles_data(PROFILES_FILE)
    mappings = data.get("mappings", {})
    return mappings.get(printer_name)


def set_printer_mapping(printer_name, profile_id):
    """
    Set the active profile for a printer.
    If profile_id is None, removes the mapping.
    """
    if _is_sql_only():
        normalized_profile_id = str(profile_id).strip() if profile_id is not None else None
        mappings = _load_filament_mappings_sql()
        if normalized_profile_id in {None, "", "none"}:
            mappings.pop(printer_name, None)
        else:
            if not get_profile(normalized_profile_id):
                return False
            mappings[printer_name] = normalized_profile_id
        _save_filament_mappings_sql(mappings)
        return True

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
            return False  # Profile not found

    data["mappings"] = mappings
    save_profiles_data(PROFILES_FILE, DATA_DIR, data)
    return True


def get_all_printer_mappings():
    """
    Get all printer-to-profile mappings.
    Returns a dict of printer_name -> profile_id.
    """
    if _is_sql_only():
        return _load_filament_mappings_sql()

    data = load_profiles_data(PROFILES_FILE)
    return data.get("mappings", {})


def set_printer_active_profile(printer_name, profile_id):
    """Compatibility alias for settings UI profile selection."""
    return set_printer_mapping(printer_name, profile_id)


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
            SELECT id, profile_uid, name, material, brand, color, filament_mode, filament_rate, cost_per_kg, grams_per_meter
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
                "brand": r["brand"],
                "color": r["color"],
                "filament_mode": r["filament_mode"],
                "filament_rate": r["filament_rate"],
                "cost_per_kg": r["cost_per_kg"],
                "grams_per_meter": r["grams_per_meter"],
            }
        elif isinstance(r, (tuple, list)):
            pid = r[1] or str(r[0])
            rec = {
                "id": r[1] or str(r[0]),
                "name": r[2],
                "material": r[3],
                "brand": r[4],
                "color": r[5],
                "filament_mode": r[6],
                "filament_rate": r[7],
                "cost_per_kg": r[8],
                "grams_per_meter": r[9],
            }
        else:
            pid = str(getattr(r, "profile_uid", "") or getattr(r, "id", "")).strip()
            rec = {}
        if pid:
            profiles[str(pid)] = rec
    return profiles


def _upsert_sql_profile(profile_data: dict) -> str:
    profile_id = str(profile_data.get("id") or profile_data.get("profile_uid") or "").strip()
    if not profile_id:
        profile_id = str(uuid.uuid4())
    name = str(profile_data.get("name") or "").strip() or "Unnamed Profile"
    material = profile_data.get("material")
    brand = profile_data.get("brand")
    color = profile_data.get("color")
    filament_mode = profile_data.get("filament_mode")
    filament_rate = profile_data.get("filament_rate")
    cost_per_kg = profile_data.get("cost_per_kg")
    grams_per_meter = profile_data.get("grams_per_meter")
    now = _utc_now_iso()

    conn = db_module.connect_db()
    db_module.apply_migrations(conn)
    row = conn.execute(
        "SELECT id FROM filament_profiles WHERE profile_uid = ? OR name = ?",
        (profile_id, name),
    ).fetchone()
    if row:
        conn.execute(
            """
            UPDATE filament_profiles
               SET profile_uid = ?, name = ?, material = ?, brand = ?, color = ?,
                   filament_mode = ?, filament_rate = ?, cost_per_kg = ?, grams_per_meter = ?,
                   updated_at = ?
             WHERE id = ?
            """,
            (
                profile_id,
                name,
                material,
                brand,
                color,
                filament_mode,
                filament_rate,
                cost_per_kg,
                grams_per_meter,
                now,
                row["id"] if isinstance(row, sqlite3.Row) else row[0],
            ),
        )
    else:
        conn.execute(
            """
            INSERT INTO filament_profiles
                (profile_uid, name, material, brand, color, filament_mode, filament_rate, cost_per_kg, grams_per_meter, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                profile_id,
                name,
                material,
                brand,
                color,
                filament_mode,
                filament_rate,
                cost_per_kg,
                grams_per_meter,
                now,
                now,
            ),
        )
    conn.commit()
    return profile_id


def _update_sql_profile(profile_id: str, updates: dict) -> bool:
    if not profile_id:
        return False
    allowed = {
        "name",
        "material",
        "brand",
        "color",
        "filament_mode",
        "filament_rate",
        "cost_per_kg",
        "grams_per_meter",
    }
    values = {k: v for k, v in (updates or {}).items() if k in allowed and v is not None}
    if not values:
        return False
    values["updated_at"] = _utc_now_iso()

    conn = db_module.connect_db()
    db_module.apply_migrations(conn)
    row = conn.execute(
        "SELECT id FROM filament_profiles WHERE profile_uid = ? OR CAST(id AS TEXT) = ?",
        (profile_id, profile_id),
    ).fetchone()
    if not row:
        return False

    set_clause = ", ".join([f"{k} = ?" for k in values.keys()])
    params = list(values.values()) + [row["id"] if isinstance(row, sqlite3.Row) else row[0]]
    conn.execute(f"UPDATE filament_profiles SET {set_clause} WHERE id = ?", params)
    conn.commit()
    return True


def _delete_sql_profile(profile_id: str) -> bool:
    if not profile_id:
        return False
    conn = db_module.connect_db()
    db_module.apply_migrations(conn)
    row = conn.execute(
        "SELECT id FROM filament_profiles WHERE profile_uid = ? OR CAST(id AS TEXT) = ?",
        (profile_id, profile_id),
    ).fetchone()
    if not row:
        return False
    conn.execute(
        "DELETE FROM filament_profiles WHERE id = ?",
        (row["id"] if isinstance(row, sqlite3.Row) else row[0],),
    )
    conn.commit()

    mappings = _load_filament_mappings_sql()
    if mappings:
        to_remove = [k for k, v in mappings.items() if v == profile_id]
        for k in to_remove:
            mappings.pop(k, None)
        _save_filament_mappings_sql(mappings)
    return True
