from __future__ import annotations
from PySide6.QtCore import Qt, QModelIndex
from ecueditor.ui.editor.table_model import TableGridModel

def set_value(model: TableGridModel, indexes, real: float) -> None:
    with model.edit_group():
        for idx in indexes:
            model.setData(idx, real, Qt.ItemDataRole.EditRole)

def _transform(model: TableGridModel, indexes, operation) -> None:
    with model.edit_group():
        for idx in indexes:
            current = model.data(idx, Qt.ItemDataRole.EditRole)
            model.setData(idx, operation(current), Qt.ItemDataRole.EditRole)

def add(model: TableGridModel, indexes, amount: float) -> None:
    _transform(model, indexes, lambda current: current + amount)

def subtract(model: TableGridModel, indexes, amount: float) -> None:
    _transform(model, indexes, lambda current: current - amount)

def multiply(model: TableGridModel, indexes, factor: float) -> None:
    _transform(model, indexes, lambda current: current * factor)

def divide(model: TableGridModel, indexes, divisor: float) -> None:
    if divisor == 0:
        raise ValueError("cannot divide selected cells by zero")
    _transform(model, indexes, lambda current: current / divisor)

def increase_percent(model: TableGridModel, indexes, percent: float) -> None:
    multiply(model, indexes, 1.0 + percent / 100.0)

def decrease_percent(model: TableGridModel, indexes, percent: float) -> None:
    multiply(model, indexes, 1.0 - percent / 100.0)

def _increment(model: TableGridModel, indexes, *, coarse: bool, sign: int) -> None:
    scale = model.current_scale
    step = (scale.coarse_increment if coarse else scale.fine_increment) * sign
    slope = scale.to_real(1) - scale.to_real(0)
    raw_dir = sign if slope >= 0 else -sign
    with model.edit_group():
        for idx in indexes:
            before = model.raw_at(idx)
            cell = model.table.cell_at(*model.cell_xy(idx))
            model.setData(
                idx,
                model.data(idx, Qt.ItemDataRole.EditRole) + step,
                Qt.ItemDataRole.EditRole,
            )
            if model.raw_at(idx) == before and step != 0:
                bound = cell.storage_max if raw_dir > 0 else cell.storage_min
                if before == bound:
                    continue
                model.set_raw_at(idx, before + raw_dir)

def increment_fine(model, indexes):    _increment(model, indexes, coarse=False, sign=+1)
def decrement_fine(model, indexes):    _increment(model, indexes, coarse=False, sign=-1)
def increment_coarse(model, indexes):  _increment(model, indexes, coarse=True,  sign=+1)
def decrement_coarse(model, indexes):  _increment(model, indexes, coarse=True,  sign=-1)

def _lerp_line(model: TableGridModel, cells: list[QModelIndex]) -> None:
    """Linearly interpolate real values between the first and last index of an ordered run."""
    if len(cells) < 3:
        return
    a = model.data(cells[0], Qt.ItemDataRole.EditRole)
    b = model.data(cells[-1], Qt.ItemDataRole.EditRole)
    n = len(cells) - 1
    for i, idx in enumerate(cells[1:-1], start=1):
        model.setData(idx, a + (b - a) * i / n, Qt.ItemDataRole.EditRole)

def interpolate_horizontal(model: TableGridModel, indexes) -> None:
    with model.edit_group():
        rows: dict[int, list[QModelIndex]] = {}
        for idx in indexes:
            rows.setdefault(idx.row(), []).append(idx)
        for row in rows.values():
            _lerp_line(model, sorted(row, key=lambda i: i.column()))

def interpolate_vertical(model: TableGridModel, indexes) -> None:
    with model.edit_group():
        cols: dict[int, list[QModelIndex]] = {}
        for idx in indexes:
            cols.setdefault(idx.column(), []).append(idx)
        for col in cols.values():
            _lerp_line(model, sorted(col, key=lambda i: i.row()))

def interpolate_2d(model: TableGridModel, indexes) -> None:
    """Bilinear fill of the selection's bounding rectangle from its four corner cells."""
    with model.edit_group():
        rows = sorted({i.row() for i in indexes}); cols = sorted({i.column() for i in indexes})
        if len(rows) < 2 or len(cols) < 2:
            interpolate_horizontal(model, indexes); return
        r0, r1, c0, c1 = rows[0], rows[-1], cols[0], cols[-1]
        q00 = model.data(model.index(r0, c0), Qt.ItemDataRole.EditRole)
        q01 = model.data(model.index(r0, c1), Qt.ItemDataRole.EditRole)
        q10 = model.data(model.index(r1, c0), Qt.ItemDataRole.EditRole)
        q11 = model.data(model.index(r1, c1), Qt.ItemDataRole.EditRole)
        for r in range(r0, r1 + 1):
            ty = (r - r0) / (r1 - r0)
            for c in range(c0, c1 + 1):
                tx = (c - c0) / (c1 - c0)
                top = q00 + (q01 - q00) * tx
                bot = q10 + (q11 - q10) * tx
                model.setData(
                    model.index(r, c),
                    top + (bot - top) * ty,
                    Qt.ItemDataRole.EditRole,
                )


def _selection_rectangle(indexes) -> tuple[list[int], list[int]]:
    valid = [index for index in indexes if index.isValid()]
    rows = sorted({index.row() for index in valid})
    columns = sorted({index.column() for index in valid})
    if not rows or not columns or len(valid) != len(rows) * len(columns):
        raise ValueError("interpolation needs one contiguous rectangular selection")
    coordinates = {(index.row(), index.column()) for index in valid}
    expected = {(row, column) for row in rows for column in columns}
    if coordinates != expected:
        raise ValueError("interpolation needs one contiguous rectangular selection")
    if rows != list(range(rows[0], rows[-1] + 1)) or columns != list(
        range(columns[0], columns[-1] + 1)
    ):
        raise ValueError("interpolation needs one contiguous rectangular selection")
    return rows, columns


def _header_coordinates(model: TableGridModel, sections: list[int], orientation) -> list[float]:
    coordinates: list[float] = []
    for section in sections:
        value = model.headerData(section, orientation, Qt.ItemDataRole.EditRole)
        try:
            coordinates.append(float(value))
        except (TypeError, ValueError):
            coordinates.append(float(section))
    steps = [second - first for first, second in zip(coordinates, coordinates[1:])]
    if steps and not (all(step > 0 for step in steps) or all(step < 0 for step in steps)):
        raise ValueError("selected axis coordinates must be strictly ordered")
    return coordinates


def _axis_weights(coordinates: list[float]) -> list[float]:
    if len(coordinates) < 2:
        return [0.0] * len(coordinates)
    span = coordinates[-1] - coordinates[0]
    if span == 0:
        raise ValueError("selected axis coordinates must be distinct")
    return [(coordinate - coordinates[0]) / span for coordinate in coordinates]


def interpolate_selection(model: TableGridModel, indexes) -> str:
    """Axis-aware one-button interpolation selected by rectangle geometry.

    A row or column receives a linear endpoint fill.  A two-dimensional rectangle
    is reconstructed from its four corners with bilinear weights.  The operation
    is deliberately rejected for holes/disjoint selections instead of guessing.
    """
    rows, columns = _selection_rectangle(indexes)
    if len(rows) == 1 and len(columns) < 2 or len(columns) == 1 and len(rows) < 2:
        raise ValueError("select at least two cells to interpolate")
    x_weights = _axis_weights(
        _header_coordinates(model, columns, Qt.Orientation.Horizontal)
    )
    y_weights = _axis_weights(_header_coordinates(model, rows, Qt.Orientation.Vertical))
    with model.edit_group():
        if len(rows) == 1:
            first = model.data(
                model.index(rows[0], columns[0]), Qt.ItemDataRole.EditRole
            )
            last = model.data(
                model.index(rows[0], columns[-1]), Qt.ItemDataRole.EditRole
            )
            for column, weight in zip(columns, x_weights):
                model.setData(
                    model.index(rows[0], column),
                    first + (last - first) * weight,
                    Qt.ItemDataRole.EditRole,
                )
            return "horizontal"
        if len(columns) == 1:
            first = model.data(
                model.index(rows[0], columns[0]), Qt.ItemDataRole.EditRole
            )
            last = model.data(
                model.index(rows[-1], columns[0]), Qt.ItemDataRole.EditRole
            )
            for row, weight in zip(rows, y_weights):
                model.setData(
                    model.index(row, columns[0]),
                    first + (last - first) * weight,
                    Qt.ItemDataRole.EditRole,
                )
            return "vertical"
        q00 = model.data(
            model.index(rows[0], columns[0]), Qt.ItemDataRole.EditRole
        )
        q01 = model.data(
            model.index(rows[0], columns[-1]), Qt.ItemDataRole.EditRole
        )
        q10 = model.data(
            model.index(rows[-1], columns[0]), Qt.ItemDataRole.EditRole
        )
        q11 = model.data(
            model.index(rows[-1], columns[-1]), Qt.ItemDataRole.EditRole
        )
        for row, ty in zip(rows, y_weights):
            for column, tx in zip(columns, x_weights):
                top = q00 + (q01 - q00) * tx
                bottom = q10 + (q11 - q10) * tx
                model.setData(
                    model.index(row, column),
                    top + (bottom - top) * ty,
                    Qt.ItemDataRole.EditRole,
                )
    return "bilinear"
