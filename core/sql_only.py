"""
SQL-only guardrails for file-backed reads.
"""
from __future__ import annotations

import inspect
import os
from typing import Optional


class SqlOnlyViolationError(RuntimeError):
    """Raised when SQL-only mode blocks file-backed reads."""


def _storage_mode() -> str:
    return str(os.getenv("KCD_STORAGE_BACKEND", "csv")).strip().lower()


def require_file_reads_allowed(resource: str = "", *, caller_hint: Optional[str] = None) -> None:
    """
    Raise SqlOnlyViolationError when SQL-only mode attempts to read local files.
    """
    if _storage_mode() != "sql":
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
