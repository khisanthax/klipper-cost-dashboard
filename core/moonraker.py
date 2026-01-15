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


def probe_moonraker_server_info(
    base_url: str,
    *,
    timeout_seconds: float = 2.5,
    preview_chars: int = 200,
) -> Dict[str, Any]:
    """
    Probe Moonraker /server/info and return structured details.

    Returns:
      {
        "ok": bool,
        "status_code": int | None,
        "content_type": str,
        "body_preview": str,
        "error": str,
        "payload": dict | None,
      }
    """
    base_url = str(base_url or "").strip().rstrip("/")
    if not base_url:
        return {
            "ok": False,
            "status_code": None,
            "content_type": "",
            "body_preview": "",
            "error": "Missing Moonraker URL",
            "payload": None,
        }

    url = f"{base_url}/server/info"
    body = b""
    content_type = ""
    status_code: Optional[int] = None
    payload: Optional[Dict[str, Any]] = None
    error = ""

    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=float(timeout_seconds)) as resp:
            status_code = getattr(resp, "status", None) or getattr(resp, "code", None)
            content_type = str(resp.headers.get("Content-Type") or "")
            body = resp.read()
    except urllib.error.HTTPError as e:
        status_code = e.code
        content_type = str(getattr(e, "headers", {}).get("Content-Type") or "")
        try:
            body = e.read() or b""
        except Exception:
            body = b""
        error = f"HTTP {e.code}"
    except urllib.error.URLError as e:
        error = f"Connection error: {e.reason}"
    except Exception as e:
        error = f"Error: {e}"

    body_text = ""
    try:
        body_text = body.decode("utf-8", errors="replace")
    except Exception:
        body_text = ""

    body_preview = body_text[: max(0, int(preview_chars or 0))] if body_text else ""

    if "application/json" in content_type.lower() and body_text:
        try:
            parsed = json.loads(body_text)
            if isinstance(parsed, dict):
                payload = parsed
        except Exception as e:
            error = f"JSON parse error: {e}"

    ok = False
    if payload:
        result = payload.get("result")
        if isinstance(result, dict):
            ok = True
        elif any(k in payload for k in ("moonraker_version", "version", "hostname")):
            ok = True

    return {
        "ok": ok,
        "status_code": status_code,
        "content_type": content_type,
        "body_preview": body_preview,
        "error": error or ("Non-JSON response" if not payload else ""),
        "payload": payload,
    }


def test_moonraker_url(base_url: str, timeout_seconds: float = 2.5) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """
    Validate that a base URL points to a reachable Moonraker instance.

    Returns:
      (ok, detail, payload)
    """
    probe = probe_moonraker_server_info(base_url, timeout_seconds=timeout_seconds)
    if probe.get("ok"):
        return True, "OK", probe.get("payload")

    detail = probe.get("error") or "Moonraker probe failed"
    if probe.get("status_code"):
        detail = f"{detail} (HTTP {probe.get('status_code')})"
    return False, detail, probe.get("payload")


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


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def _get_first(job: Dict[str, Any], keys: Tuple[str, ...]) -> Any:
    for key in keys:
        if key in job and job.get(key) is not None:
            return job.get(key)
    return None


def _normalize_history_filename(filename: str) -> str:
    name = str(filename or "").strip().lstrip("/")
    if name.lower().startswith("gcodes/"):
        name = name[7:]
    return name


def find_history_job_for_completion(
    base_url: str,
    *,
    filename: str,
    end_timestamp: float,
    window_seconds: float = 600.0,
    limit: int = 200,
) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """
    Find the most likely Moonraker history entry for a completed job.

    Matches by normalized filename and the closest end_time within the window.
    """
    ok, detail, jobs = fetch_moonraker_history(base_url, limit=limit)
    if not ok:
        return False, detail, None

    target = _normalize_history_filename(filename)
    if not target:
        return False, "Missing filename", None

    best_job = None
    best_delta = None
    for job in jobs:
        job_name = str(_get_first(job, ("filename", "name", "file")) or "").strip()
        if not job_name:
            continue
        if _normalize_history_filename(job_name) != target:
            continue

        end_ts = _as_float(_get_first(job, ("end_time", "timestamp")))
        if end_ts <= 0:
            continue

        delta = abs(end_ts - float(end_timestamp or 0.0))
        if delta > float(window_seconds):
            continue

        if best_delta is None or delta < best_delta:
            best_delta = delta
            best_job = job

    if best_job:
        return True, "OK", best_job

    return True, "No matching history entry found", None
