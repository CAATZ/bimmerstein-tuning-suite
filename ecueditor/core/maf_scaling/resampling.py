"""Strict deterministic linear resampling and 16x16 conversion."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from .units import require_finite


def _finite_tuple(values: Sequence[float], name: str) -> tuple[float, ...]:
    return tuple(
        require_finite(value, f"{name}[{index}]") for index, value in enumerate(values)
    )


def resample_curve(
    source_voltages_v: Sequence[float],
    source_flows_kg_per_hr: Sequence[float],
    target_voltages_v: Sequence[float],
) -> tuple[float, ...]:
    source_x = _finite_tuple(source_voltages_v, "source_voltages_v")
    source_y = _finite_tuple(source_flows_kg_per_hr, "source_flows_kg_per_hr")
    target_x = _finite_tuple(target_voltages_v, "target_voltages_v")
    if len(source_x) != len(source_y):
        raise ValueError("source voltage and flow lengths must match")
    if len(source_x) < 2:
        raise ValueError("at least two source samples are required")
    if any(right <= left for left, right in zip(source_x, source_x[1:], strict=False)):
        raise ValueError("source voltages must be strictly increasing; duplicates are not allowed")
    if not target_x:
        return ()
    if min(target_x) < source_x[0] or max(target_x) > source_x[-1]:
        raise ValueError(
            "target voltages fall outside the available interpolation domain "
            f"[{source_x[0]}, {source_x[-1]}]"
        )
    values = np.interp(np.asarray(target_x), np.asarray(source_x), np.asarray(source_y))
    return tuple(float(value) for value in values)


def to_16x16(values: Sequence[float]) -> tuple[tuple[float, ...], ...]:
    flat = tuple(values)
    if len(flat) != 256:
        raise ValueError(f"a 16x16 scalar requires exactly 256 values, received {len(flat)}")
    return tuple(tuple(flat[row * 16 : (row + 1) * 16]) for row in range(16))


def from_16x16(table: Sequence[Sequence[float]]) -> tuple[float, ...]:
    rows = tuple(tuple(row) for row in table)
    if len(rows) != 16 or any(len(row) != 16 for row in rows):
        raise ValueError("a 16x16 table must have exactly 16 rows of 16 values")
    return tuple(value for row in rows for value in row)
