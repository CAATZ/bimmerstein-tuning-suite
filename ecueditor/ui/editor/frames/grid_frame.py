"""Grid-backed table frame: header, verbs, axis band, rotated Y caption, legend (spec §5)."""
from __future__ import annotations
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel
from PySide6.QtGui import QPainter
from PySide6.QtCore import Qt, QSize
from ecueditor.ui.design.theme_manager import current_theme
from ecueditor.ui.design.fonts import numeric_font
from ecueditor.ui.editor.table_model import TableGridModel
from ecueditor.ui.editor.table_grid import TableGridWidget
from ecueditor.ui.editor.table_menubar import TableMenuBar
from ecueditor.ui.editor.frames.header import FrameHeader
from ecueditor.ui.editor.frames.legend import LegendStrip


def _clamp_range_text(table) -> str:
    cell = table.cells[0]
    lo, hi = cell.scale.to_real(cell.storage_min), cell.scale.to_real(cell.storage_max)
    lo, hi = min(lo, hi), max(lo, hi)
    fmt = cell.scale.format_value
    return f"range {fmt(lo)} … {fmt(hi)} {cell.scale.units}".rstrip()


def _needs_transpose(table) -> bool:
    # Column-shaped tables (1xN) always read better horizontally (spec §5/B3). In real data only
    # 2D curve defs are column-shaped (3D maps are full grids), so this shape test is equivalent to
    # the spec's "2D column def" wording while also covering the type='3D' column test fixture.
    sx, sy = table.shape()
    return sx == 1 and sy > 1


class _RotatedLabel(QWidget):
    def __init__(self, text: str, parent=None) -> None:
        super().__init__(parent); self._text = text
        self.setMinimumWidth(18)

    def text(self) -> str: return self._text
    def sizeHint(self) -> QSize: return QSize(18, 80)

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setPen(Qt.GlobalColor.gray)
        p.translate(self.width() / 2 + 4, self.height() / 2)
        p.rotate(-90)
        rect = p.fontMetrics().boundingRect(self._text)
        p.drawText(-rect.width() // 2, rect.height() // 3, self._text)
        p.end()


class GridTableFrame(QWidget):
    def __init__(self, table, parent=None, roms_provider=None) -> None:
        super().__init__(parent)
        self.setObjectName("tableFrame")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        tdef = table.definition
        transposed = _needs_transpose(table)
        self.header = FrameHeader(tdef)
        model = TableGridModel(table, presentation_transposed=transposed)
        self.grid = TableGridWidget(model)
        self.grid.autofit_columns(42)
        self.menubar = TableMenuBar(self.grid, roms_provider=roms_provider)
        self.legend = LegendStrip(model, grid=self.grid)

        # --- x-axis band: name + units left, storage-clamp annotation right ---
        h_axis_def = (tdef.y_axis if transposed else tdef.x_axis)
        v_axis_def = (None if transposed else tdef.y_axis)
        band = QHBoxLayout(); band.setContentsMargins(28, 2, 12, 2)
        name = (h_axis_def.name if h_axis_def is not None and h_axis_def.name else "X")
        units = (h_axis_def.scale.units if h_axis_def is not None and h_axis_def.scale else "")
        self._x_label = QLabel(f"{name} ({units})" if units else name)
        self._range_label = QLabel(_clamp_range_text(table))
        self._range_label.setToolTip(self._range_label.text())
        self._range_label.setFont(numeric_font(8))
        self._range_label.setStyleSheet(f"color: {current_theme().text_dim};")
        band.addWidget(self._x_label); band.addStretch(1); band.addWidget(self._range_label)
        self._band_host = QWidget(); self._band_host.setLayout(band)
        self._band_host.setToolTip(self._range_label.text())

        # --- rotated Y caption ---
        y_name = (v_axis_def.name if v_axis_def is not None and v_axis_def.name else
                  ("Y" if v_axis_def is not None else ""))
        y_units = (v_axis_def.scale.units if v_axis_def is not None and v_axis_def.scale else "")
        self._y_caption = _RotatedLabel(f"{y_name} ({y_units})" if y_units else y_name)

        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(0)
        lay.addWidget(self.header)
        lay.addWidget(self.menubar)          # verb toolbar band (mockup order: header -> verbs -> x-band -> grid -> legend)
        lay.addWidget(self._band_host)
        mid = QHBoxLayout(); mid.setContentsMargins(0, 0, 0, 0); mid.setSpacing(0)
        mid.addWidget(self._y_caption); mid.addWidget(self.grid, 1)
        lay.addLayout(mid, 1)
        lay.addWidget(self.legend)
        self._band_host.setVisible(
            h_axis_def is not None or (table.y_axis if transposed else table.x_axis) is not None)
        self._y_caption.setVisible(bool(y_name))
        self._update_metadata_visibility(self.width())

    def sizeHint(self) -> QSize:
        """Measure the current table and visible frame chrome without stale layout caching."""
        grid_hint = self.grid.sizeHint()
        # QSize() is invalid (-1, -1), not an empty zero-sized contribution.
        # Hidden optional chrome must not subtract a pixel from the table hint.
        y_hint = (
            self._y_caption.sizeHint()
            if not self._y_caption.isHidden()
            else QSize(0, 0)
        )
        band_hint = (
            self._band_host.sizeHint()
            if not self._band_host.isHidden()
            else QSize(0, 0)
        )
        header_hint = self.header.sizeHint()
        menubar_hint = self.menubar.sizeHint()
        legend_hint = self.legend.sizeHint()
        middle_width = y_hint.width() + grid_hint.width()
        middle_height = max(y_hint.height(), grid_hint.height())
        width = max(
            header_hint.width(), menubar_hint.width(), band_hint.width(),
            middle_width, legend_hint.width(),
        )
        height = (
            header_hint.height() + menubar_hint.height() + band_hint.height()
            + middle_height + legend_hint.height()
        )
        return QSize(width, height)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_metadata_visibility(event.size().width())

    def _update_metadata_visibility(self, width: int) -> None:
        """Keep the complete range available by tooltip before labels begin to collide."""
        required = (
            self._x_label.sizeHint().width()
            + self._range_label.sizeHint().width()
            + 96
        )
        self._range_label.setVisible(width >= required)

    def x_band_text(self) -> str:
        return f"{self._x_label.text()} · {self._range_label.text()}"

    def y_caption_text(self) -> str:
        return self._y_caption.text()

    def step_caption_text(self) -> str:
        return self.menubar.step_caption_text()
