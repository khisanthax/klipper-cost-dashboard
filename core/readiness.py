"""
SQL-only runtime readiness checks.
"""
from __future__ import annotations

import json
from typing import Any

from core import db as db_module


REQUIRED_SQL_TABLES = ("printers", "jobs", "user_settings", "system_events")


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
            add_check("schema_migrations", True, schema_version=result["schema_version"])

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
