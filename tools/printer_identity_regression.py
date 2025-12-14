"""
Lightweight regression harness for printer identity guardrails.

Run from repo root:
  python tools/printer_identity_regression.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile


def _load_settings(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def main() -> int:
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    with tempfile.TemporaryDirectory() as td:
        os.chdir(td)
        os.makedirs("data", exist_ok=True)

        # Minimal known-printer registry (canonical).
        with open(os.path.join("data", "settings.json"), "w", encoding="utf-8") as f:
            json.dump({"SV08": {}}, f, indent=2)

        # Import app from the repo, but run with temp working dir so data/* is isolated.
        sys.path.insert(0, repo_root)
        import app as kcd_app  # noqa: E402
        from core.config import API_KEY, SETTINGS_FILE  # noqa: E402

        client = kcd_app.app.test_client()

        # 1) job-start: swapped args (printer_name is gcode) should normalize & succeed.
        resp = client.post(
            "/job-start",
            json={"printer_name": "Cube_PETG_0.2_11m55s.gcode", "filename": "SV08", "estimated_duration": 1200, "estimated_filament_mm": 5000},
        )
        assert resp.status_code == 200, resp.data
        data = resp.get_json()
        assert data and data.get("success") is True
        assert data["job"]["printer_name"] == "SV08"
        assert data["job"]["filename"].endswith(".gcode")

        # 2) job-start: unknown printer should be rejected and not added to settings.
        resp = client.post("/job-start", json={"printer_name": "NOT_A_PRINTER", "filename": "test.gcode"})
        assert resp.status_code == 400
        settings = _load_settings(SETTINGS_FILE)
        assert "NOT_A_PRINTER" not in settings

        # 3) log-print: unknown printer should be rejected and not created.
        resp = client.post(
            "/log-print",
            headers={"X-API-Key": API_KEY},
            json={"timestamp": 1, "printer": "NOT_A_PRINTER", "filename": "test.gcode", "duration_seconds": 10, "filament_mm": 100},
        )
        assert resp.status_code == 400
        settings = _load_settings(SETTINGS_FILE)
        assert "NOT_A_PRINTER" not in settings

        # 4) log-print: swapped args should normalize & succeed (still no new printers created).
        resp = client.post(
            "/log-print",
            headers={"X-API-Key": API_KEY},
            json={"timestamp": 2, "printer": "Cube_PETG_0.2_11m55s.gcode", "filename": "SV08", "duration_seconds": 10, "filament_mm": 100},
        )
        assert resp.status_code == 200, resp.data
        settings = _load_settings(SETTINGS_FILE)
        assert set(settings.keys()) == {"SV08"}

    print("OK: printer_identity_regression")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

