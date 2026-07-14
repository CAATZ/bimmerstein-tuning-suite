from __future__ import annotations
from pathlib import Path
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QComboBox, QPushButton,
                               QListWidget, QListWidgetItem, QLabel)
from PySide6.QtGui import QColor
from ecueditor.core.rom.compare import compare_images

_EQUAL = QColor(0, 150, 0); _DIFF = QColor(200, 0, 0); _MISSING = QColor(200, 150, 0)

class CompareImagesDialog(QDialog):
    def __init__(self, roms, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Compare Images")
        self._roms = list(roms)
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        self.combo_left = QComboBox(); self.combo_right = QComboBox()
        for rom in self._roms:
            label = Path(rom.path).name if rom.path else rom.definition.romid.xmlid
            self.combo_left.addItem(label); self.combo_right.addItem(label)
        if len(self._roms) > 1:
            self.combo_right.setCurrentIndex(1)
        row.addWidget(self.combo_left); row.addWidget(QLabel("vs")); row.addWidget(self.combo_right)
        self.btn_compare = QPushButton("Compare"); row.addWidget(self.btn_compare)
        lay.addLayout(row)
        self.result_list = QListWidget(); lay.addWidget(self.result_list)
        self.summary_label = QLabel(""); lay.addWidget(self.summary_label)
        self.btn_compare.clicked.connect(self.run_compare)

    def run_compare(self) -> None:
        self.result_list.clear()
        a = self._roms[self.combo_left.currentIndex()]
        b = self._roms[self.combo_right.currentIndex()]
        cmp = compare_images(a, b)
        for name in cmp.equal:
            self._add(f"= {name}", _EQUAL)
        for name in cmp.different:
            self._add(f"≠ {name}", _DIFF)
        for name in cmp.missing:
            self._add(f"? {name} (missing in right)", _MISSING)
        self.summary_label.setText(
            f"{len(cmp.equal)} equal, {len(cmp.different)} different, {len(cmp.missing)} missing")

    def _add(self, text: str, color: QColor) -> None:
        item = QListWidgetItem(text); item.setForeground(color); self.result_list.addItem(item)
