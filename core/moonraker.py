"""
Moonraker API helpers used by KCD features (thumbnails, import tools, etc.).

Keep these helpers best-effort and dependency-free (stdlib only).
"""

from __future__ import annotations

import json
import urllib.error
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

