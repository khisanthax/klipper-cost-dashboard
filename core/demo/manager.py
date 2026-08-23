"""Safe creation, resolution, reset, expiration, and deletion of demo workspaces."""
from __future__ import annotations

from dataclasses import dataclass
from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import tempfile
from typing import Callable, Optional
import uuid

from core.demo.config import DemoConfiguration
from core.demo.registry import DemoWorkspaceRegistry, WorkspaceRecord
from core.workspace_context import WorkspaceContext, close_request_connections


class WorkspaceError(RuntimeError):
    pass


class SeedValidationError(WorkspaceError):
    pass


class WorkspaceNotFound(WorkspaceError):
    pass


class WorkspaceExpired(WorkspaceError):
    pass


class UnsafeWorkspacePath(WorkspaceError):
    pass


@dataclass(frozen=True)
class WorkspaceSession:
    token: str
    context: WorkspaceContext
    record: WorkspaceRecord


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class _ExclusiveFileLock:
    def __init__(self, path: Path):
        self.path = path
        self.fd: Optional[int] = None

    def __enter__(self):
        try:
            self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.write(self.fd, str(os.getpid()).encode("ascii"))
        except FileExistsError as exc:
            raise WorkspaceError("Workspace lifecycle operation is already in progress.") from exc
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.fd is not None:
            os.close(self.fd)
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


class DemoWorkspaceManager:
    def __init__(
        self,
        configuration: DemoConfiguration,
        *,
        clock: Callable[[], datetime] = utc_now,
        registry: Optional[DemoWorkspaceRegistry] = None,
    ):
        self.configuration = configuration
        self.clock = clock
        self.registry = registry or DemoWorkspaceRegistry(configuration.registry_path, clock)

    @staticmethod
    def hash_token(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def initialize(self) -> None:
        root = self.configuration.data_root
        if not root.exists() or not root.is_dir():
            raise WorkspaceError("Configured demo data root does not exist or is not a directory.")
        if root.is_symlink():
            raise UnsafeWorkspacePath("Configured demo data root must not be a symlink.")
        self.configuration.workspaces_root.mkdir(parents=True, exist_ok=True)
        self.registry.initialize()
        self.validate_seed()

    def _now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None:
            raise WorkspaceError("Injected demo clock must return timezone-aware UTC timestamps.")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _assert_no_symlinks(root: Path) -> None:
        if root.is_symlink():
            raise UnsafeWorkspacePath("Symlinked workspace resources are not allowed.")
        for current, dirs, files in os.walk(root, followlinks=False):
            current_path = Path(current)
            for name in dirs + files:
                if (current_path / name).is_symlink():
                    raise UnsafeWorkspacePath("Symlinks are not allowed in demo workspace data.")

    @staticmethod
    def _contained(root: Path, candidate: Path) -> Path:
        root_real = root.resolve(strict=True)
        candidate_real = candidate.resolve(strict=False)
        try:
            candidate_real.relative_to(root_real)
        except ValueError as exc:
            raise UnsafeWorkspacePath("Workspace path escapes the configured workspace root.") from exc
        return candidate_real

    def validate_seed(self) -> str:
        seed = self.configuration.seed_root
        if not seed.exists() or not seed.is_dir():
            raise SeedValidationError("Configured demo seed directory does not exist.")
        self._assert_no_symlinks(seed)
        db_path = seed / "kcd.db"
        if not db_path.is_file():
            raise SeedValidationError("Configured demo seed is missing kcd.db.")
        try:
            uri = f"file:{db_path.as_posix()}?mode=ro&immutable=1"
            conn = sqlite3.connect(uri, uri=True)
            integrity = conn.execute("PRAGMA integrity_check").fetchone()
            versions = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
        except sqlite3.Error as exc:
            raise SeedValidationError("Demo seed database is invalid or schema-incompatible.") from exc
        finally:
            if "conn" in locals():
                conn.close()
        if not integrity or str(integrity[0]).lower() != "ok":
            raise SeedValidationError("Demo seed database failed SQLite integrity_check.")
        from core import db as db_module
        required = {Path(name).stem for name in db_module._list_migration_files()}
        missing = sorted(required - versions)
        if missing:
            raise SeedValidationError(
                "Demo seed database is missing required migrations: " + ", ".join(missing)
            )
        return self.configuration.seed_name

    def _clone_seed(self, target: Path) -> None:
        self.validate_seed()
        shutil.copytree(self.configuration.seed_root, target, symlinks=False)
        for name in ("cache", "exports", "temp", "uploads"):
            (target / name).mkdir(exist_ok=True)
        self.validate_workspace_path(target)

    def validate_workspace_path(self, path: Path) -> None:
        self._contained(self.configuration.workspaces_root, path)
        if not path.is_dir():
            raise WorkspaceError("Workspace directory is missing.")
        self._assert_no_symlinks(path)
        db_path = path / "kcd.db"
        if not db_path.is_file():
            raise WorkspaceError("Workspace database is missing.")
        try:
            with closing(sqlite3.connect(db_path)) as conn:
                row = conn.execute("PRAGMA integrity_check").fetchone()
        except sqlite3.Error as exc:
            raise WorkspaceError("Workspace database is invalid.") from exc
        if not row or str(row[0]).lower() != "ok":
            raise WorkspaceError("Workspace database failed integrity_check.")

    def _context(self, record: WorkspaceRecord) -> WorkspaceContext:
        if Path(record.relative_storage_path).name != record.relative_storage_path:
            raise UnsafeWorkspacePath("Registry contains an unsafe workspace path.")
        root = self._contained(
            self.configuration.workspaces_root,
            self.configuration.workspaces_root / record.relative_storage_path,
        )
        self.validate_workspace_path(root)
        return WorkspaceContext(
            runtime_mode="demo", workspace_id=record.workspace_id, workspace_root=root,
            database_path=root / "kcd.db", reports_cache_path=root / "cache",
            exports_path=root / "exports", temporary_path=root / "temp",
            uploads_path=root / "uploads", seed_version=record.seed_version,
            storage_backend="sql",
        )

    def create_workspace(self) -> WorkspaceSession:
        seed_version = self.validate_seed()
        workspace_id = uuid.uuid4().hex
        relative = workspace_id
        token = secrets.token_urlsafe(32)
        token_hash = self.hash_token(token)
        now = self._now()
        idle = now + timedelta(hours=self.configuration.idle_ttl_hours)
        absolute = now + timedelta(hours=self.configuration.absolute_ttl_hours)
        record = WorkspaceRecord(
            workspace_id, token_hash, now, now, now, idle, absolute, "creating",
            seed_version, relative, 0, None, None,
        )
        temporary = Path(tempfile.mkdtemp(prefix=".creating-", dir=self.configuration.workspaces_root))
        final = self.configuration.workspaces_root / relative
        inserted = False
        try:
            shutil.rmtree(temporary)
            self._clone_seed(temporary)
            self.registry.insert(record)
            inserted = True
            os.replace(temporary, final)
            record = self.registry.transition(workspace_id, "creating", "active")
            return WorkspaceSession(token, self._context(record), record)
        except Exception:
            if inserted:
                self.registry.delete_record(workspace_id)
            if temporary.exists():
                shutil.rmtree(temporary)
            if final.exists():
                shutil.rmtree(final)
            raise

    def resolve_workspace(self, token: str) -> WorkspaceSession:
        if not token:
            raise WorkspaceNotFound("Demo session is missing.")
        token_hash = self.hash_token(token)
        record = self.registry.get_by_token_hash(token_hash)
        if record is None or not hmac.compare_digest(record.session_token_hash, token_hash):
            raise WorkspaceNotFound("Demo session is invalid.")
        now = self._now()
        if record.state != "active":
            raise WorkspaceExpired("Demo workspace is not active.")
        if now >= min(record.idle_expires_at, record.absolute_expires_at):
            try:
                self.registry.transition(record.workspace_id, "active", "expired")
            except Exception:
                pass
            raise WorkspaceExpired("Demo workspace has expired.")
        return WorkspaceSession(token, self._context(record), record)

    resume_workspace = resolve_workspace

    def touch_workspace(self, record: WorkspaceRecord) -> bool:
        now = self._now()
        threshold = now - timedelta(seconds=self.configuration.touch_interval_seconds)
        idle = min(
            now + timedelta(hours=self.configuration.idle_ttl_hours),
            record.absolute_expires_at,
        )
        return self.registry.touch(
            record.workspace_id, idle_expires_at=idle, minimum_last_seen=threshold
        )

    def reset_workspace(self, session: WorkspaceSession) -> WorkspaceSession:
        close_request_connections()
        record = self.registry.transition(session.record.workspace_id, "active", "resetting")
        root = session.context.workspace_root
        lock_path = self.configuration.workspaces_root / f".{record.workspace_id}.lock"
        temporary = Path(tempfile.mkdtemp(prefix=".resetting-", dir=self.configuration.workspaces_root))
        previous = self.configuration.workspaces_root / f".{record.workspace_id}.previous"
        replaced = False
        try:
            with _ExclusiveFileLock(lock_path):
                shutil.rmtree(temporary)
                self._clone_seed(temporary)
                os.replace(root, previous)
                try:
                    os.replace(temporary, root)
                    replaced = True
                except Exception:
                    os.replace(previous, root)
                    raise
                shutil.rmtree(previous)
            now = self._now()
            refreshed = self.registry.activate_after_reset(
                record.workspace_id, self.configuration.seed_name,
                min(now + timedelta(hours=self.configuration.idle_ttl_hours), record.absolute_expires_at),
            )
            return WorkspaceSession(session.token, self._context(refreshed), refreshed)
        except Exception as exc:
            if not replaced and previous.exists() and not root.exists():
                os.replace(previous, root)
            try:
                self.registry.transition(record.workspace_id, "resetting", "error", error=str(exc))
            except Exception:
                pass
            raise
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)

    def expire_workspace(self, record: WorkspaceRecord) -> WorkspaceRecord:
        return self.registry.transition(record.workspace_id, "active", "expired")

    def delete_workspace(self, record: WorkspaceRecord) -> None:
        if record.state != "deleting":
            record = self.registry.transition(record.workspace_id, record.state, "deleting")
        path = self.configuration.workspaces_root / record.relative_storage_path
        self._contained(self.configuration.workspaces_root, path)
        if path.exists():
            self._assert_no_symlinks(path)
            shutil.rmtree(path)
        self.registry.delete_record(record.workspace_id)

    def cleanup_expired_workspaces(self, *, dry_run: bool = False) -> list[str]:
        selected = self.registry.expired(self._now())
        ids = [record.workspace_id for record in selected]
        if dry_run:
            return ids
        for record in selected:
            try:
                expired = self.registry.claim_expired(record.workspace_id, self._now())
                if expired is None:
                    continue
                self.delete_workspace(expired)
            except Exception as exc:
                current = self.registry.get(record.workspace_id)
                if current and current.state != "error":
                    try:
                        self.registry.transition(current.workspace_id, current.state, "error", error=str(exc))
                    except Exception:
                        pass
        return ids
