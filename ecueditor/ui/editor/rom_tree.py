from __future__ import annotations
import re
from pathlib import Path
from PySide6.QtWidgets import (QHeaderView, QWidget, QVBoxLayout, QLineEdit, QTreeWidget,
                               QTreeWidgetItem)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from ecueditor.ui.design.icons import icon
from ecueditor.ui.design.theme_manager import current_theme

_ROM_ROLE = Qt.ItemDataRole.UserRole
_NAME_ROLE = Qt.ItemDataRole.UserRole + 1

_ICON_BY_TYPE = {"3D": "table-3d", "2D": "table-2d", "1D": "scalar",
                 "Switch": "switch", "BitwiseSwitch": "switch"}


def icon_name_for_table(tdef) -> str:
    """Map a TableDef's plain-string `type` to an icon name (also used for tab icons)."""
    return _ICON_BY_TYPE.get(getattr(tdef, "type", ""), "scalar")


class RomTreePanel(QWidget):
    table_activated = Signal(object, object)     # emits (rom, table) -- H9
    rom_opened = Signal(object)          # emits a core RomImage
    files_dropped = Signal(list)         # emits list[Path]

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("romTreePanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._roms: list[object] = []
        self._filter = ""
        self._dirty: set[tuple[int, str]] = set()
        self._failed: set[int] = set()
        self._leaf_items: dict[tuple[int, str], QTreeWidgetItem] = {}
        self._user_level = 5
        self._expanded: dict[tuple[int, str], bool] = {}
        lay = QVBoxLayout(self); lay.setContentsMargins(2, 2, 2, 2)
        self.filter_box = QLineEdit(); self.filter_box.setPlaceholderText("Filter tables (regex)…")
        self.filter_box.textChanged.connect(self.apply_filter)
        self.tree = QTreeWidget(); self.tree.setHeaderHidden(True)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setStretchLastSection(True)
        self.tree.itemClicked.connect(self._on_item_clicked)
        lay.addWidget(self.filter_box); lay.addWidget(self.tree)
        self.setAcceptDrops(True)

    # --- population ----------------------------------------------------------
    def add_rom(self, rom: object) -> None:
        self._roms.append(rom)
        self.refresh_rom_status(rom)
        self.rom_opened.emit(rom)
        self._rebuild()

    def remove_rom(self, rom: object) -> None:
        if rom in self._roms:
            self._roms.remove(rom)
            self._rebuild()
        # Purge id()-keyed state for the closed ROM so a future RomImage that CPython hands the
        # same id() can't inherit a stale dirty ●/danger ✗ badge. _rebuild() already reset
        # _leaf_items, but the filter here also covers the rom-not-present path (no _rebuild).
        rid = id(rom)
        self._dirty = {k for k in self._dirty if k[0] != rid}
        self._failed.discard(rid)
        self._leaf_items = {k: v for k, v in self._leaf_items.items() if k[0] != rid}
        self._expanded = {k: v for k, v in self._expanded.items() if k[0] != rid}

    def rom_count(self) -> int:
        return len(self._roms)

    def roms(self) -> list:
        return list(self._roms)

    def _rom_label(self, rom: object) -> str:
        path = getattr(rom, "path", None)
        return Path(path).name if path else rom.definition.romid.xmlid

    # --- status (checksum ✗ / dirty ●) ----------------------------------------
    def refresh_rom_status(self, rom: object) -> None:
        """Re-read checksum_report() so the danger ✗ badge reflects the ROM's current bytes."""
        report = rom.checksum_report() if hasattr(rom, "checksum_report") else None
        if report is not None and not report.ok:
            self._failed.add(id(rom))
        else:
            self._failed.discard(id(rom))

    def set_user_level_filter(self, level: int) -> None:
        self._user_level = int(level); self._rebuild()

    def set_dirty(self, rom: object, name: str, dirty: bool) -> None:
        key = (id(rom), name)
        (self._dirty.add if dirty else self._dirty.discard)(key)
        item = self._leaf_items.get(key)
        if item is not None:
            item.setText(0, f"{name} ●" if dirty else name)
        self._refresh_rom_label(rom)

    def _refresh_rom_label(self, rom: object) -> None:
        for i in range(self.tree.topLevelItemCount()):
            root = self.tree.topLevelItem(i)
            if root.data(0, _ROM_ROLE) is rom:
                root.setText(0, self._decorated_rom_label(rom))

    def _decorated_rom_label(self, rom: object) -> str:
        label = self._rom_label(rom)
        if id(rom) in self._failed:
            label += "  ✗"
        if any(k[0] == id(rom) for k in self._dirty):
            label += " ●"
        return label

    def _rebuild(self) -> None:
        # Capture category expansion before the tree is torn down (D-something: filtering /
        # status refreshes must not collapse categories the user opened).
        for i in range(self.tree.topLevelItemCount()):
            root = self.tree.topLevelItem(i)
            rom = root.data(0, _ROM_ROLE)
            for c in range(root.childCount()):
                cat = root.child(c)
                self._expanded[(id(rom), cat.text(0))] = cat.isExpanded()
        self.tree.clear()
        self._leaf_items = {}
        pat = re.compile(self._filter, re.IGNORECASE) if self._filter else None
        for rom in self._roms:
            root = QTreeWidgetItem([self._decorated_rom_label(rom)])
            root.setData(0, _ROM_ROLE, rom)
            if id(rom) in self._failed:
                root.setForeground(0, QColor(current_theme().danger))
                root.setIcon(0, icon("warning"))
            else:
                root.setIcon(0, icon("rom"))
            self.tree.addTopLevelItem(root); root.setExpanded(True)
            cats: dict[str, QTreeWidgetItem] = {}
            for name, tdef in rom.definition.tables.items():
                if tdef.storage_address is None:
                    continue          # not materializable for this ROM -- RomImage.tables skips
                                      # these, so rom.table(name) would KeyError on open. Listing
                                      # them makes a table look present but silently un-openable.
                if pat is not None and not pat.search(name):
                    continue
                if getattr(tdef, "user_level", 1) > self._user_level:
                    continue
                cat = tdef.category or "Uncategorized"
                node = cats.get(cat)
                if node is None:
                    node = QTreeWidgetItem([cat]); root.addChild(node)
                    node.setExpanded(self._expanded.get((id(rom), cat), False))
                    cats[cat] = node
                dirty = (id(rom), name) in self._dirty
                leaf = QTreeWidgetItem([f"{name} ●" if dirty else name])
                leaf.setData(0, _ROM_ROLE, rom); leaf.setData(0, _NAME_ROLE, name)
                leaf.setIcon(0, icon(icon_name_for_table(tdef)))
                leaf.setToolTip(0, tdef.description or "")
                node.addChild(leaf)
                self._leaf_items[(id(rom), name)] = leaf

    def apply_filter(self, text: str) -> None:
        try:
            re.compile(text)
            self._filter = text
        except re.error:
            self._filter = re.escape(text)          # invalid regex -> literal match, never crash
        self._rebuild()

    # --- activation ----------------------------------------------------------
    def _on_item_clicked(self, item: QTreeWidgetItem, _col: int) -> None:
        name = item.data(0, _NAME_ROLE)
        rom = item.data(0, _ROM_ROLE)
        if name and rom is not None:
            self.activate_table(rom, name)

    def activate_table(self, rom: object, name: str) -> None:
        self.table_activated.emit(rom, rom.table(name))

    # --- introspection (test hooks) -----------------------------------------
    def visible_table_names(self) -> list[str]:
        return [key[1] for key in self._leaf_items]

    def category_names(self) -> list[str]:
        out: list[str] = []
        for i in range(self.tree.topLevelItemCount()):
            root = self.tree.topLevelItem(i)
            out += [root.child(c).text(0) for c in range(root.childCount())]
        return out

    # --- drag & drop (fact base 1.2: drop .bin files onto the editor) --------
    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        paths = [Path(u.toLocalFile()) for u in event.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.files_dropped.emit(paths)
            event.acceptProposedAction()
