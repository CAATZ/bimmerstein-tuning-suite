from __future__ import annotations
from PySide6.QtCore import Qt, QModelIndex
from ecueditor.ui.editor.table_model import TableGridModel

def set_value(model: TableGridModel, indexes, real: float) -> None:
    with model.edit_group():
        for idx in indexes:
            model.setData(idx, real, Qt.EditRole)

def _transform(model: TableGridModel, indexes, operation) -> None:
    with model.edit_group():
        for idx in indexes:
            current = model.data(idx, Qt.EditRole)
            model.setData(idx, operation(current), Qt.EditRole)

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
            model.setData(idx, model.data(idx, Qt.EditRole) + step, Qt.EditRole)
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
    a = model.data(cells[0], Qt.EditRole)
    b = model.data(cells[-1], Qt.EditRole)
    n = len(cells) - 1
    for i, idx in enumerate(cells[1:-1], start=1):
        model.setData(idx, a + (b - a) * i / n, Qt.EditRole)

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
        q00 = model.data(model.index(r0, c0), Qt.EditRole)
        q01 = model.data(model.index(r0, c1), Qt.EditRole)
        q10 = model.data(model.index(r1, c0), Qt.EditRole)
        q11 = model.data(model.index(r1, c1), Qt.EditRole)
        for r in range(r0, r1 + 1):
            ty = (r - r0) / (r1 - r0)
            for c in range(c0, c1 + 1):
                tx = (c - c0) / (c1 - c0)
                top = q00 + (q01 - q00) * tx
                bot = q10 + (q11 - q10) * tx
                model.setData(model.index(r, c), top + (bot - top) * ty, Qt.EditRole)
