from __future__ import annotations
from dataclasses import dataclass
from typing import Sequence

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QTimer, Qt
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QTableView, QVBoxLayout, QWidget

from ecueditor.core.loggerdef.channel import LoggerChannel
from ecueditor.core.logger.engine import Sample


@dataclass
class _Row:
    channel: LoggerChannel
    value: float | None = None
    vmin: float | None = None
    vmax: float | None = None
    fmt: str = "0.0"


class DataTabModel(QAbstractTableModel):
    COL_CHANNEL, COL_VALUE, COL_UNITS, COL_MIN, COL_MAX = range(5)
    _HEADERS = ("Channel", "Value", "Units", "Min", "Max")

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._rows: list[_Row] = []
        self._index: dict[str, int] = {}
        self._pending_rows: set[int] = set()

    def set_channels(self, channels: Sequence[LoggerChannel]) -> None:
        self.beginResetModel()
        self._rows = [_Row(channel=c,
                           fmt=(c.conversion.format if c.conversion else "0.0"))
                      for c in channels]
        self._index = {c.id: i for i, c in enumerate(channels)}
        self._pending_rows.clear()
        self.endResetModel()

    def channel_ids(self) -> list[str]:
        return [row.channel.id for row in self._rows]

    def update_sample(self, sample: Sample) -> None:
        touched: list[int] = []
        for cid, val in sample.values.items():
            i = self._index.get(cid)
            if i is None:
                continue
            row = self._rows[i]
            row.value = val
            row.vmin = val if row.vmin is None else min(row.vmin, val)
            row.vmax = val if row.vmax is None else max(row.vmax, val)
            touched.append(i)
        if touched:
            self._pending_rows.update(touched)

    def flush(self) -> None:
        if self._pending_rows:
            top = self.index(min(self._pending_rows), self.COL_VALUE)
            bot = self.index(max(self._pending_rows), self.COL_MAX)
            self.dataChanged.emit(top, bot, [Qt.ItemDataRole.DisplayRole])
            self._pending_rows.clear()

    def pending_count(self) -> int:
        return len(self._pending_rows)

    def reset_min_max(self) -> None:
        """Clear every row's running minimum and maximum."""
        for row in self._rows:
            row.vmin = None
            row.vmax = None
        if self._rows:
            top = self.index(0, self.COL_MIN)
            bot = self.index(len(self._rows) - 1, self.COL_MAX)
            self.dataChanged.emit(top, bot, [Qt.ItemDataRole.DisplayRole])
        self._pending_rows.clear()

    def min_max_for(self, channel_id: str) -> tuple[float | None, float | None]:
        i = self._index.get(channel_id)
        if i is None:
            return (None, None)
        row = self._rows[i]
        return (row.vmin, row.vmax)

    # --- Qt model API ---
    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._HEADERS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self._HEADERS[section]
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or role != Qt.DisplayRole:
            return None
        row = self._rows[index.row()]
        col = index.column()
        if col == self.COL_CHANNEL:
            return row.channel.name
        if col == self.COL_UNITS:
            return row.channel.conversion.units if row.channel.conversion else ""
        val = {self.COL_VALUE: row.value, self.COL_MIN: row.vmin, self.COL_MAX: row.vmax}[col]
        if val is None:
            return ""
        decimals = row.fmt.split(".", 1)[1].__len__() if "." in row.fmt else 0
        return f"{val:.{decimals}f}"


class DataTab(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.model = DataTabModel(self)
        self.view = QTableView()
        self.view.setModel(self.model)
        self.reset_min_max_button = QPushButton("Reset min/max")
        self.reset_min_max_button.clicked.connect(self.reset_min_max)

        lay = QVBoxLayout(self)
        toolbar = QHBoxLayout()
        toolbar.addStretch(1)
        toolbar.addWidget(self.reset_min_max_button)
        lay.addLayout(toolbar)
        lay.addWidget(self.view)

        self._flush_timer = QTimer(self)
        self._flush_timer.setInterval(33)              # ~30 Hz paint ceiling (spec §7)
        self._flush_timer.timeout.connect(self.flush)
        self._flush_timer.start()

    def set_channels(self, channels: Sequence[LoggerChannel]) -> None:
        self.model.set_channels(channels)

    def update_sample(self, sample: Sample) -> None:
        self.model.update_sample(sample)

    def flush(self) -> None:
        self.model.flush()

    def pending_count(self) -> int:
        return self.model.pending_count()

    def reset_min_max(self) -> None:
        self.model.reset_min_max()
