"""Shared validation for persisted and inbound numeric values."""
from __future__ import annotations

import math
from typing import Any


class NumericValidationError(ValueError):
    """Raised when a numeric value is missing, non-finite, or outside its domain."""


def finite_float(
    value: Any,
    *,
    label: str,
    nonnegative: bool = False,
    positive: bool = False,
) -> float:
    if value is None or (isinstance(value, str) and not value.strip()):
        raise NumericValidationError(f"{label} is required")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise NumericValidationError(f"{label} must be a number") from exc
    if not math.isfinite(parsed):
        raise NumericValidationError(f"{label} must be finite")
    if positive and parsed <= 0:
        raise NumericValidationError(f"{label} must be greater than zero")
    if nonnegative and parsed < 0:
        raise NumericValidationError(f"{label} must be non-negative")
    return parsed


def optional_finite_float(
    value: Any,
    *,
    label: str,
    nonnegative: bool = False,
    positive: bool = False,
) -> float | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return finite_float(
        value,
        label=label,
        nonnegative=nonnegative,
        positive=positive,
    )
