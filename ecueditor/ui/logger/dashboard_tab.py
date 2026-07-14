from __future__ import annotations
from typing import Sequence

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QGridLayout, QScrollArea, QVBoxLayout, QWidget

from ecueditor.core.loggerdef.channel import LoggerChannel
from ecueditor.core.logger.engine import Sample
from ecueditor.core.settings import EditorSettings
from ecueditor.ui.logger.gauges import GaugeBase, STYLES, make_gauge


class DashboardTab(QWidget):
    _MAX_COLS = 3
    _CARD_MIN_WIDTH = 210

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("loggerDashboard")
        self.gauges: dict[str, GaugeBase] = {}
        self._channels: dict[str, LoggerChannel] = {}
        self._styles: dict[str, str] = {}          # channel_id -> style name (Task 8 persists this)
        self._settings: EditorSettings | None = None
        self._pending: dict[str, float] = {}
        self._current_columns = self._MAX_COLS
        self._host = QWidget()
        self._host.setObjectName("loggerDashboardHost")
        self._grid = QGridLayout(self._host)
        self._grid.setSpacing(10)                  # gaps between cards (mockup .lg-dash gap:10px)
        self._grid.setContentsMargins(12, 12, 12, 12)
        self._grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._scroll = QScrollArea()
        self._scroll.setObjectName("loggerDashboardScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setWidget(self._host)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._scroll)

        self._flush_timer = QTimer(self)
        self._flush_timer.setInterval(33)              # ~30 Hz paint ceiling (spec §7)
        self._flush_timer.timeout.connect(self.flush)
        self._flush_timer.start()

    def _style_for(self, cid: str) -> str:
        return self._styles.get(cid, STYLES[0])

    def set_settings(self, settings: EditorSettings | None) -> None:
        """Store the settings object and seed per-gauge styles from it (Task 8, spec 9.5/D10).

        Thresholds are applied per-gauge at build time (`_apply_stored_threshold`), not here --
        `set_channels`/`_place_gauge` runs after this in the normal window-construction flow.
        """
        self._settings = settings
        if settings is not None:
            self._styles.update(settings.gauge_styles)

    def _apply_stored_threshold(self, cid: str, g: GaugeBase) -> None:
        """Re-apply a persisted warn threshold to a freshly built gauge -- used by both
        `_place_gauge` (first build) and `_rebuild_gauge` (style change) so a style rebuild
        never silently drops the channel's warn threshold."""
        if self._settings is None:
            return
        spec = self._settings.warn_thresholds.get(cid)
        if spec is not None:
            mode, value = spec
            g.set_threshold(mode, value)

    def set_channels(self, channels: Sequence[LoggerChannel]) -> None:
        while self._grid.count():
            item = self._grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.gauges.clear()
        self._channels = {ch.id: ch for ch in channels}
        self._current_columns = self._columns_for_width(self._scroll.viewport().width())
        for i, ch in enumerate(channels):
            self._place_gauge(ch.id, i)
        self._reflow_for_width(self._scroll.viewport().width())

    def _place_gauge(self, cid: str, index: int) -> None:
        ch = self._channels[cid]
        g = make_gauge(self._style_for(cid), ch.name, ch.conversion)
        self._wire_gauge(cid, g)
        self._apply_stored_threshold(cid, g)
        self.gauges[cid] = g
        self._grid.addWidget(
            g, index // self._current_columns, index % self._current_columns
        )

    def _columns_for_width(self, width: int) -> int:
        usable = max(1, width - 24)
        pitch = self._CARD_MIN_WIDTH + self._grid.spacing()
        return max(1, min(self._MAX_COLS, (usable + self._grid.spacing()) // pitch))

    def _reflow_for_width(self, width: int) -> None:
        columns = self._columns_for_width(width)
        self._current_columns = columns
        for index, cid in enumerate(self._channels):
            gauge = self.gauges.get(cid)
            if gauge is not None:
                self._grid.addWidget(gauge, index // columns, index % columns)
        for column in range(self._MAX_COLS):
            self._grid.setColumnStretch(column, 1 if column < columns else 0)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._reflow_for_width(self._scroll.viewport().width())

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._reflow_for_width(self._scroll.viewport().width())

    def _wire_gauge(self, cid: str, g: GaugeBase) -> None:
        """Shared signal wiring so set_channels() and the rebuild path stay in sync (Task 7).
        Task 8 adds settings persistence on top of these same hooks."""
        g.resetMinMaxRequested.connect(g.reset_min_max)
        g.thresholdChangeRequested.connect(
            lambda payload, cid=cid: self._on_threshold_changed(cid, payload))
        g.styleChangeRequested.connect(
            lambda style, cid=cid: self._on_style_changed(cid, style))

    def _on_threshold_changed(self, cid: str, payload) -> None:
        g = self.gauges.get(cid)
        if g is None:
            return
        if payload is None:
            g.set_threshold(None)
            if self._settings is not None:
                self._settings.warn_thresholds.pop(cid, None)
        else:
            mode, value = payload
            g.set_threshold(mode, value)
            if self._settings is not None:
                self._settings.warn_thresholds[cid] = [mode, float(value)]
        self._save()

    def _on_style_changed(self, cid: str, style: str) -> None:
        self._styles[cid] = style
        if self._settings is not None:
            self._settings.gauge_styles[cid] = style
        self._rebuild_gauge(cid)
        self._save()

    def _rebuild_gauge(self, cid: str) -> None:
        """Replace one gauge in-place (same grid cell) with its currently tracked style,
        re-applying the persisted warn threshold (a style change must not drop it)."""
        old = self.gauges.get(cid)
        ch = self._channels.get(cid)
        if old is None or ch is None:
            return
        idx = self._grid.indexOf(old)
        row, col, rspan, cspan = self._grid.getItemPosition(idx)
        self._grid.removeWidget(old)
        old.deleteLater()
        g = make_gauge(self._style_for(cid), ch.name, ch.conversion)
        self._wire_gauge(cid, g)
        self._apply_stored_threshold(cid, g)
        self.gauges[cid] = g
        self._grid.addWidget(g, row, col, rspan, cspan)

    def _save(self) -> None:
        if self._settings is not None:
            from ecueditor.core.settings import save_settings
            save_settings(self._settings)

    def update_sample(self, sample: Sample) -> None:
        for cid, val in sample.values.items():
            if cid in self.gauges:
                self._pending[cid] = val

    def flush(self) -> None:
        for cid, val in self._pending.items():
            g = self.gauges.get(cid)
            if g is not None:
                g.set_value(val)
        self._pending.clear()

    def pending_count(self) -> int:
        return len(self._pending)

    def cycle_gauge_style(self) -> None:
        for cid in list(self.gauges):
            cur = self._style_for(cid)
            nxt = STYLES[(STYLES.index(cur) + 1) % len(STYLES)]
            self._styles[cid] = nxt
            self._rebuild_gauge(cid)
