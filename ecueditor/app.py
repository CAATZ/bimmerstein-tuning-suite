from __future__ import annotations
import os
import sys
from pathlib import Path
from ecueditor.core.defs.library import DefinitionLibrary
from ecueditor.core.plugins.registry import load_plugins
from ecueditor.core.settings import load_settings
from ecueditor.ui.app import AppServices, build_app

def _discover_definitions() -> list[Path]:
    raw = os.environ.get("ECUEDITOR_DEFS", "")           # os.pathsep-separated list of def files
    return [Path(p) for p in raw.split(os.pathsep) if p.strip()]


def _plugin_directory() -> Path:
    """Locate drop-in plugins beside a frozen EXE, or under the development working directory."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "plugins"
    return Path.cwd() / "plugins"

def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    loaded = load_plugins(_plugin_directory())
    settings = load_settings()
    def_paths = _discover_definitions() or [Path(p) for p in settings.definition_paths]
    services = AppServices(
        library=DefinitionLibrary(def_paths),
        plugins_loaded=loaded,
        settings=settings,
        definition_paths=def_paths,
    )
    app = build_app(services, argv)
    return int(app.exec())
