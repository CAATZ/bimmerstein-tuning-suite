from __future__ import annotations
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLineEdit,
    QStyle,
    QStyledItemDelegate,
    QTableView,
)
from PySide6.QtGui import QColor, QFontMetrics, QPalette, QPen, QPolygon
from PySide6.QtCore import (
    QEvent,
    QItemSelection,
    QItemSelectionModel,
    QPoint,
    QRect,
    QSize,
    Qt,
    Signal,
    QTimer,
)
from ecueditor.ui.design.colormaps import text_color_for
from ecueditor.ui.design.theme_manager import current_theme
from ecueditor.ui.editor.table_model import TableGridModel


class EditableAxisHeader(QHeaderView):
    """A native table header with an in-place editor for storage-backed axis cells."""

    def __init__(self, orientation, parent=None) -> None:
        super().__init__(orientation, parent)
        self._editor: QLineEdit | None = None
        self._edit_section = -1
        self.setSectionsClickable(True)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.sectionResized.connect(lambda *_args: self._position_editor())

    def active_editor(self) -> QLineEdit | None:
        return self._editor

    def begin_axis_edit(self, section: int) -> bool:
        model = self.model()
        if not isinstance(model, TableGridModel) or not model.axis_is_editable(
            section, self.orientation()
        ):
            return False
        self._close_editor()
        value = model.headerData(
            section, self.orientation(), Qt.ItemDataRole.EditRole
        )
        editor = QLineEdit(self.viewport())
        editor.setFrame(False)
        editor.setAlignment(Qt.AlignmentFlag.AlignRight)
        editor.setText(str(value))
        editor.installEventFilter(self)
        editor.returnPressed.connect(self._commit_editor)
        editor.editingFinished.connect(self._commit_editor)
        self._editor = editor
        self._edit_section = section
        self._position_editor()
        editor.show()
        editor.setFocus(Qt.FocusReason.MouseFocusReason)
        editor.selectAll()
        return True

    def _position_editor(self) -> None:
        if self._editor is None or self._edit_section < 0:
            return
        position = self.sectionViewportPosition(self._edit_section)
        extent = self.sectionSize(self._edit_section)
        if self.orientation() == Qt.Orientation.Horizontal:
            rect = QRect(position, 0, extent, self.viewport().height())
        else:
            rect = QRect(0, position, self.viewport().width(), extent)
        self._editor.setGeometry(rect.adjusted(1, 1, -1, -1))

    def _commit_editor(self) -> None:
        if self._editor is None:
            return
        editor = self._editor
        section = self._edit_section
        text = editor.text()
        self._editor = None
        self._edit_section = -1
        model = self.model()
        if isinstance(model, TableGridModel):
            model.setHeaderData(
                section, self.orientation(), text, Qt.ItemDataRole.EditRole
            )
        editor.deleteLater()

    def _close_editor(self) -> None:
        if self._editor is None:
            return
        editor = self._editor
        self._editor = None
        self._edit_section = -1
        editor.deleteLater()

    def eventFilter(self, watched, event) -> bool:
        if watched is self._editor and event.type() == QEvent.Type.KeyPress \
                and event.key() == Qt.Key.Key_Escape:
            self._close_editor()
            return True
        return super().eventFilter(watched, event)

    def mouseDoubleClickEvent(self, event) -> None:
        position = event.position().x() if self.orientation() == Qt.Orientation.Horizontal \
            else event.position().y()
        section = self.logicalIndexAt(int(position))
        if section >= 0 and self.begin_axis_edit(section):
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def mouseMoveEvent(self, event) -> None:
        position = event.position().x() if self.orientation() == Qt.Orientation.Horizontal \
            else event.position().y()
        section = self.logicalIndexAt(int(position))
        model = self.model()
        editable = isinstance(model, TableGridModel) and model.axis_is_editable(
            section, self.orientation()
        )
        self.viewport().setCursor(
            Qt.CursorShape.IBeamCursor if editable else Qt.CursorShape.ArrowCursor
        )
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
        super().leaveEvent(event)

class HeatMapDelegate(QStyledItemDelegate):
    def createEditor(self, parent, _option, _index):
        editor = QLineEdit(parent)
        editor.setFrame(False)
        editor.setAlignment(Qt.AlignmentFlag.AlignRight)
        return editor

    def setEditorData(self, editor, index) -> None:
        editor.setText(str(index.model().data(index, Qt.ItemDataRole.EditRole)))
        editor.selectAll()

    def setModelData(self, editor, model, index) -> None:
        value = editor.text().strip()
        view = self.parent()
        selected = view.selected_indexes() if isinstance(view, TableGridWidget) else []
        targets = selected if len(selected) > 1 and index in selected else [index]
        with model.edit_group():
            for target in targets:
                model.setData(target, value, Qt.ItemDataRole.EditRole)

    def paint(self, painter, option, index) -> None:
        t = current_theme()
        model = index.model()
        view = self.parent()
        selected = bool(option.state & option.state.State_Selected)
        bg = model.background_color(index)
        if bg is not None:
            painter.fillRect(option.rect, bg)
        opt = option
        if selected:
            dragging = isinstance(view, TableGridWidget) and view.selection_drag_active()
            selection_fill = QColor(t.decrease_border if dragging else t.compare_neutral)
            painter.fillRect(option.rect, selection_fill)
            opt = type(option)(option)
            opt.state &= ~(opt.state.State_Selected | opt.state.State_HasFocus)
            rgb = (selection_fill.red(), selection_fill.green(), selection_fill.blue())
            opt.palette.setColor(
                QPalette.ColorRole.Text, QColor(*text_color_for(rgb))
            )
        super().paint(painter, opt, index)

        if isinstance(view, TableGridWidget):
            edges = view.last_paste_edges(index)
            if edges:
                painter.save()
                inset = option.rect.adjusted(1, 1, -2, -2)
                width = 4 if view.paste_flash_active() else 3
                for pen in (
                    QPen(QColor("#101215"), width, Qt.PenStyle.DashLine),
                    QPen(QColor(t.warn), max(1, width - 2), Qt.PenStyle.DashLine),
                ):
                    painter.setPen(pen)
                    if "top" in edges:
                        painter.drawLine(inset.topLeft(), inset.topRight())
                    if "right" in edges:
                        painter.drawLine(inset.topRight(), inset.bottomRight())
                    if "bottom" in edges:
                        painter.drawLine(inset.bottomLeft(), inset.bottomRight())
                    if "left" in edges:
                        painter.drawLine(inset.topLeft(), inset.bottomLeft())
                painter.restore()

        change = model.change_border(index)
        if change is not None:
            painter.save()
            color = QColor(t.increase_border if change == "increase" else t.decrease_border)
            painter.setPen(QPen(color, 2))
            painter.drawRect(option.rect.adjusted(2, 2, -3, -3))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            if change == "increase":
                corner = option.rect.topRight() + QPoint(-3, 3)
                triangle = QPolygon([
                    corner, corner + QPoint(-6, 0), corner + QPoint(0, 6)
                ])
            else:
                corner = option.rect.bottomRight() + QPoint(-3, -3)
                triangle = QPolygon([
                    corner, corner + QPoint(-6, 0), corner + QPoint(0, -6)
                ])
            painter.drawPolygon(triangle)
            painter.restore()

        if model.is_live_cell(index):
            painter.save()
            painter.setPen(QPen(QColor(t.live_ring), 2))
            painter.drawRect(option.rect.adjusted(5, 5, -6, -6))
            painter.restore()

        if isinstance(view, TableGridWidget) and view.is_current_index(index):
            painter.save()
            painter.setPen(QPen(QColor("#101215"), 3))
            painter.drawRect(option.rect.adjusted(1, 1, -2, -2))
            painter.setPen(QPen(QColor("#f7f7f7"), 1))
            painter.drawRect(option.rect.adjusted(3, 3, -4, -4))
            painter.restore()

class TableGridWidget(QTableView):
    selectionSummaryChanged = Signal(str)
    _PASTE_FLASH_MS = 350

    def __init__(self, model: TableGridModel, parent=None) -> None:
        super().__init__(parent)
        self.setModel(model)
        self.setHorizontalHeader(EditableAxisHeader(Qt.Orientation.Horizontal, self))
        self.setVerticalHeader(EditableAxisHeader(Qt.Orientation.Vertical, self))
        self.setItemDelegate(HeatMapDelegate(self))
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked
                             | QAbstractItemView.EditTrigger.AnyKeyPressed)
        header = self.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignRight)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(False)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self._live_index = None          # cell highlighted by the last live-overlay value (None = cleared)
        self._density = "normal"
        self._min_col_w = 42
        self._natural_widths: list[int] = []
        self._compact_widths: list[int] = []
        self._selection_drag_active = False
        self._last_paste_cells: set[tuple[int, int]] = set()
        self._last_paste_undo_depth: int | None = None
        self._paste_flash_active = False
        self._paste_flash_timer = QTimer(self)
        self._paste_flash_timer.setSingleShot(True)
        self._paste_flash_timer.timeout.connect(self._finish_paste_flash)
        self.selectionModel().selectionChanged.connect(self._selection_visual_changed)
        self.selectionModel().currentChanged.connect(self._selection_visual_changed)
        model.modelReset.connect(lambda: self.autofit_columns(self._min_col_w))
        model.headerDataChanged.connect(lambda *_args: self.autofit_columns(self._min_col_w))

    def selected_indexes(self):
        return self.selectionModel().selectedIndexes()

    def selection_drag_active(self) -> bool:
        return self._selection_drag_active

    def is_current_index(self, index) -> bool:
        current = self.currentIndex()
        return current.isValid() and current.row() == index.row() \
            and current.column() == index.column()

    def mark_last_paste(self, indexes) -> None:
        cells = {
            (index.row(), index.column())
            for index in indexes
            if index is not None and index.isValid()
        }
        if not cells:
            self.clear_last_paste()
            return
        self._last_paste_cells = cells
        self._last_paste_undo_depth = self.model().undo_depth()
        selection = QItemSelection()
        for row, column in sorted(cells):
            index = self.model().index(row, column)
            selection.select(index, index)
        selected = self.selectionModel()
        selected.select(selection, QItemSelectionModel.SelectionFlag.ClearAndSelect)
        first_row, first_column = min(cells)
        selected.setCurrentIndex(
            self.model().index(first_row, first_column),
            QItemSelectionModel.SelectionFlag.NoUpdate,
        )
        self._paste_flash_active = True
        self._paste_flash_timer.start(self._PASTE_FLASH_MS)
        self.viewport().update()
        self._emit_selection_summary()

    def clear_last_paste(self) -> None:
        self._last_paste_cells.clear()
        self._last_paste_undo_depth = None
        self._paste_flash_active = False
        self._paste_flash_timer.stop()
        self.viewport().update()
        self._emit_selection_summary()

    def reconcile_last_paste_after_undo(self) -> None:
        if self._last_paste_undo_depth is not None \
                and self.model().undo_depth() < self._last_paste_undo_depth:
            self.clear_last_paste()
        else:
            self.viewport().update()

    def last_paste_indexes(self):
        return [self.model().index(row, column) for row, column in sorted(self._last_paste_cells)]

    def is_last_paste_index(self, index) -> bool:
        return index.isValid() and (index.row(), index.column()) in self._last_paste_cells

    def paste_flash_active(self) -> bool:
        return self._paste_flash_active

    def last_paste_edges(self, index) -> frozenset[str]:
        if not self.is_last_paste_index(index):
            return frozenset()
        row, column = index.row(), index.column()
        edges = set()
        for edge, neighbour in (
            ("top", (row - 1, column)),
            ("right", (row, column + 1)),
            ("bottom", (row + 1, column)),
            ("left", (row, column - 1)),
        ):
            if neighbour not in self._last_paste_cells:
                edges.add(edge)
        return frozenset(edges)

    @staticmethod
    def _region_shape(cells: set[tuple[int, int]], *, include_count: bool) -> str:
        rows = [row for row, _column in cells]
        columns = [column for _row, column in cells]
        width = max(columns) - min(columns) + 1
        height = max(rows) - min(rows) + 1
        shape = f"{width}×{height}"
        return f"{shape} ({len(cells)})" if include_count else shape

    def selection_summary_text(self) -> str:
        selection = {
            (index.row(), index.column()) for index in self.selected_indexes()
        }
        parts = []
        if selection:
            parts.append(f"Selected {self._region_shape(selection, include_count=True)}")
        if self._last_paste_cells:
            parts.append(
                f"Last paste {self._region_shape(self._last_paste_cells, include_count=False)}"
            )
        return " · ".join(parts)

    def _emit_selection_summary(self) -> None:
        self.selectionSummaryChanged.emit(self.selection_summary_text())

    def _selection_visual_changed(self, *_args) -> None:
        self.viewport().update()
        self._emit_selection_summary()

    def _finish_paste_flash(self) -> None:
        self._paste_flash_active = False
        self.viewport().update()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._selection_drag_active = True
        super().mousePressEvent(event)
        self.viewport().update()

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            self._selection_drag_active = False
        self.viewport().update()

    def sizeHint(self) -> QSize:
        """Describe the complete table so its MDI window can open without needless scrolling."""
        model = self.model()
        if model is None:
            return super().sizeHint()
        widths = self._natural_widths or [
            self.columnWidth(column) for column in range(model.columnCount())
        ]
        # Studio may need to clamp a tall map vertically, at which point Qt introduces a
        # vertical scrollbar and takes its width from the data viewport.  Reserve that native
        # extent up front so the last value column still opens completely visible.
        scrollbar_extent = self.style().pixelMetric(QStyle.PixelMetric.PM_ScrollBarExtent)
        width = self.verticalHeader().sizeHint().width() + sum(widths) + scrollbar_extent
        height = self.horizontalHeader().sizeHint().height() + sum(
            self.rowHeight(row) for row in range(model.rowCount())
        )
        frame = 2 * self.frameWidth()
        return QSize(width + frame, height + frame)

    def keyPressEvent(self, event) -> None:
        """Start direct numeric entry for signed values as well as ordinary digit keys."""
        text = event.text()
        modifiers = event.modifiers()
        command_modifier = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier
        direct_value_key = bool(text) and not (modifiers & command_modifier) and (
            text.isdigit() or text in {"-", "+", ".", ","}
        )
        if direct_value_key and self.currentIndex().isValid():
            started = self.edit(
                self.currentIndex(),
                QAbstractItemView.EditTrigger.AnyKeyPressed,
                event,
            )
            if started:
                return
        super().keyPressEvent(event)

    def set_color_cells(self, on: bool) -> None:
        self.model().set_color_cells(on); self.viewport().update()

    def set_density(self, density: str) -> None:
        """Select spacing floors used by the display-only Normal/Compact projection."""
        self._density = "compact" if density == "compact" else "normal"
        compact = self._density == "compact"
        self.horizontalHeader().setMinimumSectionSize(28 if compact else 36)
        self.verticalHeader().setMinimumSectionSize(14 if compact else 18)

    def autofit_columns(self, min_width: int) -> None:
        """Fit full values and compact configured padding before introducing a scrollbar."""
        self._min_col_w = min_width
        m = self.model()
        if m is not None:
            metrics = QFontMetrics(self.font())
            compact_density = self._density == "compact"
            width_floor = 28 if compact_density else 36
            text_padding = 8
            self._natural_widths = []
            self._compact_widths = []
            for c in range(m.columnCount()):
                labels = [str(m.headerData(c, Qt.Orientation.Horizontal, Qt.DisplayRole) or "")]
                labels.extend(
                    str(m.data(m.index(row, c), Qt.DisplayRole) or "")
                    for row in range(m.rowCount())
                )
                text_width = max((metrics.horizontalAdvance(label) for label in labels), default=0)
                delegate_width = self.sizeHintForColumn(c)
                compact = max(width_floor, text_width + text_padding, delegate_width)
                self._compact_widths.append(compact)
                self._natural_widths.append(max(min_width, compact))
            self._fit_columns_to_view()

    def _fit_columns_to_view(self) -> None:
        if not self._natural_widths:
            return
        available = max(0, self.viewport().width() - 1)
        preferred = self._natural_widths
        compact = self._compact_widths or preferred
        widths = preferred if sum(preferred) <= available else compact
        for column, width in enumerate(widths):
            self.setColumnWidth(column, width)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit_columns_to_view()

    # --- live-overlay hook (INTERFACES.md §ui/ contracts table_grid.py) -------
    #     Phase 2 provides this; Phase 4's LiveOverlayBridge calls set_live_value per Sample.
    @property
    def logparam(self):
        """Bound logger-channel id (mirrors TableDef.logparam); None if the table is unbound."""
        return self.model().table.definition.logparam

    def set_live_value(self, real: float | None) -> None:
        """Highlight the cell whose real value is nearest `real`; None clears the overlay.

        `real` is already an engineering value — it is never re-scaled here.
        """
        model = self.model()
        if real is None:
            self._live_index = None
            model.set_live_cell(None)
            self.viewport().update()
            return
        sx, sy = model.table.shape()
        best = None
        best_dist = None
        for x in range(sx):
            for y in range(sy):
                cell_real = model.current_scale.to_real(model.table.cell_at(x, y).raw)
                dist = abs(cell_real - real)
                if best_dist is None or dist < best_dist:
                    best_dist = dist
                    best = model.index_for_cell(x, y)
        self._live_index = best
        model.set_live_cell(best)
        self.viewport().update()
