"""Validated demo runtime configuration."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from core.runtime import RuntimeConfigurationError


@dataclass(frozen=True)
class DemoConfiguration:
    data_root: Path
    seed_name: str
    idle_ttl_hours: int
    absolute_ttl_hours: int
    touch_interval_seconds: int
    cookie_name: str = "kcd_demo_session"
    cookie_secure: bool = True
    request_lease_seconds: int = 120
    request_lease_heartbeat_seconds: int = 30
    max_request_bytes: int = 1024 * 1024
    max_text_length: int = 256
    max_notes_length: int = 4096
    max_list_length: int = 1000
    rate_general_limit: int = 300
    rate_general_window_seconds: int = 300
    rate_mutation_limit: int = 60
    rate_mutation_window_seconds: int = 300
    rate_lifecycle_limit: int = 5
    rate_lifecycle_window_seconds: int = 600
    rate_invalid_session_limit: int = 60
    rate_invalid_session_window_seconds: int = 300
    max_printers: int = 10
    max_filament_profiles: int = 50
    max_hourly_profiles: int = 25
    max_projects: int = 100
    max_history_records: int = 1000
    max_workspace_bytes: int = 25 * 1024 * 1024
    max_export_bytes: int = 10 * 1024 * 1024
    trust_proxy: bool = False
    expect_https: bool = False

    @property
    def seeds_root(self) -> Path:
        return self.data_root / "seeds"

    @property
    def seed_root(self) -> Path:
        return self.seeds_root / self.seed_name

    @property
    def workspaces_root(self) -> Path:
        return self.data_root / "workspaces"

    @property
    def registry_path(self) -> Path:
        return self.data_root / "registry" / "demo_registry.db"


def _positive_int(name: str, default: int) -> int:
    raw = str(os.getenv(name, str(default)) or "").strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeConfigurationError(f"{name} must be a positive integer.") from exc
    if value <= 0:
        raise RuntimeConfigurationError(f"{name} must be a positive integer.")
    return value


def _boolean(name: str, default: bool) -> bool:
    raw = str(os.getenv(name, "1" if default else "0") or "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise RuntimeConfigurationError(f"{name} must be a boolean value.")


def load_demo_configuration() -> DemoConfiguration:
    raw_root = str(os.getenv("KCD_DEMO_DATA_ROOT", "") or "").strip()
    if not raw_root:
        raise RuntimeConfigurationError(
            "KCD_DEMO_DATA_ROOT is required when KCD_RUNTIME_MODE=demo."
        )
    root = Path(raw_root).expanduser()
    if not root.is_absolute():
        raise RuntimeConfigurationError("KCD_DEMO_DATA_ROOT must be an absolute path.")

    storage = str(os.getenv("KCD_STORAGE_BACKEND", "") or "").strip().lower()
    if storage != "sql":
        raise RuntimeConfigurationError(
            "Demo mode requires KCD_STORAGE_BACKEND=sql; csv and dual are unsupported."
        )
    reports = str(os.getenv("KCD_REPORTS_BACKEND", "sql") or "sql").strip().lower()
    if reports != "sql":
        raise RuntimeConfigurationError("Demo mode requires KCD_REPORTS_BACKEND=sql.")

    seed_name = str(os.getenv("KCD_DEMO_SEED_NAME", "basic-v1") or "").strip()
    if not seed_name or seed_name in {".", ".."} or Path(seed_name).name != seed_name:
        raise RuntimeConfigurationError("KCD_DEMO_SEED_NAME must be a simple directory name.")

    idle = _positive_int("KCD_DEMO_IDLE_TTL_HOURS", 24)
    absolute = _positive_int("KCD_DEMO_ABSOLUTE_TTL_HOURS", 72)
    if absolute < idle:
        raise RuntimeConfigurationError(
            "KCD_DEMO_ABSOLUTE_TTL_HOURS must be at least the idle TTL."
        )
    secure = _boolean("KCD_DEMO_COOKIE_SECURE", True)
    lease_seconds = _positive_int("KCD_DEMO_REQUEST_LEASE_SECONDS", 120)
    heartbeat_seconds = _positive_int("KCD_DEMO_REQUEST_LEASE_HEARTBEAT_SECONDS", 30)
    if heartbeat_seconds >= lease_seconds:
        raise RuntimeConfigurationError(
            "KCD_DEMO_REQUEST_LEASE_HEARTBEAT_SECONDS must be less than "
            "KCD_DEMO_REQUEST_LEASE_SECONDS."
        )
    return DemoConfiguration(
        data_root=root.resolve(),
        seed_name=seed_name,
        idle_ttl_hours=idle,
        absolute_ttl_hours=absolute,
        touch_interval_seconds=_positive_int("KCD_DEMO_TOUCH_INTERVAL_SECONDS", 300),
        cookie_secure=secure,
        request_lease_seconds=lease_seconds,
        request_lease_heartbeat_seconds=heartbeat_seconds,
        max_request_bytes=_positive_int("KCD_DEMO_MAX_REQUEST_BYTES", 1024 * 1024),
        max_text_length=_positive_int("KCD_DEMO_MAX_TEXT_LENGTH", 256),
        max_notes_length=_positive_int("KCD_DEMO_MAX_NOTES_LENGTH", 4096),
        max_list_length=_positive_int("KCD_DEMO_MAX_LIST_LENGTH", 1000),
        rate_general_limit=_positive_int("KCD_DEMO_RATE_GENERAL_LIMIT", 300),
        rate_general_window_seconds=_positive_int("KCD_DEMO_RATE_GENERAL_WINDOW_SECONDS", 300),
        rate_mutation_limit=_positive_int("KCD_DEMO_RATE_MUTATION_LIMIT", 60),
        rate_mutation_window_seconds=_positive_int("KCD_DEMO_RATE_MUTATION_WINDOW_SECONDS", 300),
        rate_lifecycle_limit=_positive_int("KCD_DEMO_RATE_LIFECYCLE_LIMIT", 5),
        rate_lifecycle_window_seconds=_positive_int("KCD_DEMO_RATE_LIFECYCLE_WINDOW_SECONDS", 600),
        rate_invalid_session_limit=_positive_int("KCD_DEMO_RATE_INVALID_SESSION_LIMIT", 60),
        rate_invalid_session_window_seconds=_positive_int("KCD_DEMO_RATE_INVALID_SESSION_WINDOW_SECONDS", 300),
        max_printers=_positive_int("KCD_DEMO_MAX_PRINTERS", 10),
        max_filament_profiles=_positive_int("KCD_DEMO_MAX_FILAMENT_PROFILES", 50),
        max_hourly_profiles=_positive_int("KCD_DEMO_MAX_HOURLY_PROFILES", 25),
        max_projects=_positive_int("KCD_DEMO_MAX_PROJECTS", 100),
        max_history_records=_positive_int("KCD_DEMO_MAX_HISTORY_RECORDS", 1000),
        max_workspace_bytes=_positive_int("KCD_DEMO_MAX_WORKSPACE_BYTES", 25 * 1024 * 1024),
        max_export_bytes=_positive_int("KCD_DEMO_MAX_EXPORT_BYTES", 10 * 1024 * 1024),
        trust_proxy=_boolean("KCD_DEMO_TRUST_PROXY", False),
        expect_https=_boolean("KCD_DEMO_EXPECT_HTTPS", False),
    )
