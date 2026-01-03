"""
SQLite database utilities and migrations for KCD.
"""
from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from core.config import DATA_DIR

logger = logging.getLogger(__name__)


MIGRATIONS_DIR = os.path.join(os.path.dirname(__file__), "migrations")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _db_path() -> str:
    return os.path.join(DATA_DIR, "kcd.db")


def _ensure_data_dir() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)


def connect_db() -> sqlite3.Connection:
    _ensure_data_dir()
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    with conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def _ensure_schema_migrations(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL,
            checksum TEXT NOT NULL
        )
        """
    )


def _list_migration_files() -> list[str]:
    if not os.path.isdir(MIGRATIONS_DIR):
        return []
    entries = []
    for name in os.listdir(MIGRATIONS_DIR):
        if name.endswith(".sql"):
            entries.append(name)
    return sorted(entries)


def _checksum(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def apply_migrations(conn: sqlite3.Connection) -> list[str]:
    _ensure_schema_migrations(conn)
    applied = {
        row["version"]: row["checksum"]
        for row in conn.execute("SELECT version, checksum FROM schema_migrations")
    }
    applied_versions: list[str] = []
    for filename in _list_migration_files():
        version = filename.replace(".sql", "")
        path = os.path.join(MIGRATIONS_DIR, filename)
        with open(path, "r", encoding="utf-8") as f:
            sql = f.read()
        checksum = _checksum(sql)
        if version in applied:
            applied_versions.append(version)
            continue
        logger.info("Applying migration %s", version)
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_migrations (version, applied_at, checksum) VALUES (?, ?, ?)",
            (version, _utc_now_iso(), checksum),
        )
        conn.commit()
        applied_versions.append(version)
    return applied_versions


def current_schema_version(conn: sqlite3.Connection) -> Optional[str]:
    try:
        row = conn.execute(
            "SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1"
        ).fetchone()
        return row["version"] if row else None
    except sqlite3.Error:
        return None


def init_db() -> str:
    with connect_db() as conn:
        apply_migrations(conn)
        version = current_schema_version(conn)
    return version or "none"


def upsert_printer(
    conn: sqlite3.Connection,
    name: str,
    moonraker_url: Optional[str] = None,
    external_id: Optional[str] = None,
) -> int:
    name = str(name or "").strip()
    external_id = str(external_id or "").strip() or None
    if not name:
        raise ValueError("printer name is required")
    now = _utc_now_iso()

    if external_id:
        row = conn.execute("SELECT id FROM printers WHERE external_id = ?", (external_id,)).fetchone()
        if row:
            conn.execute(
                "UPDATE printers SET name = ?, moonraker_url = ?, updated_at = ? WHERE external_id = ?",
                (name, moonraker_url, now, external_id),
            )
            return int(row["id"])

    row = conn.execute("SELECT id FROM printers WHERE name = ?", (name,)).fetchone()
    if row:
        conn.execute(
            "UPDATE printers SET moonraker_url = ?, updated_at = ? WHERE name = ?",
            (moonraker_url, now, name),
        )
        return int(row["id"])

    conn.execute(
        "INSERT INTO printers (name, moonraker_url, external_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        (name, moonraker_url, external_id, now, now),
    )
    return int(conn.execute("SELECT id FROM printers WHERE name = ?", (name,)).fetchone()["id"])


def get_printer_id(conn: sqlite3.Connection, name: str) -> Optional[int]:
    row = conn.execute("SELECT id FROM printers WHERE name = ?", (name,)).fetchone()
    return int(row["id"]) if row else None


def job_exists(conn: sqlite3.Connection, job_uid: str) -> bool:
    row = conn.execute("SELECT 1 FROM jobs WHERE job_uid = ? LIMIT 1", (job_uid,)).fetchone()
    return bool(row)


def _to_float(value: object) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def _to_int(value: object) -> Optional[int]:
    try:
        if value is None or value == "":
            return None
        return int(float(value))
    except Exception:
        return None


def _timestamp_to_iso(value: object) -> Optional[str]:
    if value is None or value == "":
        return None
    try:
        ts = float(value)
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    except Exception:
        return None


def upsert_job(conn: sqlite3.Connection, row: dict) -> None:
    job_uid = str(row.get("job_uid") or "").strip()
    if not job_uid:
        raise ValueError("job_uid is required for DB upsert")

    printer_name = str(row.get("printer") or "").strip()
    printer_id = upsert_printer(conn, printer_name, None)

    now = _utc_now_iso()
    ended_at = _timestamp_to_iso(row.get("ended_at") or row.get("timestamp"))
    started_at = _timestamp_to_iso(row.get("started_at"))

    payload = {
        "job_uid": job_uid,
        "printer_id": printer_id,
        "filename": str(row.get("filename") or "").strip(),
        "status": str(row.get("status") or "unknown").strip().lower() or "unknown",
        "started_at": started_at,
        "ended_at": ended_at,
        "duration_seconds": _to_int(row.get("duration_seconds")),
        "paused_seconds_total": _to_float(row.get("paused_seconds_total")),
        "pause_count": _to_int(row.get("pause_count")),
        "runout_count": _to_int(row.get("runout_count")),
        "filament_mm": _to_float(row.get("filament_mm")),
        "duration_hours": _to_float(row.get("duration_hours")),
        "filament_meters": _to_float(row.get("filament_meters")),
        "rate_per_hour": _to_float(row.get("rate_per_hour")),
        "filament_mode": str(row.get("filament_mode") or "").strip() or None,
        "filament_rate": _to_float(row.get("filament_rate")),
        "grams_per_meter": _to_float(row.get("grams_per_meter")),
        "time_cost": _to_float(row.get("time_cost")),
        "material_cost": _to_float(row.get("material_cost")),
        "total_cost": _to_float(row.get("total_cost")),
        "filament_profile_id": str(row.get("filament_profile_id") or "").strip() or None,
        "filament_material": str(row.get("filament_material") or "").strip() or None,
        "failure_reason": str(row.get("failure_reason") or "").strip() or None,
        "import_source": str(row.get("import_source") or "").strip() or None,
        "import_id": str(row.get("import_id") or "").strip() or None,
        "job_outcome": str(row.get("job_outcome") or "").strip() or None,
        "duration_seconds_raw": _to_float(row.get("duration_seconds_raw")),
        "duration_seconds_est": _to_float(row.get("duration_seconds_est")),
        "duration_seconds_effective": _to_float(row.get("duration_seconds_effective")),
        "filament_mm_raw": _to_float(row.get("filament_mm_raw")),
        "filament_mm_est": _to_float(row.get("filament_mm_est")),
        "filament_mm_effective": _to_float(row.get("filament_mm_effective")),
        "thumbnail": str(row.get("thumbnail") or "").strip() or None,
        "override_rate_per_hour": _to_float(row.get("override_rate_per_hour")),
        "override_material_cost": _to_float(row.get("override_material_cost")),
        "override_total_cost": _to_float(row.get("override_total_cost")),
        "hourly_rate_profile_id": str(row.get("hourly_rate_profile_id") or "").strip() or None,
        "created_at": now,
        "updated_at": now,
    }

    cols = ", ".join(payload.keys())
    placeholders = ", ".join(["?"] * len(payload))
    updates = ", ".join([f"{k}=excluded.{k}" for k in payload.keys() if k not in ("job_uid", "created_at")])

    conn.execute(
        f"""
        INSERT INTO jobs ({cols})
        VALUES ({placeholders})
        ON CONFLICT(job_uid) DO UPDATE SET {updates}
        """,
        list(payload.values()),
    )

    logger.debug(
        "SQL upsert job_uid=%s status=%s duration_seconds=%s rate_per_hour=%s time_cost=%s",
        payload.get("job_uid"),
        payload.get("status"),
        payload.get("duration_seconds"),
        payload.get("rate_per_hour"),
        payload.get("time_cost"),
    )
