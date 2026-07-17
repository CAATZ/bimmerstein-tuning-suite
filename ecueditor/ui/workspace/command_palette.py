"""Ctrl+K fuzzy table jump (spec §4)."""
from __future__ import annotations
from collections import namedtuple
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLineEdit, QListWidget, QListWidgetItem
from PySide6.QtCore import Qt

PaletteEntry = namedtuple(
    "PaletteEntry", "rom name category description label table", defaults=(None,)
)


def fuzzy_score(query: str, text: str) -> float | None:
    q, t = query.lower(), text.lower()
    if not q:
        return 0.0
    score, ti, prev_hit = 0.0, 0, -2
    for qc in q:
        ix = t.find(qc, ti)
        if ix == -1:
            return None
        score += 1.0
        if ix == prev_hit + 1:
            score += 2.0                                   # contiguous run
        if ix == 0 or t[ix - 1] in " _-/():.":
            score += 3.0                                   # word start
        prev_hit, ti = ix, ix + 1
    return score / (1 + len(t) / 50.0)                     # mild length normalization


def entry_score(query: str, e: PaletteEntry) -> float | None:
    s = fuzzy_score(query, e.name)
    if s is not None:
        return s + 10.0                                    # name matches always outrank
    for field in (e.category or "", e.description or ""):
        s = fuzzy_score(query, field)
        if s is not None:
            return s * 0.5
    return None


class CommandPalette(QDialog):
    def __init__(self, entries: list[PaletteEntry], on_open, parent=None) -> None:
        super().__init__(parent, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Popup)
        self._entries = entries
        self._on_open = on_open
        self.setMinimumWidth(420)
        lay = QVBoxLayout(self); lay.setContentsMargins(1, 1, 1, 1); lay.setSpacing(0)
        self.edit = QLineEdit(); self.edit.setPlaceholderText("Jump to table…")
        self.list = QListWidget()
        lay.addWidget(self.edit); lay.addWidget(self.list)
        self.edit.textChanged.connect(self._refilter)
        self.edit.returnPressed.connect(self.activate_current)
        self.list.itemActivated.connect(lambda _it: self.activate_current())
        self._refilter("")

    def set_query(self, text: str) -> None:
        self.edit.setText(text)

    def visible_labels(self) -> list[str]:
        return [self.list.item(i).text() for i in range(self.list.count())]

    def activate_current(self) -> None:
        item = self.list.currentItem() or (self.list.item(0) if self.list.count() else None)
        if item is None:
            return
        e = item.data(Qt.ItemDataRole.UserRole)
        self.accept()
        self._on_open(e.rom, e.table if e.table is not None else e.name)

    def _refilter(self, text: str) -> None:
        scored = []
        for e in self._entries:
            s = entry_score(text, e)
            if s is not None:
                scored.append((s, e))
        scored.sort(key=lambda p: -p[0])
        self.list.clear()
        for _s, e in scored[:50]:
            item = QListWidgetItem(e.label)
            item.setData(Qt.ItemDataRole.UserRole, e)
            self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)

    def keyPressEvent(self, event) -> None:               # arrows navigate while typing
        if event.key() in (Qt.Key.Key_Down, Qt.Key.Key_Up):
            self.list.keyPressEvent(event); return
        super().keyPressEvent(event)
