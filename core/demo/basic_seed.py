"""Deterministic, local-only content for the versioned Basic Demo seed."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any

from core import db as db_module
from core.demo.config import DemoConfiguration
from core.workspace_context import WorkspaceContext


BASIC_SEED_NAME = "basic-v1"
SEED_FORMAT_VERSION = 1
FIXED_TIMESTAMP = "2026-08-01T12:00:00+00:00"
MANIFEST_NAME = "seed-manifest.json"

PRINTERS = (
    ("Voron 2.4", "demo-rate-production", "demo-filament-asa"),
    ("Sovol SV08", "demo-rate-standard", "demo-filament-petg"),
    ("Ender 3 V2", "demo-rate-hobby", "demo-filament-pla"),
)

FILAMENT_PROFILES = (
    ("demo-filament-pla", "Everyday PLA", "PLA", "Generic", "Ocean Blue", 22.0),
    ("demo-filament-petg", "Durable PETG", "PETG", "Generic", "Signal Orange", 27.0),
    ("demo-filament-asa", "Outdoor ASA", "ASA", "Generic", "Graphite", 32.0),
    ("demo-filament-tpu", "Flexible TPU", "TPU", "Generic", "Black", 35.0),
)

RATE_PROFILES = (
    ("demo-rate-hobby", "Hobby", "Electricity and routine wear", 1.50),
    ("demo-rate-standard", "Standard", "General workshop rate", 3.50),
    ("demo-rate-production", "Production", "High-throughput production rate", 6.00),
)

PROJECTS = (
    ("demo-project-cosplay", "Cosplay Helmet", "Printed shell, visor mounts, and finishing work.", 6.0, 32.0, 0),
    ("demo-project-workshop", "Workshop Organizers", "A practical batch of bins and tool holders.", 3.5, 27.0, 0),
    ("demo-project-prototype", "Prototype Enclosure", "Iteration history for a small electronics enclosure.", 5.0, 22.0, 0),
)

_JOB_INPUTS = (
    ("001", "Voron 2.4", "helmet_back_left.gcode", 0, 430, 735, "completed", "demo-filament-asa", "ASA"),
    ("002", "Voron 2.4", "helmet_back_right.gcode", 2, 418, 711, "completed", "demo-filament-asa", "ASA"),
    ("003", "Voron 2.4", "helmet_faceplate.gcode", 5, 515, 882, "completed", "demo-filament-asa", "ASA"),
    ("004", "Voron 2.4", "visor_mount_test.gcode", 7, 72, 84, "failed", "demo-filament-asa", "ASA"),
    ("005", "Sovol SV08", "grid_bin_2x2.gcode", 9, 164, 201, "completed", "demo-filament-petg", "PETG"),
    ("006", "Sovol SV08", "grid_bin_1x3.gcode", 11, 128, 166, "completed", "demo-filament-petg", "PETG"),
    ("007", "Sovol SV08", "caliper_holder.gcode", 14, 91, 112, "completed", "demo-filament-petg", "PETG"),
    ("008", "Sovol SV08", "driver_rack.gcode", 17, 202, 248, "completed", "demo-filament-petg", "PETG"),
    ("009", "Ender 3 V2", "enclosure_rev_a.gcode", 20, 188, 224, "completed", "demo-filament-pla", "PLA"),
    ("010", "Ender 3 V2", "enclosure_rev_b.gcode", 23, 176, 210, "completed", "demo-filament-pla", "PLA"),
    ("011", "Ender 3 V2", "enclosure_lid_rev_b.gcode", 25, 96, 121, "completed", "demo-filament-pla", "PLA"),
    ("012", "Ender 3 V2", "cable_gland_test.gcode", 28, 34, 39, "cancelled", "demo-filament-pla", "PLA"),
    ("013", "Voron 2.4", "camera_bracket.gcode", 31, 82, 97, "completed", "demo-filament-asa", "ASA"),
    ("014", "Sovol SV08", "spool_adapter.gcode", 34, 105, 126, "completed", "demo-filament-petg", "PETG"),
    ("015", "Ender 3 V2", "benchy_quality_check.gcode", 37, 48, 54, "completed", "demo-filament-pla", "PLA"),
    ("016", "Voron 2.4", "duct_revision_3.gcode", 40, 122, 143, "completed", "demo-filament-asa", "ASA"),
    ("017", "Sovol SV08", "battery_tray.gcode", 43, 154, 189, "completed", "demo-filament-petg", "PETG"),
    ("018", "Ender 3 V2", "sensor_mount.gcode", 46, 62, 73, "completed", "demo-filament-pla", "PLA"),
)


def _context(seed_root: Path, seed_name: str) -> WorkspaceContext:
    return WorkspaceContext(
        "demo", "seed-builder", seed_root, seed_root / "kcd.db",
        seed_root / "cache", seed_root / "exports", seed_root / "temp",
        seed_root / "uploads", seed_name, "sql",
    )


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _insert_profiles(conn: sqlite3.Connection) -> None:
    for uid, name, material, brand, color, cost in FILAMENT_PROFILES:
        conn.execute(
            """INSERT INTO filament_profiles
               (profile_uid,name,material,brand,color,filament_mode,filament_rate,
                cost_per_kg,grams_per_meter,created_at,updated_at)
               VALUES(?,?,?,?,?,'per_kg',?,?,3.0,?,?)""",
            (uid, name, material, brand, color, cost, cost, FIXED_TIMESTAMP, FIXED_TIMESTAMP),
        )
    for uid, name, description, rate in RATE_PROFILES:
        conn.execute(
            """INSERT INTO hourly_rate_profiles
               (profile_uid,name,description,rate_per_hour,created_at,updated_at)
               VALUES(?,?,?,?,?,?)""",
            (uid, name, description, rate, FIXED_TIMESTAMP, FIXED_TIMESTAMP),
        )


def _insert_printers_and_settings(conn: sqlite3.Connection) -> None:
    pricing: dict[str, dict[str, Any]] = {}
    mappings: dict[str, str] = {}
    rate_by_uid = {item[0]: item[3] for item in RATE_PROFILES}
    filament_by_uid = {item[0]: item[5] for item in FILAMENT_PROFILES}
    for name, rate_uid, filament_uid in PRINTERS:
        db_module.upsert_printer(conn, name, None, None)
        pricing[name] = {
            "active_rate_profile_id": rate_uid,
            "rate_per_hour": rate_by_uid[rate_uid],
            "filament_mode": "per_kg",
            "filament_rate": filament_by_uid[filament_uid],
            "grams_per_meter": 3.0,
        }
        mappings[name] = filament_uid
    settings = {
        "printer_settings": pricing,
        "filament_mappings": mappings,
        "display_settings": {
            "hidden_printers": [],
            "pause_include_paused_time_default": False,
            "projects_show_cost_totals": True,
            "tables": {},
        },
    }
    for key, value in settings.items():
        conn.execute(
            "INSERT INTO user_settings(key,value_json,updated_at) VALUES(?,?,?)",
            (key, _json(value), FIXED_TIMESTAMP),
        )


def _insert_jobs(conn: sqlite3.Connection) -> list[str]:
    anchor = datetime.fromisoformat(FIXED_TIMESTAMP)
    rate_by_printer = {
        printer: next(rate for uid, _name, _description, rate in RATE_PROFILES if uid == rate_uid)
        for printer, rate_uid, _filament_uid in PRINTERS
    }
    cost_by_profile = {uid: cost for uid, _name, _material, _brand, _color, cost in FILAMENT_PROFILES}
    job_uids: list[str] = []
    for suffix, printer, filename, age_days, minutes, grams, status, profile_uid, material in _JOB_INPUTS:
        ended = anchor - timedelta(days=age_days)
        duration_seconds = minutes * 60
        started = ended - timedelta(seconds=duration_seconds)
        rate = rate_by_printer[printer]
        material_cost = round((grams / 1000.0) * cost_by_profile[profile_uid], 2)
        time_cost = round((duration_seconds / 3600.0) * rate, 2)
        job_uid = f"demo-job-{suffix}"
        job_uids.append(job_uid)
        db_module.upsert_job(conn, {
            "job_uid": job_uid,
            "printer": printer,
            "filename": filename,
            "status": status,
            "started_at": started.timestamp(),
            "ended_at": ended.timestamp(),
            "timestamp": ended.timestamp(),
            "duration_seconds": duration_seconds,
            "paused_seconds_total": 300 if suffix in {"003", "008"} else 0,
            "pause_count": 1 if suffix in {"003", "008"} else 0,
            "runout_count": 1 if suffix == "008" else 0,
            "filament_mm": round((grams / 3.0) * 1000.0, 2),
            "duration_hours": round(duration_seconds / 3600.0, 4),
            "filament_meters": round(grams / 3.0, 3),
            "rate_per_hour": rate,
            "filament_mode": "per_kg",
            "filament_rate": cost_by_profile[profile_uid],
            "grams_per_meter": 3.0,
            "time_cost": time_cost,
            "material_cost": material_cost,
            "total_cost": round(time_cost + material_cost, 2),
            "filament_profile_id": profile_uid,
            "filament_material": material,
            "failure_reason": "First-layer adhesion" if status == "failed" else None,
            "import_source": "demo-seed",
            "import_id": job_uid,
            "job_outcome": status,
        })
    return job_uids


def _insert_projects(conn: sqlite3.Connection, job_uids: list[str]) -> None:
    project_ids: dict[str, int] = {}
    for uid, name, notes, hourly, filament, labor_only in PROJECTS:
        conn.execute(
            """INSERT INTO projects
               (project_uid,name,notes,status,hourly_rate_override,
                filament_cost_per_kg_override,labor_only,created_at,updated_at)
               VALUES(?,?,?,'active',?,?,?,?,?)""",
            (uid, name, notes, hourly, filament, labor_only, FIXED_TIMESTAMP, FIXED_TIMESTAMP),
        )
        project_ids[uid] = int(conn.execute(
            "SELECT id FROM projects WHERE project_uid=?", (uid,)
        ).fetchone()["id"])

    assignments = {
        "demo-project-cosplay": job_uids[0:4],
        "demo-project-workshop": job_uids[4:8],
        "demo-project-prototype": job_uids[8:12],
    }
    for project_uid, assigned_jobs in assignments.items():
        for job_uid in assigned_jobs:
            conn.execute(
                "INSERT INTO project_assignments(project_id,job_uid,created_at) VALUES(?,?,?)",
                (project_ids[project_uid], job_uid, FIXED_TIMESTAMP),
            )

    manual = (
        ("demo-manual-cosplay", "demo-project-cosplay", "Sanding and assembly", 3.5, 80.0, None),
        ("demo-manual-workshop", "demo-project-workshop", "Labeling and installation", 1.25, 0.0, 8.0),
        ("demo-manual-prototype", "demo-project-prototype", "Electronics fit check", 0.75, 0.0, None),
    )
    for manual_id, project_uid, title, hours, filament_g, override in manual:
        conn.execute(
            """INSERT INTO manual_jobs
               (manual_job_id,project_id,title,hours,filament_g,cost_override,notes,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?, ?,?)""",
            (manual_id, project_ids[project_uid], title, hours, filament_g, override,
             "Editable demo project work.", FIXED_TIMESTAMP, FIXED_TIMESTAMP),
        )

    plans = (
        ("demo-plan-visor", "demo-project-cosplay", "visor_trim_final.gcode", 5400, 62.0),
        ("demo-plan-bin", "demo-project-workshop", "grid_bin_3x3.gcode", 12600, 285.0),
        ("demo-plan-enclosure", "demo-project-prototype", "enclosure_rev_c.gcode", 10800, 196.0),
    )
    for plan_id, project_uid, filename, seconds, filament_g in plans:
        conn.execute(
            """INSERT INTO planned_items
               (plan_id,project_id,filename,est_time_s,est_filament_g,est_cost,
                est_cost_is_override,status,source,notes,created_at,updated_at)
               VALUES(?,?,?,?,?,0,0,'active','demo-seed','Editable planned item.',?,?)""",
            (plan_id, project_ids[project_uid], filename, seconds, filament_g,
             FIXED_TIMESTAMP, FIXED_TIMESTAMP),
        )


def _insert_events(conn: sqlite3.Connection) -> None:
    events = (
        ("demo", "Workspace ready", "This temporary workspace was cloned from Basic Demo data.", "info"),
        ("history", "Example history loaded", "Completed, failed, and cancelled records are available to explore.", "info"),
        ("projects", "Example projects loaded", "Tracked jobs, manual work, and planned items are ready to edit.", "info"),
    )
    for offset, (category, title, message, severity) in enumerate(events):
        ts = (datetime.fromisoformat(FIXED_TIMESTAMP) + timedelta(seconds=offset)).isoformat()
        conn.execute(
            "INSERT INTO system_events(ts,category,title,message,severity,meta_json) VALUES(?,?,?,?,?,?)",
            (ts, category, title, message, severity, _json({"source": "basic-v1"})),
        )


def populate_basic_seed(conn: sqlite3.Connection) -> None:
    """Populate an already-migrated empty SQLite database."""
    _insert_profiles(conn)
    _insert_printers_and_settings(conn)
    jobs = _insert_jobs(conn)
    _insert_projects(conn, jobs)
    _insert_events(conn)
    conn.execute("UPDATE printers SET created_at=?, updated_at=?", (FIXED_TIMESTAMP, FIXED_TIMESTAMP))
    conn.execute("UPDATE jobs SET created_at=?, updated_at=?", (FIXED_TIMESTAMP, FIXED_TIMESTAMP))
    conn.execute("UPDATE schema_migrations SET applied_at=?", (FIXED_TIMESTAMP,))
    conn.commit()


def seed_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    tables = (
        "printers", "filament_profiles", "hourly_rate_profiles", "projects", "jobs",
        "project_assignments", "manual_jobs", "planned_items", "system_events", "report_cache",
    )
    counts = {
        table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in tables
    }
    return {
        "counts": counts,
        "remote_printers": int(conn.execute(
            "SELECT COUNT(*) FROM printers WHERE COALESCE(TRIM(moonraker_url),'') != ''"
        ).fetchone()[0]),
        "foreign_key_violations": len(conn.execute("PRAGMA foreign_key_check").fetchall()),
        "integrity": str(conn.execute("PRAGMA integrity_check").fetchone()[0]),
    }


def logical_seed_digest(conn: sqlite3.Connection) -> str:
    """Hash stable logical rows instead of SQLite version-specific file bytes."""
    excluded = {"sqlite_sequence", "report_cache"}
    tables = [
        str(row[0]) for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ) if str(row[0]) not in excluded
    ]
    payload: dict[str, Any] = {}
    for table in tables:
        columns = [str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")]
        order = ",".join(f'"{column}"' for column in columns)
        payload[table] = [list(row) for row in conn.execute(f'SELECT * FROM "{table}" ORDER BY {order}')]
    return hashlib.sha256(_json(payload).encode("utf-8")).hexdigest()


def build_basic_seed_database(seed_root: Path, seed_name: str = BASIC_SEED_NAME) -> dict[str, Any]:
    context = _context(seed_root, seed_name)
    with closing(db_module.connect_db(context)) as conn:
        db_module.apply_migrations(conn)
        populate_basic_seed(conn)
        summary = seed_summary(conn)
        logical_digest = logical_seed_digest(conn)
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("VACUUM")
    return {"summary": summary, "logical_digest": logical_digest}


def write_seed_manifest(seed_root: Path, seed_name: str, result: dict[str, Any]) -> Path:
    database_path = seed_root / "kcd.db"
    manifest = {
        "format_version": SEED_FORMAT_VERSION,
        "seed_name": seed_name,
        "dataset": "KCD Basic Demo",
        "generated_at": FIXED_TIMESTAMP,
        "database_sha256": hashlib.sha256(database_path.read_bytes()).hexdigest(),
        "logical_digest": result["logical_digest"],
        **result["summary"],
    }
    path = seed_root / MANIFEST_NAME
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def validate_basic_seed(
    seed_root: Path,
    configuration: DemoConfiguration | None = None,
    *,
    expected_seed_name: str | None = None,
) -> dict[str, Any]:
    manifest_path = seed_root / MANIFEST_NAME
    database_path = seed_root / "kcd.db"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("format_version") != SEED_FORMAT_VERSION:
        raise ValueError("unsupported Basic Demo seed manifest format")
    if manifest.get("seed_name") != (expected_seed_name or seed_root.name):
        raise ValueError("Basic Demo seed manifest name does not match its directory")
    database_hash = hashlib.sha256(database_path.read_bytes()).hexdigest()
    if manifest.get("database_sha256") != database_hash:
        raise ValueError("Basic Demo seed database hash does not match its manifest")
    uri = f"file:{database_path.as_posix()}?mode=ro&immutable=1"
    with closing(sqlite3.connect(uri, uri=True)) as conn:
        summary = seed_summary(conn)
        digest = logical_seed_digest(conn)
    if summary != {key: manifest[key] for key in summary}:
        raise ValueError("Basic Demo seed manifest summary does not match its database")
    if manifest.get("logical_digest") != digest:
        raise ValueError("Basic Demo seed logical digest does not match its database")
    if summary["integrity"].lower() != "ok" or summary["foreign_key_violations"]:
        raise ValueError("Basic Demo seed database failed integrity validation")
    if summary["remote_printers"]:
        raise ValueError("Basic Demo seed contains a remote printer destination")
    if configuration is not None:
        limits = {
            "printers": configuration.max_printers,
            "filament_profiles": configuration.max_filament_profiles,
            "hourly_rate_profiles": configuration.max_hourly_profiles,
            "projects": configuration.max_projects,
            "jobs": configuration.max_history_records,
        }
        for table, limit in limits.items():
            if summary["counts"][table] > limit:
                raise ValueError(f"Basic Demo seed exceeds the configured {table} quota")
        total_bytes = sum(path.stat().st_size for path in seed_root.rglob("*") if path.is_file())
        if total_bytes > configuration.max_workspace_bytes:
            raise ValueError("Basic Demo seed exceeds the configured workspace byte quota")
    return manifest
