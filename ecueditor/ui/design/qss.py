"""Application stylesheet rendered from Theme tokens (spec §3; replaces resources/dark.qss)."""
from __future__ import annotations
from ecueditor.ui.design.tokens import Theme, rgba


def render_qss(t: Theme, icons_dir: str | None = None) -> str:
    qss = f"""
/* ==== base ==== */
QMainWindow, QDialog {{ background: {t.bg}; color: {t.text}; }}
QWidget {{ color: {t.text}; }}
QMessageBox {{ background: {t.surface1}; color: {t.text}; }}
QMessageBox QLabel {{ background: transparent; color: {t.text}; }}
QToolTip {{ background: {t.surface3}; color: {t.text}; border: 1px solid {t.border_strong};
            padding: {t.space[0]}px {t.space[1]}px; border-radius: {t.radius[0]}px; }}

/* ==== menus / bars ==== */
QMenuBar {{ background: {t.surface2}; color: {t.text}; border-bottom: 1px solid {t.border}; }}
QMenuBar::item {{ background: transparent; padding: {t.space[0]}px {t.space[1]}px; }}
QMenuBar::item:selected {{ background: {rgba(t.accent, 0.14)}; color: {t.text}; }}
QMenu {{ background: {t.surface3}; color: {t.text}; border: 1px solid {t.border_strong};
         border-radius: {t.radius[1]}px; padding: {t.space[0]}px; }}
QMenu::item {{ padding: {t.space[0]}px {t.space[2]}px; border-radius: {t.radius[0]}px; }}
QMenu::item:selected {{ background: {rgba(t.accent, 0.2)}; }}
QMenu::item:disabled {{ color: {t.text_disabled}; }}
QMenu::separator {{ height: 1px; background: {t.border}; margin: {t.space[0]}px; }}
QToolBar {{ background: {t.surface2}; border: none; spacing: {t.space[0]}px;
            padding: {t.space[0]}px; }}
QToolButton {{ background: transparent; border: 1px solid transparent; border-radius: {t.radius[1]}px;
               padding: 5px; margin: 0px 1px; color: {t.text}; }}
QToolButton:hover {{ background: {rgba(t.accent, 0.14)}; border-color: {rgba(t.accent, 0.35)}; }}
QToolButton:pressed {{ background: {t.accent_pressed}; color: #ffffff; }}
QToolButton:checked {{ background: {rgba(t.accent, 0.20)}; border-color: {t.accent}; }}
QToolButton:disabled {{ color: {t.text_disabled}; }}
QToolBar::separator {{ background: {t.border}; width: 1px; margin: {t.space[0]}px {t.space[1]}px; }}
QStatusBar {{ background: {t.surface2}; color: {t.text_dim};
              border-top: 1px solid {t.border}; }}

/* ==== logger surfaces ==== */
QWidget#loggerConnectionBar {{ background: {t.surface2}; color: {t.text};
    border-bottom: 1px solid {t.border}; }}
QWidget#loggerCenter {{ background: {t.bg}; color: {t.text}; }}
QWidget#loggerSelectionPanel {{ background: {t.surface1}; color: {t.text}; }}
QWidget#loggerDashboard, QWidget#loggerDashboardHost {{ background: {t.bg}; color: {t.text}; }}
QScrollArea#loggerDashboardScroll {{ background: {t.bg}; border: none; }}
QScrollArea#loggerDashboardScroll > QWidget > QWidget {{ background: {t.bg}; }}
QGroupBox {{ background: {t.surface1}; color: {t.text}; border: 1px solid {t.border_strong};
    border-radius: {t.radius[1]}px; margin-top: 10px; }}
QGroupBox::title {{ background: {t.surface1}; color: {t.text_dim};
    subcontrol-origin: margin; left: {t.space[1]}px; padding: 0 {t.space[0]}px; }}

/* ==== docks / splitters ==== */
QDockWidget {{ background: {t.surface1}; color: {t.text_dim}; titlebar-close-icon: none; }}
QDockWidget::title {{ background: {t.surface2}; padding: {t.space[0]}px {t.space[1]}px;
                      border-bottom: 1px solid {t.border}; }}
QMainWindow::separator {{ background: {t.border}; width: 2px; height: 2px; }}
QMainWindow::separator:hover {{ background: {t.accent}; }}
QSplitter::handle {{ background: {t.border}; }}
QSplitter::handle:hover {{ background: {t.accent}; }}

/* ==== MDI document workspace ==== */
QMdiArea#documentArea {{ background: {t.bg}; border: none; }}
QWidget#workspaceHost, QWidget#documentAreaViewport {{ background: {t.bg}; color: {t.text}; }}
QWidget#romTreePanel, QWidget#cellInspectorPanel {{
    background: {t.surface1}; color: {t.text}; border: none; }}
QWidget#documentNavigator {{ background: {t.surface2}; border-bottom: 1px solid {t.border}; }}
QTabBar#documentTabs {{ background: {t.surface2}; color: {t.text}; }}
QTabBar#documentTabs::tab {{ min-width: 96px; max-width: 260px; }}
QToolButton#restoreDocumentButton {{ padding: {t.space[0]}px {t.space[2]}px; }}

/* ==== tabs (dialogs and logger views) ==== */
QTabWidget::pane {{ border: 1px solid {t.border}; top: -1px; }}
QTabBar::tab {{ background: {t.surface2}; color: {t.text_dim};
                padding: {t.space[0]}px {t.space[2]}px; border-right: 1px solid {t.border};
                min-width: 60px; }}
QTabBar::tab:selected {{ background: {t.bg}; color: {t.text};
                         border-top: 2px solid {t.accent}; }}
QTabBar::tab:hover:!selected {{ background: {t.surface3}; }}
QTabBar::close-button {{ subcontrol-position: right; }}

/* ==== trees / tables / headers ==== */
QTreeView, QTreeWidget, QListView, QListWidget {{
    background: {t.surface1}; color: {t.text}; border: none;
    selection-background-color: {rgba(t.accent, 0.18)}; selection-color: {t.text};
    outline: none; }}
QTreeView::item:hover, QTreeWidget::item:hover {{ background: {rgba(t.accent, 0.08)}; }}
QTableView {{ background: {t.bg}; color: {t.text}; gridline-color: {t.grid_line};
              selection-background-color: {rgba(t.accent, 0.25)}; selection-color: {t.text};
              border: none; }}
QHeaderView::section {{ background: {t.surface3}; color: {t.text_dim};
                        border: 1px solid {t.grid_line}; padding: 2px {t.space[0]}px;
                        font-weight: 600; }}
QTableCornerButton::section {{ background: {t.surface2}; border: 1px solid {t.grid_line}; }}

/* ==== buttons / inputs ==== */
QPushButton {{ background: {t.surface3}; color: {t.text}; border: 1px solid {t.border_strong};
               border-radius: {t.radius[1]}px; padding: {t.space[0]}px {t.space[2]}px; }}
QPushButton:hover {{ border-color: {t.accent}; }}
QPushButton:pressed {{ background: {t.accent_pressed}; color: #ffffff; }}
QPushButton:disabled {{ color: {t.text_disabled}; background: {t.surface2};
                        border-color: {t.border}; }}
QPushButton:focus {{ border: 1px solid {t.focus_ring}; }}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    background: {t.surface3}; color: {t.text}; border: 1px solid {t.border_strong};
    border-radius: {t.radius[0]}px; padding: 2px {t.space[0]}px;
    selection-background-color: {rgba(t.accent, 0.35)}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
    border: 1px solid {t.focus_ring}; }}
QLineEdit[invalid="true"] {{ border: 1px solid {t.danger}; background: {rgba(t.danger, 0.10)}; }}
QComboBox QAbstractItemView {{ background: {t.surface3}; color: {t.text};
    border: 1px solid {t.border_strong}; selection-background-color: {rgba(t.accent, 0.2)}; }}
QCheckBox, QRadioButton {{ color: {t.text}; spacing: {t.space[1]}px; }}
QCheckBox:disabled, QRadioButton:disabled {{ color: {t.text_disabled}; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 14px; height: 14px;
    border: 2px solid {t.border_strong}; background: {t.surface1}; }}
QCheckBox::indicator {{ border-radius: {t.radius[0]}px; }}
QRadioButton::indicator {{ border-radius: 9px; }}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
    background: {t.accent}; border-color: {t.accent}; }}
QCheckBox::indicator:focus, QRadioButton::indicator:focus {{ border-color: {t.focus_ring}; }}

/* ==== scrollbars ==== */
QScrollBar:vertical {{ background: {t.surface1}; width: 10px; margin: 0; }}
QScrollBar:horizontal {{ background: {t.surface1}; height: 10px; margin: 0; }}
QScrollBar::handle {{ background: {t.border_strong}; border-radius: 5px; min-height: 24px;
                      min-width: 24px; }}
QScrollBar::handle:hover {{ background: {t.text_disabled}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* ==== status chips (Task 16 widgets read these via dynamic property) ==== */
QLabel[chipKind] {{ border-radius: {t.radius[3]}px; padding: 1px {t.space[1]}px;
                    background: {t.surface3}; color: {t.text_dim}; }}
QLabel[chipKind="ok"] {{ color: {t.ok}; background: {rgba(t.ok, 0.12)}; }}
QLabel[chipKind="danger"] {{ color: #ffffff; background: {t.danger_fill}; font-weight: 600; }}
QLabel[chipKind="warn"] {{ color: {t.warn}; background: {rgba(t.warn, 0.12)}; }}
QLabel[chipKind="accent"] {{ color: {t.accent}; background: {rgba(t.accent, 0.14)};
                             font-weight: 600; }}
QLabel[chipKind="info"] {{ color: {t.live_ring}; background: {rgba(t.live_ring, 0.14)};
                           font-weight: 600; }}
QLabel[chipKind="neutral"] {{ color: {t.text_dim}; background: {t.surface3}; }}

/* ==== table frames (Phase 8b polish — mockup table-frames-v2) ==== */
QWidget#tableFrame {{ background: {t.bg}; border: 1px solid {t.border_strong}; border-radius: {t.radius[3]}px; }}
QWidget#frameHeader {{ background: {t.surface1}; border-bottom: 1px solid {t.border}; }}
QLabel#frameTitle {{ color: {t.text}; background: {t.surface1}; }}

/* ==== verb toolbar band (Phase 8b polish -- mockup .tf-verbs chip buttons) ==== */
QWidget#frameVerbs {{ background: {t.surface1}; border-bottom: 1px solid {t.border}; }}
QWidget#frameVerbs QToolButton {{ color: {t.text}; background: {t.surface3}; border: 1px solid {t.border_strong};
    border-radius: {t.radius[0]}px; padding: 2px 8px; margin: 0 1px; }}
QWidget#frameVerbs QToolButton:hover {{ color: {t.accent_hover}; border-color: {t.accent};
    background: {rgba(t.accent, 0.16)}; }}
QWidget#frameVerbs QToolButton:pressed {{ color: {t.text}; border-color: {t.accent_pressed};
    background: {rgba(t.accent_pressed, 0.34)}; }}
QWidget#frameVerbs QToolButton::menu-indicator {{ image: none; }}
QWidget#frameVerbs QWidget#tfSep {{ background: {t.border_strong}; }}
QWidget#frameVerbs QLabel {{ color: {t.text_dim}; background: transparent; }}

/* ==== 3D surface workspace ==== */
QWidget#surface3dView {{ background: {t.bg}; border: 1px solid {t.border_strong};
    border-radius: {t.radius[3]}px; }}
QWidget#surfaceControls, QWidget#surfaceFooter {{ background: {t.surface1}; }}
QWidget#surfaceControls {{ border-bottom: 1px solid {t.border}; }}
QWidget#surfaceFooter {{ border-top: 1px solid {t.border}; }}
QWidget#surfaceControls > QLabel {{ color: {t.text_dim}; }}
QWidget#surfaceControls QToolButton {{ color: {t.text}; background: {t.surface3};
    border: 1px solid {t.border_strong}; border-radius: {t.radius[0]}px; padding: 3px 8px; }}
QWidget#surfaceControls QToolButton:hover {{ border-color: {t.accent}; }}
QWidget#surfaceControls QToolButton:checked {{ color: {t.accent}; border-color: {t.accent};
    background: {rgba(t.accent, 0.12)}; }}
QWidget#surfaceControls QCheckBox {{ color: {t.text_dim}; }}
QSlider#surfaceHeight::groove:horizontal {{ height: 4px; background: {t.border_strong};
    border-radius: 2px; }}
QSlider#surfaceHeight::sub-page:horizontal {{ background: {t.accent}; border-radius: 2px; }}
QSlider#surfaceHeight::handle:horizontal {{ width: 14px; height: 14px; margin: -5px 0;
    border-radius: 7px; background: {t.text}; border: 2px solid {t.accent}; }}
QLabel#surfaceHeightValue {{ color: {t.text}; min-width: 28px; }}
QLabel#surfaceValue {{ color: {t.text}; font-weight: 600; }}
"""
    if icons_dir is not None:
        qss += f"""
/* ==== spin-box arrows (image: url() is the only shape Qt QSS renders — H12/Checkpoint-1) ==== */
QSpinBox, QDoubleSpinBox {{ padding-right: 20px; }}
QSpinBox::up-button, QDoubleSpinBox::up-button {{ subcontrol-origin: border; subcontrol-position: top right;
    width: 18px; height: 11px; border-left: 1px solid {t.border_strong}; background: {t.surface2}; }}
QSpinBox::down-button, QDoubleSpinBox::down-button {{ subcontrol-origin: border; subcontrol-position: bottom right;
    width: 18px; height: 11px; border-left: 1px solid {t.border_strong}; background: {t.surface2}; }}
QSpinBox::up-button:hover, QSpinBox::down-button:hover,
QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover {{ background: {t.accent}; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url({icons_dir}/chevron-up-{t.name}.svg); width: 12px; height: 12px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: url({icons_dir}/chevron-down-{t.name}.svg); width: 12px; height: 12px; }}
"""
    return qss
