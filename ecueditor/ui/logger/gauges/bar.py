"""Bar gauge: horizontal or vertical fill bar, ports the old GaugeWidget._paint_bar and adds
warn-zone shading beyond an active threshold (spec §7). Fill pen chart_pens[0]; warn zone
danger at 25% alpha (D4: semantic color, no hard-coded hex)."""
from __future__ import annotations
from PySide6.QtCore import QLineF, Qt, QRectF
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen
from ecueditor.ui.design.fonts import numeric_font
from ecueditor.ui.logger.gauges.base import GaugeBase

_THICKNESS = 28


class BarGauge(GaugeBase):
    def __init__(self, name: str, conversion, *, orientation: str = "h", parent=None) -> None:
        super().__init__(name, conversion, parent)
        self.orientation = orientation

    def _threshold_fraction(self) -> float | None:
        if self._threshold is None:
            return None
        _mode, limit = self._threshold
        span = self.gauge_max - self.gauge_min
        if span <= 0:
            return None
        return max(0.0, min(1.0, (limit - self.gauge_min) / span))

    def _paint_body(self, p: QPainter, rect: QRectF, theme) -> None:
        if self.orientation == "v":
            self._paint_vertical(p, rect, theme)
        else:
            self._paint_horizontal(p, rect, theme)

    def _bar_rect(self, rect) -> QRectF:
        rect = QRectF(rect)
        if self.orientation == "v":
            height = max(_THICKNESS, rect.height() - 20)
            return QRectF(rect.center().x() - _THICKNESS / 2, rect.center().y() - height / 2,
                          _THICKNESS, height)
        width = max(_THICKNESS, rect.width() - 16)
        return QRectF(rect.center().x() - width / 2, rect.center().y() - _THICKNESS / 2,
                      width, _THICKNESS)

    def _range_labels(self) -> tuple[str, str]:
        return f"{self.gauge_min:g}", f"{self.gauge_max:g}"

    def _pill(self, p: QPainter, r: QRectF, color: QColor, rad: float) -> None:
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(color)
        p.drawRoundedRect(r, rad, rad)

    def _gradient_fill(self, p: QPainter, bar: QRectF, fill: QRectF, theme) -> None:
        if fill.isEmpty():
            return
        gradient = QLinearGradient(
            bar.bottomLeft() if self.orientation == "v" else bar.topLeft(),
            bar.topLeft() if self.orientation == "v" else bar.topRight(),
        )
        gradient.setColorAt(0.0, QColor(theme.chart_pens[0]))
        gradient.setColorAt(1.0, QColor(theme.chart_pens[4]))
        path = QPainterPath()
        path.addRoundedRect(bar, _THICKNESS / 2, _THICKNESS / 2)
        p.save()
        p.setClipPath(path)
        p.fillRect(fill, gradient)
        highlight = QColor("#ffffff"); highlight.setAlphaF(0.10)
        p.fillRect(QRectF(fill.left(), fill.top(), fill.width(), fill.height() / 2), highlight)
        p.restore()

    def _draw_ticks(self, p: QPainter, bar: QRectF, theme) -> None:
        tick = QColor(theme.text_disabled); tick.setAlphaF(0.45)
        p.setPen(QPen(tick, 1))
        for step in range(1, 5):
            fraction = step / 5.0
            if self.orientation == "v":
                y = bar.bottom() - bar.height() * fraction
                p.drawLine(QLineF(bar.left() + 5, y, bar.right() - 5, y))
            else:
                x = bar.left() + bar.width() * fraction
                p.drawLine(QLineF(x, bar.top() + 5, x, bar.bottom() - 5))

    def _draw_threshold(self, p: QPainter, bar: QRectF, theme) -> None:
        fraction = self._threshold_fraction()
        if fraction is None:
            return
        p.setPen(QPen(QColor(theme.danger), 2))
        if self.orientation == "v":
            y = bar.bottom() - bar.height() * fraction
            p.drawLine(QLineF(bar.left() - 3, y, bar.right() + 3, y))
        else:
            x = bar.left() + bar.width() * fraction
            p.drawLine(QLineF(x, bar.top() - 3, x, bar.bottom() + 3))

    def _draw_range(self, p: QPainter, rect: QRectF, bar: QRectF, theme) -> None:
        low, high = self._range_labels()
        p.setFont(numeric_font(7))
        p.setPen(QPen(QColor(theme.text_disabled), 1))
        if self.orientation == "v":
            p.drawText(QRectF(bar.right() + 7, bar.top() - 4, rect.right() - bar.right() - 7, 16),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, high)
            p.drawText(QRectF(bar.right() + 7, bar.bottom() - 12,
                              rect.right() - bar.right() - 7, 16),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom, low)
        else:
            p.drawText(QRectF(bar.left(), bar.bottom() + 4, bar.width() / 2, 14),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, low)
            p.drawText(QRectF(bar.center().x(), bar.bottom() + 4, bar.width() / 2, 14),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop, high)

    def _paint_horizontal(self, p: QPainter, rect: QRectF, theme) -> None:
        rad = _THICKNESS / 2
        bar = self._bar_rect(rect.adjusted(0, 0, 0, -14))
        self._pill(p, bar, QColor(theme.surface3), rad)                 # track
        frac = self._threshold_fraction()
        if frac is not None and self._threshold is not None:
            warn = QColor(theme.danger); warn.setAlphaF(0.25)
            if self._threshold[0] == "above":
                zone = QRectF(bar.left() + bar.width() * frac, bar.top(),
                              bar.width() * (1 - frac), bar.height())
            else:
                zone = QRectF(bar.left(), bar.top(), bar.width() * frac, bar.height())
            self._pill(p, zone, warn, rad)
        fill_w = bar.width() * self.needle_fraction()
        self._gradient_fill(
            p, bar, QRectF(bar.left(), bar.top(), fill_w, bar.height()), theme,
        )
        self._draw_ticks(p, bar, theme)
        self._draw_threshold(p, bar, theme)
        p.setPen(QPen(QColor(theme.border_strong), 1)); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(bar, rad, rad)
        self._draw_range(p, rect, bar, theme)

    def _paint_vertical(self, p: QPainter, rect: QRectF, theme) -> None:
        rad = _THICKNESS / 2
        bar = self._bar_rect(rect.adjusted(0, 4, -30, -4))
        self._pill(p, bar, QColor(theme.surface3), rad)                 # track
        frac = self._threshold_fraction()
        if frac is not None and self._threshold is not None:
            warn = QColor(theme.danger); warn.setAlphaF(0.25)
            if self._threshold[0] == "above":                # zone above the threshold (top)
                h = bar.height() * (1 - frac)
                zone = QRectF(bar.left(), bar.top(), bar.width(), h)
            else:                                            # zone below the threshold (bottom)
                h = bar.height() * frac
                zone = QRectF(bar.left(), bar.bottom() - h, bar.width(), h)
            self._pill(p, zone, warn, rad)
        fill_h = bar.height() * self.needle_fraction()
        self._gradient_fill(
            p, bar, QRectF(bar.left(), bar.bottom() - fill_h, bar.width(), fill_h), theme,
        )
        self._draw_ticks(p, bar, theme)
        self._draw_threshold(p, bar, theme)
        p.setPen(QPen(QColor(theme.border_strong), 1)); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(bar, rad, rad)
        self._draw_range(p, rect, bar, theme)
