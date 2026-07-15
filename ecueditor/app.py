from __future__ import annotations
import os
import sys
from pathlib import Path
from ecueditor.core.defs.library import DefinitionLibrary
from ecueditor.core.plugins.registry import PluginLoadFailure, load_plugins
from ecueditor.core.settings import load_settings
from ecueditor.runtime_paths import bundled_plugins_dir
from ecueditor.ui.app import AppServices, build_app

def _discover_definitions() -> list[Path]:
    raw = os.environ.get("ECUEDITOR_DEFS", "")           # os.pathsep-separated list of def files
    return [Path(p) for p in raw.split(os.pathsep) if p.strip()]


def _plugin_directories() -> list[Path]:
    """Return bundled plugins first, followed by user drop-ins from the working directory."""
    candidates = [bundled_plugins_dir(), _plugin_directory()]
    unique: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved not in seen:
            unique.append(candidate)
            seen.add(resolved)
    return unique


def _plugin_directory() -> Path:
    """Backward-compatible location for user-managed drop-in plugins."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "plugins"
    return Path.cwd() / "plugins"

def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    loaded: list[str] = []
    failures: list[PluginLoadFailure] = []
    for plugin_dir in _plugin_directories():
        loaded.extend(load_plugins(plugin_dir, on_error=failures.append))
    settings = load_settings()
    def_paths = _discover_definitions() or [Path(p) for p in settings.definition_paths]
    services = AppServices(
        library=DefinitionLibrary(def_paths),
        plugins_loaded=loaded,
        plugin_failures=[f"{failure.path.name}: {failure.message}" for failure in failures],
        settings=settings,
        definition_paths=def_paths,
    )
    app = build_app(services, argv)
    return int(app.exec())
