"""
SQL-only runtime readiness checks.
"""
from __future__ import annotations

import json
import os
from contextlib import closing
from typing import Any

from core import db as db_module
from core.numeric import finite_float, NumericValidationError
from core.sql_only import is_sql_only


REQUIRED_SQL_TABLES = (
    "printers",
    "jobs",
    "user_settings",
    "filament_profiles",
    "hourly_rate_profiles",
    "system_events",
)
SQL_ONLY_FAIL_FAST_ENV = "KCD_SQL_ONLY_FAIL_FAST"
VALID_FILAMENT_MODES = {"per_meter", "per_gram", "per_kg"}


class SqlOnlyStartupReadinessError(RuntimeError):
    """Raised when strict SQL-only startup readiness validation fails."""


def _env_truthy(name: str) -> bool:
    value = str(os.getenv(name, "") or "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _env_falsey(name: str) -> bool:
    value = str(os.getenv(name, "") or "").strip().lower()
    return value in {"0", "false", "no", "off"}


def _check_pause_billing_default(conn) -> tuple[bool, dict[str, Any]]:
    for key in ("display_settings", "display"):
        row = conn.execute(
            "SELECT value_json FROM user_settings WHERE key = ?",
            (key,),
        ).fetchone()
        if not row:
            continue
        raw = row["value_json"] if hasattr(row, "__getitem__") else row[0]
        try:
            data = json.loads(raw)
        except Exception as exc:
            return False, {
                "code": "invalid_display_settings",
                "message": f"user_settings.{key} is not valid JSON: {exc}",
            }
        if not isinstance(data, dict):
            return False, {
                "code": "invalid_display_settings",
                "message": f"user_settings.{key} must be a JSON object.",
            }
        if "pause_include_paused_time_default" in data or "pause_exclude_paused_time_default" in data:
            return True, {"source_key": key}

    return False, {
        "code": "missing_pause_billing_default",
        "message": "Missing pause billing default in user_settings.display_settings.",
    }


def _load_user_setting(conn, key: str):
    row = conn.execute(
        "SELECT value_json FROM user_settings WHERE key = ?",
        (key,),
    ).fetchone()
    if not row:
        return None
    raw = row["value_json"] if hasattr(row, "__getitem__") else row[0]
    return json.loads(raw) if raw else None


def _load_user_setting_dict(conn, *keys: str) -> dict[str, Any]:
    for key in keys:
        try:
            data = _load_user_setting(conn, key)
        except Exception:
            return {}
        if isinstance(data, dict):
            return data
    return {}


def _as_nonnegative_float(value: Any) -> float | None:
    try:
        return finite_float(value, label="pricing value", nonnegative=True)
    except NumericValidationError:
        return None


def _as_positive_float(value: Any) -> float | None:
    try:
        return finite_float(value, label="grams_per_meter", positive=True)
    except NumericValidationError:
        return None


def _has_valid_rate_profile(conn, profile_uid: str) -> bool:
    row = conn.execute(
        """
        SELECT rate_per_hour
          FROM hourly_rate_profiles
         WHERE profile_uid = ? OR CAST(id AS TEXT) = ?
        """,
        (profile_uid, profile_uid),
    ).fetchone()
    if not row:
        return False
    value = row["rate_per_hour"] if hasattr(row, "__getitem__") else row[0]
    return _as_nonnegative_float(value) is not None


def _has_valid_filament_profile(conn, profile_uid: str) -> bool:
    row = conn.execute(
        """
        SELECT filament_mode, filament_rate, grams_per_meter
          FROM filament_profiles
         WHERE profile_uid = ? OR CAST(id AS TEXT) = ?
        """,
        (profile_uid, profile_uid),
    ).fetchone()
    if not row:
        return False
    mode = str(row["filament_mode"] if hasattr(row, "__getitem__") else row[0] or "").strip()
    rate = row["filament_rate"] if hasattr(row, "__getitem__") else row[1]
    grams = row["grams_per_meter"] if hasattr(row, "__getitem__") else row[2]
    return (
        mode in VALID_FILAMENT_MODES
        and _as_nonnegative_float(rate) is not None
        and _as_positive_float(grams) is not None
    )


def _has_valid_printer_rate_config(conn, printer_name: str, printer_settings: dict[str, Any]) -> bool:
    active_rate_profile_id = str(printer_settings.get("active_rate_profile_id") or "").strip()
    if active_rate_profile_id:
        return _has_valid_rate_profile(conn, active_rate_profile_id)
    return _as_nonnegative_float(printer_settings.get("rate_per_hour")) is not None


def _has_valid_printer_filament_config(conn, printer_name: str, printer_settings: dict[str, Any], mappings: dict[str, Any]) -> bool:
    profile_id = str(mappings.get(printer_name) or "").strip()
    if profile_id:
        return _has_valid_filament_profile(conn, profile_id)
    mode = str(printer_settings.get("filament_mode") or "").strip()
    return (
        mode in VALID_FILAMENT_MODES
        and _as_nonnegative_float(printer_settings.get("filament_rate")) is not None
        and _as_positive_float(printer_settings.get("grams_per_meter")) is not None
    )


def _check_configured_printer_pricing(conn) -> tuple[bool, dict[str, Any]]:
    rows = conn.execute("SELECT name FROM printers ORDER BY name").fetchall()
    printer_names = [
        str(row["name"] if hasattr(row, "__getitem__") else row[0] or "").strip()
        for row in rows
    ]
    printer_names = [name for name in printer_names if name]
    display = _load_user_setting_dict(conn, "display_settings", "display")
    hidden_raw = display.get("hidden_printers", [])
    if not isinstance(hidden_raw, list):
        return False, {
            "code": "invalid_hidden_printer_state",
            "message": "user_settings.display_settings.hidden_printers must be a JSON list.",
            "printers_checked": 0,
        }
    hidden = {str(name or "").strip() for name in hidden_raw if str(name or "").strip()}
    printer_names = [name for name in printer_names if name not in hidden]
    if not printer_names:
        return True, {"printers_checked": 0}

    settings = _load_user_setting_dict(conn, "printer_settings", "settings")
    mappings = _load_user_setting_dict(conn, "filament_mappings")
    missing_rate: list[str] = []
    missing_filament: list[str] = []

    for printer_name in printer_names:
        printer_settings = settings.get(printer_name)
        if not isinstance(printer_settings, dict):
            printer_settings = {}
        if not _has_valid_printer_rate_config(conn, printer_name, printer_settings):
            missing_rate.append(printer_name)
        if not _has_valid_printer_filament_config(conn, printer_name, printer_settings, mappings):
            missing_filament.append(printer_name)

    if not missing_rate and not missing_filament:
        return True, {"printers_checked": len(printer_names)}

    detail = {
        "code": "invalid_printer_pricing_config",
        "message": "Configured SQL printers are missing DB-backed pricing/profile config.",
        "printers_checked": len(printer_names),
        "missing_rate_printers": missing_rate,
        "missing_filament_printers": missing_filament,
    }
    return False, detail


def check_sql_only_readiness() -> dict[str, Any]:
    """
    Validate the minimum required state for SQL-only runtime readiness.
    """
    result: dict[str, Any] = {
        "backend": "sql",
        "ready": False,
        "schema_version": None,
        "printers_count": 0,
        "checks": [],
        "errors": [],
    }

    def add_check(name: str, ok: bool, **extra: Any) -> None:
        check = {"name": name, "ok": bool(ok)}
        check.update(extra)
        result["checks"].append(check)

    try:
        with closing(db_module.connect_db()) as conn:
            add_check("db_connection", True)
            db_module.apply_migrations(conn)
            result["schema_version"] = db_module.current_schema_version(conn)
            schema_ok = bool(result["schema_version"])
            add_check("schema_migrations", schema_ok, schema_version=result["schema_version"])
            if not schema_ok:
                result["errors"].append(
                    {
                        "code": "missing_schema_version",
                        "message": "No schema migration version recorded after applying migrations.",
                    }
                )

            table_counts: dict[str, int] = {}
            for table_name in REQUIRED_SQL_TABLES:
                row = conn.execute(f"SELECT COUNT(*) AS c FROM {table_name}").fetchone()
                count = row["c"] if hasattr(row, "__getitem__") else int(row[0])
                table_counts[table_name] = int(count)
            result["printers_count"] = table_counts.get("printers", 0)
            add_check("required_tables", True, table_counts=table_counts)

            pause_ok, pause_detail = _check_pause_billing_default(conn)
            add_check("pause_billing_default", pause_ok, **pause_detail)
            if not pause_ok:
                result["errors"].append(pause_detail)

            pricing_ok, pricing_detail = _check_configured_printer_pricing(conn)
            add_check("configured_printer_pricing", pricing_ok, **pricing_detail)
            if not pricing_ok:
                result["errors"].append(pricing_detail)
    except Exception as exc:
        add_check("db_connection", False, message=str(exc))
        result["errors"].append(
            {
                "code": "db_unavailable",
                "message": str(exc),
            }
        )
        return result

    result["ready"] = not result["errors"]
    return result


def sql_only_fail_fast_enabled() -> bool:
    """Return whether strict SQL-only startup readiness is enabled."""
    if not is_sql_only():
        return False
    if _env_falsey(SQL_ONLY_FAIL_FAST_ENV):
        return False
    return True


def format_sql_only_readiness_failure(readiness: dict[str, Any]) -> str:
    """Render a compact startup failure message from a readiness result."""
    errors = readiness.get("errors") or []
    if not errors:
        return "SQL-only startup readiness failed."

    parts: list[str] = []
    for err in errors:
        code = str(err.get("code") or "readiness_error").strip()
        message = str(err.get("message") or "").strip()
        parts.append(f"{code}: {message}" if message else code)

    return (
        "SQL-only startup readiness failed: "
        + "; ".join(parts)
        + f". Set {SQL_ONLY_FAIL_FAST_ENV}=0 to bypass strict startup."
    )


def enforce_sql_only_startup_readiness() -> dict[str, Any] | None:
    """
    Fail fast during startup when strict SQL-only readiness is enabled.
    """
    if not is_sql_only() or not sql_only_fail_fast_enabled():
        return None

    readiness = check_sql_only_readiness()
    if readiness.get("ready"):
        return readiness

    raise SqlOnlyStartupReadinessError(format_sql_only_readiness_failure(readiness))
