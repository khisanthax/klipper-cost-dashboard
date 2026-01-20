"""
SQL-only guardrails for file-backed reads/writes.
"""
from __future__ import annotations

import inspect
import os
from typing import Optional


class SqlOnlyViolationError(RuntimeError):
    """Raised when SQL-only mode blocks file-backed reads/writes."""


def _storage_mode() -> str:
    return str(os.getenv("KCD_STORAGE_BACKEND", "csv")).strip().lower()


def is_sql_only() -> bool:
    return _storage_mode() == "sql"


def require_file_reads_allowed(resource: str = "", *, caller_hint: Optional[str] = None) -> None:
    """
    Raise SqlOnlyViolationError when SQL-only mode attempts to read local files.
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
    Raise SqlOnlyViolationError when SQL-only mode attempts to write local files.
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
