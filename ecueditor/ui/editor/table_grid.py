from __future__ import annotations
from bisect import bisect_left
import math
from typing import cast
from typing import NamedTuple

from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLineEdit,
    QStyle,
    QStyleOptionHeader,
    QStyledItemDelegate,
    QTableView,
    QToolTip,
)
from PySide6.QtGui import QColor, QFontMetrics, QPalette, QPen, QPolygon
from PySide6.QtCore import (
    QEvent,
    QItemSelection,
    QItemSelectionModel,
    QModelIndex,
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
        self._label_cache: dict[int, str] | None = None
        self.setSectionsClickable(True)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.sectionResized.connect(lambda *_args: self._position_editor())

    def active_editor(self) -> QLineEdit | None:
        return self._editor

    @staticmethod
    def _fixed_axis(value: float, decimals: int) -> str:
        label = f"{float(value):.{decimals}f}"
        if decimals:
            label = label.rstrip("0").rstrip(".")
        return "0" if label in {"-0", "+0"} else label

    def _section_count(self) -> int:
        model = self.model()
        if model is None:
            return 0
        if self.orientation() == Qt.Orientation.Horizontal:
            return model.columnCount()
        return model.rowCount()

    def _build_display_labels(self) -> dict[int, str]:
        """Return coarse display labels while keeping the model's exact values untouched."""
        model = self.model()
        count = self._section_count()
        if model is None or count < 1:
            return {}
        exact = []
        for section in range(count):
            value = model.headerData(
                section, self.orientation(), Qt.ItemDataRole.EditRole
            )
            if value is None:
                value = model.headerData(
                    section, self.orientation(), Qt.ItemDataRole.DisplayRole
                )
            exact.append(value)
        numeric: list[float] = []
        for value in exact:
            try:
                parsed = float(value)
            except (TypeError, ValueError):
                numeric = []
                break
            if not math.isfinite(parsed):
                numeric = []
                break
            numeric.append(parsed)
        if len(numeric) == count:
            for decimals in range(13):
                labels = [self._fixed_axis(value, decimals) for value in numeric]
                seen: dict[str, float] = {}
                collision = False
                for label, value in zip(labels, numeric):
                    previous = seen.setdefault(label, value)
                    if previous != value:
                        collision = True
                        break
                if not collision:
                    return dict(enumerate(labels))
            return {
                section: f"{value:.17g}" for section, value in enumerate(numeric)
            }
        return {
            section: str(
                model.headerData(
                    section, self.orientation(), Qt.ItemDataRole.DisplayRole
                )
                or ""
            )
            for section in range(count)
        }

    def _labels(self) -> dict[int, str]:
        if self._label_cache is None:
            self._label_cache = self._build_display_labels()
        return self._label_cache

    def display_label(self, section: int) -> str:
        return self._labels().get(section, "")

    def refresh_labels(self) -> set[int]:
        old = self._label_cache
        self._label_cache = None
        new = self._labels()
        changed = set(new) if old is None else {
            section for section in set(old) | set(new)
            if old.get(section) != new.get(section)
        }
        self.viewport().update()
        return changed

    def exact_tooltip(self, section: int) -> str:
        model = self.model()
        if model is None or not 0 <= section < self._section_count():
            return ""
        model_tip = model.headerData(
            section, self.orientation(), Qt.ItemDataRole.ToolTipRole
        )
        exact = model.headerData(
            section, self.orientation(), Qt.ItemDataRole.EditRole
        )
        if exact is None:
            exact_text = str(
                model.headerData(
                    section, self.orientation(), Qt.ItemDataRole.DisplayRole
                )
                or ""
            )
        else:
            try:
                exact_text = repr(float(exact))
            except (TypeError, ValueError):
                exact_text = str(exact)
        hint = "" if model_tip is None else str(model_tip)
        if hint.startswith("Exact value:"):
            return hint
        return exact_text if not hint else f"{exact_text}\n{hint}"

    def paintSection(self, painter, rect, logical_index: int) -> None:
        if not rect.isValid():
            return
        option = QStyleOptionHeader()
        self.initStyleOptionForIndex(option, logical_index)
        option.rect = rect
        option.text = self.display_label(logical_index)
        self.style().drawControl(
            QStyle.ControlElement.CE_Header, option, painter, self
        )

    def _styled_label_size(self, logical_index: int) -> QSize:
        option = QStyleOptionHeader()
        self.initStyleOptionForIndex(option, logical_index)
        option.text = self.display_label(logical_index)
        metrics = QFontMetrics(self.font())
        contents = QSize(
            metrics.horizontalAdvance(option.text),
            metrics.height(),
        )
        return self.style().sizeFromContents(
            QStyle.ContentsType.CT_HeaderSection,
            option,
            contents,
            self,
        )

    def sectionSizeFromContents(self, logical_index: int) -> QSize:
        base = super().sectionSizeFromContents(logical_index)
        styled = self._styled_label_size(logical_index)
        if self.orientation() == Qt.Orientation.Horizontal:
            return QSize(
                max(self.minimumSectionSize(), styled.width()),
                max(base.height(), styled.height()),
            )
        return QSize(
            max(1, styled.width()),
            max(self.minimumSectionSize(), styled.height()),
        )

    def sizeHint(self) -> QSize:
        hint = super().sizeHint()
        if self.orientation() != Qt.Orientation.Vertical:
            return hint
        label_width = max(
            (
                self._styled_label_size(section).width()
                for section in range(self._section_count())
            ),
            default=0,
        )
        return QSize(max(1, label_width), hint.height())

    def viewportEvent(self, event) -> bool:
        if event.type() == QEvent.Type.ToolTip:
            position = event.pos()
            coordinate = position.x() if self.orientation() == Qt.Orientation.Horizontal \
                else position.y()
            section = self.logicalIndexAt(int(coordinate))
            tooltip = self.exact_tooltip(section)
            if tooltip:
                QToolTip.showText(event.globalPos(), tooltip, self.viewport())
                return True
            QToolTip.hideText()
        return super().viewportEvent(event)

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

class _OverlayMetrics(NamedTuple):
    compact: bool
    paste_inset: int
    paste_width: int
    change_inset: int
    change_width: int
    marker_size: int
    live_inset: int
    live_width: int
    current_inset: int
    current_width: int
    current_inner_inset: int | None


class HeatMapDelegate(QStyledItemDelegate):
    @staticmethod
    def overlay_metrics(font, cell_height: int) -> _OverlayMetrics:
        compact = cell_height < QFontMetrics(font).height() + 8
        if compact:
            return _OverlayMetrics(True, 0, 1, 0, 1, 3, 0, 1, 0, 1, None)
        return _OverlayMetrics(False, 1, 3, 2, 2, 6, 5, 2, 1, 3, 3)

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
        overlay = self.overlay_metrics(opt.font, option.rect.height())

        if isinstance(view, TableGridWidget):
            edges = view.last_paste_edges(index)
            if edges:
                painter.save()
                inset_value = overlay.paste_inset
                inset = option.rect.adjusted(
                    inset_value, inset_value, -inset_value - 1, -inset_value - 1
                )
                width = overlay.paste_width
                if not overlay.compact and view.paste_flash_active():
                    width += 1
                pens = [QPen(QColor(t.warn), width, Qt.PenStyle.DashLine)]
                if not overlay.compact:
                    pens.insert(0, QPen(QColor("#101215"), width, Qt.PenStyle.DashLine))
                    pens[-1].setWidth(max(1, width - 2))
                for pen in pens:
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
            painter.setPen(QPen(color, overlay.change_width))
            inset = overlay.change_inset
            painter.drawRect(option.rect.adjusted(inset, inset, -inset - 1, -inset - 1))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            if change == "increase":
                corner = option.rect.topRight() + QPoint(-inset - 1, inset + 1)
                triangle = QPolygon([
                    corner,
                    corner + QPoint(-overlay.marker_size, 0),
                    corner + QPoint(0, overlay.marker_size),
                ])
            else:
                corner = option.rect.bottomRight() + QPoint(-inset - 1, -inset - 1)
                triangle = QPolygon([
                    corner,
                    corner + QPoint(-overlay.marker_size, 0),
                    corner + QPoint(0, -overlay.marker_size),
                ])
            painter.drawPolygon(triangle)
            painter.restore()

        if model.is_live_cell(index):
            painter.save()
            painter.setPen(QPen(QColor(t.live_ring), overlay.live_width))
            inset = overlay.live_inset
            painter.drawRect(option.rect.adjusted(inset, inset, -inset - 1, -inset - 1))
            painter.restore()

        if isinstance(view, TableGridWidget) and view.is_current_index(index):
            painter.save()
            painter.setPen(QPen(QColor(t.sel_ring), overlay.current_width))
            inset = overlay.current_inset
            painter.drawRect(option.rect.adjusted(inset, inset, -inset - 1, -inset - 1))
            if overlay.current_inner_inset is not None:
                inner = overlay.current_inner_inset
                painter.setPen(QPen(QColor(t.sel_ring_inner), 1))
                painter.drawRect(option.rect.adjusted(inner, inner, -inner - 1, -inner - 1))
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
        self._pending_autofit_columns: set[int] = set()
        self._autofit_timer = QTimer(self)
        self._autofit_timer.setSingleShot(True)
        self._autofit_timer.setInterval(0)
        self._autofit_timer.timeout.connect(self._flush_column_autofit)
        self._configuring_display = False
        self._fitting_columns = False
        self._live_lookup_values: list[float] | None = None
        self._live_lookup_entries: list[tuple[int, QModelIndex]] | None = None
        self._selection_drag_active = False
        self._last_paste_cells: set[tuple[int, int]] = set()
        self._last_paste_undo_depth: int | None = None
        self._paste_flash_active = False
        self._paste_flash_timer = QTimer(self)
        self._paste_flash_timer.setSingleShot(True)
        self._paste_flash_timer.timeout.connect(self._finish_paste_flash)
        self.selectionModel().selectionChanged.connect(self._selection_visual_changed)
        self.selectionModel().currentChanged.connect(self._selection_visual_changed)
        model.modelReset.connect(self._on_model_reset)
        model.dataChanged.connect(self._on_model_data_changed)
        model.headerDataChanged.connect(self._on_header_data_changed)

    def model(self) -> TableGridModel:
        """Return the concrete model installed by this view's constructor."""
        return cast(TableGridModel, super().model())

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
        columns_by_row: dict[int, list[int]] = {}
        for row, column in cells:
            columns_by_row.setdefault(row, []).append(column)
        for row, columns in sorted(columns_by_row.items()):
            ordered = sorted(columns)
            run_start = run_stop = ordered[0]
            for column in ordered[1:]:
                if column == run_stop + 1:
                    run_stop = column
                    continue
                selection.select(
                    self.model().index(row, run_start),
                    self.model().index(row, run_stop),
                )
                run_start = run_stop = column
            selection.select(
                self.model().index(row, run_start),
                self.model().index(row, run_stop),
            )
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
        width = self.verticalHeader().sizeHint().width() + sum(widths)
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

    def setFont(self, font) -> None:
        """Keep dense cell and axis typography on the same metric contract."""
        super().setFont(font)
        for header in (self.horizontalHeader(), self.verticalHeader()):
            if header is not None:
                header.setFont(font)
                header.updateGeometry()
        if hasattr(self, "_min_col_w") and not self._configuring_display:
            self.autofit_columns(self._min_col_w)

    def configure_display(
        self,
        *,
        font,
        density: str,
        row_height: int,
        minimum_column_width: int,
    ) -> None:
        """Apply one complete display projection and solve section geometry once."""
        self._configuring_display = True
        try:
            super().setFont(font)
            for header in (self.horizontalHeader(), self.verticalHeader()):
                header.setFont(font)
                header.updateGeometry()
            self.set_density(density)
            self.verticalHeader().setDefaultSectionSize(row_height)
            model = self.model()
            if model is not None:
                for row in range(model.rowCount()):
                    self.verticalHeader().resizeSection(row, row_height)
            self.horizontalHeader().setDefaultSectionSize(minimum_column_width)
        finally:
            self._configuring_display = False
        self.autofit_columns(minimum_column_width)
        self.updateGeometry()

    def set_density(self, density: str) -> None:
        """Select spacing floors used by the display-only Normal/Compact projection."""
        self._density = "compact" if density == "compact" else "normal"
        compact = self._density == "compact"
        self.horizontalHeader().setMinimumSectionSize(28 if compact else 36)
        self.verticalHeader().setMinimumSectionSize(14 if compact else 18)

    def _on_model_reset(self) -> None:
        horizontal = self.horizontalHeader()
        vertical = self.verticalHeader()
        if isinstance(horizontal, EditableAxisHeader):
            horizontal.refresh_labels()
        if isinstance(vertical, EditableAxisHeader):
            vertical.refresh_labels()
        self._invalidate_live_value_lookup()
        self._live_index = None
        self.model().set_live_cell(None)
        self.autofit_columns(self._min_col_w)

    def _on_model_data_changed(self, top_left, bottom_right, roles=()) -> None:
        text_roles = {
            int(Qt.ItemDataRole.DisplayRole),
            int(Qt.ItemDataRole.EditRole),
        }
        if roles and not text_roles.intersection(map(int, roles)):
            return
        self._invalidate_live_value_lookup()
        self._queue_column_autofit(top_left.column(), bottom_right.column())

    def _on_header_data_changed(self, orientation, first: int, last: int) -> None:
        header = (
            self.horizontalHeader()
            if orientation == Qt.Orientation.Horizontal
            else self.verticalHeader()
        )
        changed = header.refresh_labels() if isinstance(header, EditableAxisHeader) else set()
        if orientation == Qt.Orientation.Horizontal:
            affected = changed or set(range(first, last + 1))
            for column in affected:
                self._queue_column_autofit(column, column)
            return
        header.updateGeometry()
        self.updateGeometries()
        self._fit_columns_to_view()

    def _queue_column_autofit(self, first: int, last: int) -> None:
        model = self.model()
        if model is None or model.columnCount() < 1:
            return
        first = max(0, first)
        last = min(model.columnCount() - 1, last)
        if last < first:
            return
        self._pending_autofit_columns.update(range(first, last + 1))
        if not self._autofit_timer.isActive():
            self._autofit_timer.start()

    def _flush_column_autofit(self) -> None:
        model = self.model()
        columns = sorted(self._pending_autofit_columns)
        self._pending_autofit_columns.clear()
        if model is None or not columns:
            return
        if len(self._natural_widths) != model.columnCount() \
                or len(self._compact_widths) != model.columnCount():
            self.autofit_columns(self._min_col_w)
            return
        metrics = QFontMetrics(self.font())
        for column in columns:
            natural, compact = self._measure_column(column, metrics)
            self._natural_widths[column] = natural
            self._compact_widths[column] = compact
        self._fit_columns_to_view()

    def _measure_column(self, column: int, metrics: QFontMetrics) -> tuple[int, int]:
        model = self.model()
        header = self.horizontalHeader()
        if isinstance(header, EditableAxisHeader):
            axis_label = header.display_label(column)
        else:
            axis_label = str(
                model.headerData(
                    column, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole
                )
                or ""
            )
        labels = [
            str(model.data(model.index(row, column), Qt.ItemDataRole.DisplayRole) or "")
            for row in range(model.rowCount())
        ]
        text_width = max((metrics.horizontalAdvance(label) for label in labels), default=0)
        compact_density = self._density == "compact"
        width_floor = 28 if compact_density else 36
        header_width = (
            header.sectionSizeFromContents(column).width()
            if isinstance(header, EditableAxisHeader)
            else metrics.horizontalAdvance(axis_label) + 8
        )
        compact = max(width_floor, header_width, text_width + 8)
        return max(self._min_col_w, compact), compact

    def autofit_columns(self, min_width: int) -> None:
        """Fit full values and compact configured padding before introducing a scrollbar."""
        self._min_col_w = min_width
        m = self.model()
        if m is not None:
            metrics = QFontMetrics(self.font())
            self._autofit_timer.stop()
            self._pending_autofit_columns.clear()
            measured = [self._measure_column(column, metrics) for column in range(m.columnCount())]
            self._natural_widths = [natural for natural, _compact in measured]
            self._compact_widths = [compact for _natural, compact in measured]
            self._fit_columns_to_view()

    @staticmethod
    def _allocate_column_widths(
        preferred: list[int], minimum: list[int], available: int
    ) -> list[int]:
        """Continuously fit widths between content minima and preferred widths."""
        if not preferred or len(preferred) != len(minimum):
            return list(preferred)
        preferred = [max(int(low), int(high)) for high, low in zip(preferred, minimum)]
        minimum = [max(1, int(width)) for width in minimum]
        preferred_total = sum(preferred)
        minimum_total = sum(minimum)
        if preferred_total <= available:
            return preferred
        if minimum_total >= available:
            return minimum
        capacity = preferred_total - minimum_total
        budget = available - minimum_total
        exact = [
            low + (high - low) * budget / capacity
            for high, low in zip(preferred, minimum)
        ]
        fitted = [math.floor(width) for width in exact]
        remainder = available - sum(fitted)
        order = sorted(
            range(len(fitted)),
            key=lambda index: (-(exact[index] - fitted[index]), index),
        )
        for index in order[:remainder]:
            fitted[index] += 1
        return fitted

    def _fit_columns_to_view(self) -> None:
        if not self._natural_widths or self._fitting_columns:
            return
        self._fitting_columns = True
        try:
            extent = self.style().pixelMetric(QStyle.PixelMetric.PM_ScrollBarExtent)
            horizontal_bar = self.horizontalScrollBar()
            # maximumViewportSize() describes the stable cell area with neither
            # scrollbar installed. Deriving this from the current viewport plus
            # whichever bars happen to be visible makes fitting depend on Qt's
            # deferred layout/event order.
            maximum_viewport = self.maximumViewportSize()
            base_width = maximum_viewport.width()
            base_height = maximum_viewport.height()
            row_total = sum(self.rowHeight(row) for row in range(self.model().rowCount()))
            minimum = self._compact_widths or self._natural_widths
            vertical_needed = row_total > base_height
            widths = list(self._natural_widths)
            for _iteration in range(3):
                available_width = max(0, base_width - (extent if vertical_needed else 0))
                widths = self._allocate_column_widths(
                    self._natural_widths, minimum, available_width
                )
                horizontal_needed = sum(widths) > available_width
                available_height = max(
                    0, base_height - (extent if horizontal_needed else 0)
                )
                updated_vertical = row_total > available_height
                if updated_vertical == vertical_needed:
                    break
                vertical_needed = updated_vertical
            for column, width in enumerate(widths):
                self.setColumnWidth(column, width)
            overflow = max(0, sum(widths) - available_width)
            self.updateGeometries()
            horizontal_bar.setPageStep(available_width)
            horizontal_bar.setRange(0, overflow)
        finally:
            self._fitting_columns = False

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit_columns_to_view()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        metric_events = {
            QEvent.Type.FontChange,
            QEvent.Type.ApplicationFontChange,
            QEvent.Type.StyleChange,
        }
        if event.type() in metric_events and hasattr(self, "_min_col_w") \
                and not self._configuring_display:
            horizontal = self.horizontalHeader()
            vertical = self.verticalHeader()
            if isinstance(horizontal, EditableAxisHeader):
                horizontal.refresh_labels()
            if isinstance(vertical, EditableAxisHeader):
                vertical.refresh_labels()
            self.autofit_columns(self._min_col_w)

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
        if real is None or not math.isfinite(float(real)):
            self._set_live_index(None)
            return
        if self._live_lookup_values is None or self._live_lookup_entries is None:
            self._build_live_value_lookup()
        values = self._live_lookup_values or []
        entries = self._live_lookup_entries or []
        if not values:
            self._set_live_index(None)
            return
        position = bisect_left(values, float(real))
        candidates = {
            candidate
            for candidate in (position - 1, position)
            if 0 <= candidate < len(values)
        }
        best = min(
            candidates,
            key=lambda candidate: (
                abs(values[candidate] - float(real)),
                entries[candidate][0],
            ),
        )
        self._set_live_index(entries[best][1])

    def _invalidate_live_value_lookup(self) -> None:
        self._live_lookup_values = None
        self._live_lookup_entries = None

    def _build_live_value_lookup(self) -> None:
        """Cache a sorted real-value-to-cell index for logarithmic live lookup."""
        model = self.model()
        sx, sy = model.table.shape()
        by_value: dict[float, tuple[int, QModelIndex]] = {}
        for x in range(sx):
            for y in range(sy):
                cell_real = float(
                    model.current_scale.to_real(model.table.cell_at(x, y).raw)
                )
                if not math.isfinite(cell_real):
                    continue
                ordinal = x * sy + y
                existing = by_value.get(cell_real)
                if existing is None or ordinal < existing[0]:
                    by_value[cell_real] = (ordinal, model.index_for_cell(x, y))
        ordered = sorted(by_value.items())
        self._live_lookup_values = [value for value, _entry in ordered]
        self._live_lookup_entries = [entry for _value, entry in ordered]

    @staticmethod
    def _same_index(left, right) -> bool:
        if left is None or right is None:
            return left is right
        return left.isValid() and right.isValid() \
            and left.row() == right.row() and left.column() == right.column()

    def _set_live_index(self, index) -> None:
        previous = self._live_index
        if self._same_index(previous, index):
            return
        self._live_index = index
        self.model().set_live_cell(index)
        for dirty in (previous, index):
            if dirty is None or not dirty.isValid():
                continue
            rect = self.visualRect(dirty)
            if rect.isValid() and not rect.isEmpty():
                self.viewport().update(rect)
