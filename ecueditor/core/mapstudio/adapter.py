from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ecueditor.core.rom.table import Table

from .model import CurveData, MapData, MapValidationError, validate_axis, validate_map_axis


@dataclass(frozen=True)
class TableSnapshot:
    name: str
    kind: str
    x: np.ndarray
    y: np.ndarray | None
    values: np.ndarray
    fingerprint: tuple[object, ...]
    x_editable: bool
    y_editable: bool
    locked: bool

    def as_map(self) -> MapData:
        if self.kind != "map" or self.y is None:
            raise MapValidationError("This table is not a two-axis map.")
        return MapData(self.x, self.y, self.values, self.name)

    def as_curve(self) -> CurveData:
        if self.kind != "curve":
            raise MapValidationError("This table is not a curve.")
        return CurveData(self.x, self.values.reshape(-1), self.name)


@dataclass(frozen=True)
class QuantizedTableProposal:
    values: np.ndarray
    data_raw: np.ndarray
    x: np.ndarray | None
    x_raw: np.ndarray | None
    y: np.ndarray | None
    y_raw: np.ndarray | None


def _axis_values(axis: Table | None, size: int) -> np.ndarray:
    if axis is None:
        return np.arange(size, dtype=float)
    return np.asarray([cell.real() for cell in axis.cells[:size]], dtype=float)


def _axis_editable(axis: Table | None) -> bool:
    return bool(axis is not None and axis.definition.storage_address is not None)


def _curve_axis(table: Table, sx: int, sy: int) -> Table | None:
    """Return the physical axis that represents the curve's varying dimension."""
    if sx == 1 and sy > 1:
        preferred = (table.y_axis, table.x_axis)
    elif sy == 1 and sx > 1:
        preferred = (table.x_axis, table.y_axis)
    else:
        preferred = (table.x_axis, table.y_axis)
    expected = len(table.cells)
    for axis in preferred:
        if axis is not None and len(axis.cells) == expected:
            return axis
    return next((axis for axis in preferred if axis is not None), None)


def fingerprint_table(table: Table) -> tuple[object, ...]:
    return (
        tuple(int(cell.raw) for cell in table.cells),
        None if table.x_axis is None else tuple(cell.raw for cell in table.x_axis.cells),
        None if table.y_axis is None else tuple(cell.raw for cell in table.y_axis.cells),
    )


def snapshot_table(table: Table) -> TableSnapshot:
    sx, sy = table.shape()
    values = np.asarray([cell.real() for cell in table.cells], dtype=float)
    locked = bool(getattr(table.definition, "locked", False))
    if table.definition.type == "2D" or sx == 1 or sy == 1:
        axis = _curve_axis(table, sx, sy)
        x = _axis_values(axis, values.size)
        return TableSnapshot(
            table.name,
            "curve",
            x,
            None,
            values,
            fingerprint_table(table),
            _axis_editable(axis),
            False,
            locked,
        )
    return TableSnapshot(
        table.name,
        "map",
        _axis_values(table.x_axis, sx),
        _axis_values(table.y_axis, sy),
        values.reshape(sy, sx),
        fingerprint_table(table),
        _axis_editable(table.x_axis),
        _axis_editable(table.y_axis),
        locked,
    )


def _quantize_cells(cells, values: np.ndarray, label: str) -> tuple[np.ndarray, np.ndarray]:
    flat = np.asarray(values, dtype=float).reshape(-1)
    if flat.size != len(cells):
        raise MapValidationError(f"{label} dimensions do not match the destination table.")
    if not np.all(np.isfinite(flat)):
        raise MapValidationError(f"{label} contains a non-finite value.")
    raw = np.empty(flat.size, dtype=int)
    real = np.empty(flat.size, dtype=float)
    for index, (cell, value) in enumerate(zip(cells, flat)):
        candidate = int(round(cell.scale.to_raw(float(value))))
        if candidate < cell.storage_min or candidate > cell.storage_max:
            raise MapValidationError(
                f"{label} value {value:.12g} is outside its storage range."
            )
        raw[index] = candidate
        real[index] = cell.scale.to_real(candidate)
    return raw, real


def _quantize_axis(
    axis: Table | None,
    values,
    label: str,
    *,
    allow_unchanged_padding: bool = False,
) -> tuple[np.ndarray | None, np.ndarray | None]:
    if values is None:
        return None, None
    if axis is None or axis.definition.storage_address is None:
        raise MapValidationError(f"{label} is read-only and cannot be changed.")
    raw, real = _quantize_cells(axis.cells, np.asarray(values), label)
    try:
        validate_axis(real, label)
    except MapValidationError as exc:
        requested = np.asarray(values, dtype=float).reshape(-1)
        current_real = np.asarray([cell.real() for cell in axis.cells], dtype=float)
        current_raw = np.asarray([cell.raw for cell in axis.cells], dtype=int)
        unchanged_padding = (
            allow_unchanged_padding
            and np.array_equal(raw, current_raw)
            and np.allclose(requested, current_real, rtol=1e-12, atol=1e-12)
        )
        if unchanged_padding:
            validate_map_axis(real, label)
            return raw, real
        raise MapValidationError(
            f"{label} must remain strictly monotonic after storage quantization."
        ) from exc
    return raw, real


def quantize_table_proposal(
    table: Table,
    values,
    *,
    x=None,
    y=None,
) -> QuantizedTableProposal:
    sx, sy = table.shape()
    array = np.asarray(values, dtype=float)
    expected = (sy, sx)
    shape: tuple[int, ...]
    if table.definition.type == "2D" or sx == 1 or sy == 1:
        if array.size != len(table.cells):
            raise MapValidationError("Result dimensions do not match the destination table.")
        shape = (len(table.cells),)
        axis = _curve_axis(table, sx, sy)
        x_raw, x_real = _quantize_axis(
            axis,
            x,
            "X axis",
            allow_unchanged_padding=True,
        )
        y_raw = y_real = None
    else:
        if array.shape != expected:
            raise MapValidationError(
                f"Result dimensions must be {expected[0]} rows by {expected[1]} columns."
            )
        shape = expected
        x_raw, x_real = _quantize_axis(
            table.x_axis,
            x,
            "X axis",
            allow_unchanged_padding=True,
        )
        y_raw, y_real = _quantize_axis(
            table.y_axis,
            y,
            "Y axis",
            allow_unchanged_padding=True,
        )
    data_raw, data_real = _quantize_cells(table.cells, array, "Table result")
    return QuantizedTableProposal(
        data_real.reshape(shape),
        data_raw.reshape(shape),
        x_real,
        x_raw,
        y_real,
        y_raw,
    )
