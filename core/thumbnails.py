"""
Moonraker thumbnail helper + caching.

Design goals:
- Best-effort: any failure returns None and must never crash page renders.
- Local file cache under data/thumb_cache/ to avoid repeated Moonraker fetches.
- Minimal dependencies: uses urllib from stdlib (no requests).

SQL-only note:
  The thumbnail cache is intentionally file-backed and allowed in SQL-only mode.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from core.config import DATA_DIR, SETTINGS_FILE
from core.storage import load_settings
from core.sql_only import is_sql_only


def _is_sql_only() -> bool:
    return is_sql_only()


_CACHE_ROOT = os.getenv("KCD_THUMB_CACHE_DIR") or os.path.join(DATA_DIR, "thumb_cache")
_TTL_SECONDS = int(os.getenv("KCD_THUMB_CACHE_TTL_SECONDS", str(24 * 60 * 60)))
_MAX_FILES = int(os.getenv("KCD_THUMB_CACHE_MAX_FILES", "5000"))
_HTTP_TIMEOUT_SECONDS = 2.5

# Very small in-memory metadata cache (printer+filename) -> (ts, thumbnails_list)
_meta_cache: Dict[Tuple[str, str], Tuple[float, List[Dict[str, Any]]]] = {}

_log = logging.getLogger(__name__)


def _safe_dir(name: str) -> str:
    s = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in str(name or "").strip())
    return s or "unknown"


def _evict_cache(printer_dir: str) -> None:
    if _MAX_FILES <= 0:
        return
    try:
        if not os.path.isdir(printer_dir):
            return
        files = [
            os.path.join(printer_dir, f)
            for f in os.listdir(printer_dir)
            if f.endswith(".png")
        ]
        if len(files) <= _MAX_FILES:
            return
        files.sort(key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0)
        for p in files[: max(0, len(files) - _MAX_FILES)]:
            try:
                os.remove(p)
            except Exception:
                pass
    except Exception:
        return


def compute_thumbnail_token(printer_name: str, filename: str, *, base_url: Optional[str] = None) -> str:
    """
    Compute the canonical thumbnail token for a printer+filename.

    The token is size-independent and is used for both small/card thumbnails:
      <token>_small.png / <token>_card.png
    """
    printer_key = str(printer_name or "").strip()
    raw_filename = str(filename or "").strip()
    filename_norm = _normalize_moonraker_filename(raw_filename)
    base = str(base_url or "").strip()
    digest = hashlib.sha1(f"{base}|{printer_key}|{filename_norm}".encode("utf-8")).hexdigest()
    return digest


def compute_legacy_thumbnail_token(printer_name: str, filename: str, size_hint: str, *, base_url: Optional[str] = None) -> str:
    """
    Legacy token included size_hint in the hash; kept for backfill compatibility.
    """
    return _legacy_thumbnail_token(printer_name, filename, size_hint, base_url=base_url)


def _legacy_thumbnail_token(printer_name: str, filename: str, size_hint: str, *, base_url: Optional[str] = None) -> str:
    """
    Legacy token included size_hint in the hash; keep for backfill compatibility.
    """
    printer_key = str(printer_name or "").strip()
    raw_filename = str(filename or "").strip()
    filename_norm = _normalize_moonraker_filename(raw_filename)
    base = str(base_url or "").strip()
    hint = str(size_hint or "").strip().lower() or "small"
    digest = hashlib.sha1(f"{base}|{printer_key}|{filename_norm}|{hint}".encode("utf-8")).hexdigest()
    return digest

def _is_running_in_docker() -> bool:
    # Best-effort: this code runs both on bare-metal and inside containers.
    try:
        if os.path.exists("/.dockerenv"):
            return True
    except Exception:
        pass
    try:
        with open("/proc/1/cgroup", "r", encoding="utf-8", errors="ignore") as f:
            cg = f.read()
        if "docker" in cg or "containerd" in cg or "kubepods" in cg:
            return True
    except Exception:
        pass
    return False


def _read_install_state_clients() -> List[Dict[str, Any]]:
    path = os.path.join(DATA_DIR, "install_state.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            state = json.load(f)
        if not isinstance(state, dict):
            return []
        clients = state.get("clients", [])
        if not isinstance(clients, list):
            return []
        return [c for c in clients if isinstance(c, dict)]
    except Exception:
        return []


def resolve_moonraker_base_url(printer_name: str) -> Optional[str]:
    """
    Best-effort resolution for a per-printer Moonraker base URL.

    Priority:
    1) SQL printer configuration in SQL-only mode
    2) compatibility settings per printer: moonraker_url (if present)
    3) installer registry in compatibility modes:
       - moonraker_url (if present)
       - remote client: host -> http://<hostname>:7125
    4) compatibility fallback:
       - if NOT running in Docker: http://localhost:7125
       - if running in Docker: None (requires explicit moonraker_url)
    """
    printer_name = str(printer_name or "").strip()
    if not printer_name:
        return None

    if _is_sql_only():
        try:
            from core import db as db_module

            with db_module.connect_db() as conn:
                db_module.apply_migrations(conn)
                moonraker_url = db_module.get_printer_moonraker_url(conn, printer_name)
                if moonraker_url:
                    return moonraker_url.rstrip("/")
        except Exception:
            pass

    settings = load_settings(SETTINGS_FILE)
    moonraker_url = None
    if isinstance(settings, dict):
        # 1) settings.printers[printer].moonraker_url (if present)
        printers_cfg = settings.get("printers") if isinstance(settings.get("printers"), dict) else None
        if isinstance(printers_cfg, dict):
            moonraker_url = str(printers_cfg.get(printer_name, {}).get("moonraker_url") or "").strip()
        # 2) settings[printer].moonraker_url (legacy per-printer)
        if not moonraker_url:
            moonraker_url = str(settings.get(printer_name, {}).get("moonraker_url") or "").strip()
        # 3) settings.moonraker_url (top-level)
        if not moonraker_url:
            moonraker_url = str(settings.get("moonraker_url") or "").strip()
    if moonraker_url:
        return moonraker_url.rstrip("/")

    if _is_sql_only():
        # SQL-only runtime must not fall back to installer JSON or localhost heuristics.
        return None

    for entry in _read_install_state_clients():
        if str(entry.get("printer_name") or "").strip() != printer_name:
            continue
        ctype = str(entry.get("type") or "").strip().lower()
        explicit = str(entry.get("moonraker_url") or "").strip()
        if explicit:
            return explicit.rstrip("/")
        if ctype == "remote":
            host = str(entry.get("host") or "").strip()
            if not host:
                continue
            hostname = host.split("@")[-1].strip()
            # If a port is included (user@host:port), strip it.
            if ":" in hostname and not hostname.startswith("["):
                hostname = hostname.split(":")[0]
            if hostname:
                return f"http://{hostname}:7125"

    if _is_running_in_docker():
        _log.debug("[thumb] no moonraker_url for printer=%s (running in Docker)", printer_name)
        return None
    return "http://localhost:7125"


def _http_get_json_with_status(url: str) -> Tuple[Optional[int], Optional[Dict[str, Any]]]:
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_SECONDS) as resp:
            status = getattr(resp, "status", None)
            body = resp.read()
        data = json.loads(body.decode("utf-8", errors="replace"))
        return status, (data if isinstance(data, dict) else None)
    except Exception:
        return None, None


def _http_get_json(url: str) -> Optional[Dict[str, Any]]:
    _, data = _http_get_json_with_status(url)
    return data


def _http_get_bytes(url: str) -> Optional[bytes]:
    try:
        req = urllib.request.Request(url, headers={"Accept": "*/*"})
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_SECONDS) as resp:
            return resp.read()
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, Exception):
        return None


def _normalize_moonraker_filename(filename: str) -> str:
    """
    Normalize a filename for Moonraker APIs:
    - remove leading "/"
    - remove leading "gcodes/" (case-insensitive)
    - preserve any subfolders (e.g. "test/cube.gcode")
    """
    s = str(filename or "").strip().replace("\\", "/")
    s = s.lstrip("/")
    if s.lower().startswith("gcodes/"):
        s = s[len("gcodes/") :]
    return s.lstrip("/")


def _extract_thumbnails(metadata_json: Dict[str, Any]) -> List[Dict[str, Any]]:
    # Moonraker commonly returns {"result": {...}}.
    result = metadata_json.get("result") if isinstance(metadata_json, dict) else None
    if isinstance(result, dict):
        thumbs = result.get("thumbnails")
        if isinstance(thumbs, list):
            return [t for t in thumbs if isinstance(t, dict)]
    if isinstance(result, list):
        return [t for t in result if isinstance(t, dict)]
    # Fallback: some variants may return {"thumbnails": [...]}
    thumbs = metadata_json.get("thumbnails") if isinstance(metadata_json, dict) else None
    if isinstance(thumbs, list):
        return [t for t in thumbs if isinstance(t, dict)]
    return []


def _pick_thumbnail(thumbnails: List[Dict[str, Any]], size_hint: str) -> Optional[Dict[str, Any]]:
    if not thumbnails:
        return None

    target = 64 if str(size_hint or "").strip().lower() == "small" else 300

    def _w(t: Dict[str, Any]) -> int:
        try:
            return int(t.get("width") or t.get("w") or 0)
        except Exception:
            return 0

    # Prefer thumbnails with width info, closest to target.
    with_w = [t for t in thumbnails if _w(t) > 0]
    candidates = with_w or thumbnails
    try:
        return sorted(candidates, key=lambda t: abs(_w(t) - target) if _w(t) else 10**9)[0]
    except Exception:
        return candidates[0] if candidates else None


def _metadata_thumbnails(
    printer_name: str,
    filename: str,
    *,
    size_hint: str = "",
    base_url: Optional[str] = None,
) -> List[Dict[str, Any]]:
    printer_key = str(printer_name or "").strip()
    raw_filename = str(filename or "").strip()
    filename_norm = _normalize_moonraker_filename(raw_filename)
    key = (printer_key, filename_norm)

    if not key[0] or not key[1]:
        return []
    now = time.time()
    cached = _meta_cache.get(key)
    if cached:
        ts, thumbs = cached
        if now - ts < _TTL_SECONDS:
            return thumbs

    base = str(base_url or "").strip() or resolve_moonraker_base_url(printer_key)
    if not base:
        _meta_cache[key] = (now, [])
        return []

    hint = (str(size_hint or "").strip().lower() or "small")
    _log.debug(
        "[thumb] printer=%s base=%s raw_filename=%s normalized=%s hint=%s",
        printer_key,
        base,
        raw_filename,
        filename_norm,
        hint,
    )

    qs = urllib.parse.urlencode({"filename": filename_norm}, safe="/")
    url = f"{base}/server/files/metadata?{qs}"
    status, data = _http_get_json_with_status(url)
    thumbs = _extract_thumbnails(data or {})
    _log.debug("[thumb] metadata status=%s thumbs=%s", status, len(thumbs))

    if not thumbs:
        url2 = f"{base}/server/files/thumbnails?{qs}"
        status2, data2 = _http_get_json_with_status(url2)
        thumbs = _extract_thumbnails(data2 or {})
        _log.debug("[thumb] thumbnails status=%s thumbs=%s", status2, len(thumbs))

    _meta_cache[key] = (now, thumbs)
    return thumbs


def get_cached_thumbnail_path(
    printer_name: str,
    filename: str,
    size_hint: str,
    *,
    base_url: Optional[str] = None,
) -> Optional[str]:
    """
    Ensure a thumbnail image is present in the local cache and return its path.
    Returns None if no thumbnail is available or any fetch fails.
    """
    printer_name = str(printer_name or "").strip()
    filename = str(filename or "").strip()
    size_hint = str(size_hint or "").strip().lower() or "small"
    if not printer_name or not filename:
        return None

    base = str(base_url or "").strip() or resolve_moonraker_base_url(printer_name)
    if not base:
        return None

    filename_norm = _normalize_moonraker_filename(filename)
    cache_key = compute_thumbnail_token(printer_name, filename_norm, base_url=base)
    printer_dir = os.path.join(_CACHE_ROOT, _safe_dir(printer_name))
    os.makedirs(printer_dir, exist_ok=True)
    cache_path = os.path.join(printer_dir, f"{cache_key}_{size_hint}.png")

    try:
        if os.path.exists(cache_path):
            age = time.time() - os.path.getmtime(cache_path)
            if age < _TTL_SECONDS:
                return cache_path
    except Exception:
        pass

    # Check for legacy cache filename (size-specific token) before fetching.
    legacy_key = _legacy_thumbnail_token(printer_name, filename_norm, size_hint, base_url=base)
    legacy_path = os.path.join(printer_dir, f"{legacy_key}_{size_hint}.png")
    try:
        if os.path.exists(legacy_path):
            age = time.time() - os.path.getmtime(legacy_path)
            if age < _TTL_SECONDS:
                try:
                    shutil.copy2(legacy_path, cache_path)
                except Exception:
                    pass
                return cache_path
    except Exception:
        pass

    thumbs = _metadata_thumbnails(printer_name, filename, size_hint=size_hint, base_url=base)
    picked = _pick_thumbnail(thumbs, size_hint=size_hint)
    if not picked:
        return None

    rel = str(picked.get("relative_path") or picked.get("path") or "").strip()
    if not rel:
        return None

    # Some Moonraker responses may include a leading slash or "gcodes/" prefix.
    rel = rel.lstrip("/")
    if rel.lower().startswith("gcodes/"):
        rel = rel[len("gcodes/") :]

    try:
        w = picked.get("width") or picked.get("w") or ""
        h = picked.get("height") or picked.get("h") or ""
        _log.debug("[thumb] selected size=%sx%s relative_path=%s", w, h, rel)
    except Exception:
        pass

    rel_q = urllib.parse.quote(rel.lstrip("/"))
    thumb_url = f"{base}/server/files/gcodes/{rel_q}"
    img = _http_get_bytes(thumb_url)
    if not img:
        return None

    # Write atomically.
    try:
        tmp = f"{cache_path}.tmp"
        with open(tmp, "wb") as f:
            f.write(img)
        os.replace(tmp, cache_path)
        _evict_cache(printer_dir)
        return cache_path
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        return None
