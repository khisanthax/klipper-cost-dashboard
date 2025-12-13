"""
Lightweight G-code slicer metadata extraction (estimates only).

Constraints:
- Metadata-only parsing (no motion simulation).
- Reads only a limited portion of the file (head/tail) to avoid large memory use.
- If estimated time cannot be extracted, returns found=False with a clear error.
- Filament is returned only if it is explicitly in grams; mm/cm³/etc are ignored.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Optional, Union


_TIME_SECONDS_PATTERNS = [
    # Cura: ;TIME:12345
    re.compile(r"^\s*[;#]\s*TIME\s*:\s*(\d+)\s*$", re.IGNORECASE),
    # Generic: ; estimated printing time = 1h 2m 3s
    re.compile(r"estimated\s+printing\s+time.*?=\s*(.+)$", re.IGNORECASE),
    # Generic: ; print time: 01:23:45
    re.compile(r"(?:print\s+time|estimated\s+print\s+time)\s*[:=]\s*(\d{1,3}:\d{2}:\d{2})", re.IGNORECASE),
]

_FILAMENT_G_PATTERNS = [
    # Prusa/Orca: ; filament used [g] = 12.34
    re.compile(r"filament\s+used\s*\[\s*g\s*\]\s*=\s*([0-9]+(?:\.[0-9]+)?)", re.IGNORECASE),
    # Generic: ; Filament used: 12.34 g
    re.compile(r"filament\s+used\s*[:=]\s*([0-9]+(?:\.[0-9]+)?)\s*g\b", re.IGNORECASE),
    # Generic: ; Filament (g): 12.34
    re.compile(r"filament\s*\(\s*g\s*\)\s*[:=]\s*([0-9]+(?:\.[0-9]+)?)", re.IGNORECASE),
]

_SLICER_PATTERNS = [
    re.compile(r"generated\s+with\s+(.+)$", re.IGNORECASE),
    re.compile(r"generated\s+by\s+(.+)$", re.IGNORECASE),
    re.compile(r"^;\s*(prusaslicer|orcaslicer|bambustudio|cura)\b", re.IGNORECASE),
]


def _parse_hms(text: str) -> Optional[int]:
    """
    Parse a duration string into seconds.
    Accepts:
    - HH:MM:SS
    - '1h 2m 3s' (any subset)
    """
    s = (text or "").strip()
    if not s:
        return None

    m = re.match(r"^\s*(\d{1,3})\s*:\s*(\d{2})\s*:\s*(\d{2})\s*$", s)
    if m:
        h, mm, ss = m.groups()
        return int(h) * 3600 + int(mm) * 60 + int(ss)

    # Units form.
    hours = 0
    minutes = 0
    seconds = 0
    mh = re.search(r"(\d+)\s*h", s, re.IGNORECASE)
    if mh:
        hours = int(mh.group(1))
    mm = re.search(r"(\d+)\s*m", s, re.IGNORECASE)
    if mm:
        minutes = int(mm.group(1))
    ms = re.search(r"(\d+)\s*s", s, re.IGNORECASE)
    if ms:
        seconds = int(ms.group(1))

    if hours or minutes or seconds:
        return hours * 3600 + minutes * 60 + seconds

    return None


def _iter_head_tail_lines(path: str, max_head_lines: int, max_tail_lines: int):
    # Head
    with open(path, "rb") as f:
        for _ in range(max_head_lines):
            line = f.readline()
            if not line:
                break
            yield line

    # Tail (read last ~N bytes then split; approximate but bounded)
    file_size = os.path.getsize(path)
    if file_size <= 0:
        return
    chunk_size = min(file_size, 1024 * 1024)  # 1MB tail cap
    with open(path, "rb") as f:
        f.seek(max(0, file_size - chunk_size))
        tail = f.read()
    for line in tail.splitlines()[-max_tail_lines:]:
        yield line + b"\n"


@dataclass(frozen=True)
class GCodeMetadata:
    found: bool
    time_s: Optional[int] = None
    filament_g: Optional[float] = None
    slicer: Optional[str] = None
    error: Optional[str] = None


def extract_gcode_metadata(path: str, *, max_head_lines: int = 3000, max_tail_lines: int = 3000) -> GCodeMetadata:
    """
    Extract slicer-provided metadata estimates from a .gcode file on disk.
    """
    if not path or not os.path.exists(path):
        return GCodeMetadata(found=False, error="File not found.")

    time_s: Optional[int] = None
    filament_g: Optional[float] = None
    slicer: Optional[str] = None

    for raw in _iter_head_tail_lines(path, max_head_lines=max_head_lines, max_tail_lines=max_tail_lines):
        try:
            line = raw.decode("utf-8", errors="ignore").strip()
        except Exception:
            continue
        if not line:
            continue

        # Slicer (best-effort)
        if slicer is None:
            for sp in _SLICER_PATTERNS:
                m = sp.search(line)
                if m:
                    slicer = m.group(1).strip() if m.groups() else m.group(0).strip(";# ").strip()
                    break

        # Time
        if time_s is None:
            for tp in _TIME_SECONDS_PATTERNS:
                m = tp.search(line)
                if not m:
                    continue
                if tp.pattern.lower().startswith("^\\s*[;#]\\s*time"):
                    try:
                        time_s = int(m.group(1))
                    except Exception:
                        time_s = None
                else:
                    parsed = _parse_hms(m.group(1))
                    if parsed is not None:
                        time_s = parsed
                if time_s is not None:
                    break

        # Filament grams
        if filament_g is None:
            for fp in _FILAMENT_G_PATTERNS:
                m = fp.search(line)
                if m:
                    try:
                        filament_g = float(m.group(1))
                    except Exception:
                        filament_g = None
                    break

        if time_s is not None and filament_g is not None and slicer is not None:
            # Good enough; stop early.
            break

    if time_s is None:
        return GCodeMetadata(found=False, error="No slicer metadata found (missing estimated time).")

    return GCodeMetadata(found=True, time_s=time_s, filament_g=filament_g, slicer=slicer)

