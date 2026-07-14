from __future__ import annotations
from contextlib import contextmanager
from collections.abc import Iterator

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor
from ecueditor.core.scaling.scale import Scale
from ecueditor.core.rom.table import Table
from ecueditor.ui.design.colormaps import heat_color, text_color_for
from ecueditor.ui.design.theme_manager import current_theme


_HistoryKey = tuple[str, int, int]


class TableGridModel(QAbstractTableModel):
    def __init__(self, table: Table, parent=None, presentation_transposed: bool = False) -> None:
        super().__init__(parent)
        self._table = table
        self._pt = presentation_transposed
        self.scales: list[Scale] = [table.cells[0].scale, Scale(format="0")]   # primary + Raw Value ("0" => raw shown verbatim)
        self._scale_ix = 0
        self._colormap = "viridis"
        self._color_cells = True
        self._compare_source = None        # None | "original" | Table
        self._compare_mode = "absolute"    # "absolute" | "percent"
        self._bounds_cache: tuple[float, float] | None = None
        self._live_index: QModelIndex | None = None   # live-overlay cell (model-driven, not selection)
        self._undo_stack: list[dict[_HistoryKey, int]] = []
        self._edit_depth = 0
        self._pending_edit: dict[_HistoryKey, int] | None = None
        self._restoring_history = False
        # Belt-and-suspenders: every beginResetModel()/endResetModel() pair -- whether triggered
        # from inside this class (set_scale) or externally (clipboard.undo_all /
        # undo_selected / [TableND] paste, main_window._refresh_rom_views) -- fires
        # modelAboutToBeReset first, so this is the one place that reliably invalidates the cache
        # regardless of caller.
        self.modelAboutToBeReset.connect(self._invalidate_bounds_cache)

    def _invalidate_bounds_cache(self) -> None:
        self._bounds_cache = None

    @property
    def table(self) -> Table:
        return self._table

    @property
    def current_scale(self) -> Scale:
        return self.scales[self._scale_ix]

    def set_scale(self, index: int) -> None:
        self.beginResetModel()
        self._scale_ix = max(0, min(len(self.scales) - 1, index))
        self._bounds_cache = None            # bounds are in the new scale's real units
        self.endResetModel()

    @property
    def colormap(self) -> str:
        return self._colormap

    def set_colormap(self, name: str) -> None:
        self._colormap = name
        self._recolor()

    def _recolor(self) -> None:
        """Repaint after a color change without resetting geometry or values."""
        if self.rowCount() and self.columnCount():
            self.dataChanged.emit(self.index(0, 0),
                                  self.index(self.rowCount() - 1, self.columnCount() - 1),
                                  [Qt.BackgroundRole, Qt.ForegroundRole])

    # --- geometry ------------------------------------------------------------
    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else (self._table.shape()[0] if self._pt else self._table.shape()[1])

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else (self._table.shape()[1] if self._pt else self._table.shape()[0])

    def cell_xy(self, index) -> tuple[int, int]:
        """Map a view index to table coordinates, honoring presentation transpose."""
        if self._pt:
            return index.row(), index.column()      # transposed: view row is table x
        return index.column(), index.row()

    def index_for_cell(self, x: int, y: int):
        """Table (x, y) -> view QModelIndex, honoring presentation transpose."""
        return self.index(x, y) if self._pt else self.index(y, x)

    def _cell(self, index: QModelIndex):
        x, y = self.cell_xy(index)
        return self._table.cell_at(x, y)

    # --- data ----------------------------------------------------------------
    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid():
            return None
        cell = self._cell(index)
        real = self.current_scale.to_real(cell.raw)
        if role == Qt.DisplayRole:
            return self.current_scale.format_value(real)
        if role == Qt.EditRole:
            return real
        if role == Qt.ToolTipRole and cell.is_changed():
            original = self.current_scale.to_real(cell.original)
            delta = real - original
            direction = "Higher" if delta > 0 else "Lower"
            return (
                f"{direction} than revert point by {delta:+g} "
                f"({self.current_scale.format_value(original)} → "
                f"{self.current_scale.format_value(real)})"
            )
        if role == Qt.TextAlignmentRole:
            return int(Qt.AlignRight | Qt.AlignVCenter)
        if role == Qt.ForegroundRole:
            bg = self.background_color(index)
            if bg is None:
                return None
            return QColor(*text_color_for((bg.red(), bg.green(), bg.blue())))
        return None

    def setData(self, index: QModelIndex, value, role: int = Qt.EditRole) -> bool:
        if role != Qt.EditRole or not index.isValid():
            return False
        try:
            raw = round(self.current_scale.to_raw(float(value)))
        except (TypeError, ValueError):
            return False
        automatic_group = self._edit_depth == 0 and not self._restoring_history
        if automatic_group:
            self.begin_edit_group()
        self._record_before(index)
        self._cell(index).set_raw(raw, clamp=True)      # clamp on entry (fact base 1.1 / spec 5.1)
        self._bounds_cache = None            # edit may have moved the table's min/max
        self.dataChanged.emit(index, index)
        if automatic_group:
            self.end_edit_group()
        return True

    def flags(self, index: QModelIndex):
        base = Qt.ItemIsSelectable | Qt.ItemIsEnabled
        if getattr(self._table.definition, "locked", False):
            return base
        return base | Qt.ItemIsEditable

    # --- axis headers --------------------------------------------------------
    def _axis_for_orientation(self, orientation) -> tuple[str, Table | None]:
        if self._pt:
            orientation = (
                Qt.Orientation.Vertical
                if orientation == Qt.Orientation.Horizontal
                else Qt.Orientation.Horizontal
            )
        if orientation == Qt.Orientation.Horizontal:
            return "x", self._table.x_axis
        return "y", self._table.y_axis

    def axis_is_editable(self, section: int, orientation) -> bool:
        _role, axis = self._axis_for_orientation(orientation)
        return bool(
            axis is not None
            and axis.definition.storage_address is not None
            and 0 <= section < len(axis.cells)
            and not getattr(self._table.definition, "locked", False)
        )

    def headerData(self, section: int, orientation, role: int = Qt.DisplayRole):
        logical_orientation = orientation
        _axis_role, axis = self._axis_for_orientation(orientation)
        if axis is None:
            if role != Qt.DisplayRole:
                return None
            # Check definition axis for static_values (addendum requirement)
            if self._pt:
                logical_orientation = (
                    Qt.Orientation.Vertical
                    if orientation == Qt.Orientation.Horizontal
                    else Qt.Orientation.Horizontal
                )
            def_axis = (
                self._table.definition.x_axis
                if logical_orientation == Qt.Orientation.Horizontal
                else self._table.definition.y_axis
            )
            if def_axis is not None and def_axis.static_values is not None:
                if section < len(def_axis.static_values):
                    return str(def_axis.static_values[section])
            return str(section)
        if not 0 <= section < len(axis.cells):
            return None
        acell = axis.cell_at(section, 0)
        if role == Qt.DisplayRole:
            return acell.scale.format_value(acell.real())
        if role == Qt.EditRole:
            return acell.real()
        if role == Qt.ToolTipRole and self.axis_is_editable(section, orientation):
            return "Double-click to edit this axis value"
        if role == Qt.TextAlignmentRole:
            return int(Qt.AlignRight | Qt.AlignVCenter)
        return None

    def setHeaderData(self, section: int, orientation, value,
                      role: int = Qt.EditRole) -> bool:
        if role != Qt.EditRole or not self.axis_is_editable(section, orientation):
            return False
        axis_role, axis = self._axis_for_orientation(orientation)
        assert axis is not None
        cell = axis.cell_at(section, 0)
        try:
            raw = round(cell.scale.to_raw(float(value)))
        except (TypeError, ValueError):
            return False
        automatic_group = self._edit_depth == 0 and not self._restoring_history
        if automatic_group:
            self.begin_edit_group()
        key: _HistoryKey = (axis_role, section, 0)
        self._record_history_before(key)
        cell.set_raw(raw, clamp=True)
        self.headerDataChanged.emit(orientation, section, section)
        if automatic_group:
            self.end_edit_group()
        return True

    # --- raw access (used by edit ops) --------------------------------------
    def raw_at(self, index: QModelIndex) -> int:
        return self._cell(index).raw

    def set_raw_at(self, index: QModelIndex, value: int) -> None:
        automatic_group = self._edit_depth == 0 and not self._restoring_history
        if automatic_group:
            self.begin_edit_group()
        self._record_before(index)
        self._cell(index).set_raw(value, clamp=True)
        self._bounds_cache = None            # edit may have moved the table's min/max
        self.dataChanged.emit(index, index)
        if automatic_group:
            self.end_edit_group()

    def refresh_from_table(self) -> None:
        """Repaint values and axes after another view edits aliased ROM storage."""
        self._bounds_cache = None
        rows, columns = self.rowCount(), self.columnCount()
        if rows and columns:
            self.dataChanged.emit(self.index(0, 0), self.index(rows - 1, columns - 1))
        if columns:
            self.headerDataChanged.emit(Qt.Orientation.Horizontal, 0, columns - 1)
        if rows:
            self.headerDataChanged.emit(Qt.Orientation.Vertical, 0, rows - 1)

    # --- incremental edit history ------------------------------------------
    def begin_edit_group(self, *, capture_all: bool = False) -> None:
        """Begin one user-visible edit operation, nesting safely across helper layers."""
        if self._restoring_history:
            return
        if self._edit_depth == 0:
            self._pending_edit = {}
        self._edit_depth += 1
        if capture_all and self._pending_edit is not None:
            for y in range(self._table.shape()[1]):
                for x in range(self._table.shape()[0]):
                    self._pending_edit.setdefault(("data", x, y), self._table.cell_at(x, y).raw)
            for role, axis in (("x", self._table.x_axis), ("y", self._table.y_axis)):
                if axis is not None:
                    for section, cell in enumerate(axis.cells):
                        self._pending_edit.setdefault((role, section, 0), cell.raw)

    def end_edit_group(self) -> None:
        if self._restoring_history or self._edit_depth == 0:
            return
        self._edit_depth -= 1
        if self._edit_depth:
            return
        before = self._pending_edit or {}
        changed = {
            key: raw
            for key, raw in before.items()
            if self._history_cell(key).raw != raw
        }
        self._pending_edit = None
        if changed:
            self._undo_stack.append(changed)
            del self._undo_stack[:-200]

    @contextmanager
    def edit_group(self, *, capture_all: bool = False) -> Iterator[None]:
        self.begin_edit_group(capture_all=capture_all)
        try:
            yield
        finally:
            self.end_edit_group()

    def _record_before(self, index: QModelIndex) -> None:
        xy = self.cell_xy(index)
        self._record_history_before(("data", xy[0], xy[1]))

    def _record_history_before(self, key: _HistoryKey) -> None:
        if self._restoring_history or self._pending_edit is None:
            return
        self._pending_edit.setdefault(key, self._history_cell(key).raw)

    def _history_cell(self, key: _HistoryKey):
        role, first, second = key
        if role == "data":
            return self._table.cell_at(first, second)
        axis = self._table.x_axis if role == "x" else self._table.y_axis
        if axis is None:
            raise IndexError(f"missing {role}-axis for edit history")
        return axis.cell_at(first, 0)

    def undo_depth(self) -> int:
        return len(self._undo_stack)

    def clear_undo_history(self) -> None:
        self._undo_stack.clear()
        self._pending_edit = None
        self._edit_depth = 0

    def undo_last(self) -> bool:
        """Restore the cells touched by the most recent grouped operation."""
        if not self._undo_stack:
            return False
        before = self._undo_stack.pop()
        self._restoring_history = True
        self.beginResetModel()
        try:
            for key, raw in before.items():
                self._history_cell(key).set_raw(raw, clamp=True)
        finally:
            self.endResetModel()
            self._restoring_history = False
        return True

    # --- coloring (heat-map, warning, borders) --------------------------------
    def set_color_cells(self, on: bool) -> None:
        self._color_cells = on
        self._recolor()

    def set_compare_original(self) -> None:
        self._compare_source = "original"; self._recolor()

    def set_compare_table(self, table) -> None:
        self._compare_source = table; self._recolor()

    def set_compare_mode(self, mode: str) -> None:
        self._compare_mode = mode; self._recolor()

    def compare_off(self) -> None:
        self._compare_source = None; self._recolor()

    def _compare_reference_real(self, index: QModelIndex):
        cell = self._cell(index)
        if self._compare_source == "original":
            return self.current_scale.to_real(cell.original)
        if self._compare_source is None:
            return None
        other = self._compare_source
        ex, ey = other.shape()
        x, y = self.cell_xy(index)
        if x >= ex or y >= ey:
            return None
        return self.current_scale.to_real(other.cell_at(x, y).raw)

    def compare_delta(self, index: QModelIndex):
        ref = self._compare_reference_real(index)
        if ref is None:
            return None
        cur = self.current_scale.to_real(self._cell(index).raw)
        if self._compare_mode == "percent":
            return 0.0 if ref == 0 else (cur - ref) / abs(ref)     # fact base 1.1 getRealCompareChangeValue
        return cur - ref

    def _real_bounds(self) -> tuple[float, float]:
        if self._bounds_cache is None:
            reals = [self.current_scale.to_real(c.raw) for c in self._table.cells]
            self._bounds_cache = (min(reals), max(reals)) if reals else (0.0, 0.0)
        return self._bounds_cache

    def real_bounds(self) -> tuple[float, float]:
        """Public alias for the cached (min, max) real bounds (legend, 3D)."""
        return self._real_bounds()

    def heat_ratio(self, index: QModelIndex) -> float:
        lo, hi = self._real_bounds()
        if hi == lo:
            return 0.0
        return (self.current_scale.to_real(self._cell(index).raw) - lo) / (hi - lo)

    def background_color(self, index: QModelIndex):
        t = current_theme()
        if self._compare_source is not None:
            d = self.compare_delta(index)
            if d is None or d == 0:
                return QColor(t.compare_neutral)
            return QColor(t.increase_border) if d > 0 else QColor(t.decrease_border)
        if not self._color_cells:
            return None
        heat = heat_color(self.heat_ratio(index), self._colormap)
        return QColor(*heat)

    def change_border(self, index: QModelIndex):
        cell = self._cell(index)
        if not cell.is_changed():
            return None
        return "increase" if cell.raw > cell.original else "decrease"

    # --- live overlay (Phase-4 LiveOverlayBridge; model-driven, not selection) ---
    def set_live_cell(self, index) -> None:
        self._live_index = index if (index is not None and index.isValid()) else None

    def is_live_cell(self, index: QModelIndex) -> bool:
        return (self._live_index is not None
                and self._live_index.row() == index.row()
                and self._live_index.column() == index.column())
