"""Immutable data models for MAF scaling."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

DiameterType = Literal["inside", "outside", "unknown"]
DiameterUnit = Literal["mm", "cm", "m", "inch", "in"]


@dataclass(frozen=True, slots=True)
class DiameterMetadata:
    value: float | None
    unit: str | None
    diameter_type: DiameterType
    source_text: str | None
    uncertainty: str | None = None


@dataclass(frozen=True, slots=True)
class MafRecord:
    id: str
    display_name: str
    manufacturer: str | None
    part_number: str | None
    variant: str | None
    source_header: str
    voltage_unit: str
    flow_unit: str
    voltage_grid_id: str
    flow_values_kg_per_hr: tuple[float, ...]
    source_tube_diameter: DiameterMetadata
    default_tube_diameter_in: float
    source_workbook_filename: str
    source_sheet: str
    source_cell_range: str
    source_workbook_sha256: str
    data_sha256: str
    notes: tuple[str, ...]
    uncertainty: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ScalingRequest:
    source: str | MafRecord
    source_tube_diameter: float
    target_tube_diameter: float
    diameter_unit: DiameterUnit
    ecu_preset: str | None = "MS41"
    pullup_resistance_ohms: float | None = None
    series_resistance_ohms: float = 0.0
    final_flow_multiplier: float = 1.0


@dataclass(frozen=True, slots=True)
class ScalingResult:
    voltage_values_v: tuple[float, ...]
    flow_values_kg_per_hr: tuple[float, ...]
    source_id: str
    source_display_name: str
    pullup_resistance_ohms: float
    series_resistance_ohms: float
    source_tube_diameter_mm: float
    target_tube_diameter_mm: float
    diameter_factor: float
    final_flow_multiplier: float
    warnings: tuple[str, ...]
    algorithm_version: str
