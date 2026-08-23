"""SQLite control-plane registry for ephemeral workspaces."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Callable, Optional


ACTIVE_STATES = {"creating", "active", "resetting", "expired", "deleting", "error"}
TRANSITIONS = {
    "creating": {"active", "error", "deleting"},
    "active": {"resetting", "expired", "deleting", "error"},
    "resetting": {"active", "error"},
    "expired": {"deleting"},
    "deleting": {"error"},
    "error": {"deleting"},
}


class RegistryError(RuntimeError):
    pass


class InvalidStateTransition(RegistryError):
    pass


class RegistryConnection(sqlite3.Connection):
    """Commit or roll back and always release the registry file handle."""

    def __exit__(self, exc_type, exc, tb):
        try:
            return super().__exit__(exc_type, exc, tb)
        finally:
            self.close()


@dataclass(frozen=True)
class WorkspaceRecord:
    workspace_id: str
    session_token_hash: str
    created_at: datetime
    updated_at: datetime
    last_seen_at: datetime
    idle_expires_at: datetime
    absolute_expires_at: datetime
    state: str
    seed_version: str
    relative_storage_path: str
    reset_count: int
    approximate_storage_bytes: Optional[int]
    last_error: Optional[str]


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class DemoWorkspaceRegistry:
    def __init__(self, path: Path, clock: Callable[[], datetime]):
        self.path = Path(path)
        self.clock = clock

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS demo_workspaces (
                    workspace_id TEXT PRIMARY KEY,
                    session_token_hash TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    idle_expires_at TEXT NOT NULL,
                    absolute_expires_at TEXT NOT NULL,
                    state TEXT NOT NULL,
                    seed_version TEXT NOT NULL,
                    relative_storage_path TEXT NOT NULL UNIQUE,
                    reset_count INTEGER NOT NULL DEFAULT 0,
                    approximate_storage_bytes INTEGER,
                    last_error TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_demo_token
                    ON demo_workspaces(session_token_hash);
                CREATE INDEX IF NOT EXISTS idx_demo_expiration
                    ON demo_workspaces(idle_expires_at, absolute_expires_at);
                CREATE INDEX IF NOT EXISTS idx_demo_state
                    ON demo_workspaces(state);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, factory=RegistryConnection)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @staticmethod
    def _record(row: sqlite3.Row) -> WorkspaceRecord:
        return WorkspaceRecord(
            workspace_id=row["workspace_id"], session_token_hash=row["session_token_hash"],
            created_at=_utc(row["created_at"]), updated_at=_utc(row["updated_at"]),
            last_seen_at=_utc(row["last_seen_at"]), idle_expires_at=_utc(row["idle_expires_at"]),
            absolute_expires_at=_utc(row["absolute_expires_at"]), state=row["state"],
            seed_version=row["seed_version"], relative_storage_path=row["relative_storage_path"],
            reset_count=int(row["reset_count"]),
            approximate_storage_bytes=row["approximate_storage_bytes"], last_error=row["last_error"],
        )

    def insert(self, record: WorkspaceRecord) -> None:
        if record.state not in ACTIVE_STATES:
            raise RegistryError(f"Unknown workspace state: {record.state}")
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO demo_workspaces VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (record.workspace_id, record.session_token_hash, record.created_at.isoformat(),
                 record.updated_at.isoformat(), record.last_seen_at.isoformat(),
                 record.idle_expires_at.isoformat(), record.absolute_expires_at.isoformat(),
                 record.state, record.seed_version, record.relative_storage_path,
                 record.reset_count, record.approximate_storage_bytes, record.last_error),
            )

    def delete_record(self, workspace_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM demo_workspaces WHERE workspace_id = ?", (workspace_id,))

    def get_by_token_hash(self, token_hash: str) -> Optional[WorkspaceRecord]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM demo_workspaces WHERE session_token_hash = ?", (token_hash,)
            ).fetchone()
        return self._record(row) if row else None

    def get(self, workspace_id: str) -> Optional[WorkspaceRecord]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM demo_workspaces WHERE workspace_id = ?", (workspace_id,)
            ).fetchone()
        return self._record(row) if row else None

    def transition(self, workspace_id: str, expected: str, target: str, *, error: Optional[str] = None) -> WorkspaceRecord:
        if target not in TRANSITIONS.get(expected, set()):
            raise InvalidStateTransition(f"Invalid workspace transition {expected} -> {target}")
        now = self.clock().astimezone(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            result = conn.execute(
                "UPDATE demo_workspaces SET state=?, updated_at=?, last_error=? WHERE workspace_id=? AND state=?",
                (target, now, error, workspace_id, expected),
            )
            if result.rowcount != 1:
                raise InvalidStateTransition(
                    f"Workspace is no longer in expected state {expected}."
                )
        record = self.get(workspace_id)
        if record is None:
            raise RegistryError("Workspace disappeared after state transition.")
        return record

    def activate_after_reset(self, workspace_id: str, seed_version: str, idle_expires_at: datetime) -> WorkspaceRecord:
        now = self.clock().astimezone(timezone.utc)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            result = conn.execute(
                """UPDATE demo_workspaces SET state='active', seed_version=?, reset_count=reset_count+1,
                   updated_at=?, last_seen_at=?, idle_expires_at=?, last_error=NULL
                   WHERE workspace_id=? AND state='resetting'""",
                (seed_version, now.isoformat(), now.isoformat(), idle_expires_at.isoformat(), workspace_id),
            )
            if result.rowcount != 1:
                raise InvalidStateTransition("Workspace reset state changed concurrently.")
        return self.get(workspace_id)  # type: ignore[return-value]

    def touch(self, workspace_id: str, *, idle_expires_at: datetime, minimum_last_seen: datetime) -> bool:
        now = self.clock().astimezone(timezone.utc)
        with self._connect() as conn:
            result = conn.execute(
                """UPDATE demo_workspaces SET last_seen_at=?, idle_expires_at=?, updated_at=?
                   WHERE workspace_id=? AND state='active' AND last_seen_at <= ?""",
                (now.isoformat(), idle_expires_at.isoformat(), now.isoformat(), workspace_id,
                 minimum_last_seen.astimezone(timezone.utc).isoformat()),
            )
            return result.rowcount == 1

    def expired(self, now: datetime) -> list[WorkspaceRecord]:
        value = now.astimezone(timezone.utc).isoformat()
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM demo_workspaces WHERE state='active'
                   AND (idle_expires_at <= ? OR absolute_expires_at <= ?)
                   ORDER BY created_at""", (value, value)
            ).fetchall()
        return [self._record(row) for row in rows]

    def claim_expired(self, workspace_id: str, now: datetime) -> Optional[WorkspaceRecord]:
        """Atomically expire a still-due active row, avoiding touch/cleanup races."""
        value = now.astimezone(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            result = conn.execute(
                """UPDATE demo_workspaces SET state='expired', updated_at=?
                   WHERE workspace_id=? AND state='active'
                   AND (idle_expires_at <= ? OR absolute_expires_at <= ?)""",
                (value, workspace_id, value, value),
            )
            if result.rowcount != 1:
                return None
        return self.get(workspace_id)

    def all(self) -> list[WorkspaceRecord]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM demo_workspaces ORDER BY created_at").fetchall()
        return [self._record(row) for row in rows]
