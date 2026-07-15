"""Token-tinted SVG icons (spec §3 D7). Vendored Lucide subset (ISC), stroke=currentColor."""
from __future__ import annotations
import warnings

from ecueditor.runtime_paths import icons_dir

ICON_NAMES: tuple[str, ...] = (
    "app", "rom", "table-3d", "table-2d", "scalar", "switch", "open", "save", "close",
    "refresh", "undo", "undo-all", "revert-flag", "copy", "paste", "interpolate",
    "compare", "color", "cube", "logger", "settings", "search", "lock", "warning", "check",
)
_ICONS_DIR = icons_dir()
_warned: set[str] = set()


def _svg_bytes(name: str, color: str) -> bytes | None:
    p = _ICONS_DIR / f"{name}.svg"
    if not p.is_file():
        if name not in _warned:
            warnings.warn(f"icon {name!r} missing from {_ICONS_DIR}"); _warned.add(name)
        return None
    return p.read_text(encoding="utf-8").replace("currentColor", color).encode("utf-8")


def pixmap(name: str, color: str, size: int):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QPixmap, QPainter
    from PySide6.QtSvg import QSvgRenderer
    data = _svg_bytes(name, color)
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    if data is None:
        return pm
    renderer = QSvgRenderer(data)
    painter = QPainter(pm)
    renderer.render(painter)
    painter.end()
    return pm


def icon(name: str, color: str | None = None):
    from PySide6.QtGui import QIcon
    from ecueditor.ui.design.theme_manager import current_theme
    c = color or current_theme().text_dim
    data = _svg_bytes(name, c)
    if data is None:
        return QIcon()
    ic = QIcon()
    sizes = (16, 20, 24, 32, 48, 64) if name == "app" else (16, 20, 24, 32)
    for size in sizes:
        ic.addPixmap(pixmap(name, c, size))
    return ic
