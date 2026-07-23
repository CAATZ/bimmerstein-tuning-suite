"""Explicit unit conversion and numeric validation helpers."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from math import isfinite

_DIAMETER_TO_MM: dict[str, float] = {
    "mm": 1.0,
    "cm": 10.0,
    "m": 1000.0,
    "inch": 25.4,
    "in": 25.4,
}


def require_finite(value: float, name: str) -> float:
    numeric = float(value)
    if not isfinite(numeric):
        raise ValueError(f"{name} must be finite, not {value!r}")
    return numeric


def require_positive(value: float, name: str) -> float:
    numeric = require_finite(value, name)
    if numeric <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return numeric


def require_nonnegative(value: float, name: str) -> float:
    numeric = require_finite(value, name)
    if numeric < 0:
        raise ValueError(f"{name} must not be negative")
    return numeric


def normalize_diameter_mm(value: float, unit: str) -> float:
    normalized_unit = unit.lower().strip()
    if normalized_unit not in _DIAMETER_TO_MM:
        supported = ", ".join(sorted(_DIAMETER_TO_MM))
        raise ValueError(f"unsupported diameter unit {unit!r}; use one of {supported}")
    return require_positive(value, "diameter") * _DIAMETER_TO_MM[normalized_unit]


def round_half_up(value: float | Decimal, places: int) -> float:
    if places < 0:
        raise ValueError("places must be non-negative")
    numeric = require_finite(float(value), "value")
    quantum = Decimal("1").scaleb(-places)
    return float(Decimal(str(numeric)).quantize(quantum, rounding=ROUND_HALF_UP))
