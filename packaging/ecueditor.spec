# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for BimmerStein Tuning Suite. Build: pyinstaller packaging/ecueditor.spec
# Targets PyInstaller >= 6 (the cipher/block_cipher API was removed in 6.0, Oct 2023).
import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

ROOT = Path(SPECPATH).parent            # SPECPATH is injected by PyInstaller

# Registry built-ins and plugins are imported dynamically (load_plugins + @register),
# so PyInstaller's static analysis misses them. Force them in as hidden imports.
# collect_submodules walks whatever is present, so deferred slots (e.g. std/bytexor/copy
# checksums) are picked up automatically if/when they are added — no need to assert them here.
hiddenimports = []
for pkg in (
    "ecueditor.core.memory",                    # direct, ms41_fullread
    "ecueditor.core.checksum.builtins",         # ms41 (std/bytexor/copy are deferred backlog slots)
    "ecueditor.core.comms.protocol",            # ds2
    "ecueditor.core.comms.transport",           # d2xx, pyserial, replay
    "ecueditor.core.defs.importers",            # native XML + future importers
    "ecueditor.core.logger.analysis.builtins",  # maf, injector
    "ecueditor.core.external",                   # external data source base
):
    hiddenimports += collect_submodules(pkg)
hiddenimports += ["pyqtgraph", "matplotlib.backends.backend_qtagg", "numpy", "serial",
                  "PySide6.QtSvg"]   # runtime deps not always auto-found

# Bundle UI resources. Post-8a the real payload is resources/icons/*.svg (Lucide subset, Task 7)
# and resources/fonts/*.ttf (bundled JetBrains Mono, Task 6), pulled in wholesale by the explicit
# resources/ rglob below (resources/screenshots/ is excluded -- human-checkpoint images, not a
# runtime asset). Theming itself is no longer a shipped file: dark.qss was deleted when ui/theme.py
# moved to rendering the QSS from design tokens at runtime (Task 5/8), so PySide6.QtSvg is a
# hiddenimport above (icons are rendered via QtSvg, not baked PNGs). Definition XMLs still are NOT
# bundled: definitions are user-supplied (settings.definition_paths / ECUEDITOR_DEFS / the
# force-load picker). The collect_data_files qss/png/svg globs and the conditional app.ico below
# are future-proofing for asset types not currently under ecueditor/ itself (resources/ already
# covers icons/fonts via the rglob).
datas = collect_data_files("ecueditor", includes=["**/*.qss", "**/*.png", "**/*.svg"])
_res = ROOT / "resources"
if _res.is_dir():
    datas += [(str(p), str(p.parent.relative_to(ROOT))) for p in _res.rglob("*")
              if p.is_file() and "screenshots" not in p.relative_to(_res).parts]
for _legal_name in ("LICENSE", "THIRD_PARTY_NOTICES.md", "RELEASE_NOTES.md"):
    _legal_file = ROOT / _legal_name
    if _legal_file.is_file():
        datas.append((str(_legal_file), "."))
_manual_file = ROOT / "output" / "pdf" / "BimmerStein-Tuning-Suite-User-Manual.pdf"
if _manual_file.is_file():
    datas.append((str(_manual_file), "."))

_dependency_licenses_raw = os.environ.get("ECUEDITOR_DEPENDENCY_LICENSES", "")
if _dependency_licenses_raw:
    _dependency_licenses = Path(_dependency_licenses_raw)
    if not _dependency_licenses.is_dir():
        raise SystemExit(f"Dependency-license directory does not exist: {_dependency_licenses}")
    datas += [
        (str(p), str(Path("DEPENDENCY_LICENSES") / p.relative_to(_dependency_licenses).parent))
        for p in _dependency_licenses.rglob("*") if p.is_file()
    ]

# User-droppable plugins/ ship alongside the EXE; frozen startup resolves this directory from
# sys.executable, so shortcuts and other launchers work regardless of their working directory.
# NOTE (see Task 3): the six cookbook DEMO plugins (demoxor/demoproto/demotransport/demoxdf/demoafr/
# demowideband) live under tests/plugins/fixtures/, NOT repo-root plugins/, so they are never bundled
# into a release. Only genuine user drop-ins in plugins/ ship; the `demo*` guard below is belt-and-braces
# (it would also exclude a genuine user plugin literally named demo_*.py — see build_windows.md
# Troubleshooting).
_plugins = ROOT / "plugins"
if _plugins.is_dir():
    datas += [(str(p), "plugins") for p in _plugins.glob("*.py")
              if not p.name.startswith(("_", "demo"))]

a = Analysis(
    [str(ROOT / "ecueditor" / "__main__.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        "tkinter", "PyQt5", "PyQt6", "pytest", "pytestqt", "mypy", "ruff", "IPython",
        "OpenGL", "jupyter_rfb",
    ],
)
pyz = PYZ(a.pure)                              # PyInstaller >= 6: no cipher/zipped_data args

# PyInstaller >= 6 defaults to putting all `datas` under dist/ecueditor/_internal/.
# contents_directory="." restores the pre-6 beside-the-EXE layout so user-editable plugins/
# remains next to the launcher EXE, where the frozen executable-relative resolver finds it.
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="BimmerStein-Tuning-Suite",
          console=False, debug=False, icon=str(ROOT / "resources" / "icons" / "app.ico"),
          contents_directory=".")
coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas, name="BimmerStein-Tuning-Suite")
