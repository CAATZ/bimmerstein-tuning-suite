from __future__ import annotations
from collections import deque
from typing import Sequence

import pyqtgraph as pg
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from ecueditor.core.loggerdef.channel import LoggerChannel
from ecueditor.core.logger.engine import Sample
from ecueditor.ui.design.theme_manager import current_theme

MAX_POINTS = 200   # fact base §3.1: JFreeChart series.setMaximumItemCount(200)


def _pen(i: int) -> str:
    pens = current_theme().chart_pens
    return pens[i % len(pens)]


class GraphTab(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._channels: list[LoggerChannel] = []
        self._x: dict[str, deque] = {}
        self._y: dict[str, deque] = {}
        self._curves: dict[str, pg.PlotDataItem] = {}
        self._plots: list[pg.PlotItem] = []
        self.combined = False
        self._paused = False
        self._dirty: set[str] = set()
        self._t0: float | None = None

        self._layout_host = pg.GraphicsLayoutWidget()
        self.pause_button = QPushButton("Pause")
        self.combine_button = QPushButton("Combine")
        controls = QHBoxLayout()
        controls.addWidget(self.pause_button)
        controls.addWidget(self.combine_button)
        controls.addStretch(1)
        root = QVBoxLayout(self)
        root.addLayout(controls)
        root.addWidget(self._layout_host)
        self.pause_button.clicked.connect(self.toggle_pause)
        self.combine_button.clicked.connect(self.toggle_combine)

        self._flush_timer = QTimer(self)
        self._flush_timer.setInterval(33)              # ~30 Hz paint ceiling (spec §7)
        self._flush_timer.timeout.connect(self.flush)
        self._flush_timer.start()

    def set_channels(self, channels: Sequence[LoggerChannel]) -> None:
        self._channels = list(channels)
        wanted = {c.id for c in self._channels}
        self._dirty.intersection_update(wanted)
        for cid in set(self._x) - wanted:      # evict deques for deselected ids (C13)
            self._x.pop(cid, None)
            self._y.pop(cid, None)
        for c in self._channels:
            self._x.setdefault(c.id, deque(maxlen=MAX_POINTS))
            self._y.setdefault(c.id, deque(maxlen=MAX_POINTS))
        self._rebuild_plots()

    def _rebuild_plots(self) -> None:
        self._layout_host.clear()
        self._curves.clear()
        self._plots.clear()
        if self.combined:
            plot = self._layout_host.addPlot()
            plot.addLegend()
            self._plots.append(plot)
            for i, c in enumerate(self._channels):
                self._curves[c.id] = plot.plot([], [], pen=_pen(i), name=c.name)
        else:
            # stacked strip charts: one row per channel, own y-axis, shared (relative) time x-axis
            for i, c in enumerate(self._channels):
                plot = self._layout_host.addPlot(row=i, col=0, title=c.name)
                self._plots.append(plot)
                self._curves[c.id] = plot.plot([], [], pen=_pen(i))
            if self._plots:
                bottom = self._plots[-1]
                for plot in self._plots[:-1]:
                    plot.hideAxis("bottom")
                    plot.setXLink(bottom)
                bottom.setLabel("bottom", "time", units="s")
        for c in self._channels:      # redraw any retained history
            self._redraw(c.id)

    def update_sample(self, sample: Sample) -> None:
        if self._t0 is None:
            self._t0 = sample.timestamp_ms
        t = (sample.timestamp_ms - self._t0) / 1000.0
        for cid, val in sample.values.items():
            if cid not in self._curves:
                continue
            self._x[cid].append(t)
            self._y[cid].append(val)
            self._dirty.add(cid)

    def flush(self) -> None:
        if self._paused:            # buffering pause (C8): keep ingesting, skip repaint
            return
        for cid in list(self._dirty):
            self._redraw(cid)
        self._dirty.clear()

    def pending_count(self) -> int:
        return len(self._dirty)

    def _redraw(self, cid: str) -> None:
        curve = self._curves.get(cid)
        if curve is not None:
            curve.setData(list(self._x[cid]), list(self._y[cid]))

    def series(self, cid: str) -> tuple[list[float], list[float]]:
        return list(self._x[cid]), list(self._y[cid])

    def plot_count(self) -> int:
        return len(self._plots)

    def toggle_combine(self) -> None:
        self.combined = not self.combined
        self.combine_button.setText("Combine" if not self.combined else "Split")
        self._rebuild_plots()

    def toggle_pause(self) -> None:
        self._paused = not self._paused
        self.pause_button.setText("Resume" if self._paused else "Pause")
        if not self._paused:                     # resume: repaint the buffered backlog (C8)
            self._dirty.update(self._curves)
