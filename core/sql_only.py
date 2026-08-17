"""SQL-only business-state guardrails and validation instrumentation.

SQL-only makes business/runtime state canonical in SQLite. It does not impose a
literal zero-filesystem rule: credentials, thumbnail caches, explicit exports,
backup archives, and temporary explicit-operation files remain allowed.
"""
from __future__ import annotations

import builtins
import io
import inspect
import os
from pathlib import Path
from typing import Optional


FORBIDDEN_SQL_ONLY_RUNTIME_BASENAMES = frozenset(
    {
        "app_settings.json",
        "display.json",
        "install_state.json",
        "live_jobs.json",
        "print_costs.csv",
        "profiles.json",
        "project_assignments.json",
        "project_assignments_orphans.json",
        "project_manual_jobs.json",
        "project_plans.json",
        "projects.json",
        "recalc_runs.jsonl",
        "settings.json",
        "system_events.jsonl",
    }
)


def classify_sql_only_filesystem_path(path: object) -> str:
    """Classify paths relevant to the strict SQL-only filesystem contract."""
    try:
        normalized = os.path.normpath(os.fspath(path))
    except TypeError:
        return "unclassified"
    parts = {part.lower() for part in Path(normalized).parts}
    basename = os.path.basename(normalized).lower()
    if basename in FORBIDDEN_SQL_ONLY_RUNTIME_BASENAMES:
        return "forbidden_runtime_state"
    if basename == "secret.json":
        return "allowed_credential"
    if "thumb_cache" in parts or "thumbnail_cache" in parts:
        return "allowed_cache"
    if "backups" in parts:
        return "allowed_backup"
    if "exports" in parts:
        return "allowed_explicit_export"
    return "unclassified"


class SqlOnlyViolationError(RuntimeError):
    """Raised when SQL-only mode blocks file-backed reads/writes."""


class SqlOnlyFilesystemMonitor:
    """Observe representative Python-level file opens and reject known legacy state.

    This is deliberately a test/validation aid rather than a global sandbox. It
    catches direct ``open``/``io.open``/``os.open`` access even when a module did
    not call the centralized guard helpers.
    """

    def __init__(self) -> None:
        self.accesses: list[dict[str, str]] = []
        self._original_builtin_open = None
        self._original_io_open = None
        self._original_os_open = None

    def _record(self, path: object, operation: str) -> None:
        classification = classify_sql_only_filesystem_path(path)
        if classification == "unclassified":
            return
        rendered = os.fspath(path)
        self.accesses.append(
            {"path": str(rendered), "operation": operation, "classification": classification}
        )
        if is_sql_only() and classification == "forbidden_runtime_state":
            raise SqlOnlyViolationError(
                f"SQL-only filesystem monitor blocked {operation} access to legacy runtime state: {rendered}"
            )

    def __enter__(self):
        self._original_builtin_open = builtins.open
        self._original_io_open = io.open
        self._original_os_open = os.open

        def monitored_open(file, mode="r", *args, **kwargs):
            self._record(file, "write" if any(flag in str(mode) for flag in "wax+") else "read")
            return self._original_builtin_open(file, mode, *args, **kwargs)

        def monitored_io_open(file, mode="r", *args, **kwargs):
            self._record(file, "write" if any(flag in str(mode) for flag in "wax+") else "read")
            return self._original_io_open(file, mode, *args, **kwargs)

        def monitored_os_open(path, flags, *args, **kwargs):
            write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
            self._record(path, "write" if int(flags) & write_flags else "read")
            return self._original_os_open(path, flags, *args, **kwargs)

        builtins.open = monitored_open
        io.open = monitored_io_open
        os.open = monitored_os_open
        return self

    def __exit__(self, exc_type, exc, tb):
        builtins.open = self._original_builtin_open
        io.open = self._original_io_open
        os.open = self._original_os_open
        return False


def _storage_mode() -> str:
    return str(os.getenv("KCD_STORAGE_BACKEND", "csv")).strip().lower()


def is_sql_only() -> bool:
    return _storage_mode() == "sql"


def require_file_reads_allowed(resource: str = "", *, caller_hint: Optional[str] = None) -> None:
    """
    Block callers that have identified a legacy business-state file read.

    Deliberately allowed filesystem categories do not call this legacy-state
    guard; the release contract is documented at module level above.
    """
    if not is_sql_only():
        return

    if caller_hint:
        caller = caller_hint
    else:
        try:
            frame = inspect.stack()[2]
            mod = frame.frame.f_globals.get("__name__", "")
            caller = f"{mod}.{frame.function}"
        except Exception:
            caller = "unknown"

    msg = (
        "SQL-only mode forbids file-backed reads. "
        f"Attempted to read {resource or 'local file'} "
        f"from {caller}."
    )
    raise SqlOnlyViolationError(msg)


def require_file_writes_allowed(resource: str = "", *, caller_hint: Optional[str] = None) -> None:
    """
    Block callers that have identified a legacy business-state file write.
    """
    if not is_sql_only():
        return

    if caller_hint:
        caller = caller_hint
    else:
        try:
            frame = inspect.stack()[2]
            mod = frame.frame.f_globals.get("__name__", "")
            caller = f"{mod}.{frame.function}"
        except Exception:
            caller = "unknown"

    msg = (
        "SQL-only mode forbids file-backed writes. "
        f"Attempted to write {resource or 'local file'} "
        f"from {caller}."
    )
    raise SqlOnlyViolationError(msg)
