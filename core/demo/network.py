"""Application-layer outbound-network deny for the demo runtime."""
from __future__ import annotations

from core.runtime import is_demo_runtime


class DemoNetworkBlocked(RuntimeError):
    pass


def require_outbound_network() -> None:
    if is_demo_runtime():
        raise DemoNetworkBlocked("Outbound network access is disabled in demo mode.")
