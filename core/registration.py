"""Transactional registration; SQL is authoritative, installer metadata is ancillary."""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from core import db
from core.config import DEFAULT_PRICING
from core.printer_lifecycle import _load_setting, _save_setting
from core.readiness import _check_configured_printer_pricing


class RegistrationConflict(ValueError):
    pass


def validate_registration(name, url):
    name = str(name or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_. -]{0,79}", name) or ".gcode" in name.lower():
        raise ValueError("Printer identity must be 1–80 letters, numbers, spaces, dots, underscores or hyphens")
    parsed = urlsplit(str(url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Moonraker URL must be http(s), without credentials, query or fragment")
    parsed.port  # Validate the port before any writes.
    return name, str(url).strip().rstrip("/")


def register_printer(name, moonraker_url, *, metadata=None, relocate=False, reactivate=False, external_id=None):
    """Initialize missing defaults atomically; never merge history or replace prices."""
    name, url = validate_registration(name, moonraker_url)
    metadata = metadata or {}
    allowed = {"host", "type", "cfg_dir", "config_dir", "script_path", "master_url"}
    if not isinstance(metadata, dict) or any(k not in allowed or not isinstance(v, str) or len(v) > 1024 for k, v in metadata.items()):
        raise ValueError("Invalid installer metadata (credentials are never accepted)")
    with db.connect_db() as conn:
        db.apply_migrations(conn)
        conn.execute("BEGIN IMMEDIATE")
        records = _load_setting(conn, "client_installations")
        old = records.get(name, {})
        location = lambda m: (m.get("host", ""), m.get("cfg_dir") or m.get("config_dir", ""))
        existing = conn.execute("SELECT id,moonraker_url FROM printers WHERE name=?", (name,)).fetchone()
        if external_id:
            owner = conn.execute("SELECT name FROM printers WHERE external_id=?", (external_id,)).fetchone()
            if owner and owner["name"] != name:
                raise RegistrationConflict("External identity belongs to another printer; history merging is never implicit")
        if existing and not relocate:
            if old and metadata and location(old) != location(metadata):
                raise RegistrationConflict("Identity already has another installation; choose a new identity for a clone, or explicit relocation to preserve history")
            if existing["moonraker_url"] and existing["moonraker_url"] != url:
                raise RegistrationConflict("Endpoint changed; explicit relocation is required to preserve this identity")
        display = _load_setting(conn, "display_settings") or _load_setting(conn, "display")
        hidden = display.get("hidden_printers", [])
        if name in hidden and not reactivate:
            raise RegistrationConflict("Printer is retired/hidden; explicit reactivation is required")
        if reactivate:
            display["hidden_printers"] = [p for p in hidden if p != name]
        if not any(k in display for k in ("pause_include_paused_time_default", "pause_exclude_paused_time_default")):
            display["pause_include_paused_time_default"] = False
        settings = _load_setting(conn, "printer_settings") or _load_setting(conn, "settings")
        prices = settings.setdefault(name, {})
        if not isinstance(prices, dict):
            raise ValueError("Existing pricing is malformed; repair it explicitly before registration")
        for k, v in DEFAULT_PRICING.items():
            prices.setdefault(k, v)
        prices["moonraker_url"] = url
        printer_id = db.upsert_printer(conn, name, url, external_id=external_id)
        if external_id:
            conn.execute("UPDATE printers SET external_id=? WHERE id=?", (external_id, printer_id))
        _save_setting(conn, "printer_settings", settings)
        _save_setting(conn, "display_settings", display)
        if metadata:
            records[name] = {**old, **metadata, "moonraker_url": url}
            _save_setting(conn, "client_installations", records)
        ok, detail = _check_configured_printer_pricing(conn)
        if not ok:
            raise ValueError("Registration rolled back: SQL pricing readiness failed: " + str(detail))
    return {"printer_id": printer_id, "printer_name": name, "configured": True}


def registered_clients():
    with db.connect_db() as conn:
        db.apply_migrations(conn)
        metadata = _load_setting(conn, "client_installations")
        settings = _load_setting(conn, "printer_settings") or _load_setting(conn, "settings")
        display = _load_setting(conn, "display_settings") or _load_setting(conn, "display")
        return [{**metadata.get(r["name"], {}), "printer_name": r["name"],
                 "moonraker_url": r["moonraker_url"],
                 "configured": bool(settings.get(r["name"])),
                 "hidden": r["name"] in display.get("hidden_printers", []),
                 "metadata_recovered": r["name"] not in metadata}
                for r in conn.execute("SELECT name,moonraker_url FROM printers ORDER BY name")]
