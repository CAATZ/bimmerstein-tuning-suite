"""Resolve runtime assets consistently in source, wheel, and frozen installations."""
from __future__ import annotations

import os
import sys
import sysconfig
from pathlib import Path

from ecueditor.metadata import PRODUCT_SLUG, WINDOWS_APP_STEM


def _source_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _is_source_tree() -> bool:
    root = _source_root()
    return (root / "pyproject.toml").is_file() and (root / "resources").is_dir()


def _frozen_root() -> Path:
    return Path(sys.executable).resolve().parent


def _installed_root() -> Path:
    # pip may install a wheel with the normal, user, or --target scheme. The
    # interpreter's default sysconfig scheme only describes the first case.
    # Walk outward from the actually imported package so relocated ``share``
    # data remains coupled to the package that owns it.
    package_parent = _source_root()
    for base in (package_parent, *package_parent.parents):
        candidate = base / "share" / PRODUCT_SLUG
        if candidate.is_dir():
            return candidate
    return Path(sysconfig.get_path("data")) / "share" / PRODUCT_SLUG


def asset_root() -> Path:
    """Return the root containing ``resources/``, ``manual/``, and ``plugins/``."""
    override = os.environ.get("ECUEDITOR_ASSET_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    if getattr(sys, "frozen", False):
        return _frozen_root()
    if _is_source_tree():
        return _source_root()
    return _installed_root()


def icons_dir() -> Path:
    return asset_root() / "resources" / "icons"


def fonts_dir() -> Path:
    return asset_root() / "resources" / "fonts"


def user_manual_path() -> Path:
    root = asset_root()
    if getattr(sys, "frozen", False):
        return root / f"{WINDOWS_APP_STEM}-User-Manual.pdf"
    if _is_source_tree() and not os.environ.get("ECUEDITOR_ASSET_ROOT"):
        return root / "output" / "pdf" / f"{WINDOWS_APP_STEM}-User-Manual.pdf"
    return root / "manual" / f"{WINDOWS_APP_STEM}-User-Manual.pdf"


def bundled_plugins_dir() -> Path:
    return asset_root() / "plugins"
