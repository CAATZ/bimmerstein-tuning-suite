"""Canonical voltage grids used by MAF scaling."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

CANONICAL_GRID_ID = "canonical-256-v1"
VOLTAGE_UNIT = "V"
FLOW_UNIT = "kg/hr"


def _decimal_round_half_up(value: Decimal, places: int) -> float:
    return float(value.quantize(Decimal("1").scaleb(-places), rounding=ROUND_HALF_UP))


CANONICAL_VOLTAGES_V: tuple[float, ...] = tuple(
    _decimal_round_half_up(Decimal(index) * Decimal(5) / Decimal(256), 2)
    for index in range(256)
)

EXTENSION_VOLTAGES_V: tuple[float, ...] = tuple(
    _decimal_round_half_up(Decimal("5.00") + Decimal(index) * Decimal("0.02"), 2)
    for index in range(133)
)


def canonical_voltage_grid() -> tuple[float, ...]:
    return CANONICAL_VOLTAGES_V
