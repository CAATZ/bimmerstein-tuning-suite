"""Definition Manager (spec §5, §9.4): priority-ordered def paths with live parse status."""
from __future__ import annotations
from pathlib import Path
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QListWidget,
                               QListWidgetItem, QPushButton, QDialogButtonBox, QLabel,
                               QAbstractItemView, QFileDialog)
from PySide6.QtCore import Signal, Qt
from ecueditor.core.defs.library import DefinitionLibrary


class DefinitionManagerDialog(QDialog):
    applied = Signal(object)

    def __init__(self, paths: list[str], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Definition Manager")
        self.resize(560, 320)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Definition files, highest priority first (drag to reorder):"))
        self.list = QListWidget()
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list.model().rowsMoved.connect(lambda *_a: self._refresh_statuses())
        lay.addWidget(self.list)
        btns = QHBoxLayout()
        b_add = QPushButton("Add…"); b_add.clicked.connect(self._on_add)
        b_rm = QPushButton("Remove"); b_rm.clicked.connect(self.remove_selected)
        btns.addWidget(b_add); btns.addWidget(b_rm); btns.addStretch(1)
        lay.addLayout(btns)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                               | QDialogButtonBox.StandardButton.Apply
                               | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._on_ok); bb.rejected.connect(self.reject)
        bb.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(self.apply)
        lay.addWidget(bb)
        for p in paths:
            self._append_row(p)
        self._refresh_statuses()

    # --- rows ------------------------------------------------------------------
    def _append_row(self, path: str) -> None:
        item = QListWidgetItem(path)
        item.setData(Qt.ItemDataRole.UserRole, path)
        self.list.addItem(item)

    def paths(self) -> list[str]:
        return [self.list.item(i).data(Qt.ItemDataRole.UserRole)
                for i in range(self.list.count())]

    def add_path(self, path: str) -> None:
        self._append_row(path); self._refresh_statuses()

    def _on_add(self) -> None:
        fn, _ = QFileDialog.getOpenFileName(self, "Add definition file", "",
                                            "Definitions (*.xml);;All files (*)")
        if fn:
            self.add_path(fn)

    def remove_selected(self) -> None:
        row = self.list.currentRow()
        if row >= 0:
            self.list.takeItem(row); self._refresh_statuses()

    # --- status ------------------------------------------------------------------
    def _refresh_statuses(self) -> None:
        statuses = DefinitionLibrary(self.paths()).document_statuses()
        for i, st in enumerate(statuses):
            item = self.list.item(i)
            name = Path(st.path).name
            if st.ok:
                item.setText(f"✓  {name} — {st.rom_count} ROM defs   ({st.path})")
            else:
                item.setText(f"✗  {name} — {st.error}   ({st.path})")
            item.setToolTip(str(st.path))

    def status_texts(self) -> list[str]:
        return [self.list.item(i).text() for i in range(self.list.count())]

    # --- apply ---------------------------------------------------------------------
    def apply(self) -> None:
        self.applied.emit(DefinitionLibrary(self.paths()))

    def _on_ok(self) -> None:
        self.apply(); self.accept()
