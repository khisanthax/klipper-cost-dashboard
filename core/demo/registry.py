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
    "expired": {"deleting", "error"},
    "deleting": {"error"},
    "error": {"deleting"},
}


class RegistryError(RuntimeError):
    pass


class InvalidStateTransition(RegistryError):
    pass


class ActiveWorkspaceLease(RegistryError):
    pass


class WorkspaceCapacityReached(RegistryError):
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
                CREATE TABLE IF NOT EXISTS demo_workspace_leases (
                    lease_id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    acquired_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    FOREIGN KEY (workspace_id) REFERENCES demo_workspaces(workspace_id)
                        ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_demo_lease_workspace
                    ON demo_workspace_leases(workspace_id);
                CREATE INDEX IF NOT EXISTS idx_demo_lease_expiration
                    ON demo_workspace_leases(expires_at);
                CREATE TABLE IF NOT EXISTS demo_rate_buckets (
                    scope_hash TEXT NOT NULL,
                    category TEXT NOT NULL,
                    window_start INTEGER NOT NULL,
                    request_count INTEGER NOT NULL,
                    PRIMARY KEY (scope_hash, category, window_start)
                );
                CREATE INDEX IF NOT EXISTS idx_demo_rate_expiration
                    ON demo_rate_buckets(window_start);
                CREATE TABLE IF NOT EXISTS demo_quota_reservations (
                    reservation_id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    entity TEXT NOT NULL,
                    slot_number INTEGER NOT NULL,
                    expires_at TEXT NOT NULL,
                    FOREIGN KEY (workspace_id) REFERENCES demo_workspaces(workspace_id)
                        ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_demo_quota_workspace_entity
                    ON demo_quota_reservations(workspace_id, entity);
                CREATE INDEX IF NOT EXISTS idx_demo_quota_expiration
                    ON demo_quota_reservations(expires_at);
                """
            )
            quota_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(demo_quota_reservations)")
            }
            if "slot_number" not in quota_columns:
                conn.execute(
                    "ALTER TABLE demo_quota_reservations ADD COLUMN slot_number INTEGER NOT NULL DEFAULT 1"
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

    def insert(self, record: WorkspaceRecord, *, limit: Optional[int] = None) -> None:
        if record.state not in ACTIVE_STATES:
            raise RegistryError(f"Unknown workspace state: {record.state}")
        with self._connect() as conn:
            if limit is not None:
                conn.execute("BEGIN IMMEDIATE")
                count = conn.execute("SELECT COUNT(*) FROM demo_workspaces").fetchone()[0]
                if count >= limit:
                    raise WorkspaceCapacityReached("Demo workspace capacity reached.")
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

    @staticmethod
    def _purge_expired_leases(conn: sqlite3.Connection, now: datetime) -> None:
        conn.execute(
            "DELETE FROM demo_workspace_leases WHERE expires_at <= ?",
            (now.astimezone(timezone.utc).isoformat(),),
        )

    def acquire_lease(
        self,
        workspace_id: str,
        lease_id: str,
        *,
        acquired_at: datetime,
        expires_at: datetime,
    ) -> bool:
        now = acquired_at.astimezone(timezone.utc)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._purge_expired_leases(conn, now)
            row = conn.execute(
                """SELECT state, idle_expires_at, absolute_expires_at
                   FROM demo_workspaces WHERE workspace_id=?""",
                (workspace_id,),
            ).fetchone()
            if (
                row is None
                or row["state"] != "active"
                or now >= min(_utc(row["idle_expires_at"]), _utc(row["absolute_expires_at"]))
            ):
                return False
            conn.execute(
                """INSERT INTO demo_workspace_leases
                   (lease_id, workspace_id, acquired_at, expires_at)
                   VALUES (?, ?, ?, ?)""",
                (lease_id, workspace_id, now.isoformat(),
                 expires_at.astimezone(timezone.utc).isoformat()),
            )
            return True

    def renew_lease(self, lease_id: str, expires_at: datetime) -> bool:
        with self._connect() as conn:
            result = conn.execute(
                "UPDATE demo_workspace_leases SET expires_at=? WHERE lease_id=?",
                (expires_at.astimezone(timezone.utc).isoformat(), lease_id),
            )
            return result.rowcount == 1

    def release_lease(self, lease_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM demo_workspace_leases WHERE lease_id=?", (lease_id,))

    def active_lease_count(self, workspace_id: str, now: datetime) -> int:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._purge_expired_leases(conn, now)
            row = conn.execute(
                "SELECT COUNT(*) AS count FROM demo_workspace_leases WHERE workspace_id=?",
                (workspace_id,),
            ).fetchone()
            return int(row["count"])

    def consume_rate(
        self,
        scope_hash: str,
        category: str,
        *,
        limit: int,
        window_seconds: int,
        now: datetime,
    ) -> tuple[bool, int]:
        """Atomically consume one fixed-window allowance."""
        timestamp = int(now.astimezone(timezone.utc).timestamp())
        window_start = timestamp - (timestamp % window_seconds)
        retry_after = max(1, window_start + window_seconds - timestamp)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "DELETE FROM demo_rate_buckets WHERE window_start < ?",
                (timestamp - 86400,),
            )
            row = conn.execute(
                """SELECT request_count FROM demo_rate_buckets
                   WHERE scope_hash=? AND category=? AND window_start=?""",
                (scope_hash, category, window_start),
            ).fetchone()
            count = int(row["request_count"]) if row else 0
            if count >= limit:
                return False, retry_after
            conn.execute(
                """INSERT INTO demo_rate_buckets
                   (scope_hash, category, window_start, request_count)
                   VALUES (?, ?, ?, 1)
                   ON CONFLICT(scope_hash, category, window_start)
                   DO UPDATE SET request_count=request_count+1""",
                (scope_hash, category, window_start),
            )
        return True, retry_after

    def acquire_quota_reservation(
        self,
        reservation_id: str,
        workspace_id: str,
        entity: str,
        *,
        current_count: int,
        limit: int,
        expires_at: datetime,
        now: datetime,
    ) -> bool:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "DELETE FROM demo_quota_reservations WHERE expires_at <= ?",
                (now.astimezone(timezone.utc).isoformat(),),
            )
            conn.execute(
                """DELETE FROM demo_quota_reservations
                   WHERE workspace_id=? AND entity=? AND slot_number <= ?""",
                (workspace_id, entity, current_count),
            )
            reserved_high_water = int(conn.execute(
                """SELECT COALESCE(MAX(slot_number), 0) AS slot_number
                   FROM demo_quota_reservations WHERE workspace_id=? AND entity=?""",
                (workspace_id, entity),
            ).fetchone()["slot_number"])
            slot_number = max(current_count, reserved_high_water) + 1
            if slot_number > limit:
                return False
            conn.execute(
                """INSERT INTO demo_quota_reservations
                   (reservation_id, workspace_id, entity, slot_number, expires_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    reservation_id,
                    workspace_id,
                    entity,
                    slot_number,
                    expires_at.astimezone(timezone.utc).isoformat(),
                ),
            )
        return True

    def renew_quota_reservation(self, reservation_id: str, expires_at: datetime) -> bool:
        with self._connect() as conn:
            result = conn.execute(
                "UPDATE demo_quota_reservations SET expires_at=? WHERE reservation_id=?",
                (expires_at.astimezone(timezone.utc).isoformat(), reservation_id),
            )
            return result.rowcount == 1

    def release_quota_reservation(self, reservation_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM demo_quota_reservations WHERE reservation_id=?",
                (reservation_id,),
            )

    def transition_if_unleased(
        self,
        workspace_id: str,
        expected: str,
        target: str,
        *,
        now: datetime,
        error: Optional[str] = None,
    ) -> WorkspaceRecord:
        if target not in TRANSITIONS.get(expected, set()):
            raise InvalidStateTransition(f"Invalid workspace transition {expected} -> {target}")
        now = now.astimezone(timezone.utc)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._purge_expired_leases(conn, now)
            count = conn.execute(
                "SELECT COUNT(*) AS count FROM demo_workspace_leases WHERE workspace_id=?",
                (workspace_id,),
            ).fetchone()["count"]
            if int(count):
                raise ActiveWorkspaceLease("Workspace has active request leases.")
            result = conn.execute(
                """UPDATE demo_workspaces SET state=?, updated_at=?, last_error=?
                   WHERE workspace_id=? AND state=?""",
                (target, now.isoformat(), error, workspace_id, expected),
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
                """SELECT * FROM demo_workspaces
                   WHERE (state='active' AND idle_expires_at <= ?) OR absolute_expires_at <= ?
                   ORDER BY created_at""", (value, value)
            ).fetchall()
        return [self._record(row) for row in rows]

    def claim_expired(self, workspace_id: str, now: datetime) -> Optional[WorkspaceRecord]:
        """Atomically expire a still-due active row, avoiding touch/cleanup races."""
        value = now.astimezone(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._purge_expired_leases(conn, now)
            count = conn.execute(
                "SELECT COUNT(*) AS count FROM demo_workspace_leases WHERE workspace_id=?",
                (workspace_id,),
            ).fetchone()["count"]
            if int(count):
                raise ActiveWorkspaceLease("Workspace has active request leases.")
            result = conn.execute(
                """UPDATE demo_workspaces SET state='expired', updated_at=?
                   WHERE workspace_id=?
                   AND ((state='active' AND idle_expires_at <= ?) OR absolute_expires_at <= ?)""",
                (value, workspace_id, value, value),
            )
            if result.rowcount != 1:
                return None
        return self.get(workspace_id)

    def all(self) -> list[WorkspaceRecord]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM demo_workspaces ORDER BY created_at").fetchall()
        return [self._record(row) for row in rows]
