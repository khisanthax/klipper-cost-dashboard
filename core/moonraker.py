"""
Moonraker API helpers used by KCD features (thumbnails, import tools, etc.).

Keep these helpers best-effort and dependency-free (stdlib only).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional, Tuple


def test_moonraker_url(base_url: str, timeout_seconds: float = 2.5) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """
    Validate that a base URL points to a reachable Moonraker instance.

    Returns:
      (ok, detail, payload)
    """
    base_url = str(base_url or "").strip().rstrip("/")
    if not base_url:
        return False, "Missing Moonraker URL", None

    url = f"{base_url}/server/info"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=float(timeout_seconds)) as resp:
            body = resp.read()
        payload = json.loads(body.decode("utf-8", errors="replace"))

        if not isinstance(payload, dict):
            return False, "Unexpected response (not JSON object)", None

        # Moonraker typically returns {"result": {...}}.
        result = payload.get("result")
        if isinstance(result, dict):
            return True, "OK", payload

        # Fallback: some deployments may not wrap in "result".
        if any(k in payload for k in ("moonraker_version", "version", "hostname")):
            return True, "OK", payload

        return False, "Unexpected response (not Moonraker)", payload
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}", None
    except urllib.error.URLError as e:
        return False, f"Connection error: {e.reason}", None
    except Exception as e:
        return False, f"Error: {e}", None


def moonraker_get_json(
    base_url: str,
    path: str,
    params: Optional[Dict[str, Any]] = None,
    *,
    timeout_seconds: float = 2.5,
) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """
    Best-effort Moonraker JSON GET helper.

    Returns:
      (ok, detail, payload_dict)
    """
    base_url = str(base_url or "").strip().rstrip("/")
    path = str(path or "").strip()
    if not base_url or not path:
        return False, "Missing base_url/path", None

    url = f"{base_url}{path}"
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"

    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=float(timeout_seconds)) as resp:
            body = resp.read()
        payload = json.loads(body.decode("utf-8", errors="replace"))
        return (True, "OK", payload) if isinstance(payload, dict) else (False, "Unexpected JSON (not object)", None)
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}", None
    except urllib.error.URLError as e:
        return False, f"Connection error: {e.reason}", None
    except Exception as e:
        return False, f"Error: {e}", None


def fetch_moonraker_history(base_url: str, *, limit: Optional[int] = None) -> Tuple[bool, str, list[Dict[str, Any]]]:
    """
    Fetch Moonraker job history list for a single printer instance.

    Moonraker endpoint:
      GET /server/history/list?limit=<n>
    """
    params: Dict[str, Any] = {}
    if limit is not None:
        try:
            limit_int = int(limit)
            if limit_int > 0:
                params["limit"] = limit_int
        except Exception:
            pass

    ok, detail, payload = moonraker_get_json(base_url, "/server/history/list", params or None)
    if not ok or not isinstance(payload, dict):
        return False, detail, []

    # Moonraker typically wraps in {"result": {"jobs": [...]}}
    result = payload.get("result")
    if isinstance(result, dict):
        jobs = result.get("jobs")
        if isinstance(jobs, list):
            return True, "OK", [j for j in jobs if isinstance(j, dict)]

    jobs = payload.get("jobs")
    if isinstance(jobs, list):
        return True, "OK", [j for j in jobs if isinstance(j, dict)]

    return True, "OK (no jobs)", []
