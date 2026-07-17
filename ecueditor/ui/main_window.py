from __future__ import annotations
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any
from PySide6.QtWidgets import (
    QDockWidget,
    QMainWindow,
    QMenu,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtCore import Qt, QSize, QTimer
from ecueditor.ui.app import AppServices
from ecueditor.metadata import PRODUCT_NAME, PRODUCT_TAGLINE, display_version
from ecueditor.runtime_paths import user_manual_path
from ecueditor.ui.workspace.document_area import DocumentArea
from ecueditor.ui.workspace.document_navigator import DocumentNavigator

_log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from ecueditor.core.rom.image import RomImage


def _user_manual_path() -> Path:
    """Return the installed or source-tree user manual path."""
    return user_manual_path()

def _apply_settings_to_grid(grid, settings) -> None:
    """Per-grid projection of user-tunable display settings (theme handles all colors).

    Shared by MainWindow._apply_settings (existing open grids, on Settings-dialog OK) and
    MainWindow.open_table (grids opened afterwards) so newly opened tables aren't stuck on
    hard-coded/default styling until the user re-opens the Settings dialog.
    """
    from ecueditor.ui.design.fonts import numeric_font
    from PySide6.QtGui import QFontMetrics
    density = getattr(settings, "table_density", "normal")
    compact = density == "compact"
    font_size = max(7, settings.font_size - 3) if compact else settings.font_size
    cell_width = 30 if compact else 42
    cell_height = 14 if compact else 18
    vertical_padding = 2 if compact else 12
    font = numeric_font(font_size)
    grid.model().set_colormap(getattr(settings, "colormap", "rainbow"))
    grid.set_color_cells(settings.color_cells)
    fm = QFontMetrics(font)
    row_h = max(cell_height, fm.height() + vertical_padding)
    grid.configure_display(
        font=font,
        density=density,
        row_height=row_h,
        minimum_column_width=cell_width,
    )
    parent = grid.parentWidget()
    if parent is not None:
        layout = parent.layout()
        if layout is not None:
            layout.invalidate()
        parent.updateGeometry()
    grid.viewport().update()

class MainWindow(QMainWindow):
    def __init__(self, services: AppServices, parent=None) -> None:
        super().__init__(parent)
        self._services = services
        self._logger_window = None             # single live logger window (Phase 6b)
        self._enable3d_connection = None        # tracked 3D-action connection (H8: no bare disconnect)
        self._inspector_connection = None       # tracked selectionModel().currentChanged rebind
        self._selected_rom = None               # last explicit tree/document ROM selection
        self._reloading_rom = False             # suppress edit routing during model reset on F5
        self._open_frames: dict[tuple[object, ...], Any] = {}
        self.setWindowTitle(PRODUCT_NAME)
        from ecueditor.ui.design.icons import icon
        self.setWindowIcon(icon("app"))               # spec §3: the app finally has a window icon
        self.documents = DocumentArea()
        self.documents.documentClosed.connect(self._on_document_closed)
        self.document_navigator = DocumentNavigator(self.documents)
        workspace = QWidget(self)
        workspace.setObjectName("workspaceHost")
        workspace_layout = QVBoxLayout(workspace)
        workspace_layout.setContentsMargins(0, 0, 0, 0)
        workspace_layout.setSpacing(0)
        workspace_layout.addWidget(self.document_navigator)
        workspace_layout.addWidget(self.documents, 1)
        self.setCentralWidget(workspace)
        self.resize(1000, 700)
        self._build_actions()
        self._build_toolbar()
        self._build_dock()
        # Live theme switch (Settings dialog / View->Theme menu) repaints chrome/grid/chips/menus
        # via setStyleSheet already, but the ROM tree's per-item icon tints and the failed-ROM
        # danger foreground are computed at _rebuild() time -- wire ThemeManager.changed so the
        # tree rebuilds too, instead of staying stuck on the old theme's colors until the next
        # tree interaction (final-review finding). theme_manager is None in test helpers that
        # build MainWindow(AppServices(library=..., settings=...)) directly -- guard it.
        tm = getattr(self._services, "theme_manager", None)
        if tm is not None:
            self.documents.apply_theme(tm.theme)
            tm.changed.connect(lambda _t: self.rom_tree._rebuild())
            tm.changed.connect(self.documents.apply_theme)
        # Apply the stored user-level ceiling at startup (Task 19; _build_menus below reflects it
        # in the radio group's initial check state).
        self.rom_tree.set_user_level_filter(
            getattr(services.settings, "user_level", 5) if services.settings else 5)
        self._build_menus()
        self._build_statusbar()
        self._update_rom_actions(active=False)
        self.action_save.triggered.connect(lambda: self.save_active_rom(save_as=False))
        self.action_save_as.triggered.connect(lambda: self.save_active_rom(save_as=True))
        self.action_settings.triggered.connect(self._open_settings)
        self.action_close.triggered.connect(self._close_active_rom)
        self.action_refresh.triggered.connect(self._refresh_active_rom)
        self.action_launch_logger.triggered.connect(self._open_logger)
        self._restore_ui_state()
        # Saved Qt layouts can contain dock widths from a much larger monitor.  Apply
        # content-sized defaults after restoreState has finished so those stale values
        # cannot squeeze the table workspace on the next event-loop turn.
        QTimer.singleShot(0, self._normalize_dock_widths)

    # --- window chrome (H10, H13) ---------------------------------------------
    def closeEvent(self, event) -> None:               # quit guard + layout persistence
        from PySide6.QtWidgets import QMessageBox
        dirty = [r for r in self.rom_tree._roms if r.is_dirty()]
        if dirty:
            if QMessageBox.question(self, "Quit",
                    f"Discard unsaved changes in {len(dirty)} ROM(s) and quit?") \
                    != QMessageBox.StandardButton.Yes:
                event.ignore(); return
        if any(
            not self.documents.can_close_document(document)
            for document in self.documents.documents()
        ):
            event.ignore(); return
        s = self._services.settings
        if s is not None:
            s.ui_state = {
                "geometry": bytes(self.saveGeometry().toBase64().data()).decode("ascii"),
                "window_state": bytes(self.saveState().toBase64().data()).decode("ascii"),
                "workspace_mode": self.documents.workspace_mode(),
            }
            from ecueditor.core.settings import save_settings
            save_settings(s)
        event.accept()

    def _restore_ui_state(self) -> None:
        from PySide6.QtCore import QByteArray
        s = self._services.settings
        state = getattr(s, "ui_state", {}) if s is not None else {}
        if state.get("geometry"):
            self.restoreGeometry(QByteArray.fromBase64(state["geometry"].encode("ascii")))
        if state.get("window_state"):
            self.restoreState(QByteArray.fromBase64(state["window_state"].encode("ascii")))
        self.documents.set_workspace_mode(state.get("workspace_mode", "studio"))

    def _normalize_dock_widths(self) -> None:
        """Keep metadata docks useful without letting them dominate the workspace."""
        self.resizeDocks([self.rom_dock], [360], Qt.Orientation.Horizontal)
        self.resizeDocks([self.inspector_dock], [300], Qt.Orientation.Horizontal)

    # --- actions -------------------------------------------------------------
    def _build_actions(self) -> None:
        self.action_open = QAction("&Open ROM…", self, shortcut=QKeySequence("Ctrl+O"))
        self.action_save = QAction("&Save", self, shortcut=QKeySequence("Ctrl+S"))
        self.action_save_as = QAction("Save &As…", self, shortcut=QKeySequence("Ctrl+Shift+S"))
        self.action_close = QAction("&Close ROM", self, shortcut=QKeySequence("Ctrl+W"))
        self.action_refresh = QAction("&Reload ROM from Disk", self, shortcut=QKeySequence("F5"))
        self.action_refresh.setToolTip("Reload ROM from Disk (F5)")
        self.action_refresh.setStatusTip(
            "Reload the selected ROM from its source file. Unsaved changes require confirmation."
        )
        self.action_quit = QAction("E&xit", self, shortcut=QKeySequence("Ctrl+Q"))
        self.action_settings = QAction("&Settings…", self)
        self.action_def_manager = QAction("&Definition Manager…", self)
        self.action_def_manager.triggered.connect(self._open_definition_manager)
        self.action_compare_images = QAction("Compare &Images…", self)
        self.action_launch_logger = QAction("&Launch Logger…", self,
                                            shortcut=QKeySequence("Ctrl+L"))
        self.action_palette = QAction("Go to Table…", self, shortcut=QKeySequence("Ctrl+K"))
        self.action_palette.triggered.connect(self._open_command_palette)
        self.addAction(self.action_palette)          # app-wide shortcut (no menu yet -- Task 19)

        self.workspace_action_group = QActionGroup(self)
        self.workspace_action_group.setExclusive(True)
        self.action_workspace_studio = QAction(
            "&Studio", self, checkable=True, checked=True,
            shortcut=QKeySequence("Ctrl+Alt+1"),
        )
        self.action_workspace_compare = QAction(
            "&Compare", self, checkable=True,
            shortcut=QKeySequence("Ctrl+Alt+2"),
        )
        self.action_workspace_compare.setToolTip(
            "Compare two windows side by side; use the arrow to choose the second window"
        )
        self.action_workspace_focus = QAction(
            "&Focus", self, checkable=True,
            shortcut=QKeySequence("Ctrl+Alt+3"),
        )
        for action in (
            self.action_workspace_studio,
            self.action_workspace_compare,
            self.action_workspace_focus,
        ):
            self.workspace_action_group.addAction(action)
        self.action_workspace_studio.triggered.connect(
            lambda _checked=False: self.documents.set_workspace_mode("studio")
        )
        self.action_workspace_compare.triggered.connect(
            lambda _checked=False: self.documents.set_workspace_mode("compare")
        )
        self.action_workspace_focus.triggered.connect(
            lambda _checked=False: self.documents.toggle_focus()
        )

        # V2 internal-window commands. Ctrl+W intentionally remains "Close ROM" for V1
        # compatibility; Ctrl+F4 closes only the active table or 3D child window.
        self.action_window_close = QAction("&Close Window", self,
                                           shortcut=QKeySequence("Ctrl+F4"))
        self.action_window_close_all = QAction("Close &All Windows", self)
        self.action_window_tile = QAction("&Tile", self)
        self.action_window_cascade = QAction("&Cascade", self)
        self.action_window_minimize_all = QAction("&Minimize All", self)
        self.action_window_restore_active = QAction("Restore Active &Window", self)
        self.action_window_restore_all = QAction("&Restore All", self)
        self.action_window_next = QAction("&Next Window", self,
                                          shortcut=QKeySequence("Ctrl+Tab"))
        self.action_window_previous = QAction("&Previous Window", self,
                                              shortcut=QKeySequence("Ctrl+Shift+Tab"))
        self.action_window_close.triggered.connect(self.documents.close_active_document)
        self.action_window_close_all.triggered.connect(self.documents.close_all_documents)
        self.action_window_tile.triggered.connect(self.documents.tile_documents)
        self.action_window_cascade.triggered.connect(self.documents.cascade_documents)
        self.action_window_minimize_all.triggered.connect(self.documents.minimize_all_documents)
        self.action_window_restore_active.triggered.connect(
            self.documents.restore_active_document
        )
        self.action_window_restore_all.triggered.connect(self.documents.restore_all_documents)
        self.action_window_next.triggered.connect(self.documents.activate_next_document)
        self.action_window_previous.triggered.connect(self.documents.activate_previous_document)
        self.action_quit.triggered.connect(self.close)

    def _build_menus(self) -> None:
        mb = self.menuBar()
        self.menu_file = mb.addMenu("&File")
        for a in (self.action_open, self.action_save, self.action_save_as,
                  self.action_close, self.action_refresh):
            self.menu_file.addAction(a)
        self.menu_file.addAction(self.action_def_manager)
        self.menu_file.addSeparator(); self.menu_file.addAction(self.action_quit)
        self.menu_edit = mb.addMenu("&Edit")
        self.menu_edit.addAction(self.action_settings)
        self.menu_compare = mb.addMenu("&Compare")
        self.menu_compare.addAction(self.action_compare_images)
        self.menu_logger = mb.addMenu("&Logger")
        self.menu_logger.addAction(self.action_launch_logger)

        self.menu_view = mb.addMenu("&View")
        self.menu_view.addAction(self.rom_dock.toggleViewAction())
        self.menu_view.addAction(self.inspector_dock.toggleViewAction())
        self.menu_view.addSeparator()
        self.menu_view.addAction(self.action_palette)          # Ctrl+K (Task 18)
        self.action_rom_properties = QAction("ROM &Properties…", self)
        self.action_rom_properties.triggered.connect(self._open_rom_properties)
        self.menu_view.addAction(self.action_rom_properties)
        level_menu = self.menu_view.addMenu("&User Level")
        from PySide6.QtGui import QActionGroup
        level_group = QActionGroup(self)
        current_level = (getattr(self._services.settings, "user_level", 5)
                         if self._services.settings else 5)
        for n in range(1, 6):
            act = QAction(f"≤ {n}", self, checkable=True, checked=(n == current_level))
            act.triggered.connect(lambda _c=False, n=n: self._set_user_level(n))
            level_group.addAction(act); level_menu.addAction(act)
        theme_menu = self.menu_view.addMenu("&Theme")
        for label, value in (("Dark", "dark"), ("Light", "light"), ("System", "system")):
            act = QAction(label, self)
            act.triggered.connect(lambda _c=False, v=value: self._switch_theme(v))
            theme_menu.addAction(act)

        self.menu_window = mb.addMenu("&Window")
        self.menu_window.addAction(self.action_workspace_studio)
        self.menu_window.addAction(self.action_workspace_compare)
        self.menu_window.addAction(self.action_workspace_focus)
        self.menu_compare_with = self.menu_window.addMenu("Compare Active &With…")
        self.menu_compare_with.aboutToShow.connect(
            lambda: self._refresh_compare_with_menu(self.menu_compare_with)
        )
        self.menu_window.addSeparator()
        self.menu_window.addAction(self.action_window_close)
        self.menu_window.addAction(self.action_window_close_all)
        self.menu_window.addSeparator()
        self.menu_window.addAction(self.action_window_tile)
        self.menu_window.addAction(self.action_window_cascade)
        self.menu_window.addAction(self.action_window_minimize_all)
        self.menu_window.addAction(self.action_window_restore_active)
        self.menu_window.addAction(self.action_window_restore_all)
        self.menu_window.addSeparator()
        self.menu_window.addAction(self.action_window_next)
        self.menu_window.addAction(self.action_window_previous)
        self._window_list_separator = self.menu_window.addSeparator()
        self._window_document_actions: list[QAction] = []
        self.menu_window.aboutToShow.connect(self._refresh_window_menu)
        self.documents.documentCountChanged.connect(self._update_window_actions)
        self.documents.workspaceModeChanged.connect(self._on_workspace_mode_changed)
        self._update_window_actions(0)

        self.menu_help = mb.addMenu("&Help")
        self.action_user_manual = QAction("&User Manual", self)
        self.action_user_manual.triggered.connect(self._open_user_manual)
        act_about = QAction(f"&About {PRODUCT_NAME}", self)
        act_about.triggered.connect(self._show_about)
        act_keys = QAction("&Keyboard Shortcuts", self)
        act_keys.triggered.connect(self._show_shortcuts)
        self.menu_help.addAction(self.action_user_manual)
        self.menu_help.addAction(act_keys)
        self.menu_help.addSeparator()
        self.menu_help.addAction(act_about)

    def _update_window_actions(self, count: int) -> None:
        has_documents = count > 0
        for action in (
            self.action_window_close,
            self.action_window_close_all,
            self.action_window_tile,
            self.action_window_cascade,
            self.action_window_minimize_all,
            self.action_window_restore_active,
            self.action_window_restore_all,
        ):
            action.setEnabled(has_documents)
        self.action_window_next.setEnabled(count > 1)
        self.action_window_previous.setEnabled(count > 1)
        self.action_workspace_compare.setEnabled(count > 1)
        if hasattr(self, "menu_compare_with"):
            self.menu_compare_with.setEnabled(count > 1)

    def _refresh_compare_with_menu(self, menu: QMenu) -> None:
        """List live MDI windows to pair with the currently active document."""
        menu.clear()
        active = self.documents.active_document()
        others = [document for document in self.documents.documents() if document is not active]
        if active is None or not others:
            hint = menu.addAction("Open at least two windows")
            if hint is not None:
                hint.setEnabled(False)
            return
        for document in others:
            title = self.documents.document_title(document)
            action = menu.addAction(title)
            if action is None:
                continue
            window = self.documents.window_for_document(document)
            if window is not None:
                action.setIcon(window.windowIcon())
            action.triggered.connect(
                lambda _checked=False, first=active, second=document:
                self.documents.set_compare_documents(first, second)
            )

    def _refresh_window_menu(self) -> None:
        for action in self._window_document_actions:
            self.menu_window.removeAction(action)
            action.deleteLater()
        self._window_document_actions.clear()

        documents = self.documents.documents()
        self._window_list_separator.setVisible(bool(documents))
        active = self.documents.active_document()
        for index, document in enumerate(documents, start=1):
            title = self.documents.document_title(document)
            prefix = f"&{index} " if index < 10 else ""
            action = QAction(f"{prefix}{title}", self.menu_window, checkable=True)
            action.setChecked(document is active)
            action.triggered.connect(
                lambda _checked=False, doc=document: self.documents.set_active_document(doc)
            )
            self.menu_window.addAction(action)
            self._window_document_actions.append(action)

    def _on_workspace_mode_changed(self, mode: str) -> None:
        actions = {
            "studio": self.action_workspace_studio,
            "compare": self.action_workspace_compare,
            "focus": self.action_workspace_focus,
        }
        actions.get(mode, self.action_workspace_studio).setChecked(True)
        self.action_window_restore_active.setShortcut(
            QKeySequence("Esc") if mode == "focus" else QKeySequence()
        )
        if hasattr(self, "workspace_chip"):
            self.workspace_chip.setText(mode.title())
            if mode == "compare":
                self.workspace_chip.setToolTip(
                    "Two windows are paired side by side; choose another from Compare Active With"
                )
            else:
                self.workspace_chip.setToolTip(f"{mode.title()} workspace layout")

    def _build_toolbar(self) -> None:
        from ecueditor.ui.design.icons import icon
        self.action_open.setIcon(icon("open"))
        self.action_save.setIcon(icon("save"))
        self.action_close.setIcon(icon("close"))
        self.action_refresh.setIcon(icon("refresh"))
        self.action_launch_logger.setIcon(icon("logger"))
        self.action_settings.setIcon(icon("settings"))

        self.editor_toolbar = QToolBar("Editor", self)
        self.editor_toolbar.setObjectName("editor_toolbar")
        for a in (self.action_open, self.action_save, self.action_close, self.action_refresh):
            self.editor_toolbar.addAction(a)
        self.editor_toolbar.addSeparator()
        self.editor_toolbar.addAction(self.action_launch_logger)
        self.editor_toolbar.addAction(self.action_settings)
        self.editor_toolbar.addSeparator()
        for action in (
            self.action_workspace_studio,
            self.action_workspace_compare,
            self.action_workspace_focus,
        ):
            self.editor_toolbar.addAction(action)
        self.editor_toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        for action in (
            self.action_workspace_studio,
            self.action_workspace_compare,
            self.action_workspace_focus,
        ):
            button = self.editor_toolbar.widgetForAction(action)
            if hasattr(button, "setToolButtonStyle"):
                button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        compare_button = self.editor_toolbar.widgetForAction(self.action_workspace_compare)
        self.toolbar_compare_menu = QMenu(compare_button)
        self.toolbar_compare_menu.aboutToShow.connect(
            lambda: self._refresh_compare_with_menu(self.toolbar_compare_menu)
        )
        if isinstance(compare_button, QToolButton):
            compare_button.setMenu(self.toolbar_compare_menu)
            compare_button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
            compare_button.setToolTip(
                "Compare recent windows; use the arrow to choose which window to compare"
            )
        self.editor_toolbar.setIconSize(QSize(20, 20))
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, self.editor_toolbar)

        from ecueditor.ui.editor.table_toolbar import TableToolBar
        self.addToolBarBreak(Qt.ToolBarArea.TopToolBarArea)
        self.table_toolbar = TableToolBar(self)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, self.table_toolbar)
        self.table_toolbar.bind(None)
        self.documents.activeDocumentChanged.connect(self._on_active_document_changed)

    def _build_dock(self) -> None:
        from ecueditor.ui.editor.rom_tree import RomTreePanel
        self.rom_dock = QDockWidget("ROMs", self)
        self.rom_dock.setObjectName("rom_dock")
        self.rom_dock.setMinimumWidth(300)
        self.rom_dock.setMaximumWidth(480)
        self.rom_tree = RomTreePanel()
        self.rom_dock.setWidget(self.rom_tree)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.rom_dock)
        self.rom_tree.files_dropped.connect(self.open_files)
        self.action_open.triggered.connect(self._on_open_action)
        self.rom_tree.rom_selected.connect(self._on_rom_selected)
        self.rom_tree.table_activated.connect(self._on_table_activated)
        self.action_compare_images.triggered.connect(self._open_compare_images)
        self.rom_tree.rom_opened.connect(lambda rom: self._update_status_chips())

        from ecueditor.ui.editor.inspector import CellInspectorPanel
        self.inspector_dock = QDockWidget("Cell Inspector", self)
        self.inspector_dock.setObjectName("inspector_dock")
        self.inspector_dock.setMinimumWidth(260)
        self.inspector_dock.setMaximumWidth(360)
        self.inspector = CellInspectorPanel()
        self.inspector_dock.setWidget(self.inspector)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.inspector_dock)

    def _build_statusbar(self) -> None:
        from ecueditor.ui.workspace.status_chips import ChecksumChips, Chip
        self.checksum_chips = ChecksumChips()
        self.xmlid_chip = Chip("", "accent"); self.xmlid_chip.hide()
        self.level_chip = Chip("", "neutral")
        self.workspace_chip = Chip(self.documents.workspace_mode().title(), "neutral")
        self.statusBar().addPermanentWidget(self.workspace_chip)
        self.statusBar().addPermanentWidget(self.checksum_chips)
        self.statusBar().addPermanentWidget(self.xmlid_chip)
        self.statusBar().addPermanentWidget(self.level_chip)
        self._update_status_chips()
        failures = self._services.plugin_failures
        if failures:
            noun = "plugin" if len(failures) == 1 else "plugins"
            self.statusBar().showMessage(
                f"{len(failures)} {noun} failed to load; hover for details"
            )
            self.statusBar().setToolTip("Plugin load failures:\n" + "\n".join(failures))

    # --- state ---------------------------------------------------------------
    def _update_rom_actions(self, active: bool) -> None:
        for a in (self.action_save, self.action_save_as, self.action_close, self.action_refresh):
            a.setEnabled(active)

    # --- save / checksum status -----------------------------------------------
    def _active_rom(self):
        roms = self.rom_tree.roms()
        if self._selected_rom is not None and any(
                candidate is self._selected_rom for candidate in roms):
            return self._selected_rom
        doc = self.documents.active_document()
        if doc is not None and hasattr(doc, "rom"):
            return doc.rom
        # No active frame: only unambiguous when exactly one ROM is open. With 2+ ROMs open and
        # nothing focused, guessing (e.g. tree order) risks saving/closing/refreshing the wrong one.
        return roms[0] if len(roms) == 1 else None

    def _on_rom_selected(self, rom) -> None:
        """Make a tree click the target for ROM-wide commands such as Close ROM."""
        self._selected_rom = rom
        if self._logger_window is not None:
            self._logger_window.set_active_rom(rom)
        self._update_status_chips(rom)
        self._update_window_title()

    def _update_status_chips(self, rom=None) -> None:
        # rom=None means "look it up" (rom_opened/save/close/refresh call sites); an explicit rom
        # (e.g. from _on_active_document_changed) tracks the just-activated document directly, since
        # DocumentArea.active_document() only reflects a real activation, not a doc passed straight
        # into the handler (see _on_active_document_changed below -- H11).
        if rom is None:
            rom = self._active_rom()
        self.checksum_chips.set_report(rom.checksum_report() if rom is not None else None)
        if rom is not None:
            xmlids = " / ".join(dict.fromkeys(
                section.definition.romid.xmlid for section in rom.sections
            ))
            self.xmlid_chip.setText(xmlids); self.xmlid_chip.show()
        else:
            self.xmlid_chip.hide()
        level = getattr(self._services.settings, "user_level", 5) if self._services.settings else 5
        self.level_chip.setText(f"Level ≤{level}")

    def _update_window_title(self) -> None:
        from pathlib import Path
        rom = self._active_rom()
        if rom is None:
            self.setWindowTitle(PRODUCT_NAME); return
        label = Path(rom.path).name if rom.path else rom.definition.romid.xmlid
        dirty = " ●" if rom.is_dirty() else ""
        self.setWindowTitle(f"{PRODUCT_NAME} — {label}{dirty}")

    def _refresh_rom_views(self, rom: RomImage, *, after_reload: bool = False) -> None:
        # Repaint every open grid of the saved ROM: save()'s flush() re-syncs aliased tables and
        # moves the revert point, so stale pre-resync values / change borders must be cleared.
        for doc in self.documents.documents():
            if getattr(doc, "rom", None) is not rom:
                continue
            grid = getattr(doc, "grid", None)
            if grid is not None:
                if after_reload:
                    grid.model().clear_undo_history()
                    grid.clear_last_paste()
                    grid.set_live_value(None)
                grid.model().beginResetModel()
                grid.model().endResetModel()
            elif hasattr(doc, "refresh"):
                doc.refresh()                    # Surface3DView: re-read the table after save (Task 14)
            body = getattr(doc, "body", None)
            resync = getattr(body, "resync_from_table", None) if body is not None else None
            if resync is not None:
                resync()
            table = getattr(doc, "table", None)
            if table is not None:
                # save() moved the revert point -- clear the tab dirty-dot to match (H7)...
                self.documents.set_document_dirty(doc, table.is_changed())
                # ...and the tree leaf's dirty dot to match (Task 15).
                self.rom_tree.set_dirty(rom, table, table.is_changed())
            if after_reload:
                handle_reload = getattr(doc, "handle_rom_reloaded", None)
                if callable(handle_reload):
                    handle_reload()
        if after_reload:
            # Comparisons can target a table in another ROM. Reloading that reference
            # bypasses the normal edit signal, so invalidate every dependent open model.
            changed_tables = frozenset(rom.tables.values())
            for doc in self.documents.documents():
                grid = getattr(doc, "grid", None)
                if grid is not None:
                    grid.model().refresh_compare_reference(changed_tables)

    def _retitle_rom_documents(self, rom: RomImage) -> None:
        """Keep every open document title in sync after a successful Save As."""
        label = Path(rom.path).name if rom.path else rom.definition.romid.xmlid
        for doc in self.documents.documents():
            if getattr(doc, "rom", None) is not rom:
                continue
            table = getattr(doc, "table", None)
            if table is None:
                continue
            window = self.documents.window_for_document(doc)
            kind = str(window.property("documentKind")) if window is not None else ""
            qualifier = {
                "mapstudio": " (Map Studio)",
                "surface": " (3D)",
            }.get(kind, "")
            display_name = self._table_display_name(rom, table)
            self.documents.set_document_title(doc, f"{display_name}{qualifier} — {label}")

    def save_active_rom(self, *, save_as: bool) -> None:
        from PySide6.QtWidgets import QFileDialog, QMessageBox
        from pathlib import Path
        from ecueditor.ui.editor.save_controller import save_rom
        rom = self._active_rom()
        if rom is None:
            self.statusBar().showMessage("Select a ROM window to save", 5000)
            return
        target = rom.path
        if save_as or target is None:
            fn, _ = QFileDialog.getSaveFileName(self, "Save ROM As", str(target or ""),
                                                "ROM images (*.bin *.hex)")
            if not fn:
                return
            target = Path(fn)
            if target.exists() and target != rom.path:
                if QMessageBox.question(self, "Overwrite?", f"Overwrite {target.name}?") \
                        != QMessageBox.StandardButton.Yes:
                    return
        try:
            save_rom(rom, target)
        except Exception as exc:      # OSError (PermissionError etc.) + any other save-path failure
            QMessageBox.critical(self, "Save failed", f"{target}:\n{exc}")
            return
        self._refresh_rom_views(rom)
        self._retitle_rom_documents(rom)
        self.rom_tree.refresh_rom_status(rom)     # re-read checksum_report() -- ✗ badge post-save
        self.rom_tree._refresh_rom_label(rom)
        self._update_status_chips()
        self._update_window_title()
        self.statusBar().showMessage(f"Saved {target}", 5000)

    def _close_active_rom(self) -> None:
        from PySide6.QtWidgets import QMessageBox
        from pathlib import Path
        rom = self._active_rom()
        if rom is None:
            self.statusBar().showMessage("Select a ROM window to close", 5000)
            return
        if rom.is_dirty():
            label = Path(rom.path).name if rom.path else rom.definition.romid.xmlid
            if QMessageBox.question(self, "Close ROM", f"Discard unsaved changes to {label}?") \
                    != QMessageBox.StandardButton.Yes:
                return
        rom_documents = [
            d for d in self.documents.documents() if getattr(d, "rom", None) is rom
        ]
        if any(not self.documents.can_close_document(doc) for doc in rom_documents):
            return
        for doc in rom_documents:
            self.documents.close_document(doc, force=True)
        if hasattr(self, "_open_frames"):
            for key in [k for k in self._open_frames if k[0] == id(rom)]:
                del self._open_frames[key]
        self.rom_tree.remove_rom(rom)
        if self._selected_rom is rom:
            self._selected_rom = None
        if not self.rom_tree._roms:
            self._update_rom_actions(active=False)
        self._update_status_chips()
        self._update_window_title()

    def _on_document_closed(self, document) -> None:
        """Release shell and logger references as soon as an MDI child closes."""
        teardown = getattr(document, "teardown", None)
        if callable(teardown):
            teardown()
        else:
            unbind = getattr(document, "unbind_source_grid", None)
            if callable(unbind):
                unbind()
        grid = getattr(document, "grid", None)
        if self._logger_window is not None and grid is not None:
            self._logger_window.unregister_editor_table(grid)
        if grid is not None:
            for other in self.documents.documents():
                other_unbind = getattr(other, "unbind_source_grid", None)
                if callable(other_unbind):
                    other_unbind(grid)
        if hasattr(self, "_open_frames"):
            for key, value in list(self._open_frames.items()):
                if value is document:
                    del self._open_frames[key]

    def _refresh_active_rom(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        rom = self._active_rom()
        if rom is None:
            self.statusBar().showMessage("Select a ROM window to reload", 5000)
            return
        if rom.path is None:
            QMessageBox.critical(
                self,
                "Reload failed",
                "This ROM has no source file. Save it before reloading from disk.",
            )
            return

        path = Path(rom.path)
        label = path.name
        if not path.is_file():
            QMessageBox.critical(
                self,
                "Reload failed",
                f"The source file no longer exists:\n{path}\n\n"
                "The open ROM and any unsaved changes were left untouched.",
            )
            return
        if rom.is_dirty():
            answer = QMessageBox.question(
                self,
                "Reload ROM from disk?",
                f"Reload {label} from disk and discard all unsaved changes? "
                "This cannot be undone.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        try:
            rom.reload_from_disk()
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Reload failed",
                f"Could not reload {path}:\n{exc}\n\n"
                "The open ROM and any unsaved changes were left untouched.",
            )
            return

        self._reloading_rom = True
        try:
            self._refresh_rom_views(rom, after_reload=True)
        finally:
            self._reloading_rom = False
        self.rom_tree.clear_dirty(rom)
        self.rom_tree.refresh_rom_status(rom)
        self.rom_tree._refresh_rom_label(rom)
        self._update_status_chips(rom)
        self._update_window_title()
        self.statusBar().showMessage(f"Reloaded {label} from disk", 5000)

    def _on_open_action(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        files, _ = QFileDialog.getOpenFileNames(self, "Open ROM(s)", "",
                                                "ROM images (*.bin *.hex);;All files (*)")
        self.open_files([__import__("pathlib").Path(f) for f in files])

    def open_files(self, paths) -> None:
        from ecueditor.core.rom.image import RomImage
        from ecueditor.core.errors import NoMatchingRomError
        from PySide6.QtWidgets import QMessageBox
        for p in paths:
            rom = None
            try:
                try:
                    rom = RomImage.open(
                        p,
                        self._services.library,
                        settings=self._services.settings,
                    )
                except NoMatchingRomError:
                    rom = self._force_load(p)      # spec §5.1: offer manual definition pick
                if rom is None:
                    continue
                self.rom_tree.add_rom(rom)
                self._update_rom_actions(active=True)
            except Exception as exc:
                if rom is not None and any(
                    candidate is rom for candidate in self.rom_tree.roms()
                ):
                    try:
                        self.rom_tree.remove_rom(rom)
                    except Exception:
                        # Preserve per-file isolation even if a failing add also leaves the
                        # tree unable to rebuild while rolling its partial registration back.
                        self.rom_tree._roms = [
                            candidate
                            for candidate in self.rom_tree._roms
                            if candidate is not rom
                        ]
                    self._update_rom_actions(active=bool(self.rom_tree.roms()))
                QMessageBox.critical(
                    self,
                    "Open ROM failed",
                    f"Could not open {p}:\n{exc}",
                )
                continue

    def _candidate_xmlids(self) -> list[str]:
        from ecueditor.core.defs.parser import parse_definition_file
        out: list[str] = []
        for path in self._services.definition_paths:
            try:
                doc = parse_definition_file(path)
            except Exception:
                continue
            out += [rid.xmlid for rid in doc.rom_ids if rid.xmlid]
        return sorted(dict.fromkeys(out))          # de-dupe, stable order

    def _force_load(self, path):
        from ecueditor.core.rom.image import RomImage
        from ecueditor.ui.dialogs.force_load_dialog import ForceLoadDialog
        from PySide6.QtWidgets import QMessageBox
        xmlids = self._candidate_xmlids()
        if not xmlids:
            QMessageBox.warning(self, "No definition found",
                                f"No ECU definition matches:\n{path}")
            return None
        dlg = ForceLoadDialog(xmlids, self)
        if dlg.exec() and dlg.selected_xmlid():
            return RomImage.force_open(
                path,
                self._services.library,
                dlg.selected_xmlid(),
                settings=self._services.settings,
            )
        return None

    # --- table sub-windows -----------------------------------------------------
    def _on_table_activated(self, rom, table) -> None:
        self.open_table(rom, table)

    def open_table(self, rom, table_or_name) -> None:
        from ecueditor.ui.editor.table_frame import TableDocument
        from ecueditor.ui.editor.rom_tree import icon_name_for_table
        from ecueditor.ui.design.icons import icon
        from pathlib import Path
        table = rom.table(table_or_name) if isinstance(table_or_name, str) else table_or_name
        section, name = rom.table_key(table)
        key = (id(rom), section, name)
        existing = self._open_frames.get(key)
        if existing is not None and existing in self.documents.documents():
            self.documents.set_active_document(existing); return
        label = Path(rom.path).name if rom.path else rom.definition.romid.xmlid
        display_name = self._table_display_name(rom, table)
        doc = TableDocument(rom, table, f"{label}: {display_name}",
                            roms_provider=lambda: list(self.rom_tree._roms))
        # Decorate the document BEFORE add_document makes it active and emits the activation
        # signal.  Doing this afterward left the shared 3D action disabled until a second table
        # activation happened to rebind the toolbar.
        from ecueditor.core.rom.table import Table3D
        if isinstance(doc.table, Table3D):
            setattr(doc, "_open_3d", lambda _checked=False, d=doc: self._open_3d_view(d))
        menubar = getattr(doc, "menubar", None)
        if menubar is not None and hasattr(menubar, "mapStudioRequested"):
            menubar.mapStudioRequested.connect(lambda d=doc: self._open_map_studio(d))
        grid = getattr(doc, "grid", None)
        # Finalize font, row, and column metrics before DocumentArea asks for sizeHint().
        # Applying these after add_document made the MDI window fit the constructor's 42 px
        # columns, then widened the real grid inside it and clipped the far-right cells.
        if self._services.settings is not None:
            if grid is not None:
                _apply_settings_to_grid(grid, self._services.settings)
        # Include the ROM so identically named tables from two images remain unambiguous when
        # the internal windows are tiled or cascaded.
        tab_icon = icon(icon_name_for_table(table.definition))
        workspace_kind = "utility" if doc.grid is None or doc.table.shape() == (1, 1) else "grid"
        self.documents.add_document(
            doc, f"{display_name} — {label}", icon=tab_icon, workspace_kind=workspace_kind
        )
        self._open_frames[key] = doc
        if section == rom.sections[0].key:
            # Keep the original private registry key for single/default-section callers and
            # tests. Multi-section-safe routing always uses the canonical key above.
            self._open_frames[(id(rom), name)] = doc

        frame = getattr(doc, "frame", None)
        if frame is not None and getattr(frame, "legend", None) is not None:
            frame.legend.colormapChangeRequested.connect(self._on_colormap_changed)

        if self._logger_window is not None:
            grid = getattr(doc, "grid", None)
            if grid is not None:
                self._logger_window.register_editor_table(grid)   # live overlay (Phase 6b)

        grid = getattr(doc, "grid", None)
        if grid is not None:
            model = grid.model()
            edit_committed = getattr(model, "editCommitted", None)
            if edit_committed is not None:
                edit_committed.connect(lambda d=doc: self._on_document_edited(d))
            else:
                # Compatibility for models predating semantic edit commits. Paint-only role
                # notifications must never traverse storage aliases or rebuild a 3D surface.
                model.dataChanged.connect(
                    lambda _top, _bottom, roles, d=doc:
                    self._on_grid_data_changed(d, roles)
                )
                model.headerDataChanged.connect(
                    lambda *_a, d=doc: self._on_document_edited(d)
                )
                model.modelReset.connect(lambda d=doc: self._on_document_edited(d))
        else:
            body = getattr(doc, "body", None)
            if body is not None and hasattr(body, "edited"):
                body.edited.connect(lambda d=doc: self._on_document_edited(d))
        self._rebind_open_3d_view(doc)

    @staticmethod
    def _table_display_name(rom, table) -> str:
        if len(rom.sections) == 1:
            return table.name
        section_key, _name = rom.table_key(table)
        section = next(item for item in rom.sections if item.key == section_key)
        return f"{table.name} [{section.label}]"

    @staticmethod
    def _table_frame_key(rom, table, *qualifier) -> tuple[object, ...]:
        section, name = rom.table_key(table)
        return (id(rom), section, name, *qualifier)

    def _on_grid_data_changed(self, doc, roles=()) -> None:
        """Legacy edit routing that rejects palette and other paint-only changes."""
        if roles and not any(
            role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole)
            for role in roles
        ):
            return
        self._on_document_edited(doc)

    def _open_map_studio(self, doc) -> None:
        """Open one native Map Studio document for the source table."""
        key = self._table_frame_key(doc.rom, doc.table, "mapstudio")
        existing = self._open_frames.get(key)
        if existing is not None and existing in self.documents.documents():
            self.documents.set_active_document(existing)
            return

        from ecueditor.ui.design.icons import icon
        from ecueditor.ui.mapstudio.document import MapStudioDocument

        selection = [doc.grid.model().cell_xy(index) for index in doc.grid.selectedIndexes()]
        studio = MapStudioDocument(
            doc.rom,
            doc.table,
            initial_selection=selection,
            display_settings=self._services.settings,
        )
        studio.applyRequested.connect(
            lambda proposal, target=studio: self._apply_map_studio(target, proposal)
        )
        label = Path(doc.rom.path).name if doc.rom.path else doc.rom.definition.romid.xmlid
        display_name = self._table_display_name(doc.rom, doc.table)
        self.documents.add_document(
            studio,
            f"{display_name} (Map Studio) — {label}",
            icon=icon("interpolate"),
            workspace_kind="mapstudio",
        )
        self._open_frames[key] = studio

    def _apply_map_studio(self, studio, proposal) -> None:
        """Commit a Studio proposal through the opening table's normal edit model."""
        key = self._table_frame_key(studio.rom, studio.table)
        source = self._open_frames.get(key)
        if source is None or source not in self.documents.documents():
            self.open_table(studio.rom, studio.table)
            source = self._open_frames.get(key)
        if source is None or source.grid is None:
            return

        model = source.grid.model()
        changed = model.apply_quantized(proposal)
        studio.accept_applied(changed=changed)
        if changed and getattr(model, "editCommitted", None) is None:
            self._on_document_edited(source)
        self.documents.set_active_document(studio)

    def _open_3d_view(self, doc) -> None:
        key = self._table_frame_key(doc.rom, doc.table, "3d")
        existing = self._open_frames.get(key)
        if existing is not None and existing in self.documents.documents():
            existing.bind_source_grid(doc.grid)
            self.documents.set_active_document(existing); return
        try:
            from ecueditor.ui.editor.surface3d import Surface3DView
            colormap = getattr(self._services.settings, "colormap", "rainbow") \
                if self._services.settings else "rainbow"
            view = Surface3DView(doc.table, colormap=colormap)
        except Exception as exc:                  # Matplotlib/Qt canvas unavailable
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(self, "3D View", f"3D view unavailable: {exc}")
            return
        setattr(view, "rom", doc.rom)
        setattr(view, "table", doc.table)
        from ecueditor.ui.design.icons import icon
        from pathlib import Path
        label = Path(doc.rom.path).name if doc.rom.path else doc.rom.definition.romid.xmlid
        display_name = self._table_display_name(doc.rom, doc.table)
        self.documents.add_document(
            view, f"{display_name} (3D) — {label}", icon=icon("cube"),
            workspace_kind="surface",
        )
        self._open_frames[key] = view
        view.bind_source_grid(doc.grid)

    def _rebind_open_3d_view(self, doc) -> None:
        """Reconnect a surviving 3D document when its table grid is reopened."""
        if not hasattr(self, "_open_frames"):
            return
        key = self._table_frame_key(doc.rom, doc.table, "3d")
        view = self._open_frames.get(key)
        if view is not None and view in self.documents.documents():
            view.bind_source_grid(doc.grid)

    def _on_document_edited(self, doc) -> None:
        if self._reloading_rom:
            return
        # RomRaider semantics: a storage edit updates every table/axis alias in the ROM. The
        # core has already synchronized their DataCells; repaint any other open views that
        # overlap this table's data or axes. Guard the signals emitted by refresh_from_table()
        # so they update dirty state without recursively walking the same alias set.
        aliases = doc.rom.storage_aliases(doc.table)
        if not getattr(self, "_refreshing_storage_aliases", False):
            self._refreshing_storage_aliases = True
            try:
                for other in self.documents.documents():
                    if other is doc or getattr(other, "rom", None) is not doc.rom:
                        continue
                    other_table = getattr(other, "table", None)
                    if other_table is None or other_table not in aliases:
                        continue
                    grid = getattr(other, "grid", None)
                    if grid is not None:
                        # This model did not author the edit. Its incremental history may
                        # contain older values for the same physical bytes and must not be able
                        # to overwrite the newer alias edit on a later Undo.
                        grid.model().clear_undo_history()
                        grid.model().refresh_from_table()
                    elif hasattr(other, "queue_refresh"):
                        other.queue_refresh()
                    elif hasattr(other, "refresh"):
                        other.refresh()
                    elif hasattr(other, "refresh_stale_state"):
                        other.refresh_stale_state()
                    body = getattr(other, "body", None)
                    resync = getattr(body, "resync_from_table", None) if body is not None else None
                    if resync is not None:
                        resync()
                    other_rom = getattr(other, "rom", None)
                    if other_rom is None:
                        continue
                    primary_key = self._table_frame_key(other_rom, other_table)
                    if self._open_frames.get(primary_key) is other:
                        self.documents.set_document_dirty(other, other_table.is_changed())
            finally:
                self._refreshing_storage_aliases = False
        # A comparison target may live in another ROM and therefore is not a storage alias.
        # Its color normalization still depends on this reference table's current values.
        for other in self.documents.documents():
            grid = getattr(other, "grid", None)
            if grid is not None:
                grid.model().refresh_compare_reference(aliases)
        for table in aliases:
            self.rom_tree.set_dirty(doc.rom, table, table.is_changed())
        self.documents.set_document_dirty(doc, doc.table.is_changed())
        self.rom_tree.set_dirty(doc.rom, doc.table, doc.table.is_changed())
        self._update_window_title()

    def _on_active_document_changed(self, doc) -> None:
        if doc is not None and hasattr(doc, "rom"):
            self._selected_rom = doc.rom
        grid = getattr(doc, "grid", None) if doc is not None else None
        if grid is not None:
            self.table_toolbar.bind(grid)
        else:
            self.table_toolbar.bind(None)
        # H8: track the connection instead of a blanket disconnect() -- a bare
        # triggered.disconnect() raises/warns (libpyside RuntimeWarning) once there is nothing
        # connected, which happened whenever two non-3D frames activated back to back.
        if self._enable3d_connection is not None:
            self.table_toolbar.action_enable3d.triggered.disconnect(self._enable3d_connection)
            self._enable3d_connection = None
        if doc is not None and hasattr(doc, "_open_3d"):
            self._enable3d_connection = self.table_toolbar.action_enable3d.triggered.connect(
                doc._open_3d)
            self.table_toolbar.action_enable3d.setEnabled(True)
        else:
            self.table_toolbar.action_enable3d.setEnabled(False)
        if self._logger_window is not None:
            self._logger_window.set_active_rom(self._active_rom())   # (Phase 6b)
        # H11: chips follow activation -- pass doc.rom directly (see _update_status_chips docstring).
        if doc is not None and hasattr(doc, "rom"):
            self._update_status_chips(doc.rom)
        else:
            self._update_status_chips()
        self._update_window_title()
        # inspector follows the active grid's current cell (tracked rebind, offscreen-safe --
        # same H8 pattern as _enable3d_connection above: a bare disconnect() raises/warns once
        # nothing is connected).
        if self._inspector_connection is not None:
            sel, conn = self._inspector_connection
            sel.currentChanged.disconnect(conn)
            self._inspector_connection = None
        self.inspector.set_document(doc)
        if grid is not None:
            sel = grid.selectionModel()
            conn = lambda cur, _prev: self.inspector.show_index(cur)   # noqa: E731
            sel.currentChanged.connect(conn)
            self._inspector_connection = (sel, conn)
            self.inspector.show_index(sel.currentIndex())

    def _open_compare_images(self) -> None:
        from ecueditor.ui.editor.compare_images_dialog import CompareImagesDialog
        roms = list(self.rom_tree._roms)
        if len(roms) < 2:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(self, "Compare Images", "Open at least two ROMs to compare.")
            return
        CompareImagesDialog(roms, self).exec()

    def _open_command_palette(self) -> None:
        from pathlib import Path
        from ecueditor.ui.workspace.command_palette import CommandPalette, PaletteEntry
        level = getattr(self._services.settings, "user_level", 5) if self._services.settings else 5
        entries = []
        for rom in self.rom_tree.roms():
            label = Path(rom.path).name if rom.path else rom.definition.romid.xmlid
            for section in rom.sections:
                for name, tdef in rom.section_definitions(section.key).items():
                    if getattr(tdef, "user_level", 1) > level:
                        continue
                    table = rom.table(name, section=section.key)
                    section_text = f" · {section.label}" if len(rom.sections) > 1 else ""
                    entries.append(PaletteEntry(
                        rom=rom,
                        name=name,
                        category=tdef.category or "",
                        description=tdef.description or "",
                        label=(
                            f"{name} — {tdef.category or '?'}{section_text} · {label}"
                        ),
                        table=table,
                    ))
        pal = CommandPalette(entries, on_open=lambda rom, table: self.open_table(rom, table),
                             parent=self)
        pal.move(self.mapToGlobal(self.rect().center()) - pal.rect().center())
        pal.exec()

    # --- logger composition (Phase 6b; INTERFACES ui/ contracts) ------------------------------
    def _open_logger(self) -> None:
        if self._logger_window is not None:
            win = self._logger_window
            if win.isVisible() or win.is_connected:
                win.show(); win.raise_(); win.activateWindow()
                return
            self._logger_window = None            # closed AND disconnected: rebuild cleanly
        resolved = self._resolve_logger_def_path()
        if resolved is None:
            return
        def_path, needs_persist = resolved
        from ecueditor.core.errors import ECUEditorError
        from ecueditor.core.loggerdef.parser import parse_logger_definition
        # Import shipped registrations explicitly, then let a logger definition
        # select any protocol made available by built-ins or drop-in plugins.
        from ecueditor.core.comms import protocol as _protocol_builtins  # noqa: F401
        from ecueditor.core.plugins.registry import PROTOCOLS
        try:
            definition = parse_logger_definition(
                def_path,
                supported_protocol_ids=PROTOCOLS.keys(),
            )
        except ECUEditorError as exc:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Logger", f"Could not parse logger definition:\n{exc}")
            return
        if needs_persist:
            s = self._services.settings
            if s is not None:
                s.logger_definition_path = str(def_path)          # persist only a VALIDATED pick
                from ecueditor.core.settings import save_settings
                save_settings(s)
        from ecueditor.ui.logger.window import launch_logger_window
        win = launch_logger_window(
            definition,
            controller_factory=self._make_logger_controller_factory(definition),
            profiles=self._load_dyno_profiles(def_path),
            settings=self._services.settings,
            parent=self)
        self._logger_window = win
        window_id = id(win)
        win.destroyed.connect(
            lambda _obj=None, token=window_id: self._clear_logger_window(token)
        )
        win.set_active_rom(self._active_rom())
        for doc in self.documents.documents():                   # tables opened BEFORE launch
            grid = getattr(doc, "grid", None)
            if grid is not None:
                win.register_editor_table(grid)

    def _clear_logger_window(self, candidate) -> None:
        """Forget a logger only when the destroyed signal belongs to the current instance."""
        current = self._logger_window
        if current is None:
            return
        if candidate is current or candidate == id(current):
            self._logger_window = None

    def _resolve_logger_def_path(self):
        """(path, needs_persist) -- needs_persist is True only for a fresh QFileDialog pick, so
        _open_logger persists it (after parse_logger_definition validates it) instead of on every
        launch that reuses the already-configured path."""
        from pathlib import Path
        s = self._services.settings
        configured = getattr(s, "logger_definition_path", "") if s is not None else ""
        if configured and Path(configured).is_file():
            return Path(configured), False
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(self, "Select logger definition", "",
                                              "Logger definitions (*.xml);;All files (*)")
        if not path:
            return None
        return Path(path), True

    def _load_dyno_profiles(self, def_path):
        from pathlib import Path
        from ecueditor.core.dyno.profile import load_car_profiles
        from ecueditor.core.errors import ECUEditorError
        s = self._services.settings
        configured = getattr(s, "cars_def_path", "") if s is not None else ""
        candidates = ([Path(configured)] if configured
                      else sorted(Path(def_path).parent.glob("*cars_def.xml")))
        for p in candidates:                       # RomRaider search-path analogue (fact base §4.4)
            if p.is_file():
                try:
                    return load_car_profiles(p)
                except ECUEditorError:
                    continue                       # malformed candidate: keep looking
        return []                                  # DynoTab shows the missing-cars_def affordance

    def _make_logger_controller_factory(self, definition):
        def factory(port: str):
            # Composition per INTERFACES ui/: transport + protocol from the registries. open()/
            # init() run blocking on the connect click; async connect is part of the PARKED
            # pre-hardware bundle (docs/backlog.md "Phase 3 exit"). LoggerWindow.connect_clicked
            # catches ECUEditorError raised from here.
            from ecueditor.core.comms.connection import ConnectionManager
            from ecueditor.core.comms.protocol.base import create_registered_protocol
            from ecueditor.core.comms.transport.base import open_best_transport
            from ecueditor.core.errors import CommsError
            from ecueditor.core.logger.engine import LoggerEngine
            from ecueditor.ui.logger.controller import LoggerController
            protocol = create_registered_protocol(definition.protocol_id)
            conn = ConnectionManager(
                open_best_transport(port),
                protocol,
                module_address=definition.module_address,
            )

            def close_after_failure() -> None:
                try:
                    conn.close()
                except (Exception, SystemExit) as close_exc:
                    _log.warning(
                        "logger connection cleanup failed: %s",
                        close_exc,
                        exc_info=close_exc,
                    )

            try:
                conn.open(port)
                conn.init()
            except KeyboardInterrupt:
                close_after_failure()
                raise
            except (Exception, SystemExit) as exc:
                close_after_failure()
                if isinstance(exc, CommsError):
                    raise
                detail = str(exc) or type(exc).__name__
                raise CommsError(
                    f"logger connection setup failed on {port!r}: {detail}"
                ) from exc
            return LoggerController(LoggerEngine(conn, definition))
        return factory

    def _open_settings(self) -> None:
        from ecueditor.ui.dialogs.settings_dialog import SettingsDialog
        from ecueditor.core.settings import EditorSettings
        current = self._services.settings or EditorSettings()
        dlg = SettingsDialog(current, self)
        dlg.settings_changed.connect(self._apply_settings)
        dlg.exec()

    def _open_definition_manager(self) -> None:
        from pathlib import Path
        from ecueditor.ui.dialogs.definition_manager import DefinitionManagerDialog
        s = self._services.settings
        dlg = DefinitionManagerDialog(list(getattr(s, "definition_paths", []) or
                                           [str(p) for p in self._services.definition_paths]),
                                      self)
        def _apply(library):
            self._services.library = library
            self._services.definition_paths = [Path(p) for p in dlg.paths()]
            if s is not None:
                s.definition_paths = dlg.paths()
                from ecueditor.core.settings import save_settings
                save_settings(s)
            self.statusBar().showMessage("Definition library updated (applies to newly "
                                         "opened ROMs)", 6000)
        dlg.applied.connect(_apply)
        dlg.exec()

    def _apply_settings(self, settings) -> None:
        self._services.settings = settings
        from PySide6.QtWidgets import QApplication
        from ecueditor.ui.theme import apply_theme
        application = QApplication.instance()
        assert isinstance(application, QApplication)
        apply_theme(application, settings.theme)      # live re-theme (H6)
        for doc in self.documents.documents():
            if hasattr(doc, "apply_display_settings"):
                doc.apply_display_settings(settings)
            grid = getattr(doc, "grid", None)
            if grid is not None:
                _apply_settings_to_grid(grid, settings)
            elif hasattr(doc, "set_colormap") and not hasattr(doc, "apply_display_settings"):
                doc.set_colormap(getattr(settings, "colormap", "rainbow"))
            if hasattr(doc, "refresh_theme"):
                doc.refresh_theme()
        self.documents.fit_studio_windows_to_content()

    def _on_colormap_changed(self, name: str) -> None:
        """Legend colormap menu -> persist to settings + re-project every open grid (keeps the
        Settings dialog and the legend button in sync)."""
        s = self._services.settings
        if s is not None:
            s.colormap = name
            from ecueditor.core.settings import save_settings
            save_settings(s)
        for doc in self.documents.documents():
            grid = getattr(doc, "grid", None)
            if grid is not None:
                grid.model().set_colormap(name)
            elif hasattr(doc, "set_colormap"):
                doc.set_colormap(name)

    # --- View / Help menu slots (Task 19) ---------------------------------------------------
    def _set_user_level(self, level: int) -> None:
        if self._services.settings is not None:
            self._services.settings.user_level = level
            from ecueditor.core.settings import save_settings
            save_settings(self._services.settings)
        self.rom_tree.set_user_level_filter(level)
        self._update_status_chips()

    def _switch_theme(self, value: str) -> None:
        from PySide6.QtWidgets import QApplication
        from ecueditor.ui.theme import apply_theme
        if self._services.settings is not None:
            self._services.settings.theme = value
            from ecueditor.core.settings import save_settings
            save_settings(self._services.settings)
        application = QApplication.instance()
        assert isinstance(application, QApplication)
        apply_theme(application, value)      # same re-theme path as the Settings dialog (H6)
        for doc in self.documents.documents():
            if hasattr(doc, "refresh_theme"):
                doc.refresh_theme()

    def _open_rom_properties(self) -> None:
        rom = self._active_rom()
        if rom is None:
            self.statusBar().showMessage("Select a ROM window first", 5000)
            return
        from ecueditor.ui.dialogs.rom_properties import RomPropertiesDialog
        RomPropertiesDialog(rom, self).exec()

    def _show_about(self) -> None:
        from PySide6.QtWidgets import QMessageBox
        from ecueditor import __version__
        QMessageBox.about(self, f"About {PRODUCT_NAME}",
            f"<b>{PRODUCT_NAME} {display_version(__version__)}</b><br>"
            f"{PRODUCT_TAGLINE}<br><br>"
            "A modular, RomRaider-compatible ROM calibration editor, live data logger, "
            "and virtual dyno.<br><br>"
            "Copyright © 2026 CAATZ and contributors.<br>"
            "Licensed under GPL-2.0-or-later.<br><br>"
            "Complete license notices are installed beside the application.<br>"
            "Icons: Lucide (ISC). Numeric font: JetBrains Mono (OFL).")

    def _show_shortcuts(self) -> None:
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.information(self, "Keyboard Shortcuts",
            "Application\n"
            "  Ctrl+O            Open ROM…\n"
            "  Ctrl+S            Save\n"
            "  Ctrl+Shift+S      Save As…\n"
            "  Ctrl+W            Close ROM\n"
            "  F5                Reload ROM from Disk\n"
            "  Ctrl+L            Launch Logger…\n"
            "  Ctrl+K            Go to Table…\n"
            "\n"
            "Table editing (active frame)\n"
            "  Ctrl+Z            Undo Last Change\n"
            "  Ctrl+Shift+Z      Undo All\n"
            "  Ctrl+C            Copy Selection\n"
            "  Ctrl+Shift+C      Copy Table\n"
            "  Ctrl+V            Paste\n"
            "  Shift+I           Interpolate Selection\n"
            "  Ctrl+Shift+M      Open Map Studio\n"
            "  + / _             Increment / Decrement (coarse)\n"
            "  *                 Multiply")

    def _open_user_manual(self) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtWidgets import QMessageBox

        manual = _user_manual_path()
        if not manual.is_file():
            QMessageBox.warning(
                self,
                "User Manual",
                f"The user manual could not be found:\n{manual}",
            )
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(manual))):
            QMessageBox.warning(self, "User Manual", f"Could not open:\n{manual}")
