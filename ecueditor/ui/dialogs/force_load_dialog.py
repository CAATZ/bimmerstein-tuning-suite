from __future__ import annotations
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QDialogButtonBox)

class ForceLoadDialog(QDialog):
    """No-match force-load picker (spec §5.1): choose a definition to load a ROM as."""
    def __init__(self, xmlids, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("No matching definition")
        self._picked: str | None = None
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("No ECU definition matched this image.\n"
                             "Load it as one of the definitions below:"))
        self.list = QListWidget(); lay.addWidget(self.list)
        for xmlid in xmlids:
            self.list.addItem(QListWidgetItem(xmlid))
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        bb.button(QDialogButtonBox.StandardButton.Ok).setText("Load as selected definition")
        bb.accepted.connect(self._accept); bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _accept(self) -> None:
        it = self.list.currentItem()
        if it is not None:
            self._picked = it.text()
        self.accept()

    def select_xmlid(self, xmlid: str) -> str:
        """Test hook: emulate the user choosing `xmlid` (list click + OK)."""
        self._picked = xmlid
        return xmlid

    def selected_xmlid(self) -> str | None:
        return self._picked
