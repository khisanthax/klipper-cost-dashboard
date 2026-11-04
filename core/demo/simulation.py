"""Pure synthetic printer observations for a demo workspace."""
from time import time


def simulated_printer_status(names, created_at, *, now=None):
    observed = time() if now is None else now
    elapsed = max(0, observed - created_at)
    observations = {}
    for index, name in enumerate(sorted(names, key=str.casefold)):
        cycle = int(elapsed + index * 110) % 360
        if cycle < 180:
            state, active_seconds = "printing", 20 + cycle
        elif cycle < 240:
            state, active_seconds = "paused", 200
        elif cycle < 300:
            state, active_seconds = "complete", 200
        else:
            state, active_seconds = "standby", 0
        observations[name] = {
            "simulated": True,
            "connected": True,
            "identity_verified": False,
            "identity_warning": "Simulated printer",
            "state": state,
            "filename": "demo-calibration.gcode" if active_seconds else "",
            "print_seconds": active_seconds,
            "total_seconds": 20 + min(cycle, 239) if active_seconds else 0,
            "filament_mm": active_seconds * 6,
            "observed_at": observed,
            "error": None,
        }
    return observations
