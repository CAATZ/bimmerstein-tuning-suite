"""Heatmap legend strip (spec §5, D6, B2)."""
from __future__ import annotations

from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel, QToolButton, QMenu
from PySide6.QtGui import QPainter, QColor
from PySide6.QtCore import Signal, QSize
from ecueditor.ui.design.colormaps import heat_color
from ecueditor.ui.design.fonts import numeric_font


class _Ramp(QWidget):
    def __init__(self, model, parent=None) -> None:
        super().__init__(parent); self._model = model
        self.setMinimumHeight(10)

    def sizeHint(self) -> QSize: return QSize(120, 10)

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        w = max(1, self.width())
        for i in range(w):
            p.setPen(QColor(*heat_color(i / (w - 1) if w > 1 else 0.0, self._model.colormap)))
            p.drawLine(i, 0, i, self.height())
        p.end()


class LegendStrip(QWidget):
    colormapChangeRequested = Signal(str)

    def __init__(self, model, parent=None, *, grid=None) -> None:
        super().__init__(parent)
        self._model = model
        lay = QHBoxLayout(self); lay.setContentsMargins(12, 2, 12, 6); lay.setSpacing(8)
        self._min = QLabel(); self._min.setFont(numeric_font(8))
        self._max = QLabel(); self._max.setFont(numeric_font(8))
        self._ramp = _Ramp(model)
        self._selection = QLabel(self)
        self._selection.setFont(numeric_font(8))
        self._selection.setMaximumWidth(260)
        self._selection.setVisible(grid is not None)
        self._btn = QToolButton(); self._btn.setText("▾"); self._btn.setAutoRaise(True)
        menu = QMenu(self._btn)
        menu.addAction("Viridis (perceptual)", lambda: self.request_colormap("viridis"))
        menu.addAction("Classic Rainbow", lambda: self.request_colormap("rainbow"))
        self._btn.setMenu(menu); self._btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        lay.addWidget(self._min); lay.addWidget(self._ramp, 1); lay.addWidget(self._max)
        lay.addWidget(self._selection)
        lay.addWidget(self._btn)
        model.dataChanged.connect(lambda *_a: self.refresh())
        model.modelReset.connect(self.refresh)
        if grid is not None:
            grid.selectionSummaryChanged.connect(self._set_selection_text)
            self._set_selection_text(grid.selection_summary_text())
        self.refresh()

    def request_colormap(self, name: str) -> None:
        self.colormapChangeRequested.emit(name)

    def refresh(self) -> None:
        lo, hi = self._model.real_bounds()
        scale = self._model.current_scale
        self._min.setText(scale.format_value(lo))
        self._max.setText(f"{scale.format_value(hi)} {scale.units}".rstrip())
        self._ramp.update()

    def min_text(self) -> str: return self._min.text()
    def max_text(self) -> str: return self._max.text()
    def selection_text(self) -> str: return self._selection.text()

    def _set_selection_text(self, text: str) -> None:
        self._selection.setText(text)
        self._selection.setToolTip(text)
