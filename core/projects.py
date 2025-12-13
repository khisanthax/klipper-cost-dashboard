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
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

from core.config import DATA_DIR


PROJECTS_FILE = os.path.join(DATA_DIR, "projects.json")
ASSIGNMENTS_FILE = os.path.join(DATA_DIR, "project_assignments.json")


class ProjectsDataError(RuntimeError):
    """Raised when projects storage is unreadable/corrupt."""


@dataclass(frozen=True)
class Project:
    id: str
    name: str
    notes: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0


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


def compute_project_totals(rows: List[Dict[str, Any]]) -> Dict[str, float]:
    totals = {"prints": 0.0, "hours": 0.0, "meters": 0.0, "cost": 0.0}
    totals["prints"] = float(len(rows))
    for r in rows:
        try:
            totals["hours"] += float(r.get("duration_hours") or 0.0)
            totals["meters"] += float(r.get("filament_meters") or 0.0)
            totals["cost"] += float(r.get("total_cost") or 0.0)
        except (TypeError, ValueError):
            continue
    return totals

