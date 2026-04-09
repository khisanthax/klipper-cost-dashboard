"""
SQL-only runtime readiness checks.
"""
from __future__ import annotations

import json
import os
from typing import Any

from core import db as db_module
from core.sql_only import is_sql_only


REQUIRED_SQL_TABLES = ("printers", "jobs", "user_settings", "system_events")
SQL_ONLY_FAIL_FAST_ENV = "KCD_SQL_ONLY_FAIL_FAST"


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
        with db_module.connect_db() as conn:
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
