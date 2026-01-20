"""
Import existing CSV/JSON data into SQLite.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from core import db as db_module
from core.config import (
    CSV_FILE,
    DATA_DIR,
    DISPLAY_FILE,
    HEADERS,
    PROFILES_FILE,
    SETTINGS_FILE,
)
from core.projects import ASSIGNMENTS_FILE, PROJECTS_FILE
from core.rates import RATE_PROFILES_FILE
from core.storage import ensure_csv_schema, load_profiles_data, load_settings, load_json_file, compute_job_uid


IMPORT_REPORT_PATH = os.path.join(DATA_DIR, "import_report.json")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except Exception:
        return None


def _deterministic_job_uid(row: dict) -> str:
    payload = [
        str(row.get("printer") or "").strip(),
        str(row.get("filename") or "").strip(),
        str(row.get("timestamp") or "").strip(),
        str(row.get("duration_seconds") or "").strip(),
        str(row.get("filament_mm") or "").strip(),
    ]
    digest = hashlib.sha1("|".join(payload).encode("utf-8")).hexdigest()
    return f"job_{digest}"


def _load_csv_rows() -> List[dict]:
    if not os.path.exists(CSV_FILE):
        return []
    try:
        ensure_csv_schema(CSV_FILE, HEADERS)
    except Exception:
        pass
    rows: List[dict] = []
    with open(CSV_FILE, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append(dict(r))
    return rows


def _upsert_user_setting(conn, key: str, value: Any) -> Tuple[int, int]:
    now = _utc_now_iso()
    raw = json.dumps(value, indent=2)
    row = conn.execute("SELECT 1 FROM user_settings WHERE key = ?", (key,)).fetchone()
    if row:
        conn.execute(
            "UPDATE user_settings SET value_json = ?, updated_at = ? WHERE key = ?",
            (raw, now, key),
        )
        return (0, 1)
    conn.execute(
        "INSERT INTO user_settings (key, value_json, updated_at) VALUES (?, ?, ?)",
        (key, raw, now),
    )
    return (1, 0)


def _upsert_project(conn, project: dict) -> Tuple[int, int]:
    now = _utc_now_iso()
    project_uid = str(project.get("id") or "").strip()
    name = str(project.get("name") or "").strip()
    if not name:
        return (0, 0)

    row = conn.execute("SELECT id FROM projects WHERE project_uid = ? OR name = ?", (project_uid, name)).fetchone()
    payload = {
        "project_uid": project_uid or None,
        "name": name,
        "notes": str(project.get("notes") or ""),
        "status": str(project.get("status") or "active"),
        "hourly_rate_override": _safe_float(project.get("hourly_rate_override")),
        "filament_cost_per_kg_override": _safe_float(project.get("filament_cost_per_kg_override")),
        "labor_only": 1 if bool(project.get("labor_only")) else 0,
        "created_at": now,
        "updated_at": now,
    }
    if row:
        payload.pop("created_at")
        conn.execute(
            """
            UPDATE projects
               SET project_uid = ?,
                   name = ?,
                   notes = ?,
                   status = ?,
                   hourly_rate_override = ?,
                   filament_cost_per_kg_override = ?,
                   labor_only = ?,
                   updated_at = ?
             WHERE id = ?
            """,
            (
                payload["project_uid"],
                payload["name"],
                payload["notes"],
                payload["status"],
                payload["hourly_rate_override"],
                payload["filament_cost_per_kg_override"],
                payload["labor_only"],
                payload["updated_at"],
                int(row["id"]),
            ),
        )
        return (0, 1)

    conn.execute(
        """
        INSERT INTO projects (
            project_uid, name, notes, status,
            hourly_rate_override, filament_cost_per_kg_override, labor_only,
            created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payload["project_uid"],
            payload["name"],
            payload["notes"],
            payload["status"],
            payload["hourly_rate_override"],
            payload["filament_cost_per_kg_override"],
            payload["labor_only"],
            payload["created_at"],
            payload["updated_at"],
        ),
    )
    return (1, 0)


def run_import(skip_existing: bool = True, overwrite: bool = False) -> Dict[str, Any]:
    report: Dict[str, Any] = {
        "schema_version": None,
        "started_at": _utc_now_iso(),
        "finished_at": None,
        "counts": {},
        "warnings": {
            "orphan_assignments": [],
            "duplicate_job_uids": [],
            "skipped_rows": [],
        },
        "parity": {},
    }

    conn = db_module.connect_db()
    db_module.apply_migrations(conn)
    report["schema_version"] = db_module.current_schema_version(conn)

    counts = {
        "printers_inserted": 0,
        "printers_updated": 0,
        "filament_profiles_inserted": 0,
        "filament_profiles_updated": 0,
        "hourly_rate_profiles_inserted": 0,
        "hourly_rate_profiles_updated": 0,
        "projects_inserted": 0,
        "projects_updated": 0,
        "jobs_inserted": 0,
        "jobs_updated": 0,
        "jobs_skipped": 0,
        "assignments_inserted": 0,
        "assignments_skipped": 0,
        "settings_inserted": 0,
        "settings_updated": 0,
    }

    settings = load_settings(SETTINGS_FILE)
    printer_names = set(settings.keys()) if isinstance(settings, dict) else set()

    rows = _load_csv_rows()
    for row in rows:
        pname = str(row.get("printer") or "").strip()
        if pname:
            printer_names.add(pname)

    for pname in sorted(printer_names):
        moonraker_url = None
        if isinstance(settings, dict):
            moonraker_url = (settings.get(pname) or {}).get("moonraker_url")
        try:
            existed = db_module.get_printer_id(conn, pname)
            db_module.upsert_printer(conn, pname, moonraker_url)
            if existed:
                counts["printers_updated"] += 1
            else:
                counts["printers_inserted"] += 1
        except Exception:
            continue

    profiles_data = load_profiles_data(PROFILES_FILE)
    for profile_id, profile in (profiles_data.get("profiles") or {}).items():
        name = str(profile.get("name") or "").strip()
        if not name:
            continue
        material = profile.get("material")
        filament_mode = profile.get("filament_mode")
        filament_rate = _safe_float(profile.get("filament_rate"))
        grams_per_meter = _safe_float(profile.get("grams_per_meter"))
        cost_per_kg = None
        if filament_mode == "per_kg" and filament_rate is not None:
            cost_per_kg = filament_rate
        elif filament_mode == "per_gram" and filament_rate is not None:
            cost_per_kg = filament_rate * 1000.0
        elif filament_mode == "per_meter" and filament_rate is not None and grams_per_meter:
            cost_per_kg = filament_rate * 1000.0 / grams_per_meter
        row = conn.execute("SELECT id FROM filament_profiles WHERE profile_uid = ? OR name = ?", (profile_id, name)).fetchone()
        if row:
            conn.execute(
                """
                UPDATE filament_profiles
                   SET profile_uid = ?,
                       name = ?,
                       material = ?,
                       filament_mode = ?,
                       filament_rate = ?,
                       cost_per_kg = ?,
                       grams_per_meter = ?,
                       updated_at = ?
                 WHERE id = ?
                """,
                (
                    profile_id,
                    name,
                    material,
                    filament_mode,
                    filament_rate,
                    cost_per_kg,
                    grams_per_meter,
                    _utc_now_iso(),
                    int(row["id"]),
                ),
            )
            counts["filament_profiles_updated"] += 1
        else:
            conn.execute(
                """
                INSERT INTO filament_profiles (
                    profile_uid, name, material, filament_mode, filament_rate,
                    cost_per_kg, grams_per_meter, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    profile_id,
                    name,
                    material,
                    filament_mode,
                    filament_rate,
                    cost_per_kg,
                    grams_per_meter,
                    _utc_now_iso(),
                    _utc_now_iso(),
                ),
            )
            counts["filament_profiles_inserted"] += 1

    rate_data = load_json_file(RATE_PROFILES_FILE) or {}
    for profile_id, profile in (rate_data.get("profiles") or {}).items():
        name = str(profile.get("name") or "").strip()
        if not name:
            continue
        description = str(profile.get("description") or "").strip() or None
        rate_per_hour = _safe_float(profile.get("rate_per_hour")) or 0.0
        row = conn.execute("SELECT id FROM hourly_rate_profiles WHERE profile_uid = ? OR name = ?", (profile_id, name)).fetchone()
        if row:
            conn.execute(
                """
                UPDATE hourly_rate_profiles
                   SET profile_uid = ?,
                       name = ?,
                       description = ?,
                       rate_per_hour = ?,
                       updated_at = ?
                 WHERE id = ?
                """,
                (
                    profile_id,
                    name,
                    description,
                    rate_per_hour,
                    _utc_now_iso(),
                    int(row["id"]),
                ),
            )
            counts["hourly_rate_profiles_updated"] += 1
        else:
            conn.execute(
                """
                INSERT INTO hourly_rate_profiles (
                    profile_uid, name, description, rate_per_hour, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (profile_id, name, description, rate_per_hour, _utc_now_iso(), _utc_now_iso()),
            )
            counts["hourly_rate_profiles_inserted"] += 1

    projects_raw = load_json_file(PROJECTS_FILE) or []
    if isinstance(projects_raw, list):
        for proj in projects_raw:
            inserted, updated = _upsert_project(conn, proj if isinstance(proj, dict) else {})
            counts["projects_inserted"] += inserted
            counts["projects_updated"] += updated

    legacy_to_new: Dict[str, List[str]] = {}
    seen = {}
    for row in rows:
        job_uid = str(row.get("job_uid") or "").strip()
        if not job_uid:
            job_uid = _deterministic_job_uid(row)
        base_uid = job_uid
        suffix = 1
        while job_uid in seen:
            suffix += 1
            job_uid = f"{base_uid}-{suffix}"
        if job_uid != base_uid:
            report["warnings"]["duplicate_job_uids"].append(base_uid)
        seen[job_uid] = True
        row["job_uid"] = job_uid
        try:
            legacy_key = compute_job_uid(row)
            legacy_to_new.setdefault(legacy_key, []).append(job_uid)
        except Exception:
            pass

        exists = db_module.job_exists(conn, job_uid)
        if exists and skip_existing and not overwrite:
            counts["jobs_skipped"] += 1
            continue

        # Ensure import markers are populated
        if not str(row.get("import_source") or "").strip():
            row["import_source"] = "csv"
        if not str(row.get("job_outcome") or "").strip():
            row["job_outcome"] = str(row.get("status") or "unknown").strip().lower() or "unknown"

        try:
            db_module.upsert_job(conn, row)
            if exists:
                counts["jobs_updated"] += 1
            else:
                counts["jobs_inserted"] += 1
        except Exception as e:
            report["warnings"]["skipped_rows"].append({"job_uid": job_uid, "reason": str(e)})

    assignments_raw = load_json_file(ASSIGNMENTS_FILE) or {}
    project_id_map: Dict[str, int] = {}
    for row in conn.execute("SELECT id, project_uid FROM projects"):
        project_id_map[str(row["project_uid"] or "")] = int(row["id"])

    assignments_inserted = 0
    assignments_skipped = 0
    for job_uid, project_uid in (assignments_raw or {}).items():
        job_uid = str(job_uid or "").strip()
        project_uid = str(project_uid or "").strip()
        if not job_uid or not project_uid:
            continue
        # Map legacy assignment keys to newly imported job_uid when possible.
        if job_uid not in seen:
            mapped = legacy_to_new.get(job_uid) if legacy_to_new else None
            if mapped and len(mapped) == 1:
                job_uid = mapped[0]
        project_id = project_id_map.get(project_uid)
        if not project_id:
            report["warnings"]["orphan_assignments"].append({"job_uid": job_uid, "project_id": project_uid})
            assignments_skipped += 1
            continue
        if not db_module.job_exists(conn, job_uid):
            assignments_skipped += 1
            report["warnings"]["orphan_assignments"].append({"job_uid": job_uid, "project_id": project_uid})
            continue
        conn.execute(
            """
            INSERT OR IGNORE INTO project_assignments (project_id, job_uid, created_at)
            VALUES (?, ?, ?)
            """,
            (project_id, job_uid, _utc_now_iso()),
        )
        assignments_inserted += 1
    counts["assignments_inserted"] = assignments_inserted
    counts["assignments_skipped"] = assignments_skipped

    settings_inserted, settings_updated = _upsert_user_setting(conn, "printer_settings", settings)
    counts["settings_inserted"] += settings_inserted
    counts["settings_updated"] += settings_updated

    display_raw = load_json_file(DISPLAY_FILE) or {}
    ins, upd = _upsert_user_setting(conn, "display_settings", display_raw)
    counts["settings_inserted"] += ins
    counts["settings_updated"] += upd

    conn.commit()
    report["counts"] = counts

    # Parity snapshot
    report["parity"]["csv_job_count"] = len(rows)
    report["parity"]["db_job_count"] = int(conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0])
    try:
        report["parity"]["sum_total_cost_csv"] = sum(
            float(r.get("total_cost") or 0.0) for r in rows if str(r.get("total_cost") or "").strip()
        )
    except Exception:
        report["parity"]["sum_total_cost_csv"] = None
    try:
        report["parity"]["sum_total_cost_db"] = float(
            conn.execute("SELECT COALESCE(SUM(total_cost), 0) FROM jobs").fetchone()[0]
        )
    except Exception:
        report["parity"]["sum_total_cost_db"] = None

    report["finished_at"] = _utc_now_iso()
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(IMPORT_REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    return report


def render_import_summary(report: Dict[str, Any]) -> str:
    counts = report.get("counts", {})
    warnings = report.get("warnings", {})
    lines = [
        "Import complete.",
        f"  Printers: +{counts.get('printers_inserted', 0)} updated={counts.get('printers_updated', 0)}",
        f"  Filament profiles: +{counts.get('filament_profiles_inserted', 0)} updated={counts.get('filament_profiles_updated', 0)}",
        f"  Hourly rate profiles: +{counts.get('hourly_rate_profiles_inserted', 0)} updated={counts.get('hourly_rate_profiles_updated', 0)}",
        f"  Projects: +{counts.get('projects_inserted', 0)} updated={counts.get('projects_updated', 0)}",
        f"  Jobs: +{counts.get('jobs_inserted', 0)} updated={counts.get('jobs_updated', 0)} skipped={counts.get('jobs_skipped', 0)}",
        f"  Assignments: +{counts.get('assignments_inserted', 0)} skipped={counts.get('assignments_skipped', 0)}",
        f"  Settings keys: +{counts.get('settings_inserted', 0)} updated={counts.get('settings_updated', 0)}",
    ]

    orphan = warnings.get("orphan_assignments") or []
    dupes = warnings.get("duplicate_job_uids") or []
    if orphan:
        lines.append(f"  Warnings: {len(orphan)} orphan assignments")
    if dupes:
        lines.append(f"  Warnings: {len(dupes)} duplicate job_uid collisions")
    lines.append(f"  Report: {IMPORT_REPORT_PATH}")
    return "\n".join(lines)
