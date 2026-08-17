"""Transactional SQL-only printer lifecycle operations."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from core import db as db_module


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_setting(conn, key: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT value_json FROM user_settings WHERE key = ?",
        (key,),
    ).fetchone()
    if not row:
        return {}
    value = json.loads(row["value_json"])
    if not isinstance(value, dict):
        raise ValueError(f"user_settings.{key} must contain a JSON object")
    return value


def _save_setting(conn, key: str, value: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO user_settings (key, value_json, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET
            value_json = excluded.value_json,
            updated_at = excluded.updated_at
        """,
        (key, json.dumps(value), _utc_now_iso()),
    )


def _printer_row(conn, name: str):
    return conn.execute(
        "SELECT id, name, moonraker_url, external_id FROM printers WHERE name = ?",
        (name,),
    ).fetchone()


def _move_key(data: dict[str, Any], old_name: str, new_name: str) -> None:
    if old_name in data:
        data[new_name] = data.pop(old_name)


def _rename_hidden(display: dict[str, Any], old_name: str, new_name: str) -> None:
    hidden = display.get("hidden_printers")
    if not isinstance(hidden, list):
        return
    display["hidden_printers"] = sorted(
        {new_name if str(name).strip() == old_name else str(name).strip() for name in hidden if str(name).strip()}
    )


def rename_printer(old_name: str, new_name: str) -> None:
    old_name = str(old_name or "").strip()
    new_name = str(new_name or "").strip()
    if not old_name or not new_name:
        raise ValueError("both old and new printer names are required")
    if old_name == new_name:
        return

    with db_module.connect_db() as conn:
        db_module.apply_migrations(conn)
        if not _printer_row(conn, old_name):
            raise ValueError(f"printer not found: {old_name}")
        if _printer_row(conn, new_name):
            raise ValueError(f"printer already exists: {new_name}")

        settings = _load_setting(conn, "printer_settings")
        legacy_settings = _load_setting(conn, "settings")
        mappings = _load_setting(conn, "filament_mappings")
        display = _load_setting(conn, "display_settings")
        legacy_display = _load_setting(conn, "display")

        conn.execute(
            "UPDATE printers SET name = ?, updated_at = ? WHERE name = ?",
            (new_name, _utc_now_iso(), old_name),
        )
        for data in (settings, legacy_settings, mappings):
            _move_key(data, old_name, new_name)
        for data in (display, legacy_display):
            _rename_hidden(data, old_name, new_name)

        for key, data in (
            ("printer_settings", settings),
            ("settings", legacy_settings),
            ("filament_mappings", mappings),
            ("display_settings", display),
            ("display", legacy_display),
        ):
            if data or conn.execute("SELECT 1 FROM user_settings WHERE key = ?", (key,)).fetchone():
                _save_setting(conn, key, data)


def merge_printers(primary_name: str, secondary_name: str) -> None:
    primary_name = str(primary_name or "").strip()
    secondary_name = str(secondary_name or "").strip()
    if not primary_name or not secondary_name:
        raise ValueError("both primary and secondary printer names are required")
    if primary_name == secondary_name:
        raise ValueError("primary and secondary printers must be different")

    with db_module.connect_db() as conn:
        db_module.apply_migrations(conn)
        primary = _printer_row(conn, primary_name)
        secondary = _printer_row(conn, secondary_name)
        if not primary:
            raise ValueError(f"printer not found: {primary_name}")
        if not secondary:
            raise ValueError(f"printer not found: {secondary_name}")
        if primary["external_id"] and secondary["external_id"]:
            raise ValueError("cannot merge printers with distinct external IDs")

        settings = _load_setting(conn, "printer_settings")
        legacy_settings = _load_setting(conn, "settings")
        mappings = _load_setting(conn, "filament_mappings")
        display = _load_setting(conn, "display_settings")
        legacy_display = _load_setting(conn, "display")

        for data in (settings, legacy_settings):
            secondary_value = data.pop(secondary_name, None)
            primary_value = data.get(primary_name)
            if isinstance(secondary_value, dict):
                merged = dict(secondary_value)
                if isinstance(primary_value, dict):
                    merged.update(primary_value)
                data[primary_name] = merged
            elif primary_value is None and secondary_value is not None:
                data[primary_name] = secondary_value

        if primary_name not in mappings and secondary_name in mappings:
            mappings[primary_name] = mappings[secondary_name]
        mappings.pop(secondary_name, None)

        for data in (display, legacy_display):
            hidden = data.get("hidden_printers")
            if isinstance(hidden, list):
                data["hidden_printers"] = sorted(
                    {
                        str(name).strip()
                        for name in hidden
                        if str(name).strip() and str(name).strip() not in {primary_name, secondary_name}
                    }
                )

        conn.execute(
            "UPDATE jobs SET printer_id = ? WHERE printer_id = ?",
            (primary["id"], secondary["id"]),
        )
        conn.execute(
            "UPDATE events SET printer_id = ? WHERE printer_id = ?",
            (primary["id"], secondary["id"]),
        )
        inherited_external_id = primary["external_id"] or secondary["external_id"]
        if secondary["external_id"] and not primary["external_id"]:
            conn.execute(
                "UPDATE printers SET external_id = NULL, updated_at = ? WHERE id = ?",
                (_utc_now_iso(), secondary["id"]),
            )
        conn.execute(
            """
            UPDATE printers
            SET moonraker_url = ?, external_id = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                primary["moonraker_url"] or secondary["moonraker_url"],
                inherited_external_id,
                _utc_now_iso(),
                primary["id"],
            ),
        )
        conn.execute("DELETE FROM printers WHERE id = ?", (secondary["id"],))

        for key, data in (
            ("printer_settings", settings),
            ("settings", legacy_settings),
            ("filament_mappings", mappings),
            ("display_settings", display),
            ("display", legacy_display),
        ):
            if data or conn.execute("SELECT 1 FROM user_settings WHERE key = ?", (key,)).fetchone():
                _save_setting(conn, key, data)


def delete_printer(printer_name: str) -> None:
    """Remove active SQL config while preserving the printer row and history."""
    printer_name = str(printer_name or "").strip()
    if not printer_name:
        raise ValueError("printer name is required")

    with db_module.connect_db() as conn:
        db_module.apply_migrations(conn)
        printer = _printer_row(conn, printer_name)
        if not printer:
            raise ValueError(f"printer not found: {printer_name}")

        settings = _load_setting(conn, "printer_settings")
        legacy_settings = _load_setting(conn, "settings")
        mappings = _load_setting(conn, "filament_mappings")
        display = _load_setting(conn, "display_settings")
        legacy_display = _load_setting(conn, "display")

        settings.pop(printer_name, None)
        legacy_settings.pop(printer_name, None)
        mappings.pop(printer_name, None)
        hidden = display.get("hidden_printers")
        hidden_names = {str(name).strip() for name in hidden if str(name).strip()} if isinstance(hidden, list) else set()
        hidden_names.add(printer_name)
        display["hidden_printers"] = sorted(hidden_names)
        if legacy_display:
            legacy_hidden = legacy_display.get("hidden_printers")
            legacy_names = (
                {str(name).strip() for name in legacy_hidden if str(name).strip()}
                if isinstance(legacy_hidden, list)
                else set()
            )
            legacy_names.add(printer_name)
            legacy_display["hidden_printers"] = sorted(legacy_names)

        conn.execute(
            "UPDATE printers SET moonraker_url = NULL, external_id = NULL, updated_at = ? WHERE id = ?",
            (_utc_now_iso(), printer["id"]),
        )
        for key, data in (
            ("printer_settings", settings),
            ("settings", legacy_settings),
            ("filament_mappings", mappings),
            ("display_settings", display),
            ("display", legacy_display),
        ):
            if data or key == "display_settings" or conn.execute(
                "SELECT 1 FROM user_settings WHERE key = ?", (key,)
            ).fetchone():
                _save_setting(conn, key, data)


def reactivate_printer(
    printer_name: str,
    *,
    moonraker_url: str | None = None,
    external_id: str | None = None,
) -> int:
    """Explicitly reactivate a retired SQL printer without changing its identity."""
    printer_name = str(printer_name or "").strip()
    if not printer_name:
        raise ValueError("printer name is required")

    with db_module.connect_db() as conn:
        db_module.apply_migrations(conn)
        printer = _printer_row(conn, printer_name)
        if not printer:
            raise ValueError(f"printer not found: {printer_name}")

        display = _load_setting(conn, "display_settings")
        legacy_display = _load_setting(conn, "display")
        for data in (display, legacy_display):
            hidden = data.get("hidden_printers")
            if isinstance(hidden, list):
                data["hidden_printers"] = sorted(
                    {
                        str(name).strip()
                        for name in hidden
                        if str(name).strip() and str(name).strip() != printer_name
                    }
                )

        updates = ["updated_at = ?"]
        values: list[Any] = [_utc_now_iso()]
        if moonraker_url is not None:
            updates.append("moonraker_url = ?")
            values.append(str(moonraker_url or "").strip() or None)
        if external_id is not None:
            updates.append("external_id = ?")
            values.append(str(external_id or "").strip() or None)
        values.append(printer["id"])
        conn.execute(
            f"UPDATE printers SET {', '.join(updates)} WHERE id = ?",
            values,
        )

        for key, data in (("display_settings", display), ("display", legacy_display)):
            if data or key == "display_settings" or conn.execute(
                "SELECT 1 FROM user_settings WHERE key = ?", (key,)
            ).fetchone():
                _save_setting(conn, key, data)
        return int(printer["id"])
