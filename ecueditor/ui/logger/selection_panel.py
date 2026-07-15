from __future__ import annotations
from collections.abc import Iterable
from typing import Any, Sequence

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

    def set_channels(self, channels: Sequence[Any]) -> None:
        # Accepts any id/name row: a LoggerChannel (units via .conversion.units) OR an
        # ExternalDataItem-shaped object (units via .units, no .conversion) — duck-typed.
        self._table.blockSignals(True)
        self._table.setRowCount(0)
        self._rows.clear()
        for ch in channels:
            r = self._table.rowCount()
            self._table.insertRow(r)
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            chk.setCheckState(Qt.CheckState.Unchecked)
            chk.setData(Qt.ItemDataRole.UserRole, ch.id)
            self._table.setItem(r, 0, chk)
            self._table.setItem(r, 1, QTableWidgetItem(ch.name))
            conv = getattr(ch, "conversion", None)
            units = conv.units if conv is not None else getattr(ch, "units", "")
            combo = QComboBox()
            conversions = getattr(ch, "conversions", ()) or (() if conv is None else (conv,))
            available_units = list(dict.fromkeys(
                conversion.units for conversion in conversions
            )) or [units]
            combo.addItems(available_units)
            combo.currentTextChanged.connect(lambda _text: self.changed.emit())
            self._table.setCellWidget(r, 2, combo)
            views: dict[str, QTableWidgetItem] = {}
            for view in _VIEWS:
                view_item = QTableWidgetItem()
                view_item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
                view_item.setCheckState(Qt.CheckState.Unchecked)
                self._table.setItem(r, _VIEW_COLUMNS[view], view_item)
                views[view] = view_item
            self._rows[ch.id] = (chk, combo, views)
        self._table.blockSignals(False)

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        col = item.column()
        if col == 0:
            cid = item.data(Qt.ItemDataRole.UserRole)
            views = self._rows[cid][2]
            state = (Qt.CheckState.Checked if item.checkState() == Qt.CheckState.Checked
                     else Qt.CheckState.Unchecked)
            self._table.blockSignals(True)
            for view_item in views.values():
                view_item.setCheckState(state)
            self._table.blockSignals(False)
            self.changed.emit()
        elif col in _VIEW_COLUMNS.values():
            self.changed.emit()

    def check(self, channel_id: str, checked: bool = True) -> None:
        chk, _, _ = self._rows[channel_id]
        chk.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)

    def selected_ids(self) -> list[str]:
        return [cid for cid, (chk, _, _) in self._rows.items()
                if chk.checkState() == Qt.CheckState.Checked]

    def unselect_all(self) -> None:
        for chk, _, _ in self._rows.values():
            chk.setCheckState(Qt.CheckState.Unchecked)

    def units_for(self, channel_id: str) -> str | None:
        _chk, combo, _views = self._rows.get(channel_id, (None, None, None))
        return combo.currentText() if combo is not None else None

    def set_units(self, channel_id: str, units: str) -> None:
        _chk, combo, _views = self._rows[channel_id]
        if combo is None:
            return
        index = combo.findText(units)
        if index >= 0:
            combo.setCurrentIndex(index)

    def view_ids(self, view: str) -> list[str]:
        return [cid for cid, (_chk, _combo, views) in self._rows.items()
                if views[view].checkState() == Qt.CheckState.Checked]

    def set_view_checked(self, channel_id: str, view: str, checked: bool) -> None:
        self._rows[channel_id][2][view].setCheckState(
            Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)


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

    def set_externals(self, items: Sequence[Any]) -> None:
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

    def set_units(self, channel_id: str, units: str) -> None:
        for pane in self._panes:
            if channel_id in pane._rows:
                pane.set_units(channel_id, units)
                return
        raise KeyError(channel_id)

    def units_map(self, channel_ids: Iterable[str] | None = None) -> dict[str, str]:
        ids = list(channel_ids) if channel_ids is not None else self.selected_ids()
        return {
            channel_id: units
            for channel_id in ids
            if (units := self.units_for(channel_id)) is not None
        }

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
