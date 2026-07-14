"""Shared table-frame title band (spec §5, B1: description rendered at last)."""
from __future__ import annotations
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel
from PySide6.QtGui import QFont
from PySide6.QtCore import Qt
from ecueditor.ui.design.theme_manager import current_theme
from ecueditor.ui.design.icons import pixmap
from ecueditor.ui.workspace.status_chips import Chip


class FrameHeader(QWidget):
    def __init__(self, tdef, *, warning_style: bool = False, parent=None) -> None:
        super().__init__(parent)
        t = current_theme()
        self.setObjectName("frameHeader")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        lay = QVBoxLayout(self); lay.setContentsMargins(12, 8, 12, 6); lay.setSpacing(4)
        row = QHBoxLayout(); row.setSpacing(8); lay.addLayout(row)
        self._title = QLabel(tdef.name)
        self._title.setObjectName("frameTitle")
        self._title.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        f = QFont(self.font()); f.setPointSize(15); f.setWeight(QFont.Weight.DemiBold)
        f.setStyleStrategy(
            QFont.StyleStrategy.PreferAntialias
            | QFont.StyleStrategy.NoSubpixelAntialias
        )
        self._title.setFont(f)
        row.addWidget(self._title)
        if tdef.category:
            row.addWidget(Chip(tdef.category.upper(), "neutral"))
        row.addWidget(Chip(tdef.type.upper(), "neutral"))
        address = getattr(tdef, "storage_address", None)
        self._address_text = f"0x{address:04X}" if address is not None else ""
        if self._address_text:
            address_chip = Chip(f"ADDR {self._address_text}", "neutral")
            address_chip.setToolTip("Definition storage address")
            row.addWidget(address_chip)
        level = getattr(tdef, "user_level", 1)
        if level > 1:
            row.addWidget(Chip(f"LVL {level}", "warn" if level >= 4 else "neutral"))
        self._lock = QLabel()
        if getattr(tdef, "locked", False):
            self._lock.setPixmap(pixmap("lock", t.warn, 14))
            self._lock.setToolTip("Locked by the definition — read-only")
            row.addWidget(self._lock)
        row.addStretch(1)
        self._desc = QLabel()
        self._desc.setWordWrap(True)
        text = (tdef.description or "").strip()
        if text and warning_style:
            text = f"⚠ {text}"
            self._desc.setStyleSheet(f"color: {t.warn};")
        else:
            self._desc.setStyleSheet(f"color: {t.text_dim};")
        self._desc.setText(text)
        lay.addWidget(self._desc)
        self._desc.setVisible(bool(text))

    def title_text(self) -> str: return self._title.text()
    def description_text(self) -> str: return self._desc.text()
    def address_text(self) -> str: return self._address_text
    def has_lock_badge(self) -> bool: return bool(self._lock.pixmap() and not self._lock.pixmap().isNull())
