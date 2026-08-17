"""
Backup utilities for Klipper Cost Dashboard.

Backups are written under `data/backups/` so they survive container rebuilds
when `./data:/app/data` is bind-mounted via docker-compose.

Backup archives are an explicit filesystem exception in SQL-only mode. Both
user-invoked and enabled automatic backups use a consistent SQLite snapshot.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tarfile
import tempfile
import time
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional, Tuple

from core.config import DATA_DIR, CSV_FILE
from core.sql_only import require_file_reads_allowed, require_file_writes_allowed, is_sql_only


APP_SETTINGS_FILE = os.path.join(DATA_DIR, "app_settings.json")
BACKUPS_DIR = os.path.join(DATA_DIR, "backups")


@dataclass(frozen=True)
class BackupSettings:
    auto_backup_enabled: bool = False
    auto_backup_frequency: str = "daily"  # "daily" | "weekly"
    auto_backup_keep: int = 7
    last_auto_backup_ts: float = 0.0


def _ensure_dirs() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(BACKUPS_DIR, exist_ok=True)


def _is_sql_only() -> bool:
    return is_sql_only()


def _load_backup_settings_sql() -> dict:
    from core import db as db_module
    with closing(db_module.connect_db()) as conn:
        db_module.apply_migrations(conn)
        row = conn.execute("SELECT value_json FROM user_settings WHERE key = ?", ("backup_settings",)).fetchone()
        if not row:
            return {}
        raw = row[0] if isinstance(row, (tuple, list)) else row["value_json"]
        data = json.loads(raw) if raw else {}
        if not isinstance(data, dict):
            raise ValueError("user_settings.backup_settings must contain a JSON object")
        return data


def _create_sqlite_snapshot(source_path: str, snapshot_path: str) -> None:
    """Create and verify a transactionally consistent SQLite snapshot."""
    source = None
    destination = None
    try:
        source = sqlite3.connect(source_path, timeout=30)
        destination = sqlite3.connect(snapshot_path, timeout=30)
        source.backup(destination)
        check = destination.execute("PRAGMA integrity_check").fetchone()
        if not check or str(check[0]).strip().lower() != "ok":
            raise RuntimeError("SQLite backup snapshot failed integrity_check")
    finally:
        if destination is not None:
            destination.close()
        if source is not None:
            source.close()


def _save_backup_settings_sql(settings_dict: dict) -> None:
    try:
        from core import db as db_module
        with closing(db_module.connect_db()) as conn:
            db_module.apply_migrations(conn)
            now = datetime.now().isoformat()
            payload = json.dumps(settings_dict if isinstance(settings_dict, dict) else {}, indent=2)
            row = conn.execute("SELECT 1 FROM user_settings WHERE key = ?", ("backup_settings",)).fetchone()
            if row:
                conn.execute(
                    "UPDATE user_settings SET value_json = ?, updated_at = ? WHERE key = ?",
                    (payload, now, "backup_settings"),
                )
            else:
                conn.execute(
                    "INSERT INTO user_settings (key, value_json, updated_at) VALUES (?, ?, ?)",
                    ("backup_settings", payload, now),
                )
            conn.commit()
    except Exception:
        raise


def _read_json_file(path: str, default: Any) -> Any:
    require_file_reads_allowed(os.path.basename(path), caller_hint="core.backup._read_json_file")
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _write_json_file(path: str, data: Any) -> None:
    require_file_writes_allowed(os.path.basename(path), caller_hint="core.backup._write_json_file")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def load_backup_settings() -> BackupSettings:
    if _is_sql_only():
        b = _load_backup_settings_sql()
    else:
        raw = _read_json_file(APP_SETTINGS_FILE, {})
        if not isinstance(raw, dict):
            raw = {}
        b = raw.get("backups", {})
    if not isinstance(b, dict):
        b = {}

    enabled = bool(b.get("auto_backup_enabled") or False)
    freq = str(b.get("auto_backup_frequency") or "daily").strip().lower()
    if freq not in ("daily", "weekly"):
        freq = "daily"

    keep = b.get("auto_backup_keep")
    try:
        keep_i = int(keep)
    except Exception:
        keep_i = 7
    keep_i = max(1, min(100, keep_i))

    last_ts = b.get("last_auto_backup_ts") or 0.0
    try:
        last_ts_f = float(last_ts)
    except Exception:
        last_ts_f = 0.0

    return BackupSettings(
        auto_backup_enabled=enabled,
        auto_backup_frequency=freq,
        auto_backup_keep=keep_i,
        last_auto_backup_ts=last_ts_f,
    )


def save_backup_settings(
    *,
    auto_backup_enabled: bool,
    auto_backup_frequency: str,
    auto_backup_keep: int,
) -> BackupSettings:
    if _is_sql_only() and auto_backup_enabled:
        raise ValueError("Automatic backups are unavailable in SQL-only mode; use Backup now.")
    if _is_sql_only():
        b = _load_backup_settings_sql()
        if not isinstance(b, dict):
            b = {}
    else:
        current = _read_json_file(APP_SETTINGS_FILE, {})
        if not isinstance(current, dict):
            current = {}
        b = current.get("backups", {})
    if not isinstance(b, dict):
        b = {}

    freq = str(auto_backup_frequency or "daily").strip().lower()
    if freq not in ("daily", "weekly"):
        freq = "daily"

    keep_i = max(1, min(100, int(auto_backup_keep)))
    b["auto_backup_enabled"] = bool(auto_backup_enabled)
    b["auto_backup_frequency"] = freq
    b["auto_backup_keep"] = keep_i
    b.setdefault("last_auto_backup_ts", 0.0)

    if _is_sql_only():
        _save_backup_settings_sql(b)
    else:
        current["backups"] = b
        _write_json_file(APP_SETTINGS_FILE, current)
    return load_backup_settings()


def _frequency_seconds(freq: str) -> float:
    if str(freq).lower() == "weekly":
        return 7 * 24 * 60 * 60
    return 24 * 60 * 60


def _enforce_retention(keep: int) -> None:
    keep_i = max(1, min(100, int(keep)))
    if not os.path.isdir(BACKUPS_DIR):
        return
    files = [
        os.path.join(BACKUPS_DIR, f)
        for f in os.listdir(BACKUPS_DIR)
        if f.startswith("kcd_backup_") and f.endswith(".tar.gz")
    ]
    # newest first by mtime
    files.sort(key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0, reverse=True)
    for p in files[keep_i:]:
        try:
            os.remove(p)
        except Exception:
            pass


def create_backup_archive(*, keep: Optional[int] = None) -> str:
    """
    Create a single tar.gz archive containing:
      - data/ (entire directory)

    Excludes:
      - data/backups/ (to avoid recursively backing up backups)
    """
    _ensure_dirs()

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"kcd_backup_{ts}.tar.gz"
    out_path = os.path.join(BACKUPS_DIR, filename)

    data_dir_abs = os.path.abspath(DATA_DIR)

    from core import db as db_module

    source_db = os.path.abspath(db_module._db_path())
    has_sqlite = os.path.isfile(source_db)

    def _filter(ti: tarfile.TarInfo) -> Optional[tarfile.TarInfo]:
        # `ti.name` is based on arcname ("data/...")
        if ti.name == "data/backups" or ti.name.startswith("data/backups/"):
            return None
        if ti.name in ("data/kcd.db", "data/kcd.db-wal", "data/kcd.db-shm"):
            return None
        return ti

    try:
        with tempfile.TemporaryDirectory(prefix="kcd_backup_snapshot_") as temp_dir:
            snapshot_path = os.path.join(temp_dir, "kcd.db")
            if has_sqlite:
                _create_sqlite_snapshot(source_db, snapshot_path)

            with tarfile.open(out_path, "w:gz") as tf:
                tf.add(data_dir_abs, arcname="data", filter=_filter)
                if has_sqlite:
                    tf.add(snapshot_path, arcname="data/kcd.db")

                # If CSV is configured outside data/, include it explicitly (rare).
                csv_abs = os.path.abspath(CSV_FILE)
                if os.path.exists(csv_abs) and not csv_abs.startswith(data_dir_abs + os.sep):
                    tf.add(csv_abs, arcname=os.path.join("data", os.path.basename(csv_abs)))
    except Exception:
        try:
            if os.path.exists(out_path):
                os.remove(out_path)
        finally:
            raise

    if keep is not None:
        _enforce_retention(int(keep))

    return out_path


def maybe_run_auto_backup() -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Best-effort automatic backup.

    Returns (ran, archive_path, error_message).
    """
    if _is_sql_only():
        return False, None, "Automatic backups are disabled in SQL-only mode."

    try:
        settings = load_backup_settings()
    except Exception:
        return False, None, "Failed to load backup settings"

    if not settings.auto_backup_enabled:
        return False, None, None

    now = time.time()
    due_after = _frequency_seconds(settings.auto_backup_frequency)
    if settings.last_auto_backup_ts and now - float(settings.last_auto_backup_ts) < due_after:
        return False, None, None

    try:
        archive = create_backup_archive(keep=settings.auto_backup_keep)
    except Exception as e:
        return False, None, str(e)

    # Persist last run timestamp
    try:
        if _is_sql_only():
            b = _load_backup_settings_sql()
            if not isinstance(b, dict):
                b = {}
            b["last_auto_backup_ts"] = float(now)
            _save_backup_settings_sql(b)
        else:
            current = _read_json_file(APP_SETTINGS_FILE, {})
            if not isinstance(current, dict):
                current = {}
            b = current.get("backups", {})
            if not isinstance(b, dict):
                b = {}
            b["last_auto_backup_ts"] = float(now)
            current["backups"] = b
            _write_json_file(APP_SETTINGS_FILE, current)
    except Exception:
        # Backup succeeded; failure to persist timestamp is non-fatal.
        pass

    return True, archive, None
