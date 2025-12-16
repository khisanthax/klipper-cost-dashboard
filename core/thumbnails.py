"""
Moonraker thumbnail helper + caching.

Design goals:
- Best-effort: any failure returns None and must never crash page renders.
- Local file cache under data/thumb_cache/ to avoid repeated Moonraker fetches.
- Minimal dependencies: uses urllib from stdlib (no requests).
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from core.config import DATA_DIR, SETTINGS_FILE
from core.storage import load_settings


_CACHE_ROOT = os.path.join(DATA_DIR, "thumb_cache")
_TTL_SECONDS = 24 * 60 * 60
_HTTP_TIMEOUT_SECONDS = 2.5

# Very small in-memory metadata cache (printer+filename) -> (ts, thumbnails_list)
_meta_cache: Dict[Tuple[str, str], Tuple[float, List[Dict[str, Any]]]] = {}


def _safe_dir(name: str) -> str:
    s = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in str(name or "").strip())
    return s or "unknown"


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
    1) settings.json per printer: moonraker_url (if present)
    2) installer registry:
       - remote client: host -> http://<hostname>:7125
       - local client:  http://localhost:7125
    """
    printer_name = str(printer_name or "").strip()
    if not printer_name:
        return None

    settings = load_settings(SETTINGS_FILE)
    moonraker_url = None
    if isinstance(settings, dict):
        moonraker_url = str(settings.get(printer_name, {}).get("moonraker_url") or "").strip()
    if moonraker_url:
        return moonraker_url.rstrip("/")

    for entry in _read_install_state_clients():
        if str(entry.get("printer_name") or "").strip() != printer_name:
            continue
        ctype = str(entry.get("type") or "").strip().lower()
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
        if ctype == "local":
            return "http://localhost:7125"

    return None


def _http_get_json(url: str) -> Optional[Dict[str, Any]]:
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_SECONDS) as resp:
            body = resp.read()
        data = json.loads(body.decode("utf-8", errors="replace"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _http_get_bytes(url: str) -> Optional[bytes]:
    try:
        req = urllib.request.Request(url, headers={"Accept": "*/*"})
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_SECONDS) as resp:
            return resp.read()
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, Exception):
        return None


def _extract_thumbnails(metadata_json: Dict[str, Any]) -> List[Dict[str, Any]]:
    # Moonraker commonly returns {"result": {...}}.
    result = metadata_json.get("result") if isinstance(metadata_json, dict) else None
    if isinstance(result, dict):
        thumbs = result.get("thumbnails")
        if isinstance(thumbs, list):
            return [t for t in thumbs if isinstance(t, dict)]
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


def _metadata_thumbnails(printer_name: str, filename: str) -> List[Dict[str, Any]]:
    key = (str(printer_name or "").strip(), str(filename or "").strip())
    if not key[0] or not key[1]:
        return []
    now = time.time()
    cached = _meta_cache.get(key)
    if cached:
        ts, thumbs = cached
        if now - ts < _TTL_SECONDS:
            return thumbs

    base = resolve_moonraker_base_url(key[0])
    if not base:
        _meta_cache[key] = (now, [])
        return []

    qs = urllib.parse.urlencode({"filename": key[1]})
    url = f"{base}/server/files/metadata?{qs}"
    data = _http_get_json(url)
    thumbs = _extract_thumbnails(data or {})
    _meta_cache[key] = (now, thumbs)
    return thumbs


def get_cached_thumbnail_path(printer_name: str, filename: str, size_hint: str) -> Optional[str]:
    """
    Ensure a thumbnail image is present in the local cache and return its path.
    Returns None if no thumbnail is available or any fetch fails.
    """
    printer_name = str(printer_name or "").strip()
    filename = str(filename or "").strip()
    size_hint = str(size_hint or "").strip().lower() or "small"
    if not printer_name or not filename:
        return None

    base = resolve_moonraker_base_url(printer_name)
    if not base:
        return None

    cache_key = hashlib.sha1(f"{base}|{printer_name}|{filename}|{size_hint}".encode("utf-8")).hexdigest()
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

    thumbs = _metadata_thumbnails(printer_name, filename)
    picked = _pick_thumbnail(thumbs, size_hint=size_hint)
    if not picked:
        return None

    rel = str(picked.get("relative_path") or picked.get("path") or "").strip()
    if not rel:
        return None

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
        return cache_path
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        return None

