from __future__ import annotations
from contextlib import contextmanager
from collections.abc import Iterator
from typing import Any

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QPersistentModelIndex,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor
from ecueditor.core.scaling.scale import Scale
from ecueditor.core.rom.table import Table
from ecueditor.ui.design.colormaps import heat_color, text_color_for
from ecueditor.ui.design.theme_manager import current_theme


_HistoryKey = tuple[str, int, int]
_UNSET = object()


def _mix_colors(start: QColor, end: QColor, amount: float) -> QColor:
    """Return a deterministic RGB interpolation used by the diverging compare ramp."""
    amount = max(0.0, min(1.0, float(amount)))
    return QColor(
        round(start.red() + (end.red() - start.red()) * amount),
        round(start.green() + (end.green() - start.green()) * amount),
        round(start.blue() + (end.blue() - start.blue()) * amount),
    )


class TableGridModel(QAbstractTableModel):
    editCommitted = Signal()
    historyChanged = Signal(bool, bool)
    colorScaleChanged = Signal()

    def __init__(self, table: Table, parent=None, presentation_transposed: bool = False) -> None:
        super().__init__(parent)
        self._table = table
        self._pt = presentation_transposed
        self.scales: list[Scale] = [table.cells[0].scale, Scale(format="0")]   # primary + Raw Value ("0" => raw shown verbatim)
        self._scale_ix = 0
        self._colormap = "viridis"
        self._color_cells = True
        self._compare_source: Table | str | None = None
        self._compare_mode = "absolute"    # "absolute" | "percent"
        self._bounds_cache: tuple[float, float] | None = None
        self._compare_extent_cache: float | None = None
        self._live_index: QModelIndex | None = None   # live-overlay cell (model-driven, not selection)
        self._undo_stack: list[dict[_HistoryKey, int]] = []
        self._redo_stack: list[dict[_HistoryKey, int]] = []
        self._edit_depth = 0
        self._pending_edit: dict[_HistoryKey, int] | None = None
        self._pending_normalization_before = _UNSET
        self._edit_reset_seen = False
        self._edit_commit_serial = 0
        self._restoring_history = False
        # Belt-and-suspenders: every beginResetModel()/endResetModel() pair -- whether triggered
        # from inside this class (set_scale) or externally (clipboard.undo_all /
        # undo_selected / [TableND] paste, main_window._refresh_rom_views) -- fires
        # modelAboutToBeReset first, so this is the one place that reliably invalidates the cache
        # regardless of caller.
        self.modelAboutToBeReset.connect(self._on_model_about_to_reset)

    def _on_model_about_to_reset(self) -> None:
        if self._edit_depth:
            self._edit_reset_seen = True
        self._invalidate_value_caches()

    def _invalidate_value_caches(self) -> None:
        self._bounds_cache = None
        self._compare_extent_cache = None

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
        if name == self._colormap:
            return
        self._colormap = name
        self._recolor()
        self.colorScaleChanged.emit()

    def _recolor(self) -> None:
        """Repaint after a color change without resetting geometry or values."""
        if self.rowCount() and self.columnCount():
            self.dataChanged.emit(self.index(0, 0),
                                  self.index(self.rowCount() - 1, self.columnCount() - 1),
                                  [Qt.ItemDataRole.BackgroundRole,
                                   Qt.ItemDataRole.ForegroundRole])

    # --- geometry ------------------------------------------------------------
    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else (self._table.shape()[0] if self._pt else self._table.shape()[1])

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else (self._table.shape()[1] if self._pt else self._table.shape()[0])

    def cell_xy(self, index: QModelIndex | QPersistentModelIndex) -> tuple[int, int]:
        """Map a view index to table coordinates, honoring presentation transpose."""
        if self._pt:
            return index.row(), index.column()      # transposed: view row is table x
        return index.column(), index.row()

    def index_for_cell(self, x: int, y: int):
        """Table (x, y) -> view QModelIndex, honoring presentation transpose."""
        return self.index(x, y) if self._pt else self.index(y, x)

    def _cell(self, index: QModelIndex | QPersistentModelIndex):
        x, y = self.cell_xy(index)
        return self._table.cell_at(x, y)

    # --- data ----------------------------------------------------------------
    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ):
        if not index.isValid():
            return None
        cell = self._cell(index)
        real = self.current_scale.to_real(cell.raw)
        if role == Qt.ItemDataRole.DisplayRole:
            return self.current_scale.format_value(real)
        if role == Qt.ItemDataRole.EditRole:
            return real
        if role == Qt.ItemDataRole.ToolTipRole and cell.is_changed():
            original = self.current_scale.to_real(cell.original)
            delta = real - original
            direction = "Higher" if delta > 0 else "Lower"
            return (
                f"{direction} than revert point by {delta:+g} "
                f"({self.current_scale.format_value(original)} → "
                f"{self.current_scale.format_value(real)})"
            )
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.ForegroundRole:
            bg = self.background_color(index)
            if bg is None:
                return None
            return QColor(*text_color_for((bg.red(), bg.green(), bg.blue())))
        return None

    def setData(
        self,
        index: QModelIndex | QPersistentModelIndex,
        value,
        role: int = Qt.ItemDataRole.EditRole,
    ) -> bool:
        if role != Qt.ItemDataRole.EditRole or not index.isValid():
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
        self._invalidate_value_caches()
        if automatic_group:
            self.end_edit_group()
        return True

    def flags(self, index: QModelIndex | QPersistentModelIndex):
        base = Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
        if getattr(self._table.definition, "locked", False):
            return base
        return base | Qt.ItemFlag.ItemIsEditable

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

    def headerData(
        self, section: int, orientation, role: int = Qt.ItemDataRole.DisplayRole
    ):
        logical_orientation = orientation
        _axis_role, axis = self._axis_for_orientation(orientation)
        if axis is None:
            if role != Qt.ItemDataRole.DisplayRole:
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
        if role == Qt.ItemDataRole.DisplayRole:
            return acell.scale.format_value(acell.real())
        if role == Qt.ItemDataRole.EditRole:
            return acell.real()
        if (
            role == Qt.ItemDataRole.ToolTipRole
            and axis.definition.storage_address is not None
        ):
            units = acell.scale.units
            exact = f"Exact value: {acell.real()!r}"
            if units:
                exact += f" {units}"
            if self.axis_is_editable(section, orientation):
                exact += "\nDouble-click to edit this axis value"
            return exact
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return None

    def setHeaderData(self, section: int, orientation, value,
                      role: int = Qt.ItemDataRole.EditRole) -> bool:
        if role != Qt.ItemDataRole.EditRole or not self.axis_is_editable(
            section, orientation
        ):
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
        self._invalidate_value_caches()
        if automatic_group:
            self.end_edit_group()

    def refresh_from_table(self) -> None:
        """Repaint values and axes after another view edits aliased ROM storage."""
        self._invalidate_value_caches()
        rows, columns = self.rowCount(), self.columnCount()
        if rows and columns:
            self.dataChanged.emit(self.index(0, 0), self.index(rows - 1, columns - 1))
        if columns:
            self.headerDataChanged.emit(Qt.Orientation.Horizontal, 0, columns - 1)
        if rows:
            self.headerDataChanged.emit(Qt.Orientation.Vertical, 0, rows - 1)
        self.colorScaleChanged.emit()

    def apply_quantized(self, proposal) -> bool:
        """Apply one preflighted Map Studio proposal as one logical edit.

        The proposal contains exact raw values, so this method never performs a
        second conversion or silently clamps.  It works in table coordinates and
        is therefore independent of the grid's presentation transpose.
        """
        sx, sy = self._table.shape()
        x_axis = self._table.x_axis
        x_role = "x"
        is_curve = self._table.definition.type == "2D" or sx == 1 or sy == 1
        expected_data_shape = (len(self._table.cells),) if is_curve else (sy, sx)
        if self._proposal_shape(proposal.values) != expected_data_shape:
            raise ValueError("proposal value shape does not match the destination table")
        if self._proposal_shape(proposal.data_raw) != expected_data_shape:
            raise ValueError("proposal raw-data shape does not match the destination table")
        data_raw = list(proposal.data_raw.reshape(-1))

        # Map Studio exposes one logical curve axis even when the ROM
        # definition stores a one-column curve on its physical Y axis.
        # Route that logical axis by the varying table dimension, not by
        # whichever axis object happens to be present first.
        if is_curve and sx == 1 and sy > 1 and self._table.y_axis is not None:
            x_axis = self._table.y_axis
            x_role = "y"
        elif is_curve and x_axis is None:
            x_axis = self._table.y_axis
            x_role = "y"

        self._validate_axis_proposal("X", proposal.x, proposal.x_raw, x_axis)
        y_axis = self._table.y_axis
        if is_curve and (proposal.y is not None or proposal.y_raw is not None):
            raise ValueError("proposal Y axis is not valid for a curve destination")
        self._validate_axis_proposal("Y", proposal.y, proposal.y_raw, y_axis)

        mutations = []
        for offset, raw in enumerate(data_raw):
            x, y = offset % sx, offset // sx
            mutations.append((("data", x, y), self._table.cell_at(x, y), raw))
        if proposal.x_raw is not None:
            assert x_axis is not None
            mutations.extend(
                ((x_role, section, 0), cell, raw)
                for section, (cell, raw) in enumerate(zip(x_axis.cells, proposal.x_raw))
            )
        if proposal.y_raw is not None:
            assert y_axis is not None
            mutations.extend(
                (("y", section, 0), cell, raw)
                for section, (cell, raw) in enumerate(zip(y_axis.cells, proposal.y_raw))
            )

        # Treat QuantizedTableProposal as an external boundary even though the normal producer
        # already validates it. A malformed/custom proposal must fail before the first ROM cell
        # changes, never halfway through an ostensibly atomic Map Studio apply.
        prepared = []
        for key, cell, raw in mutations:
            try:
                candidate = int(raw)
                integral = float(raw) == candidate
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError(f"proposal contains an invalid raw value at {key}") from exc
            if not integral or not cell.storage_min <= candidate <= cell.storage_max:
                raise ValueError(f"proposal raw value is outside storage range at {key}")
            prepared.append((key, cell, candidate))

        before_commit = self._edit_commit_serial
        prepared_snapshot, owner_snapshots = self._snapshot_prepared_mutations(prepared)
        self.begin_edit_group()
        try:
            for key, cell, raw in prepared:
                self._record_history_before(key)
                cell.set_raw(raw, clamp=False)
        except BaseException:
            self._restore_prepared_mutations(prepared_snapshot, owner_snapshots)
            self.end_edit_group()
            raise
        else:
            self.end_edit_group()

        return self._edit_commit_serial != before_commit

    @staticmethod
    def _proposal_shape(values: Any) -> tuple[int, ...]:
        """Return an ndarray-like proposal shape, rejecting ambiguous flat iterables."""
        try:
            return tuple(int(dimension) for dimension in values.shape)
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError("proposal payload must expose an explicit shape") from exc

    def _validate_axis_proposal(
        self,
        label: str,
        values: Any,
        raw_values: Any,
        axis: Table | None,
    ) -> None:
        if (values is None) != (raw_values is None):
            raise ValueError(f"proposal {label} axis values and raw values must be paired")
        if raw_values is None:
            return
        if axis is None or axis.definition.storage_address is None:
            raise ValueError(f"proposal {label} axis does not match the destination table")
        expected = (len(axis.cells),)
        if (
            self._proposal_shape(values) != expected
            or self._proposal_shape(raw_values) != expected
        ):
            raise ValueError(f"proposal {label} axis shape does not match the destination table")

    @staticmethod
    def _iter_rom_cells(owner: Any) -> Iterator[Any]:
        """Yield every unique data/axis cell owned by a ROM for atomic rollback."""
        seen_tables: set[int] = set()
        seen_cells: set[int] = set()

        def walk(table: Table) -> Iterator[Any]:
            table_key = id(table)
            if table_key in seen_tables:
                return
            seen_tables.add(table_key)
            for cell in table.cells:
                cell_key = id(cell)
                if cell_key not in seen_cells:
                    seen_cells.add(cell_key)
                    yield cell
            for axis in (table.x_axis, table.y_axis):
                if axis is not None:
                    yield from walk(axis)

        for table in owner.tables.values():
            yield from walk(table)

    @classmethod
    def _snapshot_prepared_mutations(
        cls,
        prepared: list[tuple[_HistoryKey, Any, int]],
    ) -> tuple[
        dict[int, tuple[Any, int]],
        list[tuple[Any, bytes, list[tuple[Any, int]]]],
    ]:
        """Capture cells plus backing ROMs so callback propagation can be rolled back."""
        prepared_snapshot = {
            id(cell): (cell, int(cell.raw)) for _key, cell, _raw in prepared
        }
        owners: dict[int, Any] = {}
        for _key, cell, _raw in prepared:
            callback = getattr(cell, "_change_callback", None)
            owner = getattr(callback, "__self__", None)
            if owner is not None and hasattr(owner, "data") and hasattr(owner, "tables"):
                owners[id(owner)] = owner
        owner_snapshots = [
            (
                owner,
                bytes(owner.data),
                [(cell, int(cell.raw)) for cell in cls._iter_rom_cells(owner)],
            )
            for owner in owners.values()
        ]
        return prepared_snapshot, owner_snapshots

    @staticmethod
    def _restore_prepared_mutations(
        prepared_snapshot: dict[int, tuple[Any, int]],
        owner_snapshots: list[tuple[Any, bytes, list[tuple[Any, int]]]],
    ) -> None:
        restored: set[int] = set()
        for owner, image, cells in owner_snapshots:
            owner.data[:] = image
            for cell, raw in cells:
                cell.sync_raw_from_storage(raw)
                restored.add(id(cell))
        for cell_key, (cell, raw) in prepared_snapshot.items():
            if cell_key not in restored:
                cell.sync_raw_from_storage(raw)

    # --- incremental edit history ------------------------------------------
    def begin_edit_group(self, *, capture_all: bool = False) -> None:
        """Begin one user-visible edit operation, nesting safely across helper layers."""
        if self._restoring_history:
            return
        if self._edit_depth == 0:
            self._pending_edit = {}
            self._pending_normalization_before = _UNSET
            self._edit_reset_seen = False
        self._edit_depth += 1
        if capture_all and self._pending_edit is not None:
            self._capture_normalization_before_data_edit()
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
        normalization_before = self._pending_normalization_before
        reset_seen = self._edit_reset_seen
        self._pending_edit = None
        self._pending_normalization_before = _UNSET
        self._edit_reset_seen = False
        if changed:
            self._undo_stack.append(changed)
            del self._undo_stack[:-200]
            self._redo_stack.clear()
            self._edit_commit_serial += 1
            if reset_seen:
                self._invalidate_value_caches()
            else:
                self._emit_changed_keys(changed, normalization_before)
            self._emit_history_state()
            self.editCommitted.emit()

    @contextmanager
    def edit_group(self, *, capture_all: bool = False) -> Iterator[None]:
        self.begin_edit_group(capture_all=capture_all)
        try:
            yield
        finally:
            self.end_edit_group()

    def _record_before(self, index: QModelIndex | QPersistentModelIndex) -> None:
        xy = self.cell_xy(index)
        self._record_history_before(("data", xy[0], xy[1]))

    def _record_history_before(self, key: _HistoryKey) -> None:
        if self._restoring_history or self._pending_edit is None:
            return
        if key[0] == "data":
            self._capture_normalization_before_data_edit()
        self._pending_edit.setdefault(key, self._history_cell(key).raw)

    def _capture_normalization_before_data_edit(self) -> None:
        if self._pending_normalization_before is _UNSET:
            self._pending_normalization_before = self._normalization_signature()

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

    def redo_depth(self) -> int:
        return len(self._redo_stack)

    def can_undo(self) -> bool:
        return bool(self._undo_stack)

    def can_redo(self) -> bool:
        return bool(self._redo_stack)

    def _emit_history_state(self) -> None:
        self.historyChanged.emit(self.can_undo(), self.can_redo())

    def clear_undo_history(self) -> None:
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._pending_edit = None
        self._pending_normalization_before = _UNSET
        self._edit_reset_seen = False
        self._edit_depth = 0
        self._emit_history_state()

    def external_edit_committed(self) -> None:
        """Publish one semantic edit after a caller has already reset/refreshed the model."""
        self._invalidate_value_caches()
        self._edit_commit_serial += 1
        self.editCommitted.emit()

    def undo_last(self) -> bool:
        """Restore the cells touched by the most recent grouped operation."""
        return self._restore_history(self._undo_stack, self._redo_stack)

    def redo_last(self) -> bool:
        """Reapply the most recently undone grouped operation."""
        return self._restore_history(self._redo_stack, self._undo_stack)

    def _restore_history(
        self,
        source: list[dict[_HistoryKey, int]],
        destination: list[dict[_HistoryKey, int]],
    ) -> bool:
        if not source:
            return False
        target = source.pop()
        inverse = {key: self._history_cell(key).raw for key in target}
        normalization_before = (
            self._normalization_signature()
            if any(key[0] == "data" for key in target)
            else _UNSET
        )
        self._restoring_history = True
        try:
            for key, raw in target.items():
                self._history_cell(key).set_raw(raw, clamp=True)
        finally:
            self._restoring_history = False
        destination.append(inverse)
        del destination[:-200]
        self._emit_changed_keys(target, normalization_before)
        self._emit_history_state()
        self.editCommitted.emit()
        return True

    def _emit_changed_keys(
        self,
        changed: dict[_HistoryKey, int],
        normalization_before,
    ) -> None:
        data_keys = [key for key in changed if key[0] == "data"]
        if data_keys:
            self._invalidate_value_caches()
            normalization_after = self._normalization_signature()
            normalization_changed = (
                normalization_before is _UNSET
                or normalization_before != normalization_after
            )
            if normalization_changed:
                top_left = self.index(0, 0)
                bottom_right = self.index(self.rowCount() - 1, self.columnCount() - 1)
                self.colorScaleChanged.emit()
            else:
                indexes = [self.index_for_cell(key[1], key[2]) for key in data_keys]
                rows = [index.row() for index in indexes]
                columns = [index.column() for index in indexes]
                top_left = self.index(min(rows), min(columns))
                bottom_right = self.index(max(rows), max(columns))
            self.dataChanged.emit(top_left, bottom_right)

        for axis_role in ("x", "y"):
            sections = [key[1] for key in changed if key[0] == axis_role]
            if not sections:
                continue
            orientation = self._orientation_for_axis_role(axis_role)
            self.headerDataChanged.emit(orientation, min(sections), max(sections))

    def _orientation_for_axis_role(self, role: str):
        if role == "x":
            return Qt.Orientation.Vertical if self._pt else Qt.Orientation.Horizontal
        return Qt.Orientation.Horizontal if self._pt else Qt.Orientation.Vertical

    # --- coloring (heat-map, warning, borders) --------------------------------
    def set_color_cells(self, on: bool) -> None:
        if bool(on) == self._color_cells:
            return
        self._color_cells = on
        self._recolor()
        self.colorScaleChanged.emit()

    def set_compare_original(self) -> None:
        if self._compare_source == "original":
            return
        self._compare_source = "original"
        self._compare_extent_cache = None
        self._recolor()
        self.colorScaleChanged.emit()

    def set_compare_table(self, table) -> None:
        if self._compare_source is table:
            return
        self._compare_source = table
        self._compare_extent_cache = None
        self._recolor()
        self.colorScaleChanged.emit()

    def refresh_compare_reference(self, changed_tables) -> bool:
        """Invalidate a live table comparison when its reference storage changes."""
        if self._compare_source is None or self._compare_source == "original":
            return False
        if self._compare_source not in changed_tables:
            return False
        self._compare_extent_cache = None
        self._recolor()
        self.colorScaleChanged.emit()
        return True

    def set_compare_mode(self, mode: str) -> None:
        normalized = "percent" if mode == "percent" else "absolute"
        if normalized == self._compare_mode:
            return
        self._compare_mode = normalized
        self._compare_extent_cache = None
        self._recolor()
        self.colorScaleChanged.emit()

    def compare_off(self) -> None:
        if self._compare_source is None:
            return
        self._compare_source = None
        self._compare_extent_cache = None
        self._recolor()
        self.colorScaleChanged.emit()

    @property
    def compare_active(self) -> bool:
        return self._compare_source is not None

    @property
    def compare_mode(self) -> str:
        return self._compare_mode

    def _compare_reference_real(
        self, index: QModelIndex | QPersistentModelIndex
    ):
        cell = self._cell(index)
        if self._compare_source == "original":
            return self.current_scale.to_real(cell.original)
        if self._compare_source is None:
            return None
        other = self._compare_source
        if not isinstance(other, Table):
            return None
        ex, ey = other.shape()
        x, y = self.cell_xy(index)
        if x >= ex or y >= ey:
            return None
        other_cell = other.cell_at(x, y)
        if self._scale_ix == 0:
            return other_cell.real()
        return self.current_scale.to_real(other_cell.raw)

    def compare_delta(self, index: QModelIndex | QPersistentModelIndex):
        ref = self._compare_reference_real(index)
        if ref is None:
            return None
        cur = self.current_scale.to_real(self._cell(index).raw)
        if self._compare_mode == "percent":
            return 0.0 if ref == 0 else (cur - ref) / abs(ref)     # fact base 1.1 getRealCompareChangeValue
        return cur - ref

    def compare_extent(self) -> float:
        """Largest absolute comparison delta, shared by cells and the symmetric legend."""
        if self._compare_source is None:
            return 0.0
        if self._compare_extent_cache is None:
            extent = 0.0
            for row in range(self.rowCount()):
                for column in range(self.columnCount()):
                    delta = self.compare_delta(self.index(row, column))
                    if delta is not None:
                        extent = max(extent, abs(float(delta)))
            self._compare_extent_cache = extent
        return self._compare_extent_cache

    def legend_bounds(self) -> tuple[float, float]:
        if self.compare_active:
            extent = self.compare_extent()
            return -extent, extent
        return self.real_bounds()

    def legend_color(self, ratio: float) -> QColor:
        ratio = max(0.0, min(1.0, float(ratio)))
        if self.compare_active:
            return self._comparison_color(2.0 * ratio - 1.0)
        return QColor(*heat_color(ratio, self._colormap))

    def _normalization_signature(self):
        if self.compare_active:
            extent = self.compare_extent()
            return "compare", -extent, extent
        if self._color_cells:
            lo, hi = self._real_bounds()
            return "heat", lo, hi
        return None

    @staticmethod
    def _comparison_color(normalized_delta: float) -> QColor:
        theme = current_theme()
        neutral = QColor(theme.compare_neutral)
        target = QColor(
            theme.increase_border if normalized_delta > 0 else theme.decrease_border
        )
        return _mix_colors(neutral, target, abs(normalized_delta))

    def _real_bounds(self) -> tuple[float, float]:
        if self._bounds_cache is None:
            reals = [self.current_scale.to_real(c.raw) for c in self._table.cells]
            self._bounds_cache = (min(reals), max(reals)) if reals else (0.0, 0.0)
        return self._bounds_cache

    def real_bounds(self) -> tuple[float, float]:
        """Public alias for the cached (min, max) real bounds (legend, 3D)."""
        return self._real_bounds()

    def heat_ratio(self, index: QModelIndex | QPersistentModelIndex) -> float:
        lo, hi = self._real_bounds()
        if hi == lo:
            return 0.0
        return (self.current_scale.to_real(self._cell(index).raw) - lo) / (hi - lo)

    def background_color(self, index: QModelIndex | QPersistentModelIndex):
        if self._compare_source is not None:
            delta = self.compare_delta(index)
            extent = self.compare_extent()
            if delta is None or extent <= 0:
                return self._comparison_color(0.0)
            return self._comparison_color(max(-1.0, min(1.0, delta / extent)))
        if not self._color_cells:
            return None
        return self.legend_color(self.heat_ratio(index))

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
