"""Digital gauge: the value drawn huge and centered in the body (spec §7)."""
from __future__ import annotations
from PySide6.QtCore import QRectF
from PySide6.QtGui import QPainter
from ecueditor.ui.logger.gauges.base import GaugeBase


class DigitalGauge(GaugeBase):
    _draws_own_value = True     # the big centred value IS the body (no separate value line)

    def _paint_body(self, p: QPainter, rect: QRectF, theme) -> None:
        self._draw_value(p, rect, 26, theme)
