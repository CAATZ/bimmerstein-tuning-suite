"""Sparkline gauge: rolling polyline of self.history scaled to the body rect (spec §7).
Pen chart_pens[0]; alarm pen is the one specified hard-coded exception, #ffffff (D4)."""
from __future__ import annotations
from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QColor, QPainter, QPen
from ecueditor.ui.logger.gauges.base import GaugeBase


class SparklineGauge(GaugeBase):
    def _paint_body(self, p: QPainter, rect: QRectF, theme) -> None:
        pts = list(self.history)
        if len(pts) < 2:
            return
        lo, hi = min(pts), max(pts)
        span = hi - lo
        n = len(pts)
        inner = rect.adjusted(2, 4, -2, -4)

        def _xy(i: int, v: float) -> QPointF:
            x = inner.left() + inner.width() * i / (n - 1)
            frac = 0.5 if span <= 0 else (v - lo) / span
            y = inner.bottom() - inner.height() * frac
            return QPointF(x, y)

        poly = [_xy(i, v) for i, v in enumerate(pts)]
        pen_color = theme.accent_hover if self.in_alarm else theme.chart_pens[0]
        p.setPen(QPen(QColor(pen_color), 2))
        for a, b in zip(poly, poly[1:]):
            p.drawLine(a, b)
