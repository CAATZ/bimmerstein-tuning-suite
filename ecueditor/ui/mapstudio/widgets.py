from __future__ import annotations

import math
from collections.abc import Callable

import numpy as np
from PySide6.QtCore import (
    QEvent,
    QItemSelection,
    QItemSelectionModel,
    QPoint,
    QSignalBlocker,
    QSize,
    Qt,
    Signal,
    Slot,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetrics,
    QPainter,
    QPen,
    QPolygon,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractScrollArea,
    QApplication,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QSizePolicy,
    QStyle,
    QStyleOptionHeader,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QWidget,
)

from ecueditor.ui.design.colormaps import COLORMAPS, heat_color, text_color_for
from ecueditor.ui.design.fonts import numeric_font
from ecueditor.ui.design.theme_manager import current_theme

_MASK_ROLE = int(Qt.ItemDataRole.UserRole) + 1
_MIN_ZOOM_PERCENT = 20
_MAX_ZOOM_PERCENT = 180
_HEADER_TEXT_SAFETY = 2
_UNSET = object()


def _item_text(item: QTableWidgetItem | None) -> str:
    return "" if item is None else item.text()


def _fixed_axis(value: float, decimals: int) -> str:
    label = f"{float(value):.{decimals}f}"
    if decimals:
        label = label.rstrip("0").rstrip(".")
    return "0" if label in {"-0", "+0"} else label


def _format_axis_values(
    values,
    formatter: Callable[[float], str] | None = None,
) -> list[str]:
    """Use the coarsest labels that still distinguish every distinct breakpoint."""
    numeric = [float(value) for value in values]
    if formatter is not None:
        return [str(formatter(value)) for value in numeric]
    for decimals in range(13):
        labels = [_fixed_axis(value, decimals) for value in numeric]
        seen: dict[str, float] = {}
        collision = False
        for label, value in zip(labels, numeric):
            previous = seen.setdefault(label, value)
            if previous != value:
                collision = True
                break
        if not collision:
            return labels
    return [f"{value:.17g}" for value in numeric]


def _mix(left: QColor, right: QColor, amount: float) -> QColor:
    amount = max(0.0, min(1.0, float(amount)))
    return QColor(
        round(left.red() + (right.red() - left.red()) * amount),
        round(left.green() + (right.green() - left.green()) * amount),
        round(left.blue() + (right.blue() - left.blue()) * amount),
    )


class _StudioAxisHeader(QHeaderView):
    """Style-aware Studio axis header using the same metric contract as main grids."""

    def __init__(self, orientation: Qt.Orientation, parent=None) -> None:
        super().__init__(orientation, parent)

    def _label(self, logical_index: int) -> str:
        model = self.model()
        if model is None:
            return ""
        value = model.headerData(
            logical_index,
            self.orientation(),
            Qt.ItemDataRole.DisplayRole,
        )
        return "" if value is None else str(value)

    def styled_label_size(
        self,
        logical_index: int,
        text: str | None = None,
        font: QFont | None = None,
    ) -> QSize:
        """Measure text with this header's chrome, independent of its live font.

        Qt's stylesheet proxy can ignore a hypothetical ``option.fontMetrics`` and
        re-measure with the widget's current font.  Fit probes evaluate many zoom
        fonts without mutating the live header, so derive the style's decoration
        extents with the live font and add them to the requested font explicitly.
        """
        self.ensurePolished()
        option = QStyleOptionHeader()
        self.initStyleOptionForIndex(option, logical_index)
        option.text = self._label(logical_index) if text is None else str(text)
        live_metrics = QFontMetrics(self.font())
        option.fontMetrics = live_metrics
        live_contents = QSize(
            live_metrics.horizontalAdvance(option.text), live_metrics.height()
        )
        styled = self.style().sizeFromContents(
            QStyle.ContentsType.CT_HeaderSection,
            option,
            live_contents,
            self,
        )
        if font is None or font == self.font():
            return styled
        target_metrics = QFontMetrics(font)
        horizontal_chrome = max(0, styled.width() - live_contents.width())
        vertical_chrome = max(0, styled.height() - live_contents.height())
        return QSize(
            target_metrics.horizontalAdvance(option.text) + horizontal_chrome,
            target_metrics.height() + vertical_chrome,
        )

    def sectionSizeFromContents(self, logical_index: int) -> QSize:
        base = super().sectionSizeFromContents(logical_index)
        styled = self.styled_label_size(logical_index)
        if self.orientation() == Qt.Orientation.Horizontal:
            return QSize(
                max(self.minimumSectionSize(), styled.width()),
                max(base.height(), styled.height()),
            )
        # Vertical sections are explicitly row-sized by the table.  Their custom
        # painter needs the glyph height, not QSS's additional top/bottom padding;
        # letting stylesheet chrome inflate this hint breaks Compact parity and
        # makes Fit believe every data row is taller than the actual cell.
        glyph_height = QFontMetrics(self.font()).height()
        return QSize(
            max(1, styled.width()),
            max(self.minimumSectionSize(), glyph_height),
        )

    def sizeHint(self) -> QSize:
        hint = super().sizeHint()
        if self.orientation() != Qt.Orientation.Vertical:
            return hint
        model = self.model()
        count = 0 if model is None else model.rowCount()
        label_width = max(
            (self.styled_label_size(section).width() for section in range(count)),
            default=0,
        )
        return QSize(max(1, label_width), hint.height())

    def paintSection(self, painter: QPainter, rect, logical_index: int) -> None:
        if not rect.isValid():
            return
        # Paint both orientations explicitly.  Delegating the horizontal axis to
        # QHeaderView lets the global QSS pseudo-section substitute the larger
        # application font instead of this header's zoom-scaled numeric font.
        theme = current_theme()
        value = self.model().headerData(
            logical_index,
            self.orientation(),
            Qt.ItemDataRole.DisplayRole,
        )
        painter.save()
        painter.fillRect(rect, QColor(theme.surface3))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(theme.grid_line), 1))
        painter.drawRect(rect.adjusted(0, 0, -1, -1))
        painter.setFont(self.font())
        painter.setPen(QColor(theme.text_dim))
        painter.drawText(
            rect.adjusted(2, 0, -3, 0),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            "" if value is None else str(value),
        )
        painter.restore()


class _StudioCellDelegate(QStyledItemDelegate):
    """Preserve heat colors while painting Studio cell-state overlays."""

    def createEditor(self, parent, _option, _index):
        editor = QLineEdit(parent)
        editor.setFrame(False)
        editor.setAlignment(Qt.AlignmentFlag.AlignRight)
        return editor

    def setEditorData(self, editor, index) -> None:
        editor.setText(str(index.data(Qt.ItemDataRole.EditRole)))
        editor.selectAll()

    def setModelData(self, editor, model, index) -> None:
        try:
            value = float(editor.text().strip())
        except ValueError:
            return
        if not math.isfinite(value):
            return
        view = self.parent()
        if isinstance(view, ArrayTableWidget):
            view.set_selected_value(value, edited_index=index)
        else:
            model.setData(index, value, Qt.ItemDataRole.EditRole)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        theme = current_theme()
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        base_option = QStyleOptionViewItem(option)
        self.initStyleOption(base_option, index)
        view = self.parent()
        base_option.state &= ~QStyle.StateFlag.State_Selected
        base_option.state &= ~QStyle.StateFlag.State_HasFocus
        selection_fill: QColor | None = None
        if selected:
            dragging = isinstance(view, ArrayTableWidget) and view.selection_drag_active()
            selection_fill = QColor(
                theme.decrease_border if dragging else theme.compare_neutral
            )
        style = option.widget.style() if option.widget is not None else QApplication.style()
        style.drawControl(
            QStyle.ControlElement.CE_ItemViewItem,
            base_option,
            painter,
            option.widget,
        )
        compact_overlay = option.rect.height() < QFontMetrics(base_option.font).height() + 8
        if selection_fill is not None:
            rgb = (
                selection_fill.red(),
                selection_fill.green(),
                selection_fill.blue(),
            )
            painter.save()
            painter.fillRect(option.rect, selection_fill)
            painter.setPen(QColor(*text_color_for(rgb)))
            painter.setFont(base_option.font)
            painter.drawText(
                option.rect.adjusted(3, 0, -3, 0),
                base_option.displayAlignment,
                base_option.text,
            )
            painter.restore()
        if bool(index.data(_MASK_ROLE)):
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(theme.warn), 1 if compact_overlay else 2))
            inset = 0 if compact_overlay else 1
            painter.drawRect(option.rect.adjusted(inset, inset, -inset - 1, -inset - 1))
            painter.restore()
        change = view.change_border(index) if isinstance(view, ArrayTableWidget) else None
        if change is not None:
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            color = QColor(
                theme.increase_border
                if change == "increase"
                else theme.decrease_border
            )
            painter.setBrush(Qt.BrushStyle.NoBrush)
            border_width = 1 if compact_overlay else 2
            inset = 1 if compact_overlay else 2
            painter.setPen(QPen(color, border_width))
            painter.drawRect(option.rect.adjusted(inset, inset, -inset - 1, -inset - 1))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            marker_size = 3 if compact_overlay else 6
            if change == "increase":
                corner = option.rect.topRight() + QPoint(-inset - 1, inset + 1)
                triangle = QPolygon(
                    [
                        corner,
                        corner + QPoint(-marker_size, 0),
                        corner + QPoint(0, marker_size),
                    ]
                )
            else:
                corner = option.rect.bottomRight() + QPoint(-inset - 1, -inset - 1)
                triangle = QPolygon(
                    [
                        corner,
                        corner + QPoint(-marker_size, 0),
                        corner + QPoint(0, -marker_size),
                    ]
                )
            painter.drawPolygon(triangle)
            painter.restore()
        if isinstance(view, ArrayTableWidget) and view.is_current_index(index):
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            if compact_overlay:
                painter.setPen(QPen(QColor(theme.sel_ring), 1))
                painter.drawRect(option.rect.adjusted(0, 0, -1, -1))
            else:
                painter.setPen(QPen(QColor(theme.sel_ring), 3))
                painter.drawRect(option.rect.adjusted(1, 1, -2, -2))
                painter.setPen(QPen(QColor(theme.sel_ring_inner), 1))
                painter.drawRect(option.rect.adjusted(3, 3, -4, -4))
            painter.restore()


class ArrayTableWidget(QTableWidget):
    """Compact, theme-aware numerical grid for local Map Studio snapshots."""

    valuesEdited = Signal()
    valuesSynchronized = Signal()
    zoomChanged = Signal(int)

    def __init__(self, parent=None, *, colormap: str = "rainbow") -> None:
        super().__init__(parent)
        self._loading = False
        self._editable = False
        self._decimals = 3
        self._mask: np.ndarray | None = None
        self._array = np.empty((0, 0), dtype=float)
        self._edit_baseline: np.ndarray | None = None
        self._difference = False
        self._colormap = colormap if colormap in COLORMAPS else "rainbow"
        self._color_cells = True
        self._density = "normal"
        self._preferred_column_floor = 42
        self._compact_column_floor = 36
        self._row_floor = 18
        self._row_padding = 12
        self._base_font_size = 11
        self._natural_widths: list[int] = []
        self._compact_widths: list[int] = []
        self._natural_row_heights: list[int] = []
        self._natural_vertical_header_width = 0
        self._natural_horizontal_header_height = 0
        self._current_preferred_widths: list[int] = []
        self._current_minimum_widths: list[int] = []
        self._fitting_sections = False
        self._configuring_display = False
        self._zoom_percent = 100
        self._selection_drag_active = False
        self._x_values: np.ndarray | None = None
        self._y_values: np.ndarray | None = None
        self._x_labels: list[str] = []
        self._y_labels: list[str] = []
        self._x_formatter: Callable[[float], str] | None = None
        self._y_formatter: Callable[[float], str] | None = None

        self.setObjectName("mapStudioTable")
        self.setHorizontalHeader(_StudioAxisHeader(Qt.Orientation.Horizontal, self))
        self.setVerticalHeader(_StudioAxisHeader(Qt.Orientation.Vertical, self))
        self.setItemDelegate(_StudioCellDelegate(self))
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setSizeAdjustPolicy(QAbstractScrollArea.SizeAdjustPolicy.AdjustIgnored)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(280, 180)
        self.setAlternatingRowColors(False)
        self.setWordWrap(False)
        self.setShowGrid(True)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.horizontalHeader().setStretchLastSection(False)
        self.horizontalHeader().setMinimumSectionSize(28)
        self.verticalHeader().setMinimumSectionSize(14)
        self.itemChanged.connect(self._on_item_changed)
        self.selectionModel().selectionChanged.connect(lambda *_args: self.viewport().update())
        self.selectionModel().currentChanged.connect(lambda *_args: self.viewport().update())

        app = QApplication.instance()
        manager = app.property("ecueditor_theme_manager") if app is not None else None
        if manager is not None and hasattr(manager, "changed"):
            manager.changed.connect(self._theme_changed)
        self._configuring_display = True
        try:
            self._set_scaled_font(self._font_for_zoom(self._zoom_percent))
        finally:
            self._configuring_display = False

    @Slot(object)
    def _theme_changed(self, _theme) -> None:
        self.refresh_colors()
        self.horizontalHeader().viewport().update()
        self.verticalHeader().viewport().update()

    @property
    def colormap(self) -> str:
        return self._colormap

    @property
    def zoom_percent(self) -> int:
        return self._zoom_percent

    @property
    def difference(self) -> bool:
        return self._difference

    @property
    def editable(self) -> bool:
        return self._editable

    def value_range(self) -> tuple[float, float]:
        if not self._array.size:
            return 0.0, 0.0
        return float(np.min(self._array)), float(np.max(self._array))

    def configure_display(
        self,
        *,
        font_size: int = 11,
        density: str = "normal",
        color_cells: bool = True,
    ) -> None:
        """Project the main grid's deterministic Normal/Compact display contract."""
        self._density = "compact" if density == "compact" else "normal"
        compact = self._density == "compact"
        self._base_font_size = max(7, int(font_size) - 3) if compact else max(7, int(font_size))
        self._preferred_column_floor = 30 if compact else 42
        self._compact_column_floor = 28 if compact else 36
        self._row_floor = 14 if compact else 18
        self._row_padding = 2 if compact else 12
        self._color_cells = bool(color_cells)
        self.horizontalHeader().setMinimumSectionSize(self._compact_column_floor)
        self.verticalHeader().setMinimumSectionSize(self._row_floor)
        self._configuring_display = True
        try:
            self._set_scaled_font(self._font_for_zoom(self._zoom_percent))
        finally:
            self._configuring_display = False
        self._rebuild_natural_metrics()
        self._apply_dimensions()
        self.refresh_colors()

    def set_axis_formatters(
        self,
        x_formatter: Callable[[float], str] | None,
        y_formatter: Callable[[float], str] | None,
    ) -> None:
        self._x_formatter = x_formatter
        self._y_formatter = y_formatter

    def set_colormap(self, name: str) -> None:
        normalized = name if name in COLORMAPS else "rainbow"
        if normalized == self._colormap:
            return
        self._colormap = normalized
        self.refresh_colors()

    def set_zoom(self, percent: int) -> None:
        self._set_zoom_percent(percent)

    def _set_zoom_percent(self, percent: int) -> None:
        percent = max(_MIN_ZOOM_PERCENT, min(_MAX_ZOOM_PERCENT, int(round(percent))))
        if percent == self._zoom_percent:
            self._apply_dimensions()
            return
        self._zoom_percent = percent
        self._apply_dimensions()
        self.zoomChanged.emit(percent)

    def zoom_in(self) -> None:
        target = math.ceil((self._zoom_percent + 1) / 10.0) * 10
        self._set_zoom_percent(target)

    def zoom_out(self) -> None:
        target = math.floor((self._zoom_percent - 1) / 10.0) * 10
        self._set_zoom_percent(target)

    def fit_to_view(self) -> None:
        if self.rowCount() < 1 or self.columnCount() < 1:
            return
        previous = self._zoom_percent
        target = self._fit_zoom_percent(upper=100)
        self._zoom_percent = target
        self._apply_dimensions()

        # Header extents settle synchronously in _apply_dimensions(), but re-check once
        # against the polished viewport and only allow a conservative shrink.
        corrected = self._fit_zoom_percent(upper=target)
        if corrected < target:
            target = corrected
            self._zoom_percent = target
            self._apply_dimensions()
        if target != previous:
            self.zoomChanged.emit(target)

    def _fit_zoom_percent(self, *, upper: int) -> int:
        rows, columns = self.rowCount(), self.columnCount()
        if rows < 1 or columns < 1:
            return self._zoom_percent
        upper = max(_MIN_ZOOM_PERCENT, min(100, int(upper)))
        for percent in range(upper, _MIN_ZOOM_PERCENT - 1, -1):
            preferred, minimum, row_heights, font = self._scaled_geometry(percent)
            vertical_width, horizontal_height = self._header_extents(font)
            available_width, available_height = self._viewport_budget_for_headers(
                vertical_width, horizontal_height
            )
            if sum(minimum) <= available_width and sum(row_heights) <= available_height:
                return percent
        return _MIN_ZOOM_PERCENT

    def _scaled_geometry(
        self, percent: int
    ) -> tuple[list[int], list[int], list[int], QFont]:
        self.ensurePolished()
        self.horizontalHeader().ensurePolished()
        self.verticalHeader().ensurePolished()
        scale = max(0.01, percent / 100.0)
        font = self._font_for_zoom(percent)
        metrics = QFontMetrics(font)
        preferred_floor = max(1, math.floor(self._preferred_column_floor * scale))
        compact_floor = max(1, math.floor(self._compact_column_floor * scale))
        # Match the main grid's content-safe text allowance at 100%.  The
        # delegate's chrome does not scale all the way to zero, so retain a
        # six-pixel floor while still allowing very dense explicit Fit views.
        value_padding = max(6, math.floor(8 * scale))
        preferred: list[int] = []
        minimum: list[int] = []
        header = self.horizontalHeader()
        for column in range(self.columnCount()):
            text_width = max(
                (
                    metrics.horizontalAdvance(_item_text(self.item(row, column)))
                    for row in range(self.rowCount())
                ),
                default=0,
            )
            value_width = text_width + value_padding
            label = self._x_labels[column] if column < len(self._x_labels) else ""
            candidates = self._axis_label_candidates(column)
            preferred_header = self._styled_header_size(
                header, column, label, font
            ).width() + _HEADER_TEXT_SAFETY
            compact_header = min(
                (
                    self._styled_header_size(
                        header, column, candidate, font
                    ).width() + _HEADER_TEXT_SAFETY
                    for candidate in candidates
                ),
                default=preferred_header,
            )
            compact_width = max(compact_floor, value_width, compact_header)
            minimum.append(compact_width)
            preferred.append(max(preferred_floor, compact_width, preferred_header))

        scaled_row_floor = max(1, math.floor(self._row_floor * scale))
        scaled_padding = max(1, math.floor(self._row_padding * scale))
        value_height = metrics.height() + scaled_padding
        row_heights = []
        for row in range(self.rowCount()):
            row_heights.append(max(scaled_row_floor, value_height))
        return preferred, minimum, row_heights, font

    def _font_for_zoom(self, percent: int) -> QFont:
        scale = max(0.01, percent / 100.0)
        size = max(4, math.floor(self._base_font_size * scale))
        return numeric_font(size)

    @staticmethod
    def _styled_header_size(
        header: QHeaderView, section: int, text: str, font: QFont
    ) -> QSize:
        if isinstance(header, _StudioAxisHeader):
            return header.styled_label_size(section, text, font)
        metrics = QFontMetrics(font)
        return QSize(metrics.horizontalAdvance(text) + 8, metrics.height() + 4)

    def _header_extents(self, font: QFont) -> tuple[int, int]:
        vertical = self.verticalHeader()
        horizontal = self.horizontalHeader()
        vertical_width = max(
            (
                self._styled_header_size(
                    vertical,
                    row,
                    self._y_labels[row] if row < len(self._y_labels) else "Value",
                    font,
                ).width()
                for row in range(self.rowCount())
            ),
            default=vertical.sizeHint().width(),
        )
        horizontal_height = max(
            (
                self._styled_header_size(
                    horizontal,
                    column,
                    self._x_labels[column] if column < len(self._x_labels) else "",
                    font,
                ).height()
                for column in range(self.columnCount())
            ),
            default=horizontal.sizeHint().height(),
        )
        return max(1, vertical_width), max(1, horizontal_height)

    def _viewport_budget_for_headers(
        self, vertical_width: int, horizontal_height: int
    ) -> tuple[int, int]:
        available = self.maximumViewportSize()
        current_vertical = self.verticalHeader().width()
        current_horizontal = self.horizontalHeader().height()
        return (
            max(1, available.width() + current_vertical - vertical_width),
            max(1, available.height() + current_horizontal - horizontal_height),
        )

    def _set_scaled_font(self, font: QFont) -> None:
        self.setFont(font)
        self.horizontalHeader().setFont(font)
        self.verticalHeader().setFont(font)
        # QTableWidget header items otherwise retain QApplication's default font.
        # Keep their FontRole in the same projection so style-option metrics and
        # section geometry remain synchronized whenever zoom changes.
        for column in range(self.columnCount()):
            item = self.horizontalHeaderItem(column)
            if item is not None:
                item.setFont(font)
        for row in range(self.rowCount()):
            item = self.verticalHeaderItem(row)
            if item is not None:
                item.setFont(font)

    def _apply_dimensions(self) -> None:
        font = self._font_for_zoom(self._zoom_percent)
        self._configuring_display = True
        try:
            self.horizontalHeader().setMinimumSectionSize(1)
            self.verticalHeader().setMinimumSectionSize(1)
            self._set_scaled_font(font)
            self.horizontalHeader().updateGeometry()
            self.verticalHeader().updateGeometry()
            self.updateGeometries()
            preferred, minimum, row_heights, _font = self._scaled_geometry(
                self._zoom_percent
            )
            self._current_preferred_widths = preferred
            self._current_minimum_widths = minimum
            for row, height in enumerate(row_heights):
                self.setRowHeight(row, height)
        finally:
            self._configuring_display = False
        self.horizontalHeader().updateGeometry()
        self.verticalHeader().updateGeometry()
        self.updateGeometries()
        self._fit_sections_to_view()
        self._refresh_axis_labels()
        self.updateGeometry()

    @staticmethod
    def _allocate_column_widths(
        preferred: list[int], minimum: list[int], available: int
    ) -> list[int]:
        """Continuously distribute a constrained viewport without a scale cliff."""
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

    def _fit_sections_to_view(self) -> None:
        if not self._current_preferred_widths or self._fitting_sections:
            return
        if not self.isVisible():
            # Hidden tab pages have no trustworthy viewport yet.  Keep their
            # natural 100%-scale geometry and exact labels; show/resize performs
            # the first viewport allocation once Qt has real dimensions.
            for column, width in enumerate(self._current_preferred_widths):
                self.setColumnWidth(column, width)
            self.updateGeometries()
            return
        self._fitting_sections = True
        try:
            extent = self.style().pixelMetric(QStyle.PixelMetric.PM_ScrollBarExtent)
            maximum_viewport = self.maximumViewportSize()
            base_width = maximum_viewport.width()
            base_height = maximum_viewport.height()
            row_total = sum(self.rowHeight(row) for row in range(self.rowCount()))
            vertical_needed = row_total > base_height
            widths = list(self._current_preferred_widths)
            available_width = base_width
            available_height = base_height
            for _iteration in range(3):
                available_width = max(0, base_width - (extent if vertical_needed else 0))
                widths = self._allocate_column_widths(
                    self._current_preferred_widths,
                    self._current_minimum_widths,
                    available_width,
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
            horizontal_overflow = max(0, sum(widths) - available_width)
            vertical_overflow = max(0, row_total - available_height)
            self.updateGeometries()
            horizontal_bar = self.horizontalScrollBar()
            horizontal_bar.setPageStep(available_width)
            horizontal_bar.setRange(0, horizontal_overflow)
            vertical_bar = self.verticalScrollBar()
            vertical_bar.setPageStep(available_height)
            vertical_bar.setRange(0, vertical_overflow)
        finally:
            self._fitting_sections = False

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit_sections_to_view()
        self._refresh_axis_labels()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._fit_sections_to_view()
        self._refresh_axis_labels()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        metric_events = {
            QEvent.Type.FontChange,
            QEvent.Type.ApplicationFontChange,
            QEvent.Type.StyleChange,
        }
        if event.type() in metric_events and hasattr(self, "_natural_widths") \
                and not self._configuring_display:
            self._rebuild_natural_metrics()
            self._apply_dimensions()

    def set_values(
        self,
        values,
        *,
        x=None,
        y=None,
        editable: bool = False,
        decimals: int = 3,
        mask: np.ndarray | None = None,
        difference: bool = False,
    ) -> None:
        array = np.asarray(values, dtype=float)
        if array.ndim == 1:
            array = array.reshape(1, -1)
        self._loading = True
        blocker = QSignalBlocker(self)
        self._editable = editable
        self._decimals = max(0, min(12, int(decimals)))
        self._mask = None if mask is None else np.asarray(mask, dtype=bool).copy()
        self._array = array.copy()
        self._edit_baseline = array.copy() if editable else None
        self._difference = bool(difference)
        self._x_values = None if x is None else np.asarray(x, dtype=float).copy()
        self._y_values = None if y is None else np.asarray(y, dtype=float).copy()
        self._x_labels = [] if x is None else _format_axis_values(x, self._x_formatter)
        self._y_labels = [] if y is None else _format_axis_values(y, self._y_formatter)
        self.clear()
        self.setRowCount(array.shape[0])
        self.setColumnCount(array.shape[1])
        if x is not None:
            for column, (value, label) in enumerate(zip(x, self._x_labels)):
                item = QTableWidgetItem(label)
                item.setToolTip(f"{float(value):.17g}")
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                )
                self.setHorizontalHeaderItem(column, item)
        if y is not None:
            for row, (value, label) in enumerate(zip(y, self._y_labels)):
                item = QTableWidgetItem(label)
                item.setToolTip(f"{float(value):.17g}")
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                )
                self.setVerticalHeaderItem(row, item)
        elif array.shape[0] == 1:
            self.setVerticalHeaderLabels(["Value"])
        for row in range(array.shape[0]):
            for column in range(array.shape[1]):
                value = float(array[row, column])
                item = QTableWidgetItem(f"{value:.{self._decimals}f}")
                flags = Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
                if editable:
                    flags |= Qt.ItemFlag.ItemIsEditable
                item.setFlags(flags)
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                )
                item.setData(Qt.ItemDataRole.UserRole, value)
                masked = bool(
                    self._mask is not None
                    and self._mask.shape == array.shape
                    and self._mask[row, column]
                )
                item.setData(_MASK_ROLE, masked)
                item.setToolTip(f"{value:.17g}" + ("\nExtrapolated" if masked else ""))
                self.setItem(row, column, item)
        self._rebuild_natural_metrics()
        self._apply_dimensions()
        self.refresh_colors()
        self._loading = False
        del blocker
        self.valuesSynchronized.emit()

    def _rebuild_natural_metrics(self) -> bool:
        """Cache the complete 100%-scale geometry used by sizeHint and fitting."""
        self.ensurePolished()
        self.horizontalHeader().ensurePolished()
        self.verticalHeader().ensurePolished()
        previous = (
            tuple(self._natural_widths),
            tuple(self._compact_widths),
            tuple(self._natural_row_heights),
            self._natural_vertical_header_width,
            self._natural_horizontal_header_height,
        )
        natural, compact, rows, font = self._scaled_geometry(100)
        vertical_width, horizontal_height = self._header_extents(font)
        self._natural_widths = natural
        self._compact_widths = compact
        self._natural_row_heights = rows
        self._natural_vertical_header_width = vertical_width
        self._natural_horizontal_header_height = horizontal_height
        current = (
            tuple(self._natural_widths),
            tuple(self._compact_widths),
            tuple(self._natural_row_heights),
            self._natural_vertical_header_width,
            self._natural_horizontal_header_height,
        )
        return current != previous

    def _color_signature(self, array: np.ndarray | None = None):
        values = self._array if array is None else np.asarray(array, dtype=float)
        if not values.size:
            return (self._difference, 0.0, 0.0)
        minimum, maximum = float(np.min(values)), float(np.max(values))
        if self._difference:
            return (True, max(abs(minimum), abs(maximum)))
        return (False, minimum, maximum)

    def update_values(self, values, *, mask=_UNSET) -> None:
        """Synchronize same-shape values without replacing headers, items, or selection."""
        array = np.asarray(values, dtype=float)
        if array.ndim == 1:
            array = array.reshape(1, -1)
        if array.shape != self._array.shape or array.shape != (
            self.rowCount(),
            self.columnCount(),
        ):
            raise ValueError("updated values must match the existing table shape")
        if not np.all(np.isfinite(array)):
            raise ValueError("updated values must be finite")

        old_signature = self._color_signature()
        value_changes = array != self._array
        next_mask = self._mask
        mask_changes = np.zeros(array.shape, dtype=bool)
        if mask is not _UNSET:
            next_mask = None if mask is None else np.asarray(mask, dtype=bool).copy()
            if next_mask is not None and next_mask.shape != array.shape:
                raise ValueError("updated mask must match the existing table shape")
            old_mask = (
                np.zeros(array.shape, dtype=bool)
                if self._mask is None
                else self._mask
            )
            new_mask = (
                np.zeros(array.shape, dtype=bool)
                if next_mask is None
                else next_mask
            )
            mask_changes = old_mask != new_mask
        changed: list[tuple[int, int]] = [
            (int(coordinate[0]), int(coordinate[1]))
            for coordinate in np.argwhere(value_changes | mask_changes)
        ]
        if not changed:
            self._array = array.copy()
            self._mask = next_mask
            self.valuesSynchronized.emit()
            return

        self._array = array.copy()
        self._mask = next_mask
        blocker = QSignalBlocker(self)
        self.setUpdatesEnabled(False)
        try:
            for row, column in changed:
                item = self.item(row, column)
                if item is None:
                    continue
                value = float(array[row, column])
                item.setText(f"{value:.{self._decimals}f}")
                item.setData(Qt.ItemDataRole.UserRole, value)
                masked = bool(next_mask is not None and next_mask[row, column])
                item.setData(_MASK_ROLE, masked)
                item.setToolTip(f"{value:.17g}" + ("\nExtrapolated" if masked else ""))
            if self._rebuild_natural_metrics():
                self._apply_dimensions()
            recolor = None if old_signature != self._color_signature() else changed
            self.refresh_colors(recolor)
        finally:
            self.setUpdatesEnabled(True)
            del blocker
        self.viewport().update()
        self.valuesSynchronized.emit()

    @staticmethod
    def _short_axis_candidates(value: float) -> list[str]:
        magnitude = abs(value)
        suffix = ""
        divisor = 1.0
        if magnitude >= 1.0e9:
            suffix, divisor = "G", 1.0e9
        elif magnitude >= 1.0e6:
            suffix, divisor = "M", 1.0e6
        elif magnitude >= 1.0e3:
            suffix, divisor = "k", 1.0e3
        scaled = value / divisor
        candidates = []
        for decimals in (2, 1, 0):
            label = _fixed_axis(scaled, decimals) + suffix
            if label not in candidates:
                candidates.append(label)
        return candidates

    def _axis_label_candidates(self, column: int) -> list[str]:
        preferred = self._x_labels[column] if column < len(self._x_labels) else ""
        candidates = [preferred]
        if self._x_values is not None and column < len(self._x_values):
            candidates.extend(self._short_axis_candidates(float(self._x_values[column])))
        return list(dict.fromkeys(candidates))

    def _refresh_axis_labels(self) -> None:
        if not self._x_labels or self.columnCount() != len(self._x_labels):
            return
        header = self.horizontalHeader()
        font = header.font()
        for column, preferred in enumerate(self._x_labels):
            item = self.horizontalHeaderItem(column)
            if item is None:
                continue
            label = preferred
            candidates = self._axis_label_candidates(column)
            for candidate in candidates:
                if self._styled_header_size(
                    header, column, candidate, font
                ).width() + _HEADER_TEXT_SAFETY <= self.columnWidth(column):
                    label = candidate
                    break
            else:
                label = candidates[-1]
            item.setText(label)

    def refresh_colors(self, coordinates: list[tuple[int, int]] | None = None) -> None:
        if self._array.shape != (self.rowCount(), self.columnCount()):
            self.viewport().update()
            return
        theme = current_theme()
        minimum, maximum = self.value_range()
        span = maximum - minimum
        extent = max(abs(minimum), abs(maximum), 1.0e-12)
        neutral = QColor(theme.compare_neutral)
        decrease = QColor(theme.decrease_border)
        increase = QColor(theme.increase_border)
        blocker = QSignalBlocker(self)
        try:
            cells = (
                (
                    (row, column)
                    for row in range(self.rowCount())
                    for column in range(self.columnCount())
                )
                if coordinates is None
                else iter(coordinates)
            )
            for row, column in cells:
                item = self.item(row, column)
                if item is None:
                    continue
                value = float(self._array[row, column])
                if not self._color_cells:
                    background = QColor(theme.surface1)
                    foreground = QColor(theme.text)
                elif self._difference:
                    ratio = min(1.0, abs(value) / extent)
                    background = _mix(neutral, increase if value > 0 else decrease, ratio)
                    foreground = QColor(
                        *text_color_for(
                            (background.red(), background.green(), background.blue())
                        )
                    )
                else:
                    ratio = 0.0 if span == 0 else (value - minimum) / span
                    rgb = heat_color(ratio, self._colormap)
                    background = QColor(*rgb)
                    foreground = QColor(*text_color_for(rgb))
                item.setBackground(QBrush(background))
                item.setForeground(QBrush(foreground))
        finally:
            del blocker
        self.viewport().update()

    def values(self) -> np.ndarray:
        if self._array.shape != (self.rowCount(), self.columnCount()):
            raise ValueError("table dimensions do not match the cached values")
        return self._array.copy()

    def change_border(self, index) -> str | None:
        """Return the main-grid edit direction relative to this grid's load point."""
        if not self._editable or self._edit_baseline is None or not index.isValid():
            return None
        if self._edit_baseline.shape != self._array.shape:
            return None
        row, column = index.row(), index.column()
        if not (0 <= row < self._array.shape[0] and 0 <= column < self._array.shape[1]):
            return None
        current = float(self._array[row, column])
        baseline = float(self._edit_baseline[row, column])
        if current == baseline:
            return None
        return "increase" if current > baseline else "decrease"

    def x_values(self) -> np.ndarray | None:
        """Return an isolated snapshot of the displayed X breakpoints."""
        return None if self._x_values is None else self._x_values.copy()

    def y_values(self) -> np.ndarray | None:
        """Return an isolated snapshot of the displayed Y breakpoints."""
        return None if self._y_values is None else self._y_values.copy()

    def mask_values(self) -> np.ndarray | None:
        """Return an isolated snapshot of the displayed extrapolation mask."""
        return None if self._mask is None else self._mask.copy()

    def selection_mask(self) -> np.ndarray:
        mask = np.zeros((self.rowCount(), self.columnCount()), dtype=bool)
        for index in self.selectedIndexes():
            mask[index.row(), index.column()] = True
        return mask

    def select_mask(self, mask: np.ndarray) -> None:
        selected = np.asarray(mask, dtype=bool)
        if selected.shape != (self.rowCount(), self.columnCount()):
            raise ValueError("selection mask dimensions do not match the table")
        selection = QItemSelection()
        first: tuple[int, int] | None = None
        for row in range(selected.shape[0]):
            columns = np.flatnonzero(selected[row])
            if not columns.size:
                continue
            if first is None:
                first = row, int(columns[0])
            run_start = run_stop = int(columns[0])
            for column in map(int, columns[1:]):
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
        model = self.selectionModel()
        model.select(selection, QItemSelectionModel.SelectionFlag.ClearAndSelect)
        if first is not None:
            model.setCurrentIndex(
                self.model().index(*first),
                QItemSelectionModel.SelectionFlag.NoUpdate,
            )

    def select_rectangle(
        self,
        top: int,
        bottom: int,
        left: int,
        right: int,
    ) -> None:
        if self.rowCount() < 1 or self.columnCount() < 1:
            return
        top, bottom = sorted((max(0, top), min(self.rowCount() - 1, bottom)))
        left, right = sorted((max(0, left), min(self.columnCount() - 1, right)))
        selection = QItemSelection(
            self.model().index(top, left),
            self.model().index(bottom, right),
        )
        model = self.selectionModel()
        model.select(selection, QItemSelectionModel.SelectionFlag.ClearAndSelect)
        model.setCurrentIndex(
            self.model().index(top, left),
            QItemSelectionModel.SelectionFlag.NoUpdate,
        )

    def selection_drag_active(self) -> bool:
        return self._selection_drag_active

    def is_current_index(self, index) -> bool:
        current = self.currentIndex()
        return current.isValid() and current.row() == index.row() \
            and current.column() == index.column()

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

    def _selected_coordinates(self, edited_index=None) -> list[tuple[int, int]]:
        selected = sorted({(index.row(), index.column()) for index in self.selectedIndexes()})
        if edited_index is not None:
            coordinate = (edited_index.row(), edited_index.column())
            if coordinate not in selected or len(selected) < 2:
                return [coordinate]
        return selected

    def _commit_values(
        self,
        proposed: np.ndarray,
        coordinates: list[tuple[int, int]],
    ) -> bool:
        if not self._editable or not coordinates:
            return False
        array = np.asarray(proposed, dtype=float)
        if array.shape != self._array.shape or not np.all(np.isfinite(array)):
            return False
        changed = [
            (row, column)
            for row, column in coordinates
            if array[row, column] != self._array[row, column]
        ]
        if not changed:
            return False
        old_signature = self._color_signature()
        self._array = array.copy()
        blocker = QSignalBlocker(self)
        try:
            for row, column in changed:
                item = self.item(row, column)
                if item is None:
                    continue
                value = float(array[row, column])
                item.setText(f"{value:.{self._decimals}f}")
                item.setData(Qt.ItemDataRole.UserRole, value)
                masked = bool(item.data(_MASK_ROLE))
                item.setToolTip(f"{value:.17g}" + ("\nExtrapolated" if masked else ""))
        finally:
            del blocker
        if self._rebuild_natural_metrics():
            self._apply_dimensions()
        recolor = None if old_signature != self._color_signature() else changed
        self.refresh_colors(recolor)
        self.valuesEdited.emit()
        return True

    def set_selected_value(self, value: float, *, edited_index=None) -> bool:
        if not math.isfinite(float(value)):
            return False
        coordinates = self._selected_coordinates(edited_index)
        proposed = self._array.copy()
        for row, column in coordinates:
            proposed[row, column] = float(value)
        return self._commit_values(proposed, coordinates)

    def transform_selected(
        self,
        operation: Callable[[float, int, int], float],
    ) -> bool:
        coordinates = self._selected_coordinates()
        proposed = self._array.copy()
        try:
            for row, column in coordinates:
                proposed[row, column] = operation(
                    float(proposed[row, column]), row, column
                )
        except (ArithmeticError, TypeError, ValueError):
            return False
        return self._commit_values(proposed, coordinates)

    def copy_selection_text(self, dimension: str) -> str:
        coordinates = self._selected_coordinates()
        if not coordinates:
            return ""
        rows = sorted({row for row, _column in coordinates})
        columns = sorted({column for _row, column in coordinates})
        selected = set(coordinates)
        lines = [f"[Selection{dimension}]"]
        for row in rows:
            tokens = []
            for column in columns:
                item = self.item(row, column)
                tokens.append(
                    item.text() if (row, column) in selected and item is not None else "x"
                )
            lines.append("\t".join(tokens))
        return "\n".join(lines)

    def copy_table_text(self, table_type: str) -> str:
        normalized = table_type.upper()
        header = f"[Table{normalized}]"
        value_rows = [
            "\t".join(
                _item_text(self.item(row, column))
                for column in range(self.columnCount())
            )
            for row in range(self.rowCount())
        ]
        if normalized == "3D":
            lines = [header]
            if self._x_values is not None:
                lines.append(
                    "\t"
                    + "\t".join(
                        f"{float(value):.17g}" for value in self._x_values
                    )
                )
            for row, values in enumerate(value_rows):
                prefix = ""
                if self._y_values is not None:
                    prefix = f"{float(self._y_values[row]):.17g}\t"
                lines.append(prefix + values)
            return "\n".join(lines)
        if normalized == "2D" and self._x_values is not None:
            axis = "\t".join(
                f"{float(value):.17g}" for value in self._x_values
            )
            return "\n".join((header, axis, "\t".join(value_rows)))
        return "\n".join((header, "\t".join(value_rows)))

    @staticmethod
    def _numeric_token(token: str) -> float | None:
        if token in {"", "x"}:
            return None
        try:
            value = float(token)
        except ValueError:
            return None
        return value if math.isfinite(value) else None

    def paste_values_text(self, text: str) -> int:
        if not self._editable or not text.strip():
            return 0
        lines = text.splitlines()
        header = lines[0].strip() if lines else ""
        body = lines[1:] if header.startswith("[") else lines
        selected = self._selected_coordinates()
        anchor = min(selected, default=(0, 0))
        proposed = self._array.copy()
        touched: list[tuple[int, int]] = []

        def place(row: int, column: int, token: str) -> None:
            if not (0 <= row < self.rowCount() and 0 <= column < self.columnCount()):
                return
            value = self._numeric_token(token.strip())
            if value is None:
                return
            proposed[row, column] = value
            touched.append((row, column))

        if header.startswith("[Selection") or not header.startswith("[Table"):
            row0, column0 = anchor
            for row_offset, line in enumerate(body):
                for column_offset, token in enumerate(line.split("\t")):
                    place(row0 + row_offset, column0 + column_offset, token)
        elif header == "[Table3D]":
            data_lines = body
            if data_lines and data_lines[0].startswith("\t"):
                data_lines = data_lines[1:]
            for row, line in enumerate(data_lines):
                tokens = line.split("\t")
                if len(tokens) > self.columnCount():
                    tokens = tokens[-self.columnCount() :]
                for column, token in enumerate(tokens):
                    place(row, column, token)
        elif header == "[Table2D]":
            data_line = body[-1] if body else ""
            for offset, token in enumerate(data_line.split("\t")):
                row, column = divmod(offset, max(1, self.columnCount()))
                place(row, column, token)
        else:
            tokens = [token for line in body for token in line.split("\t")]
            for offset, token in enumerate(tokens):
                row, column = divmod(offset, max(1, self.columnCount()))
                place(row, column, token)

        unique = sorted(set(touched))
        if not unique:
            return 0
        self._commit_values(proposed, unique)
        mask = np.zeros(self._array.shape, dtype=bool)
        for row, column in unique:
            mask[row, column] = True
        self.select_mask(mask)
        return len(unique)

    def interpolate_selected(self) -> bool:
        coordinates = self._selected_coordinates()
        rows = sorted({row for row, _column in coordinates})
        columns = sorted({column for _row, column in coordinates})
        if not rows or not columns or len(coordinates) != len(rows) * len(columns):
            return False
        if rows != list(range(rows[0], rows[-1] + 1)) or columns != list(
            range(columns[0], columns[-1] + 1)
        ):
            return False
        if len(rows) < 2 and len(columns) < 2:
            return False
        x = (
            self._x_values[columns]
            if self._x_values is not None
            else np.asarray(columns, dtype=float)
        )
        y = (
            self._y_values[rows]
            if self._y_values is not None
            else np.asarray(rows, dtype=float)
        )
        if len(columns) > 1 and x[-1] == x[0] or len(rows) > 1 and y[-1] == y[0]:
            return False
        x_weights = (
            np.zeros(len(columns))
            if len(columns) == 1
            else (x - x[0]) / (x[-1] - x[0])
        )
        y_weights = (
            np.zeros(len(rows))
            if len(rows) == 1
            else (y - y[0]) / (y[-1] - y[0])
        )
        proposed = self._array.copy()
        if len(rows) == 1:
            first = proposed[rows[0], columns[0]]
            last = proposed[rows[0], columns[-1]]
            for column, weight in zip(columns, x_weights):
                proposed[rows[0], column] = first + (last - first) * weight
        elif len(columns) == 1:
            first = proposed[rows[0], columns[0]]
            last = proposed[rows[-1], columns[0]]
            for row, weight in zip(rows, y_weights):
                proposed[row, columns[0]] = first + (last - first) * weight
        else:
            top_left = proposed[rows[0], columns[0]]
            top_right = proposed[rows[0], columns[-1]]
            bottom_left = proposed[rows[-1], columns[0]]
            bottom_right = proposed[rows[-1], columns[-1]]
            for row, ty in zip(rows, y_weights):
                for column, tx in zip(columns, x_weights):
                    top = top_left + (top_right - top_left) * tx
                    bottom = bottom_left + (bottom_right - bottom_left) * tx
                    proposed[row, column] = top + (bottom - top) * ty
        return self._commit_values(proposed, coordinates)

    def sizeHint(self) -> QSize:
        if self.rowCount() < 1 or self.columnCount() < 1:
            return super().sizeHint()
        frame = 2 * self.frameWidth()
        vertical_header_width = self.verticalHeader().sizeHint().width()
        horizontal_header_height = self.horizontalHeader().sizeHint().height()
        return QSize(
            vertical_header_width + sum(self._natural_widths) + frame,
            horizontal_header_height
            + sum(self._natural_row_heights)
            + frame,
        )

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._loading or not self._editable:
            return
        try:
            row, column = item.row(), item.column()
            value = float(item.text())
        except (RuntimeError, ValueError):
            self._restore_item(item)
            return
        if not np.isfinite(value):
            self._restore_item(item)
            return
        old_signature = self._color_signature()
        self._array[row, column] = value
        blocker = QSignalBlocker(self)
        item.setData(Qt.ItemDataRole.UserRole, value)
        del blocker
        if self._rebuild_natural_metrics():
            self._apply_dimensions()
        recolor = None if old_signature != self._color_signature() else [(row, column)]
        self.refresh_colors(recolor)
        self.valuesEdited.emit()

    def _restore_item(self, item: QTableWidgetItem) -> None:
        try:
            value = float(item.data(Qt.ItemDataRole.UserRole))
        except (TypeError, ValueError):
            return
        blocker = QSignalBlocker(self)
        item.setText(f"{value:.{self._decimals}f}")
        del blocker


class _ColorRamp(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.colormap = "rainbow"
        self.difference = False
        self.setMinimumSize(120, 10)
        self.setMaximumHeight(10)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        width = max(1, self.width())
        theme = current_theme()
        for x in range(width):
            ratio = x / (width - 1) if width > 1 else 0.0
            if self.difference:
                neutral = QColor(theme.compare_neutral)
                if ratio < 0.5:
                    color = _mix(QColor(theme.decrease_border), neutral, ratio * 2.0)
                else:
                    color = _mix(neutral, QColor(theme.increase_border), (ratio - 0.5) * 2.0)
            else:
                color = QColor(*heat_color(ratio, self.colormap))
            painter.setPen(color)
            painter.drawLine(x, 0, x, self.height())


class ArrayLegend(QWidget):
    """Small shared legend for the active Source, Result, or Changes table."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._table: ArrayTableWidget | None = None
        self.setObjectName("mapStudioLegend")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 4, 10, 5)
        layout.setSpacing(7)
        self.minimum_label = QLabel("0")
        self.maximum_label = QLabel("0")
        self.minimum_label.setFont(numeric_font(8))
        self.maximum_label.setFont(numeric_font(8))
        self.ramp = _ColorRamp(self)
        self.palette_label = QLabel("RAINBOW")
        self.palette_label.setObjectName("mapStudioPaletteLabel")
        layout.addWidget(self.minimum_label)
        layout.addWidget(self.ramp, 1)
        layout.addWidget(self.maximum_label)
        layout.addWidget(self.palette_label)

    def set_table(self, table: ArrayTableWidget) -> None:
        self._table = table
        self.refresh()

    def refresh(self) -> None:
        if self._table is None:
            return
        minimum, maximum = self._table.value_range()
        if self._table.difference:
            extent = max(abs(minimum), abs(maximum))
            minimum, maximum = -extent, extent
        self.minimum_label.setText(f"{minimum:.7g}")
        self.maximum_label.setText(f"{maximum:.7g}")
        self.ramp.colormap = self._table.colormap
        self.ramp.difference = self._table.difference
        self.palette_label.setText(
            "CHANGES" if self._table.difference else self._table.colormap.upper()
        )
        self.ramp.update()


class TableZoomControls(QWidget):
    """Compact fit and zoom controls matching the original ECU Map Studio workflow."""

    def __init__(self, table: ArrayTableWidget, parent=None) -> None:
        super().__init__(parent)
        self.table = table
        self.setObjectName("mapStudioZoomControls")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        self.zoom_out_button = QToolButton()
        self.zoom_out_button.setText("−")
        self.zoom_out_button.setToolTip("Zoom table out")
        self.zoom_out_button.clicked.connect(table.zoom_out)
        self.reset_button = QToolButton()
        self.reset_button.setText(f"{table.zoom_percent}%")
        self.reset_button.setToolTip("Reset table zoom")
        self.reset_button.setMinimumWidth(
            QFontMetrics(self.reset_button.font()).horizontalAdvance("180%") + 20
        )
        self.reset_button.clicked.connect(lambda: table.set_zoom(100))
        self.zoom_in_button = QToolButton()
        self.zoom_in_button.setText("+")
        self.zoom_in_button.setToolTip("Zoom table in")
        self.zoom_in_button.clicked.connect(table.zoom_in)
        self.fit_button = QToolButton()
        self.fit_button.setText("Fit")
        self.fit_button.setToolTip("Fit the complete table into the current view")
        self.fit_button.clicked.connect(table.fit_to_view)
        for button in (
            self.zoom_out_button,
            self.reset_button,
            self.zoom_in_button,
            self.fit_button,
        ):
            layout.addWidget(button)
        table.zoomChanged.connect(lambda percent: self.reset_button.setText(f"{percent}%"))
