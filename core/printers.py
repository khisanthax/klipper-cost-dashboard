"""
Printer identity helpers.

Goal: treat printer identity as registry-only and never derive/create printers
from incoming job payload fields.

Canonical printer names are loaded from:
- settings.json (configured printers)
- install_state.json clients registry (installer-known printers)

Additionally:
- Any value containing ".gcode" (case-insensitive) is never treated as a printer
  name. It's treated as a filename.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from typing import Optional, Set

from core.config import DATA_DIR, SETTINGS_FILE, DISPLAY_FILE, HEADERS
from core.storage import load_display_settings, load_settings
from core import db as db_module


def looks_like_gcode_filename(value: str) -> bool:
    """
    Return True if the string should be treated as a G-code filename.

    Hard rule:
    - If it contains ".gcode" anywhere (case-insensitive) it is NOT a printer name.
    """
    s = str(value or "").strip().lower()
    if not s:
        return False
    if ".gcode" in s:
        return True
    # Common alternative extension used by some slicers/firmware.
    if s.endswith(".gco"):
        return True
    return False


def _norm(name: str) -> str:
    return str(name or "").strip()


def _load_installer_printers() -> Set[str]:
    """
    Read printer names from data/install_state.json (installer registry).

    File shape is best-effort; unreadable/corrupt state is treated as empty.
    """
    path = os.path.join(DATA_DIR, "install_state.json")
    if not os.path.exists(path):
        return set()
    try:
        with open(path, "r", encoding="utf-8") as f:
            state = json.load(f)
    except Exception:
        return set()
    if not isinstance(state, dict):
        return set()
    clients = state.get("clients", [])
    if not isinstance(clients, list):
        return set()
    out: Set[str] = set()
    for c in clients:
        if not isinstance(c, dict):
            continue
        pn = _norm(c.get("printer_name", ""))
        if pn:
            out.add(pn)
    return out


def _is_sql_only() -> bool:
    return str(os.getenv("KCD_STORAGE_BACKEND", "csv")).strip().lower() == "sql"


def _load_sql_printers() -> Set[str]:
    try:
        conn = db_module.connect_db()
        db_module.apply_migrations(conn)
        rows = conn.execute("SELECT name FROM printers").fetchall()
    except Exception:
        return set()
    out: Set[str] = set()
    for r in rows:
        if isinstance(r, sqlite3.Row):
            name = _norm(r["name"])
        elif isinstance(r, (tuple, list)):
            name = _norm(r[0])
        elif isinstance(r, dict):
            name = _norm(r.get("name"))
        else:
            name = _norm(getattr(r, "name", ""))
        if name:
            out.add(name)
    return out


def get_canonical_printer_names(include_hidden: bool = False) -> Set[str]:
    """
    Return canonical printer names from persisted registries.

    - By default, soft-hidden printers are excluded (hidden_printers in display.json).
    - Any name that looks like a .gcode filename is excluded.
    """
    if _is_sql_only():
        configured = _load_sql_printers()
        installed = set()
    else:
        settings = load_settings(SETTINGS_FILE)
        configured = {_norm(p) for p in settings.keys() if _norm(p)}
        installed = _load_installer_printers()

    hidden: Set[str] = set()
    if not include_hidden and not _is_sql_only():
        hidden = {_norm(p) for p in load_display_settings(DISPLAY_FILE, HEADERS).get("hidden_printers", [])}

    names = (configured | installed) - hidden
    names = {p for p in names if p and not looks_like_gcode_filename(p)}
    return names


@dataclass(frozen=True)
class NormalizedPrinterAndFilename:
    printer_name: str
    filename: str
    valid_printer: bool
    reason: str = ""


def normalize_incoming_printer_and_filename(
    printer_value: Optional[str],
    filename_value: Optional[str],
    canonical_printers: Set[str],
) -> NormalizedPrinterAndFilename:
    """
    Normalize an incoming (printer, filename) pair using strict rules:

    - If printer looks like a .gcode filename and filename does NOT, assume swapped and swap.
    - If printer still looks like .gcode, treat it as filename and mark printer invalid.
    - If printer is not in canonical_printers, mark invalid.

    This function never mutates any persisted printer registry.
    """
    printer = _norm(printer_value)
    filename = _norm(filename_value)

    # Fix common client bug: swapped args (printer_name == filename).
    if looks_like_gcode_filename(printer) and (not filename or not looks_like_gcode_filename(filename)):
        printer, filename = filename, printer

    if looks_like_gcode_filename(printer):
        # Hard rule: never treat .gcode as a printer name.
        if not filename:
            filename = printer
        return NormalizedPrinterAndFilename(
            printer_name="",
            filename=filename,
            valid_printer=False,
            reason=f"Rejected printer_name because it looks like a gcode filename: {printer!r}",
        )

    if printer not in canonical_printers:
        return NormalizedPrinterAndFilename(
            printer_name=printer,
            filename=filename,
            valid_printer=False,
            reason=f"Unknown printer_name received: {printer!r}",
        )

    return NormalizedPrinterAndFilename(printer_name=printer, filename=filename, valid_printer=True)
