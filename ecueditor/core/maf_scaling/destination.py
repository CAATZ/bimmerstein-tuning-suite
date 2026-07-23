"""Bridge MAF scaling results to definition-backed ECU tables."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from ecueditor.core.mapstudio import QuantizedTableProposal, quantize_table_proposal
from ecueditor.core.rom.table import Table

from .grids import CANONICAL_GRID_ID, CANONICAL_VOLTAGES_V
from .models import DiameterMetadata, MafRecord, ScalingRequest, ScalingResult
from .scaling import scale_maf

KNOWN_MAF_TABLE_NAMES = frozenset(
    {
        "MAF",
        "MAF (1024 kg/hr Mode) (256x1)",
        "MAF (2048 kg/hr Mode) (EDIT)",
    }
)


@dataclass(frozen=True, slots=True)
class MafPreview:
    result: ScalingResult
    proposal: QuantizedTableProposal
    changes: np.ndarray
    floored_count: int
    destination_min: float
    destination_max: float


def is_manual_maf_candidate(table: Table) -> bool:
    """Return whether the current table can safely receive a 256-point curve."""

    table_type = table.definition.type.casefold()
    return (
        len(table.cells) == 256
        and table.definition.storage_address is not None
        and not bool(getattr(table.definition, "locked", False))
        and "switch" not in table_type
    )


def is_known_maf_destination(table: Table) -> bool:
    return table.name.strip() in KNOWN_MAF_TABLE_NAMES and is_manual_maf_candidate(table)


def shape_maf_values(table: Table, values: Sequence[float]) -> np.ndarray:
    """Return a 256-point MAF curve in the destination definition's display shape."""

    shaped = np.asarray(tuple(values), dtype=float)
    if shaped.size != len(table.cells):
        raise ValueError(
            f"MAF source contains {shaped.size} values; destination requires {len(table.cells)}."
        )
    sx, sy = table.shape()
    if table.definition.type == "2D" or sx == 1 or sy == 1:
        return shaped
    return shaped.reshape(sy, sx)


def maf_voltage_axes(
    table: Table,
) -> tuple[tuple[float, ...], tuple[float, ...] | None]:
    """Return canonical voltage headers in the destination's display shape."""

    sx, sy = table.shape()
    if sx * sy != len(CANONICAL_VOLTAGES_V):
        raise ValueError("MAF voltage axes require a 256-cell destination.")
    if table.definition.type == "2D" or sx == 1 or sy == 1:
        return CANONICAL_VOLTAGES_V, None
    return CANONICAL_VOLTAGES_V[:sx], CANONICAL_VOLTAGES_V[::sx]


def _destination_values(table: Table) -> np.ndarray:
    return shape_maf_values(table, [cell.real() for cell in table.cells])


def table_maf_record(table: Table) -> MafRecord:
    """Snapshot a compatible table as a 256-point MAF source curve."""

    if not is_manual_maf_candidate(table):
        raise ValueError("A table MAF source must be editable and contain exactly 256 cells.")
    return MafRecord(
        id="current-table",
        display_name=f"{table.name} (current values)",
        manufacturer=None,
        part_number=None,
        variant=None,
        source_header=table.name,
        voltage_unit="V",
        flow_unit="kg/hr",
        voltage_grid_id=CANONICAL_GRID_ID,
        flow_values_kg_per_hr=tuple(
            float(value) for value in _destination_values(table).reshape(-1)
        ),
        source_tube_diameter=DiameterMetadata(None, None, "unknown", None),
        default_tube_diameter_in=3.0,
        source_workbook_filename="",
        source_sheet="",
        source_cell_range="",
        source_workbook_sha256="",
        data_sha256="",
        notes=("Snapshot of the current definition-scaled table values.",),
        uncertainty=("Source inside diameter must be supplied by the user.",),
    )


def _destination_range(table: Table) -> tuple[float, float]:
    bounds = [
        value
        for cell in table.cells
        for value in (
            cell.scale.to_real(cell.storage_min),
            cell.scale.to_real(cell.storage_max),
        )
    ]
    return float(min(bounds)), float(max(bounds))


def build_maf_preview(
    table: Table,
    request: ScalingRequest,
    *,
    floor_negative: bool = False,
) -> MafPreview:
    if not is_manual_maf_candidate(table):
        raise ValueError("MAF Scaling requires an editable numeric table with exactly 256 cells.")

    result = scale_maf(request)
    flat = np.asarray(result.flow_values_kg_per_hr, dtype=float)
    floored_count = int(np.count_nonzero(flat < 0)) if floor_negative else 0
    if floor_negative:
        flat = np.maximum(flat, 0.0)

    values = shape_maf_values(table, flat)
    proposal = quantize_table_proposal(table, values)
    current = _destination_values(table)
    destination_min, destination_max = _destination_range(table)
    return MafPreview(
        result=result,
        proposal=proposal,
        changes=proposal.values - current,
        floored_count=floored_count,
        destination_min=destination_min,
        destination_max=destination_max,
    )
