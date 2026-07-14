"""Shared gauge value, range, threshold, and presentation behavior."""
from __future__ import annotations
from collections import deque
from PySide6.QtCore import Qt, Signal, QRectF, QPointF
from PySide6.QtGui import QPainter, QColor, QPen, QBrush, QLinearGradient, QFont
from PySide6.QtWidgets import QWidget, QMenu, QInputDialog
from ecueditor.ui.design.theme_manager import current_theme
from ecueditor.ui.design.fonts import numeric_font


class GaugeBase(QWidget):
    styleChangeRequested = Signal(str)
    thresholdChangeRequested = Signal(object)     # ("above"|"below", float) | None
    resetMinMaxRequested = Signal()

    def __init__(self, name: str, conversion, parent=None) -> None:
        super().__init__(parent)
        self.name = name
        conv = conversion
        self.units = conv.units if conv else ""
        self._explicit_min = conv.gauge_min if conv else None
        self._explicit_max = conv.gauge_max if conv else None
        self.gauge_step = (conv.gauge_step if conv and conv.gauge_step else None)
        self.gauge_min = self._explicit_min if self._explicit_min is not None else 0.0
        self.gauge_max = self._explicit_max if self._explicit_max is not None else 0.0
        self._observed_min: float | None = None
        self._observed_max: float | None = None
        self._threshold: tuple[str, float] | None = None
        self.value: float | None = None
        self.history: deque[float] = deque(maxlen=120)
        self.setMinimumSize(210, 190)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)

    # --- value / range (ports the old GaugeWidget contract verbatim) -----------
    def set_value(self, value: float) -> None:
        self.value = value
        self.history.append(value)
        if self._explicit_min is None:
            self.gauge_min = value if self._observed_min is None else min(self._observed_min, value)
        if self._explicit_max is None:
            self.gauge_max = value if self._observed_max is None else max(self._observed_max, value)
        self._observed_min = value if self._observed_min is None else min(self._observed_min, value)
        self._observed_max = value if self._observed_max is None else max(self._observed_max, value)
        self.update()

    def needle_fraction(self) -> float:
        if self.value is None or self.gauge_max <= self.gauge_min:
            return 0.0
        return max(0.0, min(1.0, (self.value - self.gauge_min) / (self.gauge_max - self.gauge_min)))

    def observed_min(self): return self._observed_min
    def observed_max(self): return self._observed_max

    def reset_min_max(self) -> None:
        self._observed_min = self._observed_max = None
        if self._explicit_min is None: self.gauge_min = 0.0
        if self._explicit_max is None: self.gauge_max = 0.0
        self.update()

    # --- thresholds (D10: values live in settings; dashboard persists) ----------
    def set_threshold(self, mode: str | None, value: float = 0.0) -> None:
        self._threshold = (mode, value) if mode else None
        self.update()

    @property
    def threshold(self): return self._threshold

    @property
    def in_alarm(self) -> bool:
        if self._threshold is None or self.value is None:
            return False
        mode, limit = self._threshold
        return self.value > limit if mode == "above" else self.value < limit

    # --- shared chrome (rounded card, mockup logger-dashboard.html) ----------------
    _draws_own_value = False        # DigitalGauge draws its value in the body instead

    def _draw_value(self, p: QPainter, rect: QRectF, size: int, theme) -> None:
        """Big monospace number + small units, centred as one unit in `rect`
        (accent_hover in alarm, else text; units always text_dim)."""
        num = "—" if self.value is None else f"{self.value:g}"
        units = self.units or ""
        num_font = numeric_font(size, bold=True)
        unit_font = numeric_font(max(8, round(size * 0.45)))
        p.setFont(num_font); fmn = p.fontMetrics()
        num_w = fmn.horizontalAdvance(num); asc, desc = fmn.ascent(), fmn.descent()
        u_txt = ("  " + units) if units else ""
        p.setFont(unit_font); u_w = p.fontMetrics().horizontalAdvance(u_txt)
        x0 = rect.center().x() - (num_w + u_w) / 2.0
        baseline = rect.center().y() + (asc - desc) / 2.0
        p.setFont(num_font)
        p.setPen(QPen(QColor(theme.accent_hover if self.in_alarm else theme.text), 1))
        p.drawText(QPointF(x0, baseline), num)
        if units:
            p.setFont(unit_font); p.setPen(QPen(QColor(theme.text_dim), 1))
            p.drawText(QPointF(x0 + num_w, baseline), u_txt)

    def paintEvent(self, _ev) -> None:
        t = current_theme()
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Fill the whole widget opaquely with the container bg FIRST so the rounded-card corners
        # read as the dashboard background and the widget is fully opaque (fixes the sibling-bleed
        # seen when the grid is captured via a parent grab()/render()).
        p.fillRect(self.rect(), QColor(t.bg))
        outer = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
        radius = float(t.radius[3])                        # 8px rounded card
        if self.in_alarm:                                  # D4: SUBTLE red wash + border + ⚠ icon
            grad = QLinearGradient(outer.topLeft(), outer.bottomLeft())
            hi = QColor(t.danger); hi.setAlphaF(0.12)
            lo = QColor(t.danger); lo.setAlphaF(0.05)
            grad.setColorAt(0.0, hi); grad.setColorAt(1.0, lo)
            p.setBrush(QBrush(grad)); p.setPen(QPen(QColor(t.danger), 1.4))
        else:
            p.setBrush(QColor(t.surface1)); p.setPen(QPen(QColor(t.border), 1))
        p.drawRoundedRect(outer, radius, radius)

        rect = outer.adjusted(11, 9, -11, -9)
        title_font = QFont(self.font()); title_font.setPointSizeF(8.0); title_font.setBold(True)
        title_font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 106)
        p.setFont(title_font)
        p.setPen(QPen(QColor(t.accent_hover if self.in_alarm else t.text_dim), 1))
        title = (f"⚠  {self.name}" if self.in_alarm else self.name).upper()
        p.drawText(rect, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter, title)

        body = rect.adjusted(0, 24, 0, -47)
        self._paint_body(p, body, t)

        if not self._draws_own_value:                      # needle/bar/sparkline: value below body
            self._draw_value(p, QRectF(rect.left(), rect.bottom() - 42,
                                       rect.width(), 24), 19, t)

        p.setFont(numeric_font(7)); p.setPen(QPen(QColor(t.text_disabled), 1))
        mm = ("" if self._observed_min is None
              else f"min {self._observed_min:g} · max {self._observed_max:g}")
        p.drawText(rect, Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter, mm)
        p.end()

    def _paint_body(self, p: QPainter, rect: QRectF, theme) -> None:  # pragma: no cover
        raise NotImplementedError

    # --- context menu ----------------------------------------------------------------
    def _menu(self, pos) -> None:
        from ecueditor.ui.logger.gauges import STYLES
        menu = QMenu(self)
        style_menu = menu.addMenu("Gauge style")
        for s in STYLES:
            style_menu.addAction(s, lambda s=s: self.styleChangeRequested.emit(s))
        menu.addSeparator()
        menu.addAction("Warn above…", lambda: self._ask_threshold("above"))
        menu.addAction("Warn below…", lambda: self._ask_threshold("below"))
        menu.addAction("Clear warning", lambda: self.thresholdChangeRequested.emit(None))
        menu.addSeparator()
        menu.addAction("Reset min/max", self.resetMinMaxRequested.emit)
        menu.exec(self.mapToGlobal(pos))

    def _ask_threshold(self, mode: str) -> None:
        val, ok = QInputDialog.getDouble(self, f"Warn {mode}", f"Alarm when value is {mode}:",
                                         self.value or 0.0, -1e9, 1e9, 2)
        if ok:
            self.thresholdChangeRequested.emit((mode, val))
