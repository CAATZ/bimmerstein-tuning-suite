from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut, QShowEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ecueditor.core.maf_scaling import (
    CANONICAL_VOLTAGES_V,
    list_mafs,
    new_maf_record,
    save_mafs,
    to_16x16,
)
from ecueditor.core.mapstudio import UndoHistory
from ecueditor.ui.mapstudio.widgets import (
    ArrayLegend,
    ArrayTableWidget,
    TableZoomControls,
)


class MafTransferFunctionManager(QDialog):
    """Edit the user-owned MAF transfer-function catalog."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("MAF Transfer Function Manager")
        self.setMinimumSize(1050, 680)
        self.resize(1180, 760)
        self._records = list(list_mafs())
        self._current_id: str | None = None
        self._loading = False
        self._initial_fit_pending = True
        self._history: UndoHistory[np.ndarray] = UndoHistory(np.array_equal)

        root = QVBoxLayout(self)
        title = QLabel("MAF Transfer Functions")
        title.setObjectName("frameTitle")
        root.addWidget(title)
        help_text = QLabel(
            "Select a MAF to edit its name, default inside diameter, and 256-point "
            "0-4.98 V transfer function. Changes are saved in the user catalog."
        )
        help_text.setObjectName("mapStudioHelp")
        help_text.setWordWrap(True)
        root.addWidget(help_text)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.setChildrenCollapsible(False)
        left = QWidget(splitter)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 8, 0)
        self.maf_list = QListWidget(left)
        self.maf_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        left_layout.addWidget(self.maf_list, 1)
        list_buttons = QHBoxLayout()
        self.add_button = QPushButton("Add")
        self.delete_button = QPushButton("Delete")
        list_buttons.addWidget(self.add_button)
        list_buttons.addWidget(self.delete_button)
        left_layout.addLayout(list_buttons)

        editor = QWidget(splitter)
        editor_layout = QVBoxLayout(editor)
        editor_layout.setContentsMargins(8, 0, 0, 0)
        form = QFormLayout()
        self.name_edit = QLineEdit(editor)
        self.default_diameter_box = QDoubleSpinBox(editor)
        self.default_diameter_box.setRange(0.25, 20.0)
        self.default_diameter_box.setDecimals(3)
        self.default_diameter_box.setSingleStep(0.25)
        self.default_diameter_box.setSuffix(" in")
        form.addRow("MAF name", self.name_edit)
        form.addRow("Default inside diameter", self.default_diameter_box)
        editor_layout.addLayout(form)

        self.transfer_table = ArrayTableWidget()

        def table_shortcut(
            sequence: QKeySequence, slot: Callable[[], None]
        ) -> QShortcut:
            shortcut = QShortcut(sequence, self.transfer_table)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(slot)
            return shortcut

        self.copy_shortcut = table_shortcut(
            QKeySequence(QKeySequence.StandardKey.Copy),
            self._copy_transfer_selection,
        )
        self.copy_table_shortcut = table_shortcut(
            QKeySequence("Ctrl+Shift+C"),
            self._copy_transfer_table,
        )
        self.paste_shortcut = table_shortcut(
            QKeySequence(QKeySequence.StandardKey.Paste),
            self._paste_transfer_function,
        )
        self.undo_shortcut = table_shortcut(
            QKeySequence(QKeySequence.StandardKey.Undo),
            self._undo_transfer_edit,
        )
        self.redo_shortcut = table_shortcut(
            QKeySequence(QKeySequence.StandardKey.Redo),
            self._redo_transfer_edit,
        )
        self.transfer_table.valuesEdited.connect(self._record_transfer_edit)
        self.transfer_legend = ArrayLegend()
        self.transfer_legend.set_table(self.transfer_table)
        editor_layout.addWidget(self.transfer_table, 1)
        table_footer = QHBoxLayout()
        table_footer.addWidget(self.transfer_legend, 1)
        self.transfer_zoom = TableZoomControls(self.transfer_table)
        table_footer.addWidget(self.transfer_zoom)
        editor_layout.addLayout(table_footer)

        splitter.addWidget(left)
        splitter.addWidget(editor)
        splitter.setSizes([280, 880])
        root.addWidget(splitter, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self.maf_list.currentRowChanged.connect(self._load_row)
        self.add_button.clicked.connect(self.add_transfer_function)
        self.delete_button.clicked.connect(self.delete_selected)
        for record in self._records:
            self._append_item(record.id, record.display_name)
        if self._records:
            self.maf_list.setCurrentRow(0)
        else:
            self._set_editor_enabled(False)

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 - Qt API
        super().showEvent(event)
        if self._initial_fit_pending:
            self._initial_fit_pending = False
            self.transfer_zoom.fit_after_layout()

    def _append_item(self, maf_id: str, name: str) -> None:
        item = QListWidgetItem(name or "(Unnamed MAF)")
        item.setData(Qt.ItemDataRole.UserRole, maf_id)
        self.maf_list.addItem(item)

    def _record_index(self, maf_id: str) -> int:
        return next(index for index, record in enumerate(self._records) if record.id == maf_id)

    def _commit_current(self) -> None:
        if self._loading or self._current_id is None:
            return
        index = self._record_index(self._current_id)
        record = self._records[index]
        name = self.name_edit.text().strip()
        self._records[index] = replace(
            record,
            display_name=name,
            default_tube_diameter_in=self.default_diameter_box.value(),
            flow_values_kg_per_hr=tuple(
                float(value) for value in self.transfer_table.values().reshape(-1)
            ),
        )
        for row in range(self.maf_list.count()):
            item = self.maf_list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == self._current_id:
                item.setText(name or "(Unnamed MAF)")
                break

    def _load_row(self, row: int) -> None:
        if self._loading:
            return
        self._commit_current()
        item = self.maf_list.item(row) if row >= 0 else None
        if item is None:
            self._current_id = None
            self._history.clear()
            self._set_editor_enabled(False)
            return
        self._current_id = str(item.data(Qt.ItemDataRole.UserRole))
        record = self._records[self._record_index(self._current_id)]
        self._loading = True
        try:
            self._set_editor_enabled(True)
            self.name_edit.setText(record.display_name)
            self.default_diameter_box.setValue(record.default_tube_diameter_in)
            self.transfer_table.set_values(
                to_16x16(record.flow_values_kg_per_hr),
                x=CANONICAL_VOLTAGES_V[:16],
                y=CANONICAL_VOLTAGES_V[::16],
                editable=True,
                decimals=2,
            )
            self._history.reset(self.transfer_table.values())
            self.transfer_legend.refresh()
        finally:
            self._loading = False

    def _set_editor_enabled(self, enabled: bool) -> None:
        self.name_edit.setEnabled(enabled)
        self.default_diameter_box.setEnabled(enabled)
        self.transfer_table.setEnabled(enabled)
        self.delete_button.setEnabled(enabled)

    def _paste_transfer_function(self) -> None:
        self.transfer_table.paste_values_text(QApplication.clipboard().text())

    def _copy_transfer_selection(self) -> None:
        text = self.transfer_table.copy_selection_text("3D")
        if text:
            QApplication.clipboard().setText(text)

    def _copy_transfer_table(self) -> None:
        QApplication.clipboard().setText(self.transfer_table.copy_table_text("3D"))

    def _record_transfer_edit(self) -> None:
        if not self._loading:
            self._history.record(self.transfer_table.values())

    def _restore_transfer_edit(self, values: np.ndarray | None) -> None:
        if values is None:
            return
        selection = self.transfer_table.selection_mask()
        self.transfer_table.update_values(values)
        self.transfer_table.select_mask(selection)
        self.transfer_legend.refresh()

    def _undo_transfer_edit(self) -> None:
        self._restore_transfer_edit(self._history.undo())

    def _redo_transfer_edit(self) -> None:
        self._restore_transfer_edit(self._history.redo())

    def add_transfer_function(self) -> None:
        self._commit_current()
        record = new_maf_record(
            "New MAF Transfer Function",
            3.0,
            (0.0,) * 256,
        )
        self._records.append(record)
        self._append_item(record.id, record.display_name)
        self.maf_list.setCurrentRow(self.maf_list.count() - 1)

    def delete_selected(self) -> None:
        row = self.maf_list.currentRow()
        if row < 0:
            return
        item = self.maf_list.item(row)
        maf_id = str(item.data(Qt.ItemDataRole.UserRole))
        self._loading = True
        try:
            self._records.pop(self._record_index(maf_id))
            self.maf_list.takeItem(row)
            self._current_id = None
        finally:
            self._loading = False
        if self.maf_list.count():
            self.maf_list.setCurrentRow(min(row, self.maf_list.count() - 1))
        else:
            self._set_editor_enabled(False)

    def save(self) -> None:
        self._commit_current()
        try:
            save_mafs(self._records)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "MAF Transfer Functions", str(exc))
            return
        self.accept()
