"""Request-local mutable storage selection for normal and demo runtimes."""
from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from pathlib import Path
import sqlite3
from typing import Optional

from core.runtime import is_demo_runtime


class WorkspaceContextError(RuntimeError):
    """Raised when mutable storage cannot be resolved safely."""


@dataclass(frozen=True)
class WorkspaceContext:
    runtime_mode: str
    workspace_id: Optional[str]
    workspace_root: Path
    database_path: Path
    reports_cache_path: Path
    exports_path: Path
    temporary_path: Path
    uploads_path: Path
    seed_version: Optional[str]
    storage_backend: str


_current_context: ContextVar[Optional[WorkspaceContext]] = ContextVar(
    "kcd_workspace_context", default=None
)
_open_connections: ContextVar[Optional[set[sqlite3.Connection]]] = ContextVar(
    "kcd_workspace_connections", default=None
)


def normal_workspace_context(data_dir: str = "data") -> WorkspaceContext:
    root = Path(data_dir).resolve()
    return WorkspaceContext(
        runtime_mode="normal",
        workspace_id=None,
        workspace_root=root,
        database_path=root / "kcd.db",
        reports_cache_path=root / "reports-cache",
        exports_path=root,
        temporary_path=root,
        uploads_path=root / "thumb_cache",
        seed_version=None,
        storage_backend="normal",
    )


def bind_workspace_context(context: WorkspaceContext) -> Token:
    _open_connections.set(set())
    return _current_context.set(context)


def reset_workspace_context(token: Token) -> None:
    close_request_connections()
    _current_context.reset(token)
    _open_connections.set(None)


def get_workspace_context(*, required: bool = False) -> Optional[WorkspaceContext]:
    context = _current_context.get()
    if context is None and (required or is_demo_runtime()):
        raise WorkspaceContextError(
            "Demo mutable storage requires a valid request-scoped workspace."
        )
    return context


def register_connection(connection: sqlite3.Connection) -> None:
    connections = _open_connections.get()
    if connections is not None:
        connections.add(connection)


def unregister_connection(connection: sqlite3.Connection) -> None:
    connections = _open_connections.get()
    if connections is not None:
        connections.discard(connection)


def close_request_connections() -> None:
    connections = _open_connections.get()
    if not connections:
        return
    for connection in tuple(connections):
        try:
            connection.close()
        except sqlite3.Error:
            pass
    connections.clear()
