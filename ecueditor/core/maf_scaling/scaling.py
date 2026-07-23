"""The shared configurable MAF scaling engine."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from .catalog import get_maf
from .diagnostics import source_curve_warnings
from .grids import CANONICAL_VOLTAGES_V, EXTENSION_VOLTAGES_V
from .models import MafRecord, ScalingRequest, ScalingResult
from .resampling import resample_curve
from .units import (
    normalize_diameter_mm,
    require_finite,
    require_nonnegative,
    require_positive,
    round_half_up,
)

ALGORITHM_VERSION = "maf-scaling-v1"
ELECTRICAL_PRESETS_OHMS: dict[str, float] = {"MS41": 8980.0, "MS43": 4700.0}


def _resolve_record(source: str | MafRecord) -> MafRecord:
    if isinstance(source, MafRecord):
        return source
    if isinstance(source, str):
        return get_maf(source)
    raise TypeError("source must be a catalog ID or MafRecord")


def _resolve_pullup_resistance(request: ScalingRequest) -> float:
    if request.pullup_resistance_ohms is not None:
        return require_positive(request.pullup_resistance_ohms, "pullup_resistance_ohms")
    if request.ecu_preset is None:
        raise ValueError("provide an ECU preset or pullup_resistance_ohms")
    preset = request.ecu_preset.upper()
    try:
        return ELECTRICAL_PRESETS_OHMS[preset]
    except KeyError as error:
        supported = ", ".join(ELECTRICAL_PRESETS_OHMS)
        raise ValueError(f"unknown ECU preset {request.ecu_preset!r}; use {supported}") from error


def _extended_curve(record: MafRecord) -> tuple[tuple[float, ...], tuple[float, ...]]:
    source_x = np.asarray(CANONICAL_VOLTAGES_V, dtype=float)
    source_y = np.asarray(record.flow_values_kg_per_hr, dtype=float)
    coefficients = np.polyfit(source_x, source_y, deg=4)
    extension_y = np.polyval(coefficients, np.asarray(EXTENSION_VOLTAGES_V, dtype=float))
    return (
        CANONICAL_VOLTAGES_V + EXTENSION_VOLTAGES_V,
        record.flow_values_kg_per_hr + tuple(float(value) for value in extension_y),
    )


def _adjust_voltages(
    source_voltages_v: Sequence[float],
    pullup_resistance_ohms: float,
    series_resistance_ohms: float,
) -> tuple[float, ...]:
    ratio = pullup_resistance_ohms / (series_resistance_ohms + pullup_resistance_ohms)
    return tuple(round_half_up(voltage * ratio, 3) for voltage in source_voltages_v)


def scale_maf(request: ScalingRequest) -> ScalingResult:
    if not isinstance(request, ScalingRequest):
        raise TypeError("scale_maf expects a ScalingRequest")
    record = _resolve_record(request.source)
    pullup = _resolve_pullup_resistance(request)
    series = require_nonnegative(request.series_resistance_ohms, "series_resistance_ohms")
    source_diameter_mm = normalize_diameter_mm(
        request.source_tube_diameter, request.diameter_unit
    )
    target_diameter_mm = normalize_diameter_mm(
        request.target_tube_diameter, request.diameter_unit
    )
    multiplier = require_finite(request.final_flow_multiplier, "final_flow_multiplier")
    diameter_factor = (target_diameter_mm / source_diameter_mm) ** 2

    extended_voltages, extended_flows = _extended_curve(record)
    adjusted_voltages = _adjust_voltages(extended_voltages, pullup, series)
    scaled_flows = tuple(flow * diameter_factor for flow in extended_flows)
    interpolated = resample_curve(adjusted_voltages, scaled_flows, CANONICAL_VOLTAGES_V)
    output_flows = tuple(flow * multiplier for flow in interpolated)

    return ScalingResult(
        voltage_values_v=CANONICAL_VOLTAGES_V,
        flow_values_kg_per_hr=output_flows,
        source_id=record.id,
        source_display_name=record.display_name,
        pullup_resistance_ohms=pullup,
        series_resistance_ohms=series,
        source_tube_diameter_mm=source_diameter_mm,
        target_tube_diameter_mm=target_diameter_mm,
        diameter_factor=diameter_factor,
        final_flow_multiplier=multiplier,
        warnings=source_curve_warnings(record.flow_values_kg_per_hr),
        algorithm_version=ALGORITHM_VERSION,
    )
