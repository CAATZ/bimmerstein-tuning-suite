from __future__ import annotations
from typing import Callable
from PySide6.QtWidgets import QWidget, QHBoxLayout, QToolButton, QLabel, QMenu
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtCore import Qt
from ecueditor.ui.editor import edit_ops, clipboard
from ecueditor.ui.editor.table_grid import TableGridWidget
from ecueditor.ui.design.icons import icon


class TableMenuBar(QWidget):
    """The frame's verb band: a horizontal toolbar of labeled chip buttons (mockup .tf-verbs),
    not a QMenuBar dropdown. Builds the same QActions/wiring/shortcut-scoping as before --
    only the presentation (chips instead of Edit/Compare menus) changed."""

    def __init__(self, grid: TableGridWidget, parent=None,
                 roms_provider: Callable[[], list] | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("frameVerbs")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._grid = grid
        self._roms_provider = roms_provider
        is3d = grid.model().table.shape()[1] > 1

        # --- actions: same names/shortcuts/wiring as the old QMenuBar build --------------
        self.action_undo_sel = QAction("Undo", self, shortcut=QKeySequence("Ctrl+Z"))
        self.action_undo_all = QAction("Undo All", self, shortcut=QKeySequence("Ctrl+Shift+Z"))
        self.action_revert = QAction("Set Revert Point", self)
        self.action_copy_sel = QAction("Copy Sel", self, shortcut=QKeySequence("Ctrl+C"))
        self.action_copy_table = QAction("Copy Table", self, shortcut=QKeySequence("Ctrl+Shift+C"))
        self.action_paste = QAction("Paste", self, shortcut=QKeySequence("Ctrl+V"))
        self.action_interpolate = QAction("Interp", self, shortcut=QKeySequence("Shift+I"))
        self.action_undo_sel.setToolTip("Undo Last Change (Ctrl+Z)")
        self.action_undo_all.setToolTip("Undo All Changes (Ctrl+Shift+Z)")
        self.action_revert.setToolTip("Set Revert Point")
        self.action_copy_sel.setToolTip("Copy Selection (Ctrl+C)")
        self.action_copy_table.setToolTip("Copy Table (Ctrl+Shift+C)")
        self.action_paste.setToolTip("Paste (Ctrl+V)")
        self.action_interpolate.setToolTip("Interpolate (Shift+I)")
        self.action_undo_sel.setIcon(icon("undo"))
        self.action_undo_all.setIcon(icon("undo-all"))
        self.action_revert.setIcon(icon("revert-flag"))
        self.action_copy_sel.setIcon(icon("copy"))
        self.action_copy_table.setIcon(icon("copy"))
        self.action_paste.setIcon(icon("paste"))
        self.action_interpolate.setIcon(icon("interpolate"))

        if is3d:
            self.action_h_interp = QAction("↔", self, shortcut=QKeySequence("Shift+H"))
            self.action_v_interp = QAction("↕", self, shortcut=QKeySequence("Shift+V"))
            self.action_h_interp.setToolTip("Horizontal Interpolate (Shift+H)")
            self.action_v_interp.setToolTip("Vertical Interpolate (Shift+V)")
            self.action_h_interp.setIcon(icon("interpolate"))
            self.action_v_interp.setIcon(icon("interpolate"))
            self.action_h_interp.triggered.connect(lambda: self._op(edit_ops.interpolate_horizontal))
            self.action_v_interp.triggered.connect(lambda: self._op(edit_ops.interpolate_vertical))

        self.action_undo_sel.triggered.connect(self._undo_last)
        self.action_undo_all.triggered.connect(self._undo_all)
        self.action_revert.triggered.connect(self._set_revert_point)
        self.action_copy_sel.triggered.connect(lambda: clipboard.copy_selection(self._grid.model(), self._sel()))
        self.action_copy_table.triggered.connect(lambda: clipboard.copy_table(self._grid.model()))
        self.action_paste.triggered.connect(self._paste)
        self.action_interpolate.triggered.connect(lambda: self._op(edit_ops.interpolate_2d))

        self.action_show_changes = QAction("Show Changes", self, checkable=True)
        self.action_compare_to = QAction("Compare To Table…", self)
        self.action_compare_to.setIcon(icon("compare"))
        self.action_percent = QAction("Percent", self, checkable=True)
        self.action_absolute = QAction("Absolute", self, checkable=True)
        self.action_compare_off = QAction("Compare Off", self)
        self._compare_mode_group = QActionGroup(self)
        self._compare_mode_group.setExclusive(True)
        self._compare_mode_group.addAction(self.action_percent)
        self._compare_mode_group.addAction(self.action_absolute)
        self.action_absolute.setChecked(True)     # matches TableGridModel's default compare_mode
        self.action_show_changes.toggled.connect(self._on_show_changes_toggled)
        self.action_compare_to.triggered.connect(self._on_compare_to)
        self.action_percent.triggered.connect(lambda: self._grid.model().set_compare_mode("percent"))
        self.action_absolute.triggered.connect(lambda: self._grid.model().set_compare_mode("absolute"))
        self.action_compare_off.triggered.connect(self._on_compare_off)

        for a in self.actions_list():
            self._grid.addAction(a)          # make shortcuts fire when the grid has focus
            # per-frame duplicate sequences are ambiguous under the default WindowShortcut when 2+ frames are open -- scope each to its focused frame
            a.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)

        # --- render: a horizontal band of labeled chip buttons (mockup .tf-verbs) --------
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(4)
        self._verb_buttons: list[QToolButton] = []

        def chip(action: QAction, *, text_only: bool = False) -> QToolButton:
            btn = QToolButton(self)
            btn.setDefaultAction(action)
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly if text_only
                                    else Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            lay.addWidget(btn)
            if not text_only:
                self._verb_buttons.append(btn)
            return btn

        def sep() -> None:
            s = QWidget(self)
            s.setObjectName("tfSep")
            s.setFixedSize(1, 16)
            lay.addWidget(s, 0, Qt.AlignmentFlag.AlignVCenter)

        chip(self.action_undo_sel)
        chip(self.action_undo_all)
        self._revert_button = chip(self.action_revert)
        sep()
        chip(self.action_copy_sel)
        chip(self.action_copy_table)
        chip(self.action_paste)
        sep()
        chip(self.action_interpolate)
        if is3d:
            chip(self.action_h_interp, text_only=True)
            chip(self.action_v_interp, text_only=True)
        sep()

        self._compare_btn = QToolButton(self)
        self._compare_btn.setText("Compare ▾")
        self._compare_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self._compare_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        compare_menu = QMenu(self._compare_btn)
        for a in (self.action_show_changes, self.action_compare_to, self.action_percent,
                  self.action_absolute, self.action_compare_off):
            compare_menu.addAction(a)
        self._compare_btn.setMenu(compare_menu)
        lay.addWidget(self._compare_btn)

        lay.addStretch(1)
        self._step_label = QLabel(self._step_caption(), self)
        lay.addWidget(self._step_label)
        self._apply_compact_layout(self.width())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_compact_layout(event.size().width())

    def _apply_compact_layout(self, width: int) -> None:
        compact = width < 900
        style = (Qt.ToolButtonStyle.ToolButtonIconOnly if compact
                 else Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        for button in self._verb_buttons:
            button.setToolButtonStyle(style)
        self._step_label.setVisible(not compact)

    def _step_caption(self) -> str:
        sc = self._grid.model().table.cells[0].scale
        return f"fine ±{sc.fine_increment:g} · coarse ±{sc.coarse_increment:g}"

    def step_caption_text(self) -> str:
        return self._step_label.text()

    def actions_list(self):
        out = [self.action_undo_sel, self.action_undo_all, self.action_revert, self.action_copy_sel,
               self.action_copy_table, self.action_paste, self.action_interpolate]
        out += [getattr(self, n) for n in ("action_h_interp", "action_v_interp") if hasattr(self, n)]
        out += [self.action_show_changes, self.action_compare_to, self.action_percent,
                self.action_absolute, self.action_compare_off]
        return out

    def _sel(self):
        return self._grid.selected_indexes()

    def _op(self, fn) -> None:
        fn(self._grid.model(), self._sel())

    def _paste(self) -> None:
        pasted = clipboard.paste(self._grid.model(), self._sel())
        self._grid.mark_last_paste(pasted)

    def _undo_last(self) -> None:
        if self._grid.model().undo_last():
            self._grid.reconcile_last_paste_after_undo()

    def _undo_all(self) -> None:
        clipboard.undo_all(self._grid.model())
        self._grid.clear_last_paste()

    def _set_revert_point(self) -> None:
        clipboard.set_revert_point(self._grid.model())
        self._grid.clear_last_paste()

    # --- compare (fact base 1.3 JTableChooser; Phase 2 plan-gap wiring) -------
    def _on_show_changes_toggled(self, checked: bool) -> None:
        if checked:
            self._grid.model().set_compare_original()
        else:
            self._grid.model().compare_off()

    def _on_compare_to(self) -> None:
        from ecueditor.ui.editor.table_chooser import TableChooserDialog
        roms = self._roms_provider() if self._roms_provider is not None else []
        dlg = TableChooserDialog(roms, target_name=self._grid.model().table.name,
                                 target_shape=self._grid.model().table.shape(), parent=self)
        if dlg.exec():
            picked = dlg.picked_table()
            if picked is not None:               # OK-with-no-pick is a no-op (callers must null-check)
                self._grid.model().set_compare_table(picked)

    def _on_compare_off(self) -> None:
        self._grid.model().compare_off()
        self.action_show_changes.setChecked(False)
