from __future__ import annotations
from pathlib import Path
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QTreeWidget, QTreeWidgetItem,
                               QDialogButtonBox, QCheckBox)
from PySide6.QtCore import Qt

_ROM_ROLE = Qt.ItemDataRole.UserRole
_NAME_ROLE = Qt.ItemDataRole.UserRole + 1

class TableChooserDialog(QDialog):
    """Pick a table from a tree of open ROMs (fact base 1.3 JTableChooser)."""
    def __init__(self, roms, target_name: str, target_shape: tuple[int, int] | None = None,
                 parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Compare '{target_name}' to…")
        self._picked = None
        self._target_shape = target_shape
        self._leaf_shapes: list = []
        lay = QVBoxLayout(self)
        self.tree = QTreeWidget(); self.tree.setHeaderHidden(True); lay.addWidget(self.tree)
        for rom in roms:
            label = Path(rom.path).name if rom.path else rom.definition.romid.xmlid
            root = QTreeWidgetItem([label]); self.tree.addTopLevelItem(root); root.setExpanded(True)
            for name, tdef in rom.definition.tables.items():
                leaf = QTreeWidgetItem([name])
                leaf.setData(0, _ROM_ROLE, rom); leaf.setData(0, _NAME_ROLE, name)
                root.addChild(leaf)
                shape = (tdef.size_x or 1, tdef.size_y or 1)
                self._leaf_shapes.append((leaf, shape))
        self.tree.itemDoubleClicked.connect(self._on_double)
        self.show_all = QCheckBox("Show all shapes")
        self.show_all.toggled.connect(self._apply_shape_filter)
        lay.addWidget(self.show_all)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._accept); bb.rejected.connect(self.reject); lay.addWidget(bb)
        self._apply_shape_filter()

    def _on_double(self, item, _col) -> None:
        if item.data(0, _NAME_ROLE):
            self.select_table(item.data(0, _ROM_ROLE), item.data(0, _NAME_ROLE)); self.accept()

    def _accept(self) -> None:
        it = self.tree.currentItem()
        if it and it.data(0, _NAME_ROLE):
            self.select_table(it.data(0, _ROM_ROLE), it.data(0, _NAME_ROLE))
        self.accept()

    def select_table(self, rom, name: str):
        self._picked = rom.table(name)
        return self._picked

    def picked_table(self):
        return self._picked

    def _apply_shape_filter(self) -> None:
        show_all = self.show_all.isChecked() or self._target_shape is None
        for leaf, shape in self._leaf_shapes:
            ok = show_all or shape == self._target_shape
            leaf.setDisabled(not ok)

    def leaf_enabled_map(self) -> dict[str, bool]:
        return {leaf.text(0): not leaf.isDisabled() for leaf, _s in self._leaf_shapes}
