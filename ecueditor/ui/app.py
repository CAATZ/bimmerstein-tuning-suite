from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING
from PySide6.QtWidgets import QApplication
from ecueditor import __version__
from ecueditor.core.defs.library import DefinitionLibrary
from ecueditor.core.settings import EditorSettings
from ecueditor.metadata import PRODUCT_NAME, PUBLISHER

if TYPE_CHECKING:
    from ecueditor.ui.design.theme_manager import ThemeManager

@dataclass
class AppServices:
    """Everything the UI needs from the core, assembled once at startup."""
    library: DefinitionLibrary
    plugins_loaded: list[str] = field(default_factory=list)
    settings: EditorSettings | None = None
    definition_paths: list[Path] = field(default_factory=list)   # def files behind `library` (force-load picker)
    theme_manager: ThemeManager | None = None
    plugin_failures: list[str] = field(default_factory=list)

def build_app(services: AppServices, argv: list[str] | None = None) -> QApplication:
    instance = QApplication.instance()
    if isinstance(instance, QApplication):
        app = instance
    else:
        app = QApplication(argv if argv is not None else [])
    app.setApplicationName(PRODUCT_NAME)
    app.setApplicationVersion(__version__)
    app.setOrganizationName(PUBLISHER)
    from ecueditor.ui.design.theme_manager import ThemeManager
    from ecueditor.ui.design.tokens import DARK
    services.theme_manager = ThemeManager(DARK)
    app.setProperty("ecueditor_theme_manager", services.theme_manager)
    from ecueditor.ui.theme import apply_theme
    apply_theme(app, getattr(services.settings, "theme", "dark") if services.settings else "dark")
    from ecueditor.ui.main_window import MainWindow
    window = MainWindow(services)
    app.setProperty("ecueditor_window", window)   # keep a strong reference alive
    # Deferred show(): calling window.show() synchronously here dies with
    # "Windows fatal exception: access violation" under pytest-qt (Windows,
    # Python 3.14, PySide6 6.11, offscreen). The deferral applies at runtime
    # too: the window appears on the first event-loop iteration of app.exec().
    # Do not inline show() without re-running tests/ui on Windows.
    from PySide6.QtCore import QTimer
    QTimer.singleShot(0, window.show)
    return app
