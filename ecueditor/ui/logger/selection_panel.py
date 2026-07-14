from __future__ import annotations
from typing import Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QComboBox, QGroupBox, QVBoxLayout, QWidget,
                               QTableWidget, QTableWidgetItem)

from ecueditor.core.loggerdef.channel import LoggerChannel


_VIEWS = ("livedata", "graph", "dash")
_VIEW_COLUMNS = {"livedata": 3, "graph": 4, "dash": 5}


class _CheckboxList(QGroupBox):
    """A titled 6-column table: [poll check] name [units combo] [Data][Graph][Dash],
    one row per channel. Checking the poll column defaults all three view columns on
    (RomRaider default); each view column also toggles independently thereafter."""
    changed = Signal()

    def __init__(self, title: str) -> None:
        super().__init__(title)
        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels(["", "Parameter", "Units", "Data", "Graph", "Dash"])
        self._table.verticalHeader().setVisible(False)
        self._rows: dict[str, tuple[QTableWidgetItem, QComboBox | None, dict[str, QTableWidgetItem]]] = {}
        lay = QVBoxLayout(self)
        lay.addWidget(self._table)
        self._table.itemChanged.connect(self._on_item_changed)

    def set_channels(self, channels: Sequence[object]) -> None:
        # Accepts any id/name row: a LoggerChannel (units via .conversion.units) OR an
        # ExternalDataItem-shaped object (units via .units, no .conversion) — duck-typed.
        self._table.blockSignals(True)
        self._table.setRowCount(0)
        self._rows.clear()
        for ch in channels:
            r = self._table.rowCount()
            self._table.insertRow(r)
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            chk.setCheckState(Qt.Unchecked)
            chk.setData(Qt.UserRole, ch.id)
            self._table.setItem(r, 0, chk)
            self._table.setItem(r, 1, QTableWidgetItem(ch.name))
            conv = getattr(ch, "conversion", None)
            units = conv.units if conv is not None else getattr(ch, "units", "")
            combo = QComboBox()
            combo.addItem(units)
            self._table.setCellWidget(r, 2, combo)
            views: dict[str, QTableWidgetItem] = {}
            for view in _VIEWS:
                view_item = QTableWidgetItem()
                view_item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
                view_item.setCheckState(Qt.Unchecked)
                self._table.setItem(r, _VIEW_COLUMNS[view], view_item)
                views[view] = view_item
            self._rows[ch.id] = (chk, combo, views)
        self._table.blockSignals(False)

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        col = item.column()
        if col == 0:
            cid = item.data(Qt.UserRole)
            views = self._rows[cid][2]
            state = Qt.Checked if item.checkState() == Qt.Checked else Qt.Unchecked
            self._table.blockSignals(True)
            for view_item in views.values():
                view_item.setCheckState(state)
            self._table.blockSignals(False)
            self.changed.emit()
        elif col in _VIEW_COLUMNS.values():
            self.changed.emit()

    def check(self, channel_id: str, checked: bool = True) -> None:
        chk, _, _ = self._rows[channel_id]
        chk.setCheckState(Qt.Checked if checked else Qt.Unchecked)

    def selected_ids(self) -> list[str]:
        return [cid for cid, (chk, _, _) in self._rows.items()
                if chk.checkState() == Qt.Checked]

    def unselect_all(self) -> None:
        for chk, _, _ in self._rows.values():
            chk.setCheckState(Qt.Unchecked)

    def units_for(self, channel_id: str) -> str | None:
        _chk, combo, _views = self._rows.get(channel_id, (None, None, None))
        return combo.currentText() if combo is not None else None

    def view_ids(self, view: str) -> list[str]:
        return [cid for cid, (_chk, _combo, views) in self._rows.items()
                if views[view].checkState() == Qt.Checked]

    def set_view_checked(self, channel_id: str, view: str, checked: bool) -> None:
        self._rows[channel_id][2][view].setCheckState(
            Qt.Checked if checked else Qt.Unchecked)


class ParameterSelectionPanel(QWidget):
    selectionChanged = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("loggerSelectionPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._parameters = _CheckboxList("Parameters")
        self._switches = _CheckboxList("Switches")
        self._externals = _CheckboxList("External")
        lay = QVBoxLayout(self)
        for pane in (self._parameters, self._switches, self._externals):
            pane.changed.connect(self.selectionChanged)
            lay.addWidget(pane)

    @property
    def _panes(self) -> tuple[_CheckboxList, ...]:
        return (self._parameters, self._switches, self._externals)

    def set_channels(self, channels: Sequence[LoggerChannel]) -> None:
        self._parameters.set_channels(channels)

    def set_switches(self, channels: Sequence[LoggerChannel]) -> None:
        self._switches.set_channels(channels)

    def set_externals(self, items: Sequence[object]) -> None:
        # ExternalDataItem-shaped rows (duck-typed .id/.name/.units) per INTERFACES.md
        self._externals.set_channels(items)

    def check(self, channel_id: str, checked: bool = True) -> None:
        for pane in self._panes:
            if channel_id in pane._rows:
                pane.check(channel_id, checked)
                return
        raise KeyError(channel_id)

    def selected_ids(self) -> list[str]:
        out: list[str] = []
        for pane in self._panes:
            out.extend(pane.selected_ids())
        return out

    def unselect_all(self) -> None:
        for pane in self._panes:
            pane.unselect_all()

    def units_for(self, channel_id: str) -> str | None:
        for pane in self._panes:
            if channel_id in pane._rows:
                return pane.units_for(channel_id)
        return None

    def view_ids(self, view: str) -> list[str]:
        out: list[str] = []
        for pane in self._panes:
            out.extend(pane.view_ids(view))
        return out

    def set_view_checked(self, channel_id: str, view: str, checked: bool) -> None:
        for pane in self._panes:
            if channel_id in pane._rows:
                pane.set_view_checked(channel_id, view, checked)
                return
        raise KeyError(channel_id)
