"""Needle (dial) gauge: ports the old GaugeWidget._paint_dial arc + needle, adds tick marks
(spec §7). Arc pen chart_pens[0]; needle theme.text; ticks share the arc pen."""
from __future__ import annotations
import math
from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from ecueditor.ui.logger.gauges.base import GaugeBase

_MAX_TICKS = 24
_EVEN_TICKS = 5


class NeedleGauge(GaugeBase):
    def _tick_fractions(self) -> list[float]:
        """Fractions (0..1 along the 270-degree sweep) at which to draw a tick mark."""
        explicit_range = self._explicit_min is not None and self._explicit_max is not None
        span = self.gauge_max - self.gauge_min
        if span <= 0:
            return [0.0]
        if self.gauge_step and explicit_range:
            fracs = []
            v = self.gauge_min
            count = 0
            while v <= self.gauge_max + 1e-9 and count < _MAX_TICKS:
                fracs.append(max(0.0, min(1.0, (v - self.gauge_min) / span)))
                v += self.gauge_step
                count += 1
            return fracs
        return [i / (_EVEN_TICKS - 1) for i in range(_EVEN_TICKS)]

    @staticmethod
    def _dial_geometry(rect: QRectF) -> tuple[QPointF, float]:
        pad = 6.0
        radius = max(0.0, min(rect.width() / 2.0 - pad, rect.height() - pad * 2.0))
        return QPointF(rect.center().x(), rect.bottom() - pad), radius

    @staticmethod
    def _point(center: QPointF, radius: float, fraction: float) -> QPointF:
        angle = math.pi * (1.0 - fraction)
        return QPointF(
            center.x() + radius * math.cos(angle),
            center.y() - radius * math.sin(angle),
        )

    @classmethod
    def _arc_path(
        cls, center: QPointF, radius: float, end_fraction: float = 1.0
    ) -> QPainterPath:
        path = QPainterPath()
        steps = max(1, round(48 * max(0.0, min(1.0, end_fraction))))
        path.moveTo(cls._point(center, radius, 0.0))
        for step in range(1, steps + 1):
            fraction = end_fraction * step / steps
            path.lineTo(cls._point(center, radius, fraction))
        return path

    def _paint_body(self, p: QPainter, rect: QRectF, theme) -> None:
        center, radius = self._dial_geometry(rect)
        if radius <= 8.0:
            return
        track_radius = radius - 4.0
        stroke = max(5.0, radius * 0.075)

        track = QPen(QColor(theme.surface3), stroke)
        track.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(track)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(self._arc_path(center, track_radius))

        active_color = theme.accent_hover if self.in_alarm else theme.chart_pens[0]
        active = QPen(QColor(active_color), stroke)
        active.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(active)
        p.drawPath(self._arc_path(center, track_radius, self.needle_fraction()))

        p.setPen(QPen(QColor(theme.text_disabled), 1.2))
        for fraction in self._tick_fractions():
            inner = self._point(center, track_radius - stroke - 6.0, fraction)
            outer = self._point(center, track_radius - stroke - 1.5, fraction)
            p.drawLine(inner, outer)

        needle_fraction = self.needle_fraction()
        needle_end = self._point(center, track_radius - stroke - 10.0, needle_fraction)
        p.setPen(QPen(QColor(theme.text), 2.4, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap))
        p.drawLine(center, needle_end)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(active_color))
        p.drawEllipse(center, 5.0, 5.0)
        p.setBrush(QColor(theme.surface1))
        p.drawEllipse(center, 2.2, 2.2)
