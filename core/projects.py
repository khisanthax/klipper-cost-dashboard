"""
Projects feature for Klipper Cost Dashboard.

Data storage:
- Projects are stored in `data/projects.json` as a list of project objects.
- Job membership is stored in `data/project_assignments.json` as a mapping:
    job_uid -> project_id

Delete behavior:
- Deleting a project unassigns its jobs (membership mapping entries are removed)
  and then removes the project record. No CSV history rows are deleted.

This module deliberately does not alter the CSV schema; project membership is an
optional overlay managed by the UI.
"""

from __future__ import annotations

import json
import os
import time
import uuid
import sqlite3
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

from core.config import DATA_DIR
from core.config import DEFAULT_PRICING
from core import db as db_module
from core.storage import compute_job_uid
from core.sql_only import require_file_reads_allowed


PROJECTS_FILE = os.path.join(DATA_DIR, "projects.json")
ASSIGNMENTS_FILE = os.path.join(DATA_DIR, "project_assignments.json")
ASSIGNMENTS_ORPHANS_FILE = os.path.join(DATA_DIR, "project_assignments_orphans.json")
MANUAL_JOBS_FILE = os.path.join(DATA_DIR, "project_manual_jobs.json")
PLANS_FILE = os.path.join(DATA_DIR, "project_plans.json")


class ProjectsDataError(RuntimeError):
    """Raised when projects storage is unreadable/corrupt."""


@dataclass(frozen=True)
class Project:
    id: str
    name: str
    notes: str = ""
    hourly_rate_override: Optional[float] = None
    filament_cost_per_kg_override: Optional[float] = None
    labor_only: bool = False
    created_at: float = 0.0
    updated_at: float = 0.0


@dataclass(frozen=True)
class ManualJob:
    manual_job_id: str
    project_id: str
    title: str
    hours: float
    filament_g: float = 0.0
    cost_override: Optional[float] = None
    created_at: str = ""
    updated_at: str = ""
    notes: str = ""


@dataclass(frozen=True)
class PlannedItem:
    plan_id: str
    project_id: str
    filename: str
    created_at: str
    est_time_s: int
    est_filament_g: Optional[float] = None
    est_cost: float = 0.0
    est_cost_is_override: bool = False
    status: str = "active"  # "active" | "fulfilled"
    source: str = ""
    notes: str = ""
    converted_to_manual_job_id: Optional[str] = None


def _ensure_data_dir() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)


def _is_sql_only() -> bool:
    return str(os.getenv("KCD_STORAGE_BACKEND", "csv")).strip().lower() == "sql"


def _read_json(path: str, default: Any) -> Any:
    require_file_reads_allowed(path, caller_hint="core.projects._read_json")
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise ProjectsDataError(f"Could not parse {path}: {e}") from e
    except OSError as e:
        raise ProjectsDataError(f"Could not read {path}: {e}") from e


def _write_json(path: str, data: Any) -> None:
    _ensure_data_dir()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except OSError as e:
        raise ProjectsDataError(f"Could not write {path}: {e}") from e


def ensure_projects_files() -> None:
    """Create projects/assignments files if missing."""
    if _is_sql_only():
        return
    _ensure_data_dir()
    if not os.path.exists(PROJECTS_FILE):
        _write_json(PROJECTS_FILE, [])
    if not os.path.exists(ASSIGNMENTS_FILE):
        _write_json(ASSIGNMENTS_FILE, {})
    if not os.path.exists(MANUAL_JOBS_FILE):
        _write_json(MANUAL_JOBS_FILE, [])
    if not os.path.exists(PLANS_FILE):
        _write_json(PLANS_FILE, [])


def _parse_iso_to_epoch(value: object) -> float:
    if value is None:
        return 0.0
    raw = str(value or "").strip()
    if not raw:
        return 0.0
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def _project_uid_from_row(row: sqlite3.Row) -> str:
    pid = row["project_uid"] if "project_uid" in row.keys() else None
    if pid:
        return str(pid)
    return str(row["id"])


def _load_projects_sql() -> Dict[str, Project]:
    try:
        conn = db_module.connect_db()
        db_module.apply_migrations(conn)
        rows = conn.execute(
            """
            SELECT
                id,
                project_uid,
                name,
                notes,
                status,
                hourly_rate_override,
                filament_cost_per_kg_override,
                labor_only,
                created_at,
                updated_at
            FROM projects
            """
        ).fetchall()
    except Exception:
        return {}

    projects: Dict[str, Project] = {}
    for row in rows:
        pid = _project_uid_from_row(row)
        name = str(row["name"] or "").strip()
        if not pid or not name:
            continue
        projects[pid] = Project(
            id=pid,
            name=name,
            notes=str(row["notes"] or ""),
            hourly_rate_override=_opt_nonneg_float(row["hourly_rate_override"]),
            filament_cost_per_kg_override=_opt_nonneg_float(row["filament_cost_per_kg_override"]),
            labor_only=bool(row["labor_only"] or False),
            created_at=_parse_iso_to_epoch(row["created_at"]),
            updated_at=_parse_iso_to_epoch(row["updated_at"]),
        )
    return projects


def _save_projects_sql(projects: Iterable[Project]) -> None:
    conn = db_module.connect_db()
    db_module.apply_migrations(conn)
    now = _iso_now()
    for p in projects:
        conn.execute(
            """
            INSERT INTO projects (
                project_uid,
                name,
                notes,
                status,
                hourly_rate_override,
                filament_cost_per_kg_override,
                labor_only,
                created_at,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_uid) DO UPDATE SET
                name=excluded.name,
                notes=excluded.notes,
                status=excluded.status,
                hourly_rate_override=excluded.hourly_rate_override,
                filament_cost_per_kg_override=excluded.filament_cost_per_kg_override,
                labor_only=excluded.labor_only,
                updated_at=excluded.updated_at
            """,
            (
                p.id,
                p.name,
                p.notes,
                "active",
                p.hourly_rate_override,
                p.filament_cost_per_kg_override,
                1 if p.labor_only else 0,
                now,
                now,
            ),
        )
    conn.commit()


def _load_assignments_sql() -> Dict[str, str]:
    conn = db_module.connect_db()
    db_module.apply_migrations(conn)
    assignments: Dict[str, str] = {}
    for row in conn.execute(
        """
        SELECT pa.job_uid, p.id AS project_id, p.project_uid
        FROM project_assignments pa
        JOIN projects p ON pa.project_id = p.id
        """
    ):
        job_uid = str(row["job_uid"] or "").strip()
        if not job_uid:
            continue
        pid = str(row["project_uid"] or row["project_id"] or "").strip()
        if not pid:
            continue
        assignments[job_uid] = pid
    return assignments


def _save_assignments_sql(assignments: Dict[str, str]) -> None:
    conn = db_module.connect_db()
    db_module.apply_migrations(conn)
    conn.execute("DELETE FROM project_assignments")
    now = _iso_now()
    for job_uid, project_uid in (assignments or {}).items():
        job_uid = str(job_uid or "").strip()
        project_uid = str(project_uid or "").strip()
        if not job_uid or not project_uid:
            continue
        row = conn.execute(
            "SELECT id FROM projects WHERE project_uid = ? OR name = ?",
            (project_uid, project_uid),
        ).fetchone()
        if not row:
            continue
        project_id = row["id"] if isinstance(row, sqlite3.Row) else row[0]
        conn.execute(
            "INSERT OR REPLACE INTO project_assignments (project_id, job_uid, created_at) VALUES (?, ?, ?)",
            (project_id, job_uid, now),
        )
    conn.commit()


def _resolve_project_db_id(conn: sqlite3.Connection, project_uid: str) -> Optional[int]:
    if not project_uid:
        return None
    row = conn.execute(
        "SELECT id FROM projects WHERE project_uid = ? OR name = ?",
        (project_uid, project_uid),
    ).fetchone()
    if not row:
        return None
    if isinstance(row, sqlite3.Row):
        return int(row["id"])
    return int(row[0])


def _load_manual_jobs_sql() -> Dict[str, List[ManualJob]]:
    conn = db_module.connect_db()
    db_module.apply_migrations(conn)
    jobs_by_project: Dict[str, List[ManualJob]] = {}
    rows = conn.execute(
        """
        SELECT
            mj.manual_job_id,
            mj.title,
            mj.hours,
            mj.filament_g,
            mj.cost_override,
            mj.notes,
            mj.created_at,
            mj.updated_at,
            p.project_uid,
            p.id AS project_id
        FROM manual_jobs mj
        JOIN projects p ON mj.project_id = p.id
        """
    ).fetchall()
    for row in rows:
        pid = str(row["project_uid"] or row["project_id"] or "").strip()
        if not pid:
            continue
        try:
            hours = float(row["hours"] or 0.0)
        except (TypeError, ValueError):
            hours = 0.0
        try:
            filament_g = float(row["filament_g"] or 0.0)
        except (TypeError, ValueError):
            filament_g = 0.0
        cost_override = row["cost_override"]
        if cost_override is not None:
            try:
                cost_override = float(cost_override)
            except (TypeError, ValueError):
                cost_override = None

        mj = ManualJob(
            manual_job_id=str(row["manual_job_id"] or "").strip(),
            project_id=pid,
            title=str(row["title"] or "").strip(),
            hours=hours,
            filament_g=filament_g,
            cost_override=cost_override,
            created_at=str(row["created_at"] or ""),
            updated_at=str(row["updated_at"] or ""),
            notes=str(row["notes"] or ""),
        )
        if mj.manual_job_id and mj.title:
            jobs_by_project.setdefault(pid, []).append(mj)
    return jobs_by_project


def _save_manual_jobs_sql(jobs_by_project: Dict[str, List[ManualJob]]) -> None:
    conn = db_module.connect_db()
    db_module.apply_migrations(conn)
    conn.execute("DELETE FROM manual_jobs")
    for pid, jobs in jobs_by_project.items():
        project_id = _resolve_project_db_id(conn, pid)
        if project_id is None:
            continue
        for j in jobs:
            conn.execute(
                """
                INSERT OR REPLACE INTO manual_jobs (
                    manual_job_id,
                    project_id,
                    title,
                    hours,
                    filament_g,
                    cost_override,
                    notes,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    j.manual_job_id,
                    project_id,
                    j.title,
                    float(j.hours or 0.0),
                    float(j.filament_g or 0.0),
                    j.cost_override,
                    j.notes,
                    j.created_at,
                    j.updated_at,
                ),
            )
    conn.commit()


def _load_plans_sql() -> Dict[str, List[PlannedItem]]:
    conn = db_module.connect_db()
    db_module.apply_migrations(conn)
    by_project: Dict[str, List[PlannedItem]] = {}
    rows = conn.execute(
        """
        SELECT
            pi.plan_id,
            pi.filename,
            pi.est_time_s,
            pi.est_filament_g,
            pi.est_cost,
            pi.est_cost_is_override,
            pi.status,
            pi.source,
            pi.notes,
            pi.converted_to_manual_job_id,
            pi.created_at,
            pi.updated_at,
            p.project_uid,
            p.id AS project_id
        FROM planned_items pi
        JOIN projects p ON pi.project_id = p.id
        """
    ).fetchall()
    for row in rows:
        pid = str(row["project_uid"] or row["project_id"] or "").strip()
        if not pid:
            continue
        try:
            est_time_s = int(float(row["est_time_s"] or 0))
        except (TypeError, ValueError):
            est_time_s = 0
        est_filament_g = row["est_filament_g"]
        if est_filament_g is not None:
            try:
                est_filament_g = float(est_filament_g)
            except (TypeError, ValueError):
                est_filament_g = None
        est_cost = 0.0
        try:
            est_cost = float(row["est_cost"] or 0.0)
        except (TypeError, ValueError):
            est_cost = 0.0
        status = str(row["status"] or "active").strip().lower()
        if status not in ("active", "fulfilled"):
            status = "active"
        plan = PlannedItem(
            plan_id=str(row["plan_id"] or "").strip(),
            project_id=pid,
            filename=str(row["filename"] or "").strip(),
            created_at=str(row["created_at"] or ""),
            est_time_s=est_time_s,
            est_filament_g=est_filament_g,
            est_cost=est_cost,
            est_cost_is_override=bool(row["est_cost_is_override"] or False),
            status=status,
            source=str(row["source"] or ""),
            notes=str(row["notes"] or ""),
            converted_to_manual_job_id=str(row["converted_to_manual_job_id"] or "").strip() or None,
        )
        if plan.plan_id and plan.filename and plan.est_time_s > 0:
            by_project.setdefault(pid, []).append(plan)
    return by_project


def _save_plans_sql(by_project: Dict[str, List[PlannedItem]]) -> None:
    conn = db_module.connect_db()
    db_module.apply_migrations(conn)
    conn.execute("DELETE FROM planned_items")
    for pid, items in by_project.items():
        project_id = _resolve_project_db_id(conn, pid)
        if project_id is None:
            continue
        for p in items:
            conn.execute(
                """
                INSERT OR REPLACE INTO planned_items (
                    plan_id,
                    project_id,
                    filename,
                    est_time_s,
                    est_filament_g,
                    est_cost,
                    est_cost_is_override,
                    status,
                    source,
                    notes,
                    converted_to_manual_job_id,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    p.plan_id,
                    project_id,
                    p.filename,
                    int(p.est_time_s or 0),
                    p.est_filament_g,
                    float(p.est_cost or 0.0),
                    1 if p.est_cost_is_override else 0,
                    p.status,
                    p.source,
                    p.notes,
                    p.converted_to_manual_job_id,
                    p.created_at,
                    p.created_at,
                ),
            )
    conn.commit()


def _upsert_project_sql(project: Project) -> None:
    conn = db_module.connect_db()
    db_module.apply_migrations(conn)
    now = _iso_now()
    conn.execute(
        """
        INSERT INTO projects (
            project_uid,
            name,
            notes,
            status,
            hourly_rate_override,
            filament_cost_per_kg_override,
            labor_only,
            created_at,
            updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(project_uid) DO UPDATE SET
            name=excluded.name,
            notes=excluded.notes,
            status=excluded.status,
            hourly_rate_override=excluded.hourly_rate_override,
            filament_cost_per_kg_override=excluded.filament_cost_per_kg_override,
            labor_only=excluded.labor_only,
            updated_at=excluded.updated_at
        """,
        (
            project.id,
            project.name,
            project.notes,
            "active",
            project.hourly_rate_override,
            project.filament_cost_per_kg_override,
            1 if project.labor_only else 0,
            now,
            now,
        ),
    )
    conn.commit()


def _create_project_sql(project: Project) -> None:
    _upsert_project_sql(project)


def _update_project_sql(project: Project) -> None:
    _upsert_project_sql(project)


def _delete_project_sql(project_uid: str) -> None:
    conn = db_module.connect_db()
    db_module.apply_migrations(conn)
    row = conn.execute(
        "SELECT id FROM projects WHERE project_uid = ? OR name = ?",
        (project_uid, project_uid),
    ).fetchone()
    if not row:
        return
    project_id = row["id"] if isinstance(row, sqlite3.Row) else row[0]
    conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    conn.commit()


def _safe_read_orphaned_assignments() -> Optional[List[Dict[str, Any]]]:
    """
    Best-effort reader for the orphaned legacy assignments file.

    This must never raise ProjectsDataError, because migration runs on /projects GET.
    If the orphans file is corrupt/unreadable, return None so we can keep the live
    assignments map intact (avoid silently discarding evidence).
    """
    if not os.path.exists(ASSIGNMENTS_ORPHANS_FILE):
        return []
    try:
        with open(ASSIGNMENTS_ORPHANS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    if not isinstance(data, list):
        return None
    cleaned: List[Dict[str, Any]] = []
    for item in data:
        if isinstance(item, dict):
            cleaned.append(item)
    return cleaned


def _safe_write_orphaned_assignments(items: List[Dict[str, Any]]) -> bool:
    """
    Best-effort writer for orphaned legacy assignments.

    Returns True on success. Never raises ProjectsDataError.
    """
    try:
        _ensure_data_dir()
        with open(ASSIGNMENTS_ORPHANS_FILE, "w", encoding="utf-8") as f:
            json.dump(items, f, indent=2)
        return True
    except Exception:
        return False


def load_projects() -> Dict[str, Project]:
    if _is_sql_only():
        return _load_projects_sql()
    ensure_projects_files()
    raw = _read_json(PROJECTS_FILE, [])
    if not isinstance(raw, list):
        raise ProjectsDataError(f"{PROJECTS_FILE} must contain a JSON list")

    projects: Dict[str, Project] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        pid = str(item.get("id") or "").strip()
        name = str(item.get("name") or "").strip()
        if not pid or not name:
            continue
        notes = str(item.get("notes") or "")
        created_at = float(item.get("created_at") or 0.0)
        updated_at = float(item.get("updated_at") or 0.0)

        def _opt_nonneg_float(value) -> Optional[float]:
            if value is None or str(value).strip() == "":
                return None
            try:
                f = float(value)
            except (TypeError, ValueError):
                return None
            return f if f >= 0 else None

        hourly_rate_override = _opt_nonneg_float(item.get("hourly_rate_override"))
        filament_cost_per_kg_override = _opt_nonneg_float(item.get("filament_cost_per_kg_override"))
        labor_only = bool(item.get("labor_only") or False)

        projects[pid] = Project(
            id=pid,
            name=name,
            notes=notes,
            hourly_rate_override=hourly_rate_override,
            filament_cost_per_kg_override=filament_cost_per_kg_override,
            labor_only=labor_only,
            created_at=created_at,
            updated_at=updated_at,
        )
    return projects


def save_projects(projects: Iterable[Project]) -> None:
    if _is_sql_only():
        _save_projects_sql(projects)
        return
    payload: List[Dict[str, Any]] = []
    for p in projects:
        payload.append(
            {
                "id": p.id,
                "name": p.name,
                "notes": p.notes,
                "hourly_rate_override": p.hourly_rate_override,
                "filament_cost_per_kg_override": p.filament_cost_per_kg_override,
                "labor_only": bool(p.labor_only),
                "created_at": float(p.created_at or 0.0),
                "updated_at": float(p.updated_at or 0.0),
            }
        )
    _write_json(PROJECTS_FILE, payload)


def load_assignments() -> Dict[str, str]:
    if _is_sql_only():
        return _load_assignments_sql()
    ensure_projects_files()
    raw = _read_json(ASSIGNMENTS_FILE, {})
    if not isinstance(raw, dict):
        raise ProjectsDataError(f"{ASSIGNMENTS_FILE} must contain a JSON object")
    assignments: Dict[str, str] = {}
    for k, v in raw.items():
        key = str(k or "").strip()
        pid = str(v or "").strip()
        if key and pid:
            assignments[key] = pid
    return assignments


def save_assignments(assignments: Dict[str, str]) -> None:
    if _is_sql_only():
        _save_assignments_sql(assignments)
        return
    _write_json(ASSIGNMENTS_FILE, dict(assignments))


def load_manual_jobs() -> Dict[str, List[ManualJob]]:
    """
    Return manual jobs grouped by project_id.

    Storage is a list of dicts in `data/project_manual_jobs.json`.
    """
    if _is_sql_only():
        return _load_manual_jobs_sql()
    ensure_projects_files()
    raw = _read_json(MANUAL_JOBS_FILE, [])
    if not isinstance(raw, list):
        raise ProjectsDataError(f"{MANUAL_JOBS_FILE} must contain a JSON list")

    jobs_by_project: Dict[str, List[ManualJob]] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        mid = str(item.get("manual_job_id") or "").strip()
        pid = str(item.get("project_id") or "").strip()
        title = str(item.get("title") or item.get("description") or "").strip()
        if not mid or not pid or not title:
            continue
        try:
            hours = float(item.get("hours") or 0.0)
        except (TypeError, ValueError):
            hours = 0.0
        try:
            filament_g = float(item.get("filament_g") or 0.0)
        except (TypeError, ValueError):
            filament_g = 0.0

        cost_override_raw = item.get("cost_override", None)
        cost_override: Optional[float]
        if cost_override_raw is None or str(cost_override_raw).strip() == "":
            cost_override = None
        else:
            try:
                cost_override = float(cost_override_raw)
            except (TypeError, ValueError):
                cost_override = None

        created_at = str(item.get("created_at") or "").strip()
        updated_at = str(item.get("updated_at") or "").strip()
        notes = str(item.get("notes") or "")

        mj = ManualJob(
            manual_job_id=mid,
            project_id=pid,
            title=title,
            hours=hours,
            filament_g=filament_g,
            cost_override=cost_override,
            created_at=created_at,
            updated_at=updated_at,
            notes=notes,
        )
        jobs_by_project.setdefault(pid, []).append(mj)

    return jobs_by_project


def _save_manual_jobs(jobs_by_project: Dict[str, List[ManualJob]]) -> None:
    if _is_sql_only():
        _save_manual_jobs_sql(jobs_by_project)
        return
    payload: List[Dict[str, Any]] = []
    for pid, jobs in jobs_by_project.items():
        for j in jobs:
            payload.append(
                {
                    "manual_job_id": j.manual_job_id,
                    "project_id": j.project_id,
                    "title": j.title,
                    "hours": float(j.hours or 0.0),
                    "filament_g": float(j.filament_g or 0.0),
                    "cost_override": j.cost_override,
                    "created_at": j.created_at,
                    "updated_at": j.updated_at,
                    "notes": j.notes,
                }
            )
    _write_json(MANUAL_JOBS_FILE, payload)


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _default_filament_cost_per_kg() -> float:
    """
    Convert global DEFAULT_PRICING filament settings to an approximate $/kg.

    This is used for planned/manual entries that only provide filament grams.
    """
    mode = str(DEFAULT_PRICING.get("filament_mode") or "per_meter")
    rate = float(DEFAULT_PRICING.get("filament_rate") or 0.0)
    grams_per_meter = float(DEFAULT_PRICING.get("grams_per_meter") or 0.0)

    if mode == "per_kg":
        return max(rate, 0.0)
    if mode == "per_gram":
        return max(rate * 1000.0, 0.0)
    if mode == "per_meter":
        if grams_per_meter <= 0:
            return 0.0
        return max(rate * 1000.0 / grams_per_meter, 0.0)
    return 0.0


def _project_pricing(project: Optional[Project]) -> Tuple[float, float, bool]:
    """
    Resolve effective pricing defaults for a project.

    Priority:
      - project overrides (if set)
      - global DEFAULT_PRICING-derived values
    """
    hourly_rate = float(DEFAULT_PRICING.get("rate_per_hour") or 0.0)
    filament_per_kg = _default_filament_cost_per_kg()
    labor_only = False

    if project is not None:
        if project.hourly_rate_override is not None:
            try:
                hourly_rate = float(project.hourly_rate_override)
            except (TypeError, ValueError):
                pass
        if project.filament_cost_per_kg_override is not None:
            try:
                filament_per_kg = float(project.filament_cost_per_kg_override)
            except (TypeError, ValueError):
                pass
        labor_only = bool(project.labor_only)

    return max(hourly_rate, 0.0), max(filament_per_kg, 0.0), labor_only


def compute_planned_item_cost_from_pricing(
    *,
    est_time_s: int,
    est_filament_g: Optional[float],
    hourly_rate: float,
    filament_cost_per_kg: float,
    labor_only: bool,
) -> float:
    # Match compute_costs billing rule: minimum 1h for any non-zero job.
    duration_hours = max(float(est_time_s or 0) / 3600.0, 0.0)
    billed_hours = max(duration_hours, 1.0) if duration_hours > 0 else 0.0
    time_cost = billed_hours * float(hourly_rate or 0.0)

    material_cost = 0.0
    if not labor_only and est_filament_g is not None:
        try:
            g = float(est_filament_g)
        except (TypeError, ValueError):
            g = 0.0
        if g > 0:
            material_cost = (g / 1000.0) * float(filament_cost_per_kg or 0.0)

    return float(time_cost + material_cost)


def _planned_cost_from_defaults(est_time_s: int, est_filament_g: Optional[float]) -> float:
    """
    Compute planned item cost using existing default pricing settings.

    - Time cost: uses the same 1-hour minimum billing rule as job cost calculation.
    - Material cost: computed only if filament grams are provided, using default
      filament_mode/filament_rate/grams_per_meter.
    """
    hourly_rate = float(DEFAULT_PRICING.get("rate_per_hour") or 0.0)
    filament_per_kg = _default_filament_cost_per_kg()
    return compute_planned_item_cost_from_pricing(
        est_time_s=est_time_s,
        est_filament_g=est_filament_g,
        hourly_rate=hourly_rate,
        filament_cost_per_kg=filament_per_kg,
        labor_only=False,
    )


def create_manual_job(
    project_id: str,
    title: str,
    hours: float,
    filament_g: Optional[float] = None,
    cost_override: Optional[float] = None,
    notes: str = "",
) -> ManualJob:
    pid = str(project_id or "").strip()
    if not pid:
        raise ValueError("project_id is required")

    projects = load_projects()
    if pid not in projects:
        raise ValueError("Project not found")

    title = str(title or "").strip()
    if not title:
        raise ValueError("Description is required")

    try:
        hours_f = float(hours)
    except (TypeError, ValueError):
        raise ValueError("Hours must be a number")
    if hours_f <= 0:
        raise ValueError("Hours must be greater than 0")

    filament_f = 0.0
    if filament_g is not None and str(filament_g).strip() != "":
        try:
            filament_f = float(filament_g)
        except (TypeError, ValueError):
            filament_f = 0.0
    if filament_f < 0:
        raise ValueError("Filament grams must be >= 0")

    override_f: Optional[float]
    if cost_override is None or str(cost_override).strip() == "":
        override_f = None
    else:
        try:
            override_f = float(cost_override)
        except (TypeError, ValueError):
            override_f = None
    if override_f is not None and override_f < 0:
        raise ValueError("Cost override must be >= 0")

    mj = ManualJob(
        manual_job_id=uuid.uuid4().hex,
        project_id=pid,
        title=title,
        hours=hours_f,
        filament_g=filament_f,
        cost_override=override_f,
        created_at=_iso_now(),
        updated_at=_iso_now(),
        notes=str(notes or ""),
    )

    jobs_by_project = load_manual_jobs()
    jobs_by_project.setdefault(pid, []).append(mj)
    _save_manual_jobs(jobs_by_project)
    return mj


def update_manual_job(
    manual_job_id: str,
    title: str,
    hours: float,
    filament_g: Optional[float] = None,
    cost_override: Optional[float] = None,
    notes: str = "",
) -> ManualJob:
    mid = str(manual_job_id or "").strip()
    if not mid:
        raise ValueError("manual_job_id is required")

    title = str(title or "").strip()
    if not title:
        raise ValueError("Description is required")

    try:
        hours_f = float(hours)
    except (TypeError, ValueError):
        raise ValueError("Hours must be a number")
    if hours_f <= 0:
        raise ValueError("Hours must be greater than 0")

    filament_f = 0.0
    if filament_g is not None and str(filament_g).strip() != "":
        try:
            filament_f = float(filament_g)
        except (TypeError, ValueError):
            filament_f = 0.0
    if filament_f < 0:
        raise ValueError("Filament grams must be >= 0")

    override_f: Optional[float]
    if cost_override is None or str(cost_override).strip() == "":
        override_f = None
    else:
        try:
            override_f = float(cost_override)
        except (TypeError, ValueError):
            override_f = None
    if override_f is not None and override_f < 0:
        raise ValueError("Cost override must be >= 0")

    jobs_by_project = load_manual_jobs()
    for pid, jobs in jobs_by_project.items():
        for idx, j in enumerate(jobs):
            if j.manual_job_id == mid:
                updated = ManualJob(
                    manual_job_id=j.manual_job_id,
                    project_id=j.project_id,
                    title=title,
                    hours=hours_f,
                    filament_g=filament_f,
                    cost_override=override_f,
                    created_at=j.created_at,
                    updated_at=_iso_now(),
                    notes=str(notes or ""),
                )
                jobs[idx] = updated
                _save_manual_jobs(jobs_by_project)
                return updated

    raise ValueError("Manual job not found")


def delete_manual_job(manual_job_id: str) -> None:
    mid = str(manual_job_id or "").strip()
    if not mid:
        return
    jobs_by_project = load_manual_jobs()
    changed = False
    for pid, jobs in list(jobs_by_project.items()):
        new_jobs = [j for j in jobs if j.manual_job_id != mid]
        if len(new_jobs) != len(jobs):
            jobs_by_project[pid] = new_jobs
            changed = True
    if changed:
        _save_manual_jobs(jobs_by_project)


def _manual_job_cost(
    mj: ManualJob,
    *,
    hourly_rate: float,
    filament_cost_per_kg: float,
    labor_only: bool,
) -> float:
    if mj.cost_override is not None:
        try:
            return float(mj.cost_override)
        except (TypeError, ValueError):
            return 0.0

    try:
        hours = float(mj.hours or 0.0)
    except (TypeError, ValueError):
        hours = 0.0

    time_cost = hours * float(hourly_rate or 0.0)

    material_cost = 0.0
    if not labor_only:
        try:
            g = float(mj.filament_g or 0.0)
        except (TypeError, ValueError):
            g = 0.0
        if g > 0:
            material_cost = (g / 1000.0) * float(filament_cost_per_kg or 0.0)

    return float(time_cost + material_cost)


def compute_manual_job_cost(
    mj: ManualJob,
    *,
    project: Optional[Project] = None,
    hourly_rate: Optional[float] = None,
    filament_cost_per_kg: Optional[float] = None,
    labor_only: Optional[bool] = None,
) -> float:
    """
    Compute the cost contribution of a manual job.

    Rules:
    - If cost_override is present, it is used as the exact job cost.
    - Otherwise:
        time_cost = hours * hourly_rate
        material_cost = (filament_g/1000) * filament_cost_per_kg (unless labor_only)
    """
    pr_hourly, pr_fil_per_kg, pr_labor_only = _project_pricing(project)
    if hourly_rate is None:
        hourly_rate = pr_hourly
    if filament_cost_per_kg is None:
        filament_cost_per_kg = pr_fil_per_kg
    if labor_only is None:
        labor_only = pr_labor_only

    return _manual_job_cost(
        mj,
        hourly_rate=float(hourly_rate or 0.0),
        filament_cost_per_kg=float(filament_cost_per_kg or 0.0),
        labor_only=bool(labor_only),
    )


def load_plans() -> Dict[str, List[PlannedItem]]:
    """Load planned items grouped by project_id."""
    if _is_sql_only():
        return _load_plans_sql()
    ensure_projects_files()
    raw = _read_json(PLANS_FILE, [])
    if not isinstance(raw, list):
        raise ProjectsDataError(f"{PLANS_FILE} must contain a JSON list")

    by_project: Dict[str, List[PlannedItem]] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        plan_id = str(item.get("plan_id") or "").strip()
        pid = str(item.get("project_id") or "").strip()
        filename = str(item.get("filename") or "").strip()
        created_at = str(item.get("created_at") or "").strip()
        status = str(item.get("status") or "active").strip().lower()
        source = str(item.get("source") or "").strip()
        notes = str(item.get("notes") or "")
        converted_to_manual_job_id = item.get("converted_to_manual_job_id", None)
        converted_to_manual_job_id = str(converted_to_manual_job_id).strip() if converted_to_manual_job_id else None
        if status not in ("active", "fulfilled"):
            status = "active"

        try:
            est_time_s = int(float(item.get("est_time_s") or 0))
        except (TypeError, ValueError):
            est_time_s = 0

        est_filament_g_raw = item.get("est_filament_g", None)
        est_filament_g: Optional[float]
        if est_filament_g_raw is None or str(est_filament_g_raw).strip() == "":
            est_filament_g = None
        else:
            try:
                est_filament_g = float(est_filament_g_raw)
            except (TypeError, ValueError):
                est_filament_g = None

        try:
            est_cost = float(item.get("est_cost") or 0.0)
        except (TypeError, ValueError):
            est_cost = 0.0

        est_cost_is_override = bool(item.get("est_cost_is_override") or False)

        if not plan_id or not pid or not filename or est_time_s <= 0:
            continue

        by_project.setdefault(pid, []).append(
            PlannedItem(
                plan_id=plan_id,
                project_id=pid,
                filename=filename,
                created_at=created_at,
                est_time_s=est_time_s,
                est_filament_g=est_filament_g,
                est_cost=est_cost,
                est_cost_is_override=est_cost_is_override,
                status=status,
                source=source,
                notes=notes,
                converted_to_manual_job_id=converted_to_manual_job_id,
            )
        )

    return by_project


def _save_plans(by_project: Dict[str, List[PlannedItem]]) -> None:
    if _is_sql_only():
        _save_plans_sql(by_project)
        return
    payload: List[Dict[str, Any]] = []
    for pid, items in by_project.items():
        for p in items:
            payload.append(
                {
                    "plan_id": p.plan_id,
                    "project_id": p.project_id,
                    "filename": p.filename,
                    "created_at": p.created_at,
                    "est_time_s": int(p.est_time_s),
                    "est_filament_g": p.est_filament_g,
                    "est_cost": float(p.est_cost or 0.0),
                    "est_cost_is_override": bool(p.est_cost_is_override),
                    "status": p.status,
                    "source": p.source,
                    "notes": p.notes,
                    "converted_to_manual_job_id": p.converted_to_manual_job_id,
                }
            )
    _write_json(PLANS_FILE, payload)


def create_plan_item(
    project_id: str,
    filename: str,
    est_time_s: int,
    est_filament_g: Optional[float] = None,
    source: str = "",
    notes: str = "",
    est_cost_override: Optional[float] = None,
) -> PlannedItem:
    pid = str(project_id or "").strip()
    if not pid:
        raise ValueError("project_id is required")
    projects = load_projects()
    if pid not in projects:
        raise ValueError("Project not found")

    filename = str(filename or "").strip()
    if not filename:
        raise ValueError("filename is required")

    try:
        time_s = int(float(est_time_s))
    except (TypeError, ValueError):
        time_s = 0
    if time_s <= 0:
        raise ValueError("Estimated time must be provided")

    project = projects.get(pid)
    hourly_rate, filament_cost_per_kg, labor_only = _project_pricing(project)

    est_cost_is_override = False
    if est_cost_override is not None and str(est_cost_override).strip() != "":
        try:
            est_cost = float(est_cost_override)
            est_cost_is_override = True
        except (TypeError, ValueError):
            est_cost = compute_planned_item_cost_from_pricing(
                est_time_s=time_s,
                est_filament_g=est_filament_g,
                hourly_rate=hourly_rate,
                filament_cost_per_kg=filament_cost_per_kg,
                labor_only=labor_only,
            )
            est_cost_is_override = False
    else:
        est_cost = compute_planned_item_cost_from_pricing(
            est_time_s=time_s,
            est_filament_g=est_filament_g,
            hourly_rate=hourly_rate,
            filament_cost_per_kg=filament_cost_per_kg,
            labor_only=labor_only,
        )
        est_cost_is_override = False
    item = PlannedItem(
        plan_id=uuid.uuid4().hex,
        project_id=pid,
        filename=filename,
        created_at=_iso_now(),
        est_time_s=time_s,
        est_filament_g=est_filament_g,
        est_cost=est_cost,
        est_cost_is_override=est_cost_is_override,
        status="active",
        source=str(source or "").strip(),
        notes=str(notes or ""),
        converted_to_manual_job_id=None,
    )

    plans = load_plans()
    plans.setdefault(pid, []).append(item)
    _save_plans(plans)
    return item


def update_plan_item(
    plan_id: str,
    *,
    filename: Optional[str] = None,
    est_time_s: Optional[int] = None,
    est_filament_g: Optional[float] = None,
    est_cost: Optional[float] = None,
    source: Optional[str] = None,
    notes: Optional[str] = None,
) -> PlannedItem:
    """
    Update an existing planned item (estimates-only overlay).

    - Does not touch CSV history.
    - If est_cost is None, cost is recomputed from defaults using time + filament.
    """
    pid = str(plan_id or "").strip()
    if not pid:
        raise ValueError("plan_id is required")

    plans = load_plans()
    for proj_id, items in plans.items():
        for idx, it in enumerate(items):
            if it.plan_id != pid:
                continue

            new_filename = it.filename if filename is None else str(filename or "").strip() or it.filename

            time_s = it.est_time_s
            if est_time_s is not None and str(est_time_s).strip() != "":
                try:
                    time_s = int(float(est_time_s))
                except (TypeError, ValueError):
                    time_s = 0
            if time_s <= 0:
                raise ValueError("Estimated time must be greater than 0")

            filament_g = est_filament_g
            if filament_g is None:
                filament_g = it.est_filament_g

            projects_map = load_projects()
            project = projects_map.get(it.project_id)
            hourly_rate, filament_cost_per_kg, labor_only = _project_pricing(project)

            cost_is_override = False
            if est_cost is None or str(est_cost).strip() == "":
                cost = compute_planned_item_cost_from_pricing(
                    est_time_s=time_s,
                    est_filament_g=filament_g,
                    hourly_rate=hourly_rate,
                    filament_cost_per_kg=filament_cost_per_kg,
                    labor_only=labor_only,
                )
                cost_is_override = False
            else:
                try:
                    cost = float(est_cost)
                    cost_is_override = True
                except (TypeError, ValueError):
                    cost = compute_planned_item_cost_from_pricing(
                        est_time_s=time_s,
                        est_filament_g=filament_g,
                        hourly_rate=hourly_rate,
                        filament_cost_per_kg=filament_cost_per_kg,
                        labor_only=labor_only,
                    )
                    cost_is_override = False

            new_source = it.source if source is None else str(source or "").strip()
            new_notes = it.notes if notes is None else str(notes or "")

            updated = PlannedItem(
                plan_id=it.plan_id,
                project_id=it.project_id,
                filename=new_filename,
                created_at=it.created_at,
                est_time_s=time_s,
                est_filament_g=filament_g,
                est_cost=cost,
                est_cost_is_override=cost_is_override,
                status=it.status,
                source=new_source,
                notes=new_notes,
                converted_to_manual_job_id=it.converted_to_manual_job_id,
            )
            items[idx] = updated
            _save_plans(plans)
            return updated

    raise ValueError("Planned item not found")


def set_plan_status(plan_id: str, status: str) -> None:
    pid = str(plan_id or "").strip()
    if not pid:
        return
    status = str(status or "").strip().lower()
    if status not in ("active", "fulfilled"):
        status = "active"
    plans = load_plans()
    changed = False
    for proj_id, items in plans.items():
        for idx, it in enumerate(items):
            if it.plan_id == pid:
                items[idx] = PlannedItem(
                    plan_id=it.plan_id,
                    project_id=it.project_id,
                    filename=it.filename,
                    created_at=it.created_at,
                    est_time_s=it.est_time_s,
                    est_filament_g=it.est_filament_g,
                    est_cost=it.est_cost,
                    est_cost_is_override=it.est_cost_is_override,
                    status=status,
                    source=it.source,
                    notes=it.notes,
                    converted_to_manual_job_id=it.converted_to_manual_job_id,
                )
                changed = True
                break
    if changed:
        _save_plans(plans)


def _set_plan_converted(plan_id: str, manual_job_id: str) -> None:
    pid = str(plan_id or "").strip()
    if not pid:
        return
    manual_job_id = str(manual_job_id or "").strip()
    if not manual_job_id:
        return

    plans = load_plans()
    changed = False
    for proj_id, items in plans.items():
        for idx, it in enumerate(items):
            if it.plan_id == pid:
                items[idx] = PlannedItem(
                    plan_id=it.plan_id,
                    project_id=it.project_id,
                    filename=it.filename,
                    created_at=it.created_at,
                    est_time_s=it.est_time_s,
                    est_filament_g=it.est_filament_g,
                    est_cost=it.est_cost,
                    est_cost_is_override=it.est_cost_is_override,
                    status="fulfilled",
                    source=it.source,
                    notes=it.notes,
                    converted_to_manual_job_id=manual_job_id,
                )
                changed = True
                break
    if changed:
        _save_plans(plans)


def convert_plan_item_to_manual(project_id: str, plan_id: str) -> ManualJob:
    """
    Convert a planned item into a manual job for the same project.

    - Creates a manual job with hours derived from est_time_s.
    - Uses planned filename as the title.
    - Copies filament grams if present.
    - Does not force cost_override (manual cost remains time-based unless overridden by user).
    - Marks the planned item as fulfilled and stores converted_to_manual_job_id.

    Idempotency:
    - If planned item already has converted_to_manual_job_id, raises ValueError.
    """
    pid = str(project_id or "").strip()
    if not pid:
        raise ValueError("Project is required")
    plan_id = str(plan_id or "").strip()
    if not plan_id:
        raise ValueError("Planned item is required")

    projects_map = load_projects()
    if pid not in projects_map:
        raise ValueError("Project not found")

    plans = load_plans()
    plan: Optional[PlannedItem] = None
    for it in plans.get(pid, []):
        if it.plan_id == plan_id:
            plan = it
            break
    if not plan:
        raise ValueError("Planned item not found")
    if plan.converted_to_manual_job_id:
        raise ValueError("This planned item was already converted to a manual job.")

    hours = float(plan.est_time_s or 0) / 3600.0
    title = plan.filename
    notes = f"Converted from planned item {plan.plan_id}"
    mj = create_manual_job(
        project_id=pid,
        title=title,
        hours=hours,
        filament_g=plan.est_filament_g if plan.est_filament_g is not None else None,
        cost_override=None,
        notes=notes,
    )

    # Only mark the plan fulfilled if manual job creation succeeded.
    _set_plan_converted(plan.plan_id, mj.manual_job_id)
    return mj


def delete_plan_item(plan_id: str) -> None:
    pid = str(plan_id or "").strip()
    if not pid:
        return
    plans = load_plans()
    changed = False
    for proj_id, items in list(plans.items()):
        new_items = [it for it in items if it.plan_id != pid]
        if len(new_items) != len(items):
            plans[proj_id] = new_items
            changed = True
    if changed:
        _save_plans(plans)


def compute_planned_item_cost(item: PlannedItem, project: Optional[Project]) -> float:
    """
    Compute the effective cost for a planned item.

    - If the item has an explicit cost override, use it.
    - Otherwise compute from current project defaults (or global defaults).
    """
    if item.est_cost_is_override:
        try:
            return float(item.est_cost or 0.0)
        except (TypeError, ValueError):
            return 0.0

    hourly_rate, filament_cost_per_kg, labor_only = _project_pricing(project)
    return compute_planned_item_cost_from_pricing(
        est_time_s=int(item.est_time_s or 0),
        est_filament_g=item.est_filament_g,
        hourly_rate=hourly_rate,
        filament_cost_per_kg=filament_cost_per_kg,
        labor_only=labor_only,
    )


def compute_project_projection(plans: List[PlannedItem], project: Optional[Project] = None) -> Dict[str, float]:
    """Projected totals from ACTIVE planned items only."""
    projected = {"count": 0.0, "hours": 0.0, "cost": 0.0}
    for p in plans:
        if p.status != "active":
            continue
        projected["count"] += 1.0
        projected["hours"] += float(p.est_time_s or 0) / 3600.0
        projected["cost"] += float(compute_planned_item_cost(p, project))
    return projected

def _opt_nonneg_float(value) -> Optional[float]:
    if value is None or str(value).strip() == "":
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f >= 0 else None


def create_project(
    name: str,
    notes: str = "",
    *,
    hourly_rate_override: Optional[float] = None,
    filament_cost_per_kg_override: Optional[float] = None,
    labor_only: bool = False,
) -> Project:
    name = str(name or "").strip()
    if not name:
        raise ValueError("Project name is required")
    now = time.time()
    pid = uuid.uuid4().hex
    project = Project(
        id=pid,
        name=name,
        notes=str(notes or ""),
        hourly_rate_override=_opt_nonneg_float(hourly_rate_override),
        filament_cost_per_kg_override=_opt_nonneg_float(filament_cost_per_kg_override),
        labor_only=bool(labor_only),
        created_at=now,
        updated_at=now,
    )
    if _is_sql_only():
        _create_project_sql(project)
        return project
    projects = load_projects()
    projects[pid] = project
    save_projects(projects.values())
    return project


def update_project(
    project_id: str,
    name: str,
    notes: str = "",
    *,
    hourly_rate_override: Optional[float] = None,
    filament_cost_per_kg_override: Optional[float] = None,
    labor_only: Optional[bool] = None,
) -> Project:
    pid = str(project_id or "").strip()
    if not pid:
        raise ValueError("project_id is required")
    name = str(name or "").strip()
    if not name:
        raise ValueError("Project name is required")

    if _is_sql_only():
        existing = load_projects().get(pid)
        if not existing:
            raise ValueError("Project not found")
        updated = Project(
            id=pid,
            name=name,
            notes=str(notes or ""),
            hourly_rate_override=_opt_nonneg_float(hourly_rate_override) if hourly_rate_override is not None else existing.hourly_rate_override,
            filament_cost_per_kg_override=_opt_nonneg_float(filament_cost_per_kg_override) if filament_cost_per_kg_override is not None else existing.filament_cost_per_kg_override,
            labor_only=bool(labor_only) if labor_only is not None else bool(existing.labor_only),
            created_at=existing.created_at,
            updated_at=time.time(),
        )
        _update_project_sql(updated)
        return updated

    projects = load_projects()
    existing = projects.get(pid)
    if not existing:
        raise ValueError("Project not found")

    now = time.time()
    updated = Project(
        id=pid,
        name=name,
        notes=str(notes or ""),
        hourly_rate_override=_opt_nonneg_float(hourly_rate_override) if hourly_rate_override is not None else existing.hourly_rate_override,
        filament_cost_per_kg_override=_opt_nonneg_float(filament_cost_per_kg_override) if filament_cost_per_kg_override is not None else existing.filament_cost_per_kg_override,
        labor_only=bool(labor_only) if labor_only is not None else bool(existing.labor_only),
        created_at=existing.created_at,
        updated_at=now,
    )
    projects[pid] = updated
    save_projects(projects.values())
    return updated


def delete_project(project_id: str) -> None:
    """Delete a project and unassign any jobs mapped to it."""
    pid = str(project_id or "").strip()
    if not pid:
        return

    if _is_sql_only():
        _delete_project_sql(pid)
        return

    projects = load_projects()
    if pid in projects:
        projects.pop(pid, None)
        save_projects(projects.values())

    assignments = load_assignments()
    new_assignments = {k: v for k, v in assignments.items() if v != pid}
    if new_assignments != assignments:
        save_assignments(new_assignments)

    # Remove manual jobs in this project.
    jobs_by_project = load_manual_jobs()
    if pid in jobs_by_project:
        jobs_by_project.pop(pid, None)
        _save_manual_jobs(jobs_by_project)

    # Remove planned items in this project.
    plans_by_project = load_plans()
    if pid in plans_by_project:
        plans_by_project.pop(pid, None)
        _save_plans(plans_by_project)


def assign_jobs(job_keys: Iterable[str], project_id: str) -> None:
    pid = str(project_id or "").strip()
    if not pid:
        raise ValueError("project_id is required")

    projects = load_projects()
    if pid not in projects:
        raise ValueError("Project not found")

    assignments = load_assignments()
    for key in job_keys:
        k = str(key or "").strip()
        if k:
            assignments[k] = pid
    save_assignments(assignments)


def unassign_jobs(job_keys: Iterable[str]) -> None:
    assignments = load_assignments()
    changed = False
    for key in job_keys:
        k = str(key or "").strip()
        if k and k in assignments:
            assignments.pop(k, None)
            changed = True
    if changed:
        save_assignments(assignments)


def job_key(row: Dict[str, Any]) -> str:
    """
    Create a stable identifier for a history row without modifying CSV schema.

    Uses a tuple of (timestamp_raw, printer, filename, duration_seconds, filament_mm).
    This is not cryptographically unique, but is stable enough for UI-managed
    assignment and avoids relying on CSV row indices.
    """
    ts = str(row.get("timestamp_raw") or row.get("timestamp") or "").strip()
    printer = str(row.get("printer") or "").strip()
    filename = str(row.get("filename") or "").strip()
    duration = str(row.get("duration_seconds") or "").strip()
    filament = str(row.get("filament_mm") or "").strip()
    return "|".join([ts, printer, filename, duration, filament])


def job_uid(row: Dict[str, Any]) -> str:
    """
    Return a stable UID for a history row.

    Prefer the precomputed `job_uid` field (added by core.storage.load_rows_raw),
    otherwise return an empty string (job_uid is expected to be persisted).
    """
    existing = row.get("job_uid")
    if isinstance(existing, str) and existing.strip():
        return existing.strip()
    return ""


def migrate_assignments_to_job_uid(rows: List[Dict[str, Any]]) -> Tuple[Dict[str, str], int]:
    """
    Migrate legacy assignment keys (job_key / legacy computed uid) to the persisted job_uid.

    This is safe and idempotent:
      - If assignments already use job_uid keys, it does nothing.
      - If a legacy key cannot be mapped to a current row, it is removed from active
        assignments and recorded to `data/project_assignments_orphans.json` (if possible).

    Returns (assignments, orphans_added) where orphans_added counts newly recorded orphan
    entries written during this run (useful for a one-time banner on /projects).
    """
    if _is_sql_only():
        return load_assignments(), 0
    assignments = load_assignments()
    if not assignments:
        return assignments, 0

    def _looks_like_uid(k: str) -> bool:
        # Persisted UIDs are UUID4 strings; legacy computed IDs were "job_<hash>".
        try:
            import uuid as _uuid
            _uuid.UUID(k)
            return True
        except Exception:
            return False

    # Build mapping from legacy keys -> persisted job_uid for current rows.
    legacy_to_uid: Dict[str, str] = {}
    for r in rows:
        try:
            persisted = job_uid(r)
            if not persisted:
                continue
            legacy_to_uid[job_key(r)] = persisted
            legacy_to_uid[str(r.get("legacy_job_uid") or compute_job_uid(r) or "").strip()] = persisted
        except Exception:
            continue

    migrated: Dict[str, str] = {}
    changed = False
    dropped = 0
    migrated_count = 0
    unresolved_legacy: List[Tuple[str, str]] = []
    for k, pid in assignments.items():
        key = str(k or "").strip()
        if not key:
            changed = True
            dropped += 1
            continue
        if _looks_like_uid(key):
            migrated[key] = pid
            continue
        mapped = legacy_to_uid.get(key)
        if mapped:
            migrated[mapped] = pid
            changed = True
            migrated_count += 1
        else:
            unresolved_legacy.append((key, str(pid)))

    orphans_added = 0
    if unresolved_legacy:
        # Record unresolved legacy keys before removing them from the active map.
        existing_orphans = _safe_read_orphaned_assignments()
        if existing_orphans is None:
            # Preserve assignments map intact if we can't safely persist evidence.
            try:
                import logging
                logging.getLogger(__name__).warning(
                    "Could not read %s; skipping orphan cleanup of %s legacy assignment(s).",
                    ASSIGNMENTS_ORPHANS_FILE,
                    len(unresolved_legacy),
                )
            except Exception:
                pass
            return assignments, 0

        existing_pairs = set()
        for item in existing_orphans:
            try:
                existing_pairs.add((str(item.get("key") or ""), str(item.get("value") or "")))
            except Exception:
                continue

        now = _iso_now()
        for key, value in unresolved_legacy:
            pair = (key, value)
            if pair in existing_pairs:
                continue
            existing_orphans.append({"key": key, "value": value, "orphaned_at": now})
            existing_pairs.add(pair)
            orphans_added += 1

        if not _safe_write_orphaned_assignments(existing_orphans):
            # Preserve assignments map intact if we can't safely persist evidence.
            try:
                import logging
                logging.getLogger(__name__).warning(
                    "Could not write %s; skipping orphan cleanup of %s legacy assignment(s).",
                    ASSIGNMENTS_ORPHANS_FILE,
                    len(unresolved_legacy),
                )
            except Exception:
                pass
            return assignments, 0

        # Now it's safe to drop unresolved legacy keys from active assignments.
        changed = True
        dropped += len(unresolved_legacy)

    if changed and migrated != assignments:
        save_assignments(migrated)
        try:
            import logging
            logging.getLogger(__name__).warning(
                "Migrated project assignments to persisted job_uid: migrated=%s dropped=%s orphans_added=%s",
                migrated_count,
                dropped,
                orphans_added,
            )
        except Exception:
            pass
        return migrated, orphans_added

    return assignments, 0


def recalculate() -> Tuple[Dict[str, Project], Dict[str, str]]:
    """
    Clean up references after mutations:
    - Drop assignment entries referencing missing projects.
    Returns (projects, assignments).
    """
    projects = load_projects()
    assignments = load_assignments()
    valid_ids = set(projects.keys())
    cleaned = {k: v for k, v in assignments.items() if v in valid_ids}
    if cleaned != assignments:
        save_assignments(cleaned)
        assignments = cleaned
    return projects, assignments


def recalculate_all() -> Tuple[Dict[str, Project], Dict[str, str], Dict[str, List[ManualJob]], Dict[str, List[PlannedItem]]]:
    """
    Recalculate/cleanup all project-related stores:
    - Drop assignment entries referencing missing projects.
    - Drop manual jobs referencing missing projects.
    Returns (projects, assignments, manual_jobs_by_project, plans_by_project).
    """
    projects, assignments = recalculate()
    jobs_by_project = load_manual_jobs()
    valid_ids = set(projects.keys())
    cleaned_jobs = {pid: jobs for pid, jobs in jobs_by_project.items() if pid in valid_ids}
    if cleaned_jobs != jobs_by_project:
        _save_manual_jobs(cleaned_jobs)
        jobs_by_project = cleaned_jobs
    plans_by_project = load_plans()
    cleaned_plans = {pid: items for pid, items in plans_by_project.items() if pid in valid_ids}
    if cleaned_plans != plans_by_project:
        _save_plans(cleaned_plans)
        plans_by_project = cleaned_plans

    return projects, assignments, jobs_by_project, plans_by_project


def group_rows_by_project(rows: List[Dict[str, Any]]) -> Tuple[Dict[str, List[Dict[str, Any]]], List[Dict[str, Any]]]:
    """
    Return (project_jobs, unassigned_jobs) where project_jobs maps project_id to row list.
    Each row will have added `job_uid` (stable) and `job_key` (legacy) fields.
    """
    projects, assignments = recalculate()
    project_jobs: Dict[str, List[Dict[str, Any]]] = {pid: [] for pid in projects.keys()}
    unassigned: List[Dict[str, Any]] = []

    for r in rows:
        uid = job_uid(r)
        jk = job_key(r)
        rr = dict(r)
        rr["job_uid"] = uid
        rr["job_key"] = jk  # legacy, for backward compatibility
        pid = assignments.get(uid) or assignments.get(jk)
        if pid and pid in project_jobs:
            project_jobs[pid].append(rr)
        else:
            unassigned.append(rr)

    return project_jobs, unassigned


def compute_project_totals(
    tracked_rows: List[Dict[str, Any]],
    manual_jobs: Optional[List[ManualJob]] = None,
    project: Optional[Project] = None,
) -> Dict[str, float]:
    """
    Compute derived totals for a project (tracked jobs + manual jobs).

    - Filament totals are tracked separately:
        tracked -> meters
        manual -> grams (optional)
    - Manual job cost is time-only unless cost_override is provided.
    """
    manual_jobs = manual_jobs or []
    totals = {"prints": 0.0, "hours": 0.0, "meters": 0.0, "filament_g": 0.0, "cost": 0.0}
    totals["prints"] = float(len(tracked_rows) + len(manual_jobs))

    for r in tracked_rows:
        try:
            totals["hours"] += float(r.get("duration_hours") or 0.0)
            totals["meters"] += float(r.get("filament_meters") or 0.0)
            totals["cost"] += float(r.get("total_cost") or 0.0)
        except (TypeError, ValueError):
            continue

    hourly_rate, filament_cost_per_kg, labor_only = _project_pricing(project)
    for mj in manual_jobs:
        try:
            totals["hours"] += float(mj.hours or 0.0)
            totals["filament_g"] += float(mj.filament_g or 0.0)
            totals["cost"] += _manual_job_cost(
                mj,
                hourly_rate=float(hourly_rate or 0.0),
                filament_cost_per_kg=float(filament_cost_per_kg or 0.0),
                labor_only=bool(labor_only),
            )
        except (TypeError, ValueError):
            continue
    return totals


def import_json_to_sql(apply: bool = False) -> Dict[str, Any]:
    """
    One-time import: copy legacy JSON-backed projects data into SQL tables.

    This function reads legacy JSON files directly and should only be invoked
    explicitly via the CLI import command. It never flips KCD_STORAGE_BACKEND.
    """
    status = "ok"
    errors: List[str] = []

    def _read_json_direct(path: str, default: Any) -> Any:
        if not os.path.exists(path):
            return default
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as exc:
            errors.append(f"failed to read {path}: {exc}")
            return default

    raw_projects = _read_json_direct(PROJECTS_FILE, [])
    raw_assignments = _read_json_direct(ASSIGNMENTS_FILE, {})
    raw_manual_jobs = _read_json_direct(MANUAL_JOBS_FILE, [])
    raw_plans = _read_json_direct(PLANS_FILE, [])

    projects_map: Dict[str, Project] = {}
    if isinstance(raw_projects, list):
        for item in raw_projects:
            if not isinstance(item, dict):
                continue
            pid = str(item.get("id") or "").strip()
            name = str(item.get("name") or "").strip()
            if not pid or not name:
                continue
            notes = str(item.get("notes") or "")
            created_at = float(item.get("created_at") or 0.0)
            updated_at = float(item.get("updated_at") or 0.0)

            def _opt_nonneg_float(value) -> Optional[float]:
                if value is None or str(value).strip() == "":
                    return None
                try:
                    fval = float(value)
                except (TypeError, ValueError):
                    return None
                return fval if fval >= 0 else None

            hourly_rate_override = _opt_nonneg_float(item.get("hourly_rate_override"))
            filament_cost_per_kg_override = _opt_nonneg_float(item.get("filament_cost_per_kg_override"))
            labor_only = bool(item.get("labor_only") or False)
            projects_map[pid] = Project(
                id=pid,
                name=name,
                notes=notes,
                hourly_rate_override=hourly_rate_override,
                filament_cost_per_kg_override=filament_cost_per_kg_override,
                labor_only=labor_only,
                created_at=created_at,
                updated_at=updated_at,
            )
    else:
        errors.append(f"{PROJECTS_FILE} must contain a JSON list")

    assignments: Dict[str, str] = {}
    if isinstance(raw_assignments, dict):
        for k, v in raw_assignments.items():
            key = str(k or "").strip()
            pid = str(v or "").strip()
            if key and pid:
                assignments[key] = pid
    else:
        errors.append(f"{ASSIGNMENTS_FILE} must contain a JSON object")

    manual_jobs: Dict[str, List[ManualJob]] = {}
    if isinstance(raw_manual_jobs, list):
        for item in raw_manual_jobs:
            if not isinstance(item, dict):
                continue
            mid = str(item.get("manual_job_id") or "").strip()
            pid = str(item.get("project_id") or "").strip()
            title = str(item.get("title") or item.get("description") or "").strip()
            if not mid or not pid or not title:
                continue
            try:
                hours = float(item.get("hours") or 0.0)
            except (TypeError, ValueError):
                hours = 0.0
            try:
                filament_g = float(item.get("filament_g") or 0.0)
            except (TypeError, ValueError):
                filament_g = 0.0
            cost_override_raw = item.get("cost_override", None)
            cost_override: Optional[float]
            if cost_override_raw is None or str(cost_override_raw).strip() == "":
                cost_override = None
            else:
                try:
                    cost_override = float(cost_override_raw)
                except (TypeError, ValueError):
                    cost_override = None
            created_at = str(item.get("created_at") or "")
            updated_at = str(item.get("updated_at") or "")
            job = ManualJob(
                manual_job_id=mid,
                project_id=pid,
                title=title,
                hours=hours,
                filament_g=filament_g,
                cost_override=cost_override,
                created_at=created_at,
                updated_at=updated_at,
            )
            manual_jobs.setdefault(pid, []).append(job)
    else:
        errors.append(f"{MANUAL_JOBS_FILE} must contain a JSON list")

    plans: Dict[str, List[PlannedItem]] = {}
    if isinstance(raw_plans, list):
        for item in raw_plans:
            if not isinstance(item, dict):
                continue
            plan_id = str(item.get("plan_id") or "").strip()
            pid = str(item.get("project_id") or "").strip()
            filename = str(item.get("filename") or "").strip()
            created_at = str(item.get("created_at") or "").strip()
            status = str(item.get("status") or "active").strip().lower()
            source = str(item.get("source") or "").strip()
            notes = str(item.get("notes") or "")
            converted_to_manual_job_id = item.get("converted_to_manual_job_id", None)
            converted_to_manual_job_id = str(converted_to_manual_job_id).strip() if converted_to_manual_job_id else None
            if status not in ("active", "fulfilled"):
                status = "active"

            try:
                est_time_s = int(float(item.get("est_time_s") or 0))
            except (TypeError, ValueError):
                est_time_s = 0

            est_filament_g_raw = item.get("est_filament_g", None)
            est_filament_g: Optional[float]
            if est_filament_g_raw is None or str(est_filament_g_raw).strip() == "":
                est_filament_g = None
            else:
                try:
                    est_filament_g = float(est_filament_g_raw)
                except (TypeError, ValueError):
                    est_filament_g = None

            est_cost_raw = item.get("est_cost", None)
            est_cost: Optional[float]
            if est_cost_raw is None or str(est_cost_raw).strip() == "":
                est_cost = None
            else:
                try:
                    est_cost = float(est_cost_raw)
                except (TypeError, ValueError):
                    est_cost = None

            est_cost_is_override = bool(item.get("est_cost_is_override") or False)
            status = str(status or "active")
            if not plan_id or not pid or not filename:
                continue
            plan = PlannedItem(
                plan_id=plan_id,
                project_id=pid,
                filename=filename,
                created_at=created_at,
                status=status,
                source=source,
                notes=notes,
                converted_to_manual_job_id=converted_to_manual_job_id,
                est_time_s=est_time_s,
                est_filament_g=est_filament_g,
                est_cost=est_cost,
                est_cost_is_override=est_cost_is_override,
            )
            plans.setdefault(pid, []).append(plan)
    else:
        errors.append(f"{PLANS_FILE} must contain a JSON list")

    manual_count = sum(len(v) for v in manual_jobs.values())
    plan_count = sum(len(v) for v in plans.values())

    report = {
        "status": status,
        "apply": bool(apply),
        "scanned": {
            "projects": len(projects_map),
            "assignments": len(assignments),
            "manual_jobs": manual_count,
            "planned_items": plan_count,
        },
        "imported": {
            "projects": 0,
            "assignments": 0,
            "manual_jobs": 0,
            "planned_items": 0,
        },
        "skipped": 0,
        "duplicates": 0,
        "errors": errors,
    }

    if not apply:
        return report

    _save_projects_sql(projects_map.values())
    _save_assignments_sql(assignments)
    _save_manual_jobs_sql(manual_jobs)
    _save_plans_sql(plans)

    report["imported"] = {
        "projects": len(projects_map),
        "assignments": len(assignments),
        "manual_jobs": manual_count,
        "planned_items": plan_count,
    }
    return report
