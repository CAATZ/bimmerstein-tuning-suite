from __future__ import annotations
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from ecueditor.ui.editor.table_model import TableGridModel

def _clip():
    return QApplication.clipboard()

def copy_table(model: TableGridModel) -> None:
    _clip().setText(model.table.to_text())

def copy_selection(model: TableGridModel, indexes) -> None:
    if not indexes:
        return
    rows = sorted({i.row() for i in indexes}); cols = sorted({i.column() for i in indexes})
    sel = {(i.row(), i.column()) for i in indexes}
    dim = "3D" if model.table.shape()[1] > 1 else "1D"
    lines = [f"[Selection{dim}]"]
    for r in rows:
        cells = []
        for c in cols:
            if (r, c) in sel:
                cells.append(model.current_scale.format_value(model.current_scale.to_real(model.raw_at(model.index(r, c)))))
            else:
                cells.append("x")           # RomRaider gap placeholder
        lines.append("\t".join(cells))
    _clip().setText("\n".join(lines))

def paste(model: TableGridModel, indexes) -> list:
    text = _clip().text()
    if not text.strip():
        return []
    anchor = min((i.row() * model.columnCount() + i.column() for i in indexes), default=0)
    anchor_row, anchor_col = anchor // model.columnCount(), anchor % model.columnCount()
    stripped = text.strip()
    touched: set[tuple[int, int]] = set()
    if stripped.startswith("[Selection"):
        # [SelectionND] blocks are display-scale data -- copy_selection formats each value via
        # model.current_scale. Core's paste_text only parses the [TableND] golden format (it
        # strips a "[Table"-prefixed header and expects an axis row / leading y values), so it
        # can't be reused here without silently shifting or corrupting values. Parse the
        # selection block in the UI instead and write through model.setData, which converts via
        # model.current_scale.to_raw -- the exact inverse of how copy_selection formatted it, so
        # a copy/paste round-trip is correct under whichever scale is active (including "Raw
        # Value").
        rows, cols = model.rowCount(), model.columnCount()
        with model.edit_group():
            for r, line in enumerate(stripped.splitlines()[1:]):
                row = anchor_row + r
                if row >= rows:
                    continue
                for c, token in enumerate(line.split("\t")):
                    col = anchor_col + c
                    if col >= cols or token == "x":     # RomRaider gap placeholder
                        continue
                    try:
                        value = float(token)
                    except ValueError:
                        continue                        # malformed token: skip, don't crash
                    idx = model.index(row, col)
                    touched.add((row, col))
                    # Idempotent paste: copy_selection formats at display precision (lossy), so
                    # round-tripping an unchanged value via to_raw can land on a neighbour byte.
                    if model.current_scale.format_value(value) == model.data(idx, Qt.DisplayRole):
                        continue
                    model.setData(idx, value, Qt.EditRole)
    else:
        # core parses [TableND] only (golden format pinned in Phase 1)
        before = {
            (row, col): model.raw_at(model.index(row, col))
            for row in range(model.rowCount())
            for col in range(model.columnCount())
        }
        with model.edit_group(capture_all=True):
            model.beginResetModel()
            model.table.paste_text(text, anchor=anchor)
            model.endResetModel()
        if stripped.startswith("[Table"):
            # Copy Table represents the full data grid even when some formatted values
            # round-trip idempotently. Keep the complete pasted footprint visible.
            touched.update(before)
        else:
            touched.update(
                (row, col)
                for (row, col), raw in before.items()
                if model.raw_at(model.index(row, col)) != raw
            )
    return [model.index(row, col) for row, col in sorted(touched)]

def undo_selected(model: TableGridModel, indexes) -> None:
    model.beginResetModel()
    for idx in indexes:
        x, y = model.cell_xy(idx)
        model.table.cell_at(x, y).undo()
    model.endResetModel()
    model.clear_undo_history()

def undo_all(model: TableGridModel) -> None:
    model.beginResetModel(); model.table.undo_all(); model.endResetModel()
    model.clear_undo_history()

def set_revert_point(model: TableGridModel) -> None:
    model.beginResetModel()
    model.table.set_revert_point()
    model.endResetModel()
    model.clear_undo_history()
