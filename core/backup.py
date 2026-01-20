"""
Backup utilities for Klipper Cost Dashboard.

Backups are written under `data/backups/` so they survive container rebuilds
when `./data:/app/data` is bind-mounted via docker-compose.

SQL-only note:
  Backups are file-backed and are blocked in SQL-only mode to avoid runtime
  JSON/CSV reads/writes.
"""

from __future__ import annotations

import json
import os
import tarfile
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional, Tuple

from core.config import DATA_DIR, CSV_FILE
from core.sql_only import require_file_reads_allowed, require_file_writes_allowed


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
    backups_dir_abs = os.path.abspath(BACKUPS_DIR)

    def _filter(ti: tarfile.TarInfo) -> Optional[tarfile.TarInfo]:
        # `ti.name` is based on arcname ("data/...")
        if ti.name == "data/backups" or ti.name.startswith("data/backups/"):
            return None
        return ti

    with tarfile.open(out_path, "w:gz") as tf:
        # Add the entire data/ directory (includes print_costs.csv and all project JSON files).
        tf.add(data_dir_abs, arcname="data", filter=_filter)

        # If CSV is configured outside data/, include it explicitly (rare).
        try:
            csv_abs = os.path.abspath(CSV_FILE)
            if os.path.exists(csv_abs) and not csv_abs.startswith(data_dir_abs + os.sep):
                tf.add(csv_abs, arcname=os.path.join("data", os.path.basename(csv_abs)))
        except Exception:
            pass

    if keep is not None:
        _enforce_retention(int(keep))

    return out_path


def maybe_run_auto_backup() -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Best-effort automatic backup.

    Returns (ran, archive_path, error_message).
    """
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
