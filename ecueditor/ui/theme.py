from __future__ import annotations
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication
from ecueditor.ui.design.tokens import theme_by_name, DARK
from ecueditor.ui.design.qss import render_qss
from ecueditor.ui.design.fonts import register_fonts
from ecueditor.runtime_paths import icons_dir

_NATIVE_PALETTE_ATTR = "_ecueditor_native_palette"
_NATIVE_FONT_ATTR = "_ecueditor_native_font"


def _native_palette(app: QApplication) -> QPalette:
    """Return the live platform palette captured before project theming."""
    palette = getattr(app, _NATIVE_PALETTE_ATTR, None)
    if palette is None:
        palette = QPalette(app.palette())
        setattr(app, _NATIVE_PALETTE_ATTR, palette)
    return QPalette(palette)


def _native_font(app: QApplication) -> QFont:
    """Return the platform font captured before project antialiasing is applied."""
    font = getattr(app, _NATIVE_FONT_ATTR, None)
    if font is None:
        font = QFont(app.font())
        setattr(app, _NATIVE_FONT_ATTR, font)
    return QFont(font)


def _grayscale_antialiased_font(font: QFont) -> QFont:
    result = QFont(font)
    result.setStyleStrategy(
        QFont.StyleStrategy.PreferAntialias
        | QFont.StyleStrategy.NoSubpixelAntialias
    )
    return result


def _theme_palette(app: QApplication, theme) -> QPalette:
    """Project theme tokens onto native widgets that do not paint from QSS."""
    palette = app.style().standardPalette()
    colors = {
        QPalette.ColorRole.Window: theme.bg,
        QPalette.ColorRole.WindowText: theme.text,
        QPalette.ColorRole.Base: theme.surface1,
        QPalette.ColorRole.AlternateBase: theme.surface2,
        QPalette.ColorRole.ToolTipBase: theme.surface3,
        QPalette.ColorRole.ToolTipText: theme.text,
        QPalette.ColorRole.Text: theme.text,
        QPalette.ColorRole.Button: theme.surface3,
        QPalette.ColorRole.ButtonText: theme.text,
        QPalette.ColorRole.BrightText: theme.danger,
        QPalette.ColorRole.Link: theme.accent,
        QPalette.ColorRole.Highlight: theme.accent,
        QPalette.ColorRole.HighlightedText: "#ffffff",
        QPalette.ColorRole.PlaceholderText: theme.text_disabled,
    }
    for role, color in colors.items():
        palette.setColor(role, QColor(color))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text,
                 QPalette.ColorRole.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(theme.text_disabled))
    return palette


def apply_theme(app: QApplication, theme: str) -> None:
    """Apply a UI theme by name — the single theming entry point (INTERFACES §ui/theme.py).

    "dark"/"light" render the token stylesheet; "system" or unknown restores the live native
    palette and clears project styling. Also registers bundled fonts, pushes pyqtgraph global
    colors, and updates the app's ThemeManager (app property "ecueditor_theme_manager") so
    painter widgets repaint live.
    """
    t = theme_by_name(theme)
    register_fonts()
    icon_path = icons_dir().as_posix()
    native_palette = _native_palette(app)
    native_font = _native_font(app)
    if t is None:
        app.setStyleSheet("")
        app.setPalette(native_palette)
        app.setFont(native_font)
    else:
        app.setPalette(_theme_palette(app, t))
        app.setFont(_grayscale_antialiased_font(native_font))
        app.setStyleSheet(render_qss(t, icon_path))
    pg_theme = t or DARK
    try:                                    # pyqtgraph is a gui-extra dep; config is global
        import pyqtgraph as pg
        pg.setConfigOption("background", pg_theme.bg)
        pg.setConfigOption("foreground", pg_theme.text_dim)
    except Exception:  # noqa: BLE001 — pg absent or headless GL quirk: chrome still themes
        pass
    mgr = app.property("ecueditor_theme_manager")
    if mgr is not None:
        mgr.set_theme(t or DARK)
