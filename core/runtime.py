"""KCD runtime-mode selection with a normal-mode compatible default."""
from __future__ import annotations

import os


class RuntimeConfigurationError(RuntimeError):
    """Raised when runtime configuration is unsafe or unsupported."""


def runtime_mode() -> str:
    value = str(os.getenv("KCD_RUNTIME_MODE", "normal") or "normal").strip().lower()
    if value not in {"normal", "demo"}:
        raise RuntimeConfigurationError(
            "KCD_RUNTIME_MODE must be either 'normal' or 'demo'."
        )
    return value


def is_demo_runtime() -> bool:
    return runtime_mode() == "demo"
