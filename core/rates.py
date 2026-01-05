"""
Hourly Rate Profiles management.

Provides CRUD helpers for rate profiles stored in a JSON file, similar to filament profiles.
"""
import os
import uuid
import sqlite3
from datetime import datetime, timezone
from core import db as db_module
from core.config import DATA_DIR
from core.storage import load_json_file, save_json_file

# File path for storing rate profiles
RATE_PROFILES_FILE = os.path.join(DATA_DIR, "rate_profiles.json")


def _is_sql_only() -> bool:
    return str(os.getenv("KCD_STORAGE_BACKEND", "csv")).strip().lower() == "sql"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_sql_profiles():
    try:
        conn = db_module.connect_db()
        db_module.apply_migrations(conn)
        rows = conn.execute(
            "SELECT id, profile_uid, name, description, rate_per_hour FROM hourly_rate_profiles"
        ).fetchall()
    except Exception:
        return {"profiles": {}}

    profiles = {}
    for r in rows:
        if isinstance(r, sqlite3.Row):
            pid = r["profile_uid"] or str(r["id"])
            profiles[str(pid)] = {
                "id": str(pid),
                "name": r["name"],
                "description": r["description"],
                "rate_per_hour": r["rate_per_hour"],
            }
        elif isinstance(r, (tuple, list)):
            pid = r[1] or str(r[0])
            profiles[str(pid)] = {
                "id": str(pid),
                "name": r[2],
                "description": r[3],
                "rate_per_hour": r[4],
            }
    return {"profiles": profiles}


def _load_data():
    if _is_sql_only():
        return _load_sql_profiles()
    data = load_json_file(RATE_PROFILES_FILE)
    if not data or not isinstance(data, dict):
        return {"profiles": {}}
    if "profiles" not in data or not isinstance(data["profiles"], dict):
        data["profiles"] = {}
    return data


def _save_data(data):
    if _is_sql_only():
        return
    save_json_file(RATE_PROFILES_FILE, DATA_DIR, data)


def list_rate_profiles():
    """Return dict of rate profiles keyed by id."""
    data = _load_data()
    return data.get("profiles", {})


def get_rate_profile(profile_id):
    """Get a single rate profile by id."""
    profiles = list_rate_profiles()
    return profiles.get(profile_id)


def upsert_rate_profile(profile_data):
    """
    Add or update a rate profile.
    If id is missing, a new one is generated.
    Returns the profile id.
    """
    if _is_sql_only():
        return _upsert_rate_profile_sql(profile_data)

    data = _load_data()
    profiles = data.get("profiles", {})

    profile_id = profile_data.get("id") or str(uuid.uuid4())
    profile_data["id"] = profile_id

    # Normalize expected fields
    profile = profiles.get(profile_id, {})
    profile.update(profile_data)
    profiles[profile_id] = profile
    data["profiles"] = profiles
    _save_data(data)
    return profile_id


def update_rate_profile(profile_id, updates):
    """Update fields on an existing rate profile."""
    if _is_sql_only():
        return _update_rate_profile_sql(profile_id, updates)

    data = _load_data()
    profiles = data.get("profiles", {})
    if profile_id not in profiles:
        return False

    allowed = {"name", "description", "rate_per_hour"}
    for key, val in updates.items():
        if key in allowed and val is not None:
            profiles[profile_id][key] = val

    data["profiles"] = profiles
    _save_data(data)
    return True


def delete_rate_profile(profile_id):
    """Delete a rate profile if it exists."""
    if _is_sql_only():
        return _delete_rate_profile_sql(profile_id)

    data = _load_data()
    profiles = data.get("profiles", {})
    if profile_id in profiles:
        del profiles[profile_id]
        data["profiles"] = profiles
        _save_data(data)
        return True
    return False


def _upsert_rate_profile_sql(profile_data: dict) -> str:
    profile_id = str(profile_data.get("id") or profile_data.get("profile_uid") or "").strip()
    if not profile_id:
        profile_id = str(uuid.uuid4())
    name = str(profile_data.get("name") or "").strip() or "Unnamed Rate"
    description = profile_data.get("description")
    rate_per_hour = profile_data.get("rate_per_hour")
    now = _utc_now_iso()

    conn = db_module.connect_db()
    db_module.apply_migrations(conn)
    row = conn.execute(
        "SELECT id FROM hourly_rate_profiles WHERE profile_uid = ? OR name = ?",
        (profile_id, name),
    ).fetchone()
    if row:
        conn.execute(
            """
            UPDATE hourly_rate_profiles
               SET profile_uid = ?, name = ?, description = ?, rate_per_hour = ?, updated_at = ?
             WHERE id = ?
            """,
            (
                profile_id,
                name,
                description,
                rate_per_hour,
                now,
                row["id"] if isinstance(row, sqlite3.Row) else row[0],
            ),
        )
    else:
        conn.execute(
            """
            INSERT INTO hourly_rate_profiles
                (profile_uid, name, description, rate_per_hour, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                profile_id,
                name,
                description,
                rate_per_hour,
                now,
                now,
            ),
        )
    conn.commit()
    return profile_id


def _update_rate_profile_sql(profile_id: str, updates: dict) -> bool:
    if not profile_id:
        return False
    allowed = {"name", "description", "rate_per_hour"}
    values = {k: v for k, v in (updates or {}).items() if k in allowed and v is not None}
    if not values:
        return False
    values["updated_at"] = _utc_now_iso()

    conn = db_module.connect_db()
    db_module.apply_migrations(conn)
    row = conn.execute(
        "SELECT id FROM hourly_rate_profiles WHERE profile_uid = ? OR CAST(id AS TEXT) = ?",
        (profile_id, profile_id),
    ).fetchone()
    if not row:
        return False

    set_clause = ", ".join([f"{k} = ?" for k in values.keys()])
    params = list(values.values()) + [row["id"] if isinstance(row, sqlite3.Row) else row[0]]
    conn.execute(f"UPDATE hourly_rate_profiles SET {set_clause} WHERE id = ?", params)
    conn.commit()
    return True


def _delete_rate_profile_sql(profile_id: str) -> bool:
    if not profile_id:
        return False
    conn = db_module.connect_db()
    db_module.apply_migrations(conn)
    row = conn.execute(
        "SELECT id FROM hourly_rate_profiles WHERE profile_uid = ? OR CAST(id AS TEXT) = ?",
        (profile_id, profile_id),
    ).fetchone()
    if not row:
        return False
    conn.execute(
        "DELETE FROM hourly_rate_profiles WHERE id = ?",
        (row["id"] if isinstance(row, sqlite3.Row) else row[0],),
    )
    conn.commit()
    return True
