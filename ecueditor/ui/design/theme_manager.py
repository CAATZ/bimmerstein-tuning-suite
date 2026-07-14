"""Current-theme distribution for painter-based widgets (spec §3)."""
from __future__ import annotations
from PySide6.QtCore import QObject, Signal
from ecueditor.ui.design.tokens import Theme, DARK

_current: Theme = DARK


def current_theme() -> Theme:
    """The active Theme for painter code (delegates, gauges, 3D). DARK before startup wiring."""
    return _current


class ThemeManager(QObject):
    changed = Signal(object)

    def __init__(self, theme: Theme, parent=None) -> None:
        super().__init__(parent)
        global _current
        _current = theme
        self._theme = theme

    @property
    def theme(self) -> Theme:
        return self._theme

    def set_theme(self, theme: Theme) -> None:
        global _current
        if theme is self._theme:
            return
        self._theme = theme
        _current = theme
        self.changed.emit(theme)
