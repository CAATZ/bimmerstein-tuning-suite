from __future__ import annotations
import re
from pathlib import Path
from typing import cast
from PySide6.QtWidgets import (QHeaderView, QWidget, QVBoxLayout, QLineEdit, QTreeWidget,
                               QTreeWidgetItem)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from ecueditor.ui.design.icons import icon
from ecueditor.ui.design.theme_manager import current_theme
from ecueditor.core.rom.image import RomImage

_ROM_ROLE = Qt.ItemDataRole.UserRole
_NAME_ROLE = Qt.ItemDataRole.UserRole + 1
_SECTION_ROLE = Qt.ItemDataRole.UserRole + 2

_ICON_BY_TYPE = {"3D": "table-3d", "2D": "table-2d", "1D": "scalar",
                 "Switch": "switch", "BitwiseSwitch": "switch"}


def icon_name_for_table(tdef) -> str:
    """Map a TableDef's plain-string `type` to an icon name (also used for tab icons)."""
    return _ICON_BY_TYPE.get(getattr(tdef, "type", ""), "scalar")


class RomTreePanel(QWidget):
    table_activated = Signal(object, object)     # emits (rom, table) -- H9
    rom_selected = Signal(object)         # emits the ROM represented by any clicked tree item
    rom_opened = Signal(object)          # emits a core RomImage
    files_dropped = Signal(list)         # emits list[Path]

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("romTreePanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._roms: list[RomImage] = []
        self._filter = ""
        self._dirty: set[tuple[int, str, str]] = set()
        self._failed: set[int] = set()
        self._leaf_items: dict[tuple[int, str], QTreeWidgetItem] = {}
        self._section_leaf_items: dict[tuple[int, str, str], QTreeWidgetItem] = {}
        self._user_level = 5
        self._expanded: dict[tuple[object, ...], bool] = {}
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
    def add_rom(self, rom: RomImage) -> None:
        self._roms.append(rom)
        self.refresh_rom_status(rom)
        self.rom_opened.emit(rom)
        self._rebuild()

    def remove_rom(self, rom: RomImage) -> None:
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
        self._section_leaf_items = {
            k: v for k, v in self._section_leaf_items.items() if k[0] != rid
        }
        self._expanded = {k: v for k, v in self._expanded.items() if k[0] != rid}

    def rom_count(self) -> int:
        return len(self._roms)

    def roms(self) -> list[RomImage]:
        return list(self._roms)

    def _rom_label(self, rom: RomImage) -> str:
        path = getattr(rom, "path", None)
        return Path(path).name if path else rom.definition.romid.xmlid

    # --- status (checksum ✗ / dirty ●) ----------------------------------------
    def refresh_rom_status(self, rom: RomImage) -> None:
        """Re-read checksum_report() so the danger ✗ badge reflects the ROM's current bytes."""
        report = rom.checksum_report() if hasattr(rom, "checksum_report") else None
        if report is not None and not report.ok:
            self._failed.add(id(rom))
        else:
            self._failed.discard(id(rom))

    def set_user_level_filter(self, level: int) -> None:
        self._user_level = int(level); self._rebuild()

    def set_dirty(self, rom: RomImage, table_or_name, dirty: bool) -> None:
        table = rom.table(table_or_name) if isinstance(table_or_name, str) else table_or_name
        section, name = rom.table_key(table)
        key = (id(rom), section, name)
        (self._dirty.add if dirty else self._dirty.discard)(key)
        item = self._section_leaf_items.get(key)
        if item is not None:
            item.setText(0, f"{name} ●" if dirty else name)
        self._refresh_rom_label(rom)

    def clear_dirty(self, rom: RomImage) -> None:
        """Clear every table/ROM dirty decoration after a full disk reload."""
        rid = id(rom)
        self._dirty = {key for key in self._dirty if key[0] != rid}
        for (owner_id, _section, name), item in self._section_leaf_items.items():
            if owner_id == rid:
                item.setText(0, name)
        self._refresh_rom_label(rom)

    def _refresh_rom_label(self, rom: RomImage) -> None:
        for i in range(self.tree.topLevelItemCount()):
            root = self.tree.topLevelItem(i)
            if root is None:
                continue
            if root.data(0, _ROM_ROLE) is rom:
                root.setText(0, self._decorated_rom_label(rom))

    def _decorated_rom_label(self, rom: RomImage) -> str:
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
            if root is None:
                continue
            rom = cast(RomImage, root.data(0, _ROM_ROLE))
            for c in range(root.childCount()):
                child = root.child(c)
                if child is None:
                    continue
                section = child.data(0, _SECTION_ROLE)
                if section:
                    self._expanded[(id(rom), section)] = child.isExpanded()
                    for j in range(child.childCount()):
                        category = child.child(j)
                        if category is not None:
                            self._expanded[(id(rom), section, category.text(0))] = (
                                category.isExpanded()
                            )
                else:
                    self._expanded[
                        (id(rom), rom.sections[0].key, child.text(0))
                    ] = child.isExpanded()
        self.tree.clear()
        self._leaf_items = {}
        self._section_leaf_items = {}
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
            multi_section = len(rom.sections) > 1
            for section in rom.sections:
                section_parent = root
                if multi_section:
                    section_parent = QTreeWidgetItem([section.label])
                    section_parent.setData(0, _ROM_ROLE, rom)
                    section_parent.setData(0, _SECTION_ROLE, section.key)
                    section_parent.setExpanded(
                        self._expanded.get((id(rom), section.key), True)
                    )
                    root.addChild(section_parent)

                cats: dict[str, QTreeWidgetItem] = {}
                for name, tdef in rom.section_definitions(section.key).items():
                    if pat is not None and not pat.search(name):
                        continue
                    if getattr(tdef, "user_level", 1) > self._user_level:
                        continue
                    category_name = tdef.category or "Uncategorized"
                    node = cats.get(category_name)
                    if node is None:
                        node = QTreeWidgetItem([category_name])
                        section_parent.addChild(node)
                        node.setExpanded(
                            self._expanded.get(
                                (id(rom), section.key, category_name), False
                            )
                        )
                        cats[category_name] = node
                    key = (id(rom), section.key, name)
                    dirty = key in self._dirty
                    leaf = QTreeWidgetItem([f"{name} ●" if dirty else name])
                    leaf.setData(0, _ROM_ROLE, rom)
                    leaf.setData(0, _NAME_ROLE, name)
                    leaf.setData(0, _SECTION_ROLE, section.key)
                    leaf.setIcon(0, icon(icon_name_for_table(tdef)))
                    leaf.setToolTip(0, tdef.description or "")
                    node.addChild(leaf)
                    self._section_leaf_items[key] = leaf
                    self._leaf_items.setdefault((id(rom), name), leaf)
                if multi_section and section_parent.childCount() == 0:
                    root.removeChild(section_parent)

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
        section = item.data(0, _SECTION_ROLE)
        if rom is not None:
            self.rom_selected.emit(rom)
        if name and rom is not None:
            self.activate_table(rom, name, section=section)

    def activate_table(self, rom: RomImage, name: str, *, section: str | None = None) -> None:
        self.table_activated.emit(rom, rom.table(name, section=section))

    # --- introspection (test hooks) -----------------------------------------
    def visible_table_names(self) -> list[str]:
        return [key[2] for key in self._section_leaf_items]

    def category_names(self) -> list[str]:
        out: list[str] = []
        for i in range(self.tree.topLevelItemCount()):
            root = self.tree.topLevelItem(i)
            if root is None:
                continue
            for c in range(root.childCount()):
                child = root.child(c)
                if child is not None:
                    if child.data(0, _SECTION_ROLE):
                        out.extend(
                            child.child(j).text(0) for j in range(child.childCount())
                        )
                    else:
                        out.append(child.text(0))
        return out

    def section_names(self) -> list[str]:
        out: list[str] = []
        for i in range(self.tree.topLevelItemCount()):
            root = self.tree.topLevelItem(i)
            if root is None:
                continue
            for c in range(root.childCount()):
                child = root.child(c)
                if child is not None and child.data(0, _SECTION_ROLE):
                    out.append(child.text(0))
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
