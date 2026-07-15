"""Bundled tabular-figure numeric font (spec §3 D8). UI chrome stays on the system font."""
from __future__ import annotations
from ecueditor.runtime_paths import fonts_dir

NUMERIC_FAMILY = "JetBrains Mono"
_FALLBACK_FAMILY = "Consolas"
_FONTS_DIR = fonts_dir()
_state: dict[str, bool | None] = {"loaded": None}


def register_fonts() -> bool:
    """Load the vendored TTFs into the app font database. Idempotent; False on any failure."""
    if _state["loaded"] is not None:
        return bool(_state["loaded"])
    from PySide6.QtGui import QFontDatabase
    ok = True
    for name in ("JetBrainsMono-Regular.ttf", "JetBrainsMono-Bold.ttf"):
        p = _FONTS_DIR / name
        if not p.is_file() or QFontDatabase.addApplicationFont(str(p)) == -1:
            ok = False
    _state["loaded"] = ok
    return ok


def numeric_font(size: int, bold: bool = False):
    from PySide6.QtGui import QFont
    family = NUMERIC_FAMILY if register_fonts() else _FALLBACK_FAMILY
    f = QFont(family, size, QFont.Weight.Bold if bold else QFont.Weight.Normal)
    f.setStyleStrategy(
        QFont.StyleStrategy.PreferAntialias
        | QFont.StyleStrategy.NoSubpixelAntialias
    )
    return f
