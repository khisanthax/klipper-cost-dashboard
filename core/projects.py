"""
Projects feature for Klipper Cost Dashboard.

Data storage:
- Projects are stored in `data/projects.json` as a list of project objects.
- Job membership is stored in `data/project_assignments.json` as a mapping:
    job_key -> project_id

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
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

from core.config import DATA_DIR
from core.config import DEFAULT_PRICING


PROJECTS_FILE = os.path.join(DATA_DIR, "projects.json")
ASSIGNMENTS_FILE = os.path.join(DATA_DIR, "project_assignments.json")
MANUAL_JOBS_FILE = os.path.join(DATA_DIR, "project_manual_jobs.json")
PLANS_FILE = os.path.join(DATA_DIR, "project_plans.json")


class ProjectsDataError(RuntimeError):
    """Raised when projects storage is unreadable/corrupt."""


@dataclass(frozen=True)
class Project:
    id: str
    name: str
    notes: str = ""
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
    status: str = "active"  # "active" | "fulfilled"
    source: str = ""
    notes: str = ""
    converted_to_manual_job_id: Optional[str] = None


def _ensure_data_dir() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)


def _read_json(path: str, default: Any) -> Any:
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
    _ensure_data_dir()
    if not os.path.exists(PROJECTS_FILE):
        _write_json(PROJECTS_FILE, [])
    if not os.path.exists(ASSIGNMENTS_FILE):
        _write_json(ASSIGNMENTS_FILE, {})
    if not os.path.exists(MANUAL_JOBS_FILE):
        _write_json(MANUAL_JOBS_FILE, [])
    if not os.path.exists(PLANS_FILE):
        _write_json(PLANS_FILE, [])


def load_projects() -> Dict[str, Project]:
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
        projects[pid] = Project(id=pid, name=name, notes=notes, created_at=created_at, updated_at=updated_at)
    return projects


def save_projects(projects: Iterable[Project]) -> None:
    payload: List[Dict[str, Any]] = []
    for p in projects:
        payload.append(
            {
                "id": p.id,
                "name": p.name,
                "notes": p.notes,
                "created_at": float(p.created_at or 0.0),
                "updated_at": float(p.updated_at or 0.0),
            }
        )
    _write_json(PROJECTS_FILE, payload)


def load_assignments() -> Dict[str, str]:
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
    _write_json(ASSIGNMENTS_FILE, dict(assignments))


def load_manual_jobs() -> Dict[str, List[ManualJob]]:
    """
    Return manual jobs grouped by project_id.

    Storage is a list of dicts in `data/project_manual_jobs.json`.
    """
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


def _planned_cost_from_defaults(est_time_s: int, est_filament_g: Optional[float]) -> float:
    """
    Compute planned item cost using existing default pricing settings.

    - Time cost: uses the same 1-hour minimum billing rule as job cost calculation.
    - Material cost: computed only if filament grams are provided, using default
      filament_mode/filament_rate/grams_per_meter.
    """
    rate_per_hour = float(DEFAULT_PRICING.get("rate_per_hour") or 0.0)
    duration_hours = max(float(est_time_s or 0) / 3600.0, 0.0)
    billed_hours = 0.0
    if duration_hours > 0:
        billed_hours = max(duration_hours, 1.0)
    time_cost = billed_hours * rate_per_hour

    material_cost = 0.0
    if est_filament_g is not None:
        try:
            g = float(est_filament_g)
        except (TypeError, ValueError):
            g = 0.0
        mode = str(DEFAULT_PRICING.get("filament_mode") or "per_meter")
        rate = float(DEFAULT_PRICING.get("filament_rate") or 0.0)
        gpm = float(DEFAULT_PRICING.get("grams_per_meter") or 0.0)
        if mode == "per_gram":
            material_cost = rate * max(g, 0.0)
        elif mode == "per_kg":
            material_cost = rate * (max(g, 0.0) / 1000.0)
        elif mode == "per_meter" and gpm > 0:
            meters = max(g, 0.0) / gpm
            material_cost = rate * meters

    return float(time_cost + material_cost)


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


def _manual_job_cost(mj: ManualJob, hourly_rate: float) -> float:
    if mj.cost_override is not None:
        try:
            return float(mj.cost_override)
        except (TypeError, ValueError):
            return 0.0
    # v1: time-only cost, filament optional is tracked but not monetized here.
    try:
        return float(mj.hours or 0.0) * float(hourly_rate or 0.0)
    except (TypeError, ValueError):
        return 0.0


def compute_manual_job_cost(mj: ManualJob, hourly_rate: Optional[float] = None) -> float:
    """
    Compute the cost contribution of a manual job.

    v1 behavior:
    - If cost_override is present, it is used as the exact job cost.
    - Otherwise, cost is time-only: hours * hourly_rate.
    - Filament grams are tracked but not monetized here (until a clear pricing
      rule exists for manual entries).
    """
    if hourly_rate is None:
        hourly_rate = float(DEFAULT_PRICING.get("rate_per_hour") or 0.0)
    return _manual_job_cost(mj, hourly_rate=float(hourly_rate or 0.0))


def load_plans() -> Dict[str, List[PlannedItem]]:
    """Load planned items grouped by project_id."""
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
                status=status,
                source=source,
                notes=notes,
                converted_to_manual_job_id=converted_to_manual_job_id,
            )
        )

    return by_project


def _save_plans(by_project: Dict[str, List[PlannedItem]]) -> None:
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

    est_cost = _planned_cost_from_defaults(time_s, est_filament_g=est_filament_g)
    item = PlannedItem(
        plan_id=uuid.uuid4().hex,
        project_id=pid,
        filename=filename,
        created_at=_iso_now(),
        est_time_s=time_s,
        est_filament_g=est_filament_g,
        est_cost=est_cost,
        status="active",
        source=str(source or "").strip(),
        notes=str(notes or ""),
        converted_to_manual_job_id=None,
    )

    plans = load_plans()
    plans.setdefault(pid, []).append(item)
    _save_plans(plans)
    return item


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


def compute_project_projection(plans: List[PlannedItem]) -> Dict[str, float]:
    """Projected totals from ACTIVE planned items only."""
    projected = {"count": 0.0, "hours": 0.0, "cost": 0.0}
    for p in plans:
        if p.status != "active":
            continue
        projected["count"] += 1.0
        projected["hours"] += float(p.est_time_s or 0) / 3600.0
        projected["cost"] += float(p.est_cost or 0.0)
    return projected

def create_project(name: str, notes: str = "") -> Project:
    name = str(name or "").strip()
    if not name:
        raise ValueError("Project name is required")
    now = time.time()
    pid = uuid.uuid4().hex
    project = Project(id=pid, name=name, notes=str(notes or ""), created_at=now, updated_at=now)
    projects = load_projects()
    projects[pid] = project
    save_projects(projects.values())
    return project


def update_project(project_id: str, name: str, notes: str = "") -> Project:
    pid = str(project_id or "").strip()
    if not pid:
        raise ValueError("project_id is required")
    name = str(name or "").strip()
    if not name:
        raise ValueError("Project name is required")

    projects = load_projects()
    existing = projects.get(pid)
    if not existing:
        raise ValueError("Project not found")

    now = time.time()
    updated = Project(
        id=pid,
        name=name,
        notes=str(notes or ""),
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
    Each row will have an added `job_key` field.
    """
    projects, assignments = recalculate()
    project_jobs: Dict[str, List[Dict[str, Any]]] = {pid: [] for pid in projects.keys()}
    unassigned: List[Dict[str, Any]] = []

    for r in rows:
        jk = job_key(r)
        rr = dict(r)
        rr["job_key"] = jk
        pid = assignments.get(jk)
        if pid and pid in project_jobs:
            project_jobs[pid].append(rr)
        else:
            unassigned.append(rr)

    return project_jobs, unassigned


def compute_project_totals(
    tracked_rows: List[Dict[str, Any]],
    manual_jobs: Optional[List[ManualJob]] = None,
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

    hourly_rate = float(DEFAULT_PRICING.get("rate_per_hour") or 0.0)
    for mj in manual_jobs:
        try:
            totals["hours"] += float(mj.hours or 0.0)
            totals["filament_g"] += float(mj.filament_g or 0.0)
            totals["cost"] += _manual_job_cost(mj, hourly_rate=hourly_rate)
        except (TypeError, ValueError):
            continue
    return totals
