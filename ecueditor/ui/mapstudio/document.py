from __future__ import annotations

import re

import numpy as np
from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QAction, QFont, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFrame,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ecueditor.core.mapstudio import (
    CurveData,
    CurveResampleResult,
    MapData,
    MapValidationError,
    ResampleResult,
    UndoHistory,
    build_safety_report,
    collapse_duplicate_curve,
    collapse_duplicate_map,
    detect_anomalies,
    detect_curve_anomalies,
    even_axis,
    fingerprint_table,
    quantize_table_proposal,
    repair_curve_selection,
    repair_selected_region,
    resample_curve,
    resample_map,
    smooth_entire_curve,
    smooth_entire_table,
    snapshot_table,
)
from ecueditor.ui.mapstudio.visualization import CurveReviewDialog, MapReviewDialog
from ecueditor.ui.mapstudio.widgets import (
    ArrayLegend,
    ArrayTableWidget,
    TableZoomControls,
)
from ecueditor.ui.workspace.status_chips import Chip


def _map_equal(left: MapData, right: MapData) -> bool:
    return bool(
        np.array_equal(left.x, right.x)
        and np.array_equal(left.y, right.y)
        and np.array_equal(left.z, right.z)
    )


def _curve_equal(left: CurveData, right: CurveData) -> bool:
    return bool(np.array_equal(left.x, right.x) and np.array_equal(left.values, right.values))


CalibrationData = MapData | CurveData


def _calibration_equal(left: CalibrationData, right: CalibrationData) -> bool:
    if isinstance(left, MapData) and isinstance(right, MapData):
        return _map_equal(left, right)
    if isinstance(left, CurveData) and isinstance(right, CurveData):
        return _curve_equal(left, right)
    return False


class _CurrentPageStack(QStackedWidget):
    """Size a mode switch from its active page, not the hidden custom-axis editor."""

    def sizeHint(self) -> QSize:
        page = self.currentWidget()
        return page.sizeHint() if page is not None else super().sizeHint()

    def minimumSizeHint(self) -> QSize:
        page = self.currentWidget()
        return page.minimumSizeHint() if page is not None else super().minimumSizeHint()


class SmoothingPreviewDialog(QDialog):
    def __init__(
        self,
        before,
        proposed,
        parent=None,
        *,
        colormap: str = "rainbow",
        display_settings=None,
        decimals: int = 3,
        operation_label: str = "smoothing",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Review proposed {operation_label}")
        self.setObjectName("mapStudioPreviewDialog")
        screen = self.screen()
        available = screen.availableGeometry().size() if screen is not None else QSize(1200, 800)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        warning = QLabel(
            "Review every changed value. A smoother calibration is not automatically safer."
        )
        warning.setObjectName("mapStudioPreviewWarning")
        warning.setWordWrap(True)
        layout.addWidget(warning)
        self.tabs = QTabWidget(self)
        self.proposed_table = ArrayTableWidget(colormap=colormap)
        self.difference_table = ArrayTableWidget(colormap=colormap)
        if display_settings is not None:
            for table in (self.proposed_table, self.difference_table):
                table.configure_display(
                    font_size=int(getattr(display_settings, "font_size", 11)),
                    density=str(getattr(display_settings, "table_density", "normal")),
                    color_cells=bool(getattr(display_settings, "color_cells", True)),
                )
        if isinstance(before, MapData):
            self.proposed_table.set_values(
                proposed.z,
                x=proposed.x,
                y=proposed.y,
                decimals=decimals,
            )
            self.difference_table.set_values(
                proposed.z - before.z,
                x=proposed.x,
                y=proposed.y,
                decimals=decimals,
                difference=True,
            )
        else:
            self.proposed_table.set_values(
                proposed.values,
                x=proposed.x,
                decimals=decimals,
            )
            self.difference_table.set_values(
                proposed.values - before.values,
                x=proposed.x,
                decimals=decimals,
                difference=True,
            )
        self.tabs.addTab(self._table_page(self.proposed_table), "Proposed")
        self.tabs.addTab(self._table_page(self.difference_table), "Difference")
        layout.addWidget(self.tabs)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        preferred = layout.sizeHint().expandedTo(QSize(620, 400))
        self.resize(
            min(preferred.width(), round(available.width() * 0.9)),
            min(preferred.height(), round(available.height() * 0.9)),
        )

    @staticmethod
    def _table_page(table: ArrayTableWidget) -> QWidget:
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(0)
        page_layout.addWidget(table, 1)
        footer = QWidget(page)
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(6, 3, 6, 0)
        legend = ArrayLegend()
        legend.set_table(table)
        footer_layout.addWidget(legend, 1)
        footer_layout.addWidget(TableZoomControls(table))
        page_layout.addWidget(footer)
        return page

class MapStudioDocument(QWidget):
    """Snapshot-based Map Studio document hosted by BimmerStein's MDI area."""

    applyRequested = Signal(object)

    def __init__(
        self,
        rom,
        table,
        initial_selection=None,
        parent=None,
        *,
        display_settings=None,
    ) -> None:
        super().__init__(parent)
        self.rom = rom
        self.table = table
        self._display_settings = display_settings
        self._colormap = getattr(display_settings, "colormap", "rainbow")
        self.snapshot = snapshot_table(table)
        self.source_data: MapData | None = None
        self.curve_source: CurveData | None = None
        self.result: ResampleResult | None = None
        self.curve_result: CurveResampleResult | None = None
        self._result_source: MapData | CurveData | None = None
        self.source_region: (
            tuple[int, int] | tuple[int, int, int, int] | None
        ) = None
        self._visual_windows: list[QDialog] = []
        self._updating_table = False
        self._updating_axis_fields = False
        self._automatic_x_bounds: tuple[float, float] = (
            float(self.snapshot.x[0]),
            float(self.snapshot.x[-1]),
        )
        self._automatic_y_bounds: tuple[float, float] | None = (
            None
            if self.snapshot.y is None
            else (float(self.snapshot.y[0]), float(self.snapshot.y[-1]))
        )
        self._stale = False
        self._status_before_stale: tuple[str, str, str] | None = None
        self._source_history: UndoHistory[CalibrationData] = UndoHistory(
            _calibration_equal
        )
        self._result_history: UndoHistory[CalibrationData] = UndoHistory(
            _calibration_equal
        )
        self._build_ui()
        if display_settings is not None:
            self.apply_display_settings(display_settings)
        self._load_snapshot(initial_selection)

    def _build_ui(self) -> None:
        from ecueditor.ui.design.icons import icon

        self.setObjectName("mapStudioDocument")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QWidget(self)
        header.setObjectName("mapStudioHeader")
        header.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(14, 8, 14, 8)
        header_layout.setSpacing(8)
        heading = QVBoxLayout()
        heading.setSpacing(1)
        title = QLabel(self.table.name)
        title.setObjectName("frameTitle")
        title_font = QFont(self.font())
        title_font.setPointSize(14)
        title_font.setWeight(QFont.Weight.DemiBold)
        title.setFont(title_font)
        subtitle = QLabel("Map Studio · local resampling workspace")
        subtitle.setObjectName("mapStudioSubtitle")
        heading.addWidget(title)
        heading.addWidget(subtitle)
        header_layout.addLayout(heading, 1)
        sx, sy = self.table.shape()
        header_layout.addWidget(Chip("MAP STUDIO", "accent"))
        dimension_text = f"{sx} × {sy} MAP" if sy > 1 else f"{sx} POINT CURVE"
        self.dimension_chip = Chip(dimension_text, "neutral")
        header_layout.addWidget(self.dimension_chip)
        self.workspace_chip = Chip("LOCAL", "info")
        self.workspace_chip.setToolTip(
            "The opening table remains unchanged until you apply the generated result."
        )
        header_layout.addWidget(self.workspace_chip)
        root.addWidget(header)

        toolbar = QWidget(self)
        toolbar.setObjectName("frameVerbs")
        toolbar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(10, 5, 10, 5)
        toolbar_layout.setSpacing(4)

        def tool_button(text: str, icon_name: str, tooltip: str) -> QToolButton:
            button = QToolButton(toolbar)
            button.setText(text)
            button.setIcon(icon(icon_name))
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            button.setToolTip(tooltip)
            return button

        def separator() -> None:
            line = QWidget(toolbar)
            line.setObjectName("tfSep")
            line.setFixedWidth(1)
            line.setMinimumHeight(20)
            toolbar_layout.addWidget(line)

        self.anomaly_button = tool_button(
            "Find anomalies",
            "warning",
            "Select numerically suspicious cells in the active Source or Result",
        )
        self.repair_button = tool_button(
            "Repair selection",
            "interpolate",
            "Preview a local repair of selected cells in the active Source or Result",
        )
        self.smooth_button = tool_button(
            "Smooth table",
            "interpolate",
            "Preview smoothing across the complete active Source or Result",
        )
        self.undo_button = tool_button("Undo", "undo", "Undo the last local Studio change")
        self.redo_button = tool_button("Redo", "refresh", "Redo the last local Studio change")
        self.visualize_button = tool_button(
            "Visualize", "cube", "Review the active table as slices and a 3D surface"
        )
        self.safety_button = tool_button(
            "Safety report", "warning", "Review numerical changes and extrapolated cells"
        )
        for button in (self.anomaly_button, self.repair_button, self.smooth_button):
            toolbar_layout.addWidget(button)
        separator()
        for button in (self.undo_button, self.redo_button):
            toolbar_layout.addWidget(button)
        separator()
        toolbar_layout.addStretch(1)
        toolbar_layout.addWidget(self.visualize_button)
        toolbar_layout.addWidget(self.safety_button)
        root.addWidget(toolbar)

        self.splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.splitter.setObjectName("mapStudioSplitter")
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(1)

        workspace = QWidget(self.splitter)
        workspace.setObjectName("mapStudioWorkspace")
        workspace.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        workspace_layout = QVBoxLayout(workspace)
        workspace_layout.setContentsMargins(10, 9, 9, 9)
        workspace_layout.setSpacing(6)

        panel_header = QWidget(workspace)
        panel_header.setObjectName("mapStudioPanelHeader")
        panel_layout = QHBoxLayout(panel_header)
        panel_layout.setContentsMargins(2, 0, 2, 0)
        panel_layout.setSpacing(7)
        panel_text = QVBoxLayout()
        panel_text.setSpacing(0)
        self.panel_title_label = QLabel("Source calibration")
        self.panel_title_label.setObjectName("mapStudioPanelTitle")
        self.panel_subtitle_label = QLabel(
            "Select a contiguous region to define the interpolation source."
        )
        self.panel_subtitle_label.setObjectName("mapStudioPanelSubtitle")
        panel_text.addWidget(self.panel_title_label)
        panel_text.addWidget(self.panel_subtitle_label)
        panel_layout.addLayout(panel_text, 1)
        self.panel_state_chip = Chip("SOURCE", "neutral")
        panel_layout.addWidget(self.panel_state_chip)
        workspace_layout.addWidget(panel_header)

        self.tabs = QTabWidget(workspace)
        self.tabs.setObjectName("mapStudioTabs")
        self.source_table = ArrayTableWidget(colormap=self._colormap)
        self.result_table = ArrayTableWidget(colormap=self._colormap)
        self.changes_table = ArrayTableWidget(colormap=self._colormap)
        x_formatter, y_formatter = self._axis_formatters()
        for table in (self.source_table, self.result_table, self.changes_table):
            table.set_axis_formatters(x_formatter, y_formatter)
        self.source_legend = ArrayLegend()
        self.result_legend = ArrayLegend()
        self.changes_legend = ArrayLegend()
        self.source_legend.set_table(self.source_table)
        self.result_legend.set_table(self.result_table)
        self.changes_legend.set_table(self.changes_table)
        self.edit_bar = QWidget(workspace)
        self.edit_bar.setObjectName("mapStudioEditBar")
        self.edit_bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        edit_layout = QHBoxLayout(self.edit_bar)
        edit_layout.setContentsMargins(2, 2, 2, 2)
        edit_layout.setSpacing(4)

        def edit_action(
            text: str,
            slot,
            *,
            icon_name: str | None = None,
            shortcut: str | QKeySequence | None = None,
        ) -> QAction:
            action = QAction(text, self)
            if icon_name is not None:
                action.setIcon(icon(icon_name))
            if shortcut is not None:
                action.setShortcut(QKeySequence(shortcut))
            action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            action.triggered.connect(slot)
            self.addAction(action)
            return action

        self.action_copy_selection = edit_action(
            "Copy Selection", self.copy_active_selection, icon_name="copy", shortcut="Ctrl+C"
        )
        self.action_copy_table = edit_action(
            "Copy Entire Table",
            self.copy_active_table,
            icon_name="copy",
            shortcut="Ctrl+Shift+C",
        )
        self.action_paste = edit_action(
            "Paste", self.paste_active, icon_name="paste", shortcut="Ctrl+V"
        )
        self.action_fine_decrease = edit_action(
            "Fine −", lambda: self._step_active(coarse=False, sign=-1)
        )
        self.action_fine_increase = edit_action(
            "Fine +", lambda: self._step_active(coarse=False, sign=1)
        )
        self.action_coarse_decrease = edit_action(
            "Coarse −", lambda: self._step_active(coarse=True, sign=-1), shortcut="_"
        )
        self.action_coarse_increase = edit_action(
            "Coarse +", lambda: self._step_active(coarse=True, sign=1), shortcut="+"
        )
        self.action_set_value = edit_action(
            "Set Value…", lambda: self._prompt_adjustment("set")
        )
        self.action_add = edit_action("Add…", lambda: self._prompt_adjustment("add"))
        self.action_subtract = edit_action(
            "Subtract…", lambda: self._prompt_adjustment("subtract")
        )
        self.action_multiply = edit_action(
            "Multiply…", lambda: self._prompt_adjustment("multiply"), shortcut="*"
        )
        self.action_divide = edit_action(
            "Divide…", lambda: self._prompt_adjustment("divide")
        )
        self.action_increase_percent = edit_action(
            "Increase by Percent…", lambda: self._prompt_adjustment("increase_percent")
        )
        self.action_decrease_percent = edit_action(
            "Decrease by Percent…", lambda: self._prompt_adjustment("decrease_percent")
        )
        self.action_interpolate_selection = edit_action(
            "Interpolate Selection", self._interpolate_active, icon_name="interpolate"
        )

        self.copy_button = QToolButton(self.edit_bar)
        self.copy_button.setText("Copy")
        self.copy_button.setIcon(icon("copy"))
        self.copy_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.copy_button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        copy_menu = QMenu(self.copy_button)
        copy_menu.addAction(self.action_copy_selection)
        copy_menu.addAction(self.action_copy_table)
        self.copy_button.setMenu(copy_menu)
        self.copy_button.clicked.connect(self.copy_active_selection)

        self.paste_button = QToolButton(self.edit_bar)
        self.paste_button.setDefaultAction(self.action_paste)
        self.paste_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)

        self.adjust_button = QToolButton(self.edit_bar)
        self.adjust_button.setText("Adjust")
        self.adjust_button.setIcon(icon("interpolate"))
        self.adjust_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.adjust_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        adjust_menu = QMenu(self.adjust_button)
        for action in (
            self.action_fine_decrease,
            self.action_fine_increase,
            self.action_coarse_decrease,
            self.action_coarse_increase,
        ):
            adjust_menu.addAction(action)
        adjust_menu.addSeparator()
        for action in (
            self.action_set_value,
            self.action_add,
            self.action_subtract,
            self.action_multiply,
            self.action_divide,
            self.action_increase_percent,
            self.action_decrease_percent,
        ):
            adjust_menu.addAction(action)
        adjust_menu.addSeparator()
        adjust_menu.addAction(self.action_interpolate_selection)
        self.adjust_button.setMenu(adjust_menu)

        self.edit_hint = QLabel("Local edits · double-click or type a value")
        self.edit_hint.setObjectName("mapStudioEditHint")
        edit_layout.addWidget(self.copy_button)
        edit_layout.addWidget(self.paste_button)
        edit_layout.addWidget(self.adjust_button)
        edit_layout.addStretch(1)
        edit_layout.addWidget(self.edit_hint)
        workspace_layout.addWidget(self.edit_bar)

        self.tabs.addTab(
            self._table_page(self.source_table, self.source_legend), "Source"
        )
        self.tabs.addTab(
            self._table_page(self.result_table, self.result_legend), "Result"
        )
        self.tabs.addTab(
            self._table_page(self.changes_table, self.changes_legend), "Changes"
        )
        workspace_layout.addWidget(self.tabs, 1)
        self.splitter.addWidget(workspace)

        self.inspector_scroll = QScrollArea(self.splitter)
        self.inspector_scroll.setObjectName("mapStudioInspectorScroll")
        self.inspector_scroll.setWidgetResizable(True)
        self.inspector_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.inspector_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.inspector_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.inspector_scroll.setMinimumWidth(270)
        self.inspector_scroll.setMaximumWidth(330)
        inspector = QWidget()
        inspector.setObjectName("mapStudioInspector")
        inspector.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        inspector.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        inspector_layout = QVBoxLayout(inspector)
        inspector_layout.setContentsMargins(10, 10, 10, 12)
        inspector_layout.setSpacing(10)

        source_group = QGroupBox("1 · Source region", inspector)
        source_group.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        source_layout = QVBoxLayout(source_group)
        source_layout.setContentsMargins(9, 13, 9, 8)
        source_layout.setSpacing(5)
        source_help = QLabel(
            "Select the unique data region; repeated rows and columns outside it are padding."
        )
        source_help.setObjectName("mapStudioHelp")
        source_help.setWordWrap(True)
        source_help.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        source_layout.addWidget(source_help)
        self.source_summary_label = Chip("NO SOURCE CAPTURED", "neutral")
        source_layout.addWidget(self.source_summary_label)
        self.capture_button = QPushButton("Use Region")
        self.capture_button.setIcon(icon("check"))
        self.detect_button = QPushButton("Detect")
        self.detect_button.setIcon(icon("search"))
        self.detect_button.setToolTip("Detect the unique region outside repeated padding")
        self.capture_button.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed
        )
        self.detect_button.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed
        )
        source_actions = QHBoxLayout()
        source_actions.setSpacing(5)
        source_actions.addWidget(self.capture_button)
        source_actions.addWidget(self.detect_button)
        source_layout.addLayout(source_actions)
        inspector_layout.addWidget(source_group)

        target_group = QGroupBox("2 · Destination grid", inspector)
        target_group.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        target_layout = QVBoxLayout(target_group)
        target_layout.setContentsMargins(9, 13, 9, 8)
        target_layout.setSpacing(5)
        self.target_mode = QComboBox()
        self.target_mode.addItem("Automatic range", "auto")
        self.target_mode.addItem("Custom axes", "custom")
        target_layout.addWidget(self.target_mode)

        self.target_mode.setToolTip("The opening table remains the destination grid.")
        self.target_mode.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed
        )
        self.target_stack = _CurrentPageStack(target_group)
        self.target_stack.setObjectName("mapStudioTargetStack")
        self.target_stack.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        automatic_page = QWidget()
        automatic_layout = QGridLayout(automatic_page)
        automatic_layout.setContentsMargins(0, 2, 0, 2)
        automatic_layout.setHorizontalSpacing(6)
        automatic_layout.setVerticalSpacing(5)
        minimum_heading = QLabel("Minimum")
        maximum_heading = QLabel("Maximum")
        minimum_heading.setObjectName("mapStudioFieldHeading")
        maximum_heading.setObjectName("mapStudioFieldHeading")
        automatic_layout.addWidget(minimum_heading, 0, 1)
        automatic_layout.addWidget(maximum_heading, 0, 2)
        self.x_min = self._number_box()
        self.x_max = self._number_box()
        self.y_min = self._number_box()
        self.y_max = self._number_box()
        for box in (self.x_min, self.x_max, self.y_min, self.y_max):
            box.setMinimumWidth(0)
            box.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.x_min_label = QLabel("X")
        self.x_max_label = maximum_heading
        self.y_min_label = QLabel("Y")
        self.y_max_label = maximum_heading
        automatic_layout.addWidget(self.x_min_label, 1, 0)
        automatic_layout.addWidget(self.x_min, 1, 1)
        automatic_layout.addWidget(self.x_max, 1, 2)
        automatic_layout.addWidget(self.y_min_label, 2, 0)
        automatic_layout.addWidget(self.y_min, 2, 1)
        automatic_layout.addWidget(self.y_max, 2, 2)
        automatic_layout.setColumnStretch(1, 1)
        automatic_layout.setColumnStretch(2, 1)
        self.target_stack.addWidget(automatic_page)

        custom_page = QWidget()
        custom_layout = QVBoxLayout(custom_page)
        custom_layout.setContentsMargins(0, 2, 0, 2)
        custom_layout.setSpacing(4)
        self.target_x_label = QLabel("X breakpoints")
        self.target_y_label = QLabel("Y breakpoints")
        self.target_x_text = QPlainTextEdit()
        self.target_y_text = QPlainTextEdit()
        self.target_x_text.setMinimumHeight(64)
        self.target_x_text.setMaximumHeight(84)
        self.target_y_text.setMinimumHeight(64)
        self.target_y_text.setMaximumHeight(84)
        self.target_x_text.setMinimumWidth(0)
        self.target_y_text.setMinimumWidth(0)
        self.target_x_text.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        self.target_y_text.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        self.target_x_text.setPlaceholderText("X breakpoints, comma or whitespace separated")
        self.target_y_text.setPlaceholderText("Y breakpoints, comma or whitespace separated")
        custom_layout.addWidget(self.target_x_label)
        custom_layout.addWidget(self.target_x_text)
        custom_layout.addWidget(self.target_y_label)
        custom_layout.addWidget(self.target_y_text)
        self.target_stack.addWidget(custom_page)
        target_layout.addWidget(self.target_stack)
        self.target_count_label = Chip("", "neutral")
        self.target_count_label.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        target_layout.addWidget(self.target_count_label)
        inspector_layout.addWidget(target_group)

        method_group = QGroupBox("3 · Resampling", inspector)
        method_group.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        method_layout = QFormLayout(method_group)
        method_layout.setContentsMargins(9, 13, 9, 8)
        method_layout.setSpacing(5)
        method_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.method_combo = QComboBox()
        self.boundary_combo = QComboBox()
        self.method_combo.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed
        )
        self.boundary_combo.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed
        )
        self.boundary_combo.addItem("Hold edge values", "hold")
        self.boundary_combo.addItem("Linear to destination", "linear_to_destination")
        self.boundary_combo.addItem("Limited linear", "linear")
        self.boundary_combo.addItem("Do not extrapolate", "disallow")
        self.boundary_combo.setToolTip(
            "Linear to destination continues the final source slope across the complete "
            "target grid. Limited linear stops after the configured maximum distance."
        )
        self.edge_limit = QDoubleSpinBox()
        self.edge_limit.setRange(0.05, 20.0)
        self.edge_limit.setValue(1.0)
        self.edge_limit.setSingleStep(0.25)
        self.edge_limit.setToolTip(
            "Maximum distance beyond each source edge. 1.00 continues the slope for one "
            "final source interval, then holds that extrapolated value."
        )
        method_layout.addRow("Method", self.method_combo)
        method_layout.addRow("Boundary", self.boundary_combo)
        method_layout.addRow("Maximum edge intervals", self.edge_limit)
        self.edge_limit_label = method_layout.labelForField(self.edge_limit)
        self.expand_button = QPushButton("Expand Region to Full Grid")
        self.expand_button.setIcon(icon("interpolate"))
        self.expand_button.setToolTip(
            "Use the selected source region across every cell of the opening grid "
            "and generate the preview immediately."
        )
        self.generate_button = QPushButton("Generate Preview")
        self.generate_button.setIcon(icon("interpolate"))
        self.generate_button.setToolTip(
            "Recalculate a local preview from the current target range, method, "
            "and boundary settings. Nothing is written until Apply."
        )
        self.generate_button.setProperty("buttonRole", "primary")
        self.expand_button.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed
        )
        self.generate_button.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed
        )
        method_layout.addRow(self.expand_button)
        method_layout.addRow(self.generate_button)
        inspector_layout.addWidget(method_group)
        inspector_layout.addStretch(1)
        self.inspector_scroll.setWidget(inspector)
        self.splitter.addWidget(self.inspector_scroll)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 0)
        self.splitter.setSizes([720, 290])
        root.addWidget(self.splitter, 1)

        footer = QWidget(self)
        footer.setObjectName("mapStudioFooter")
        footer.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        bottom = QHBoxLayout(footer)
        bottom.setContentsMargins(12, 6, 12, 7)
        bottom.setSpacing(7)
        self.status_chip = Chip("READY", "neutral")
        self.status_label = QLabel()
        self.status_label.setObjectName("mapStudioStatus")
        self.status_label.setWordWrap(False)
        self.status_label.setMinimumWidth(0)
        self.status_label.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        self.reload_button = QPushButton("Reload Source")
        self.reload_button.setIcon(icon("refresh"))
        self.apply_button = QPushButton(f"Apply to {self.table.name}")
        self.apply_button.setIcon(icon("check"))
        self.apply_button.setProperty("buttonRole", "primary")
        bottom.addWidget(self.status_chip)
        bottom.addWidget(self.status_label, 1)
        bottom.addWidget(self.reload_button)
        bottom.addWidget(self.apply_button)
        root.addWidget(footer)

        self.capture_button.clicked.connect(self.capture_source_region)
        self.detect_button.clicked.connect(self.detect_active_region)
        self.expand_button.clicked.connect(self.expand_to_full_grid)
        self.generate_button.clicked.connect(self.generate_result)
        self.target_mode.currentIndexChanged.connect(self._target_mode_changed)
        self.target_mode.currentIndexChanged.connect(self._resampling_settings_changed)
        self.method_combo.currentIndexChanged.connect(self._resampling_settings_changed)
        self.boundary_combo.currentIndexChanged.connect(self._boundary_changed)
        self.boundary_combo.currentIndexChanged.connect(self._resampling_settings_changed)
        self.edge_limit.valueChanged.connect(self._resampling_settings_changed)
        self.target_x_text.textChanged.connect(self._resampling_settings_changed)
        self.target_y_text.textChanged.connect(self._resampling_settings_changed)
        self.x_min.valueChanged.connect(
            lambda value: self._automatic_bound_edited("x", 0, value)
        )
        self.x_max.valueChanged.connect(
            lambda value: self._automatic_bound_edited("x", 1, value)
        )
        self.y_min.valueChanged.connect(
            lambda value: self._automatic_bound_edited("y", 0, value)
        )
        self.y_max.valueChanged.connect(
            lambda value: self._automatic_bound_edited("y", 1, value)
        )
        self.source_table.valuesEdited.connect(self._source_edited)
        self.result_table.valuesEdited.connect(self._result_edited)
        self.anomaly_button.clicked.connect(self.detect_active_anomalies)
        self.repair_button.clicked.connect(self.repair_active_selection)
        self.smooth_button.clicked.connect(self.smooth_active)
        self.undo_button.clicked.connect(self.undo_local)
        self.redo_button.clicked.connect(self.redo_local)
        self.undo_button.setShortcut(QKeySequence.StandardKey.Undo)
        self.redo_button.setShortcut(QKeySequence.StandardKey.Redo)
        self.visualize_button.clicked.connect(self.visualize_active)
        self.safety_button.clicked.connect(self.show_safety_report)
        self.reload_button.clicked.connect(self.reload_source)
        self.apply_button.clicked.connect(self.request_apply)
        self.tabs.currentChanged.connect(self._active_tab_changed)
        self.source_table.itemSelectionChanged.connect(self._source_selection_changed)
        self.source_table.itemSelectionChanged.connect(self._refresh_edit_actions)
        self.result_table.itemSelectionChanged.connect(self._refresh_edit_actions)
        self.changes_table.itemSelectionChanged.connect(self._refresh_edit_actions)
        self._target_mode_changed()
        self._boundary_changed()
        self._refresh_edit_actions()

    @staticmethod
    def _table_page(table: ArrayTableWidget, legend: ArrayLegend) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(table, 1)
        footer = QWidget(page)
        footer.setObjectName("mapStudioTableFooter")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(0, 0, 6, 0)
        footer_layout.setSpacing(6)
        footer_layout.addWidget(legend, 1)
        footer_layout.addWidget(TableZoomControls(table))
        layout.addWidget(footer)
        return page

    def sizeHint(self) -> QSize:
        root = self.layout()
        workspace = self.splitter.widget(0)
        if workspace is None:
            return super().sizeHint().expandedTo(self.minimumSizeHint())
        workspace_layout = workspace.layout()
        if root is None or workspace_layout is None:
            return super().sizeHint().expandedTo(self.minimumSizeHint())

        workspace_margins = workspace_layout.contentsMargins()
        workspace_items: list[QWidget] = []
        for index in range(workspace_layout.count()):
            item = workspace_layout.itemAt(index)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None:
                workspace_items.append(widget)
        workspace_width = max(
            (widget.sizeHint().width() for widget in workspace_items),
            default=0,
        ) + workspace_margins.left() + workspace_margins.right()
        workspace_height = sum(widget.sizeHint().height() for widget in workspace_items)
        workspace_height += workspace_margins.top() + workspace_margins.bottom()
        workspace_height += max(0, len(workspace_items) - 1) * workspace_layout.spacing()

        inspector_width = max(
            self.inspector_scroll.minimumWidth(),
            min(self.inspector_scroll.maximumWidth(), 290),
        )
        splitter_width = workspace_width + inspector_width + self.splitter.handleWidth()
        splitter_height = workspace_height

        root_margins = root.contentsMargins()
        child_hints: list[QSize] = []
        for index in range(root.count()):
            item = root.itemAt(index)
            if item is None:
                continue
            widget = item.widget()
            if widget is None:
                continue
            child_hints.append(
                QSize(splitter_width, splitter_height)
                if widget is self.splitter
                else widget.sizeHint()
            )
        width = max((hint.width() for hint in child_hints), default=0)
        width += root_margins.left() + root_margins.right()
        height = sum(hint.height() for hint in child_hints)
        height += root_margins.top() + root_margins.bottom()
        height += max(0, len(child_hints) - 1) * root.spacing()
        return QSize(width, height).expandedTo(self.minimumSizeHint())

    def minimumSizeHint(self) -> QSize:
        return QSize(680, 420)

    def _axis_formatters(self):
        def formatter(axis):
            if axis is None or not axis.cells:
                return None
            return axis.cells[0].scale.format_value

        if self.snapshot.kind == "curve":
            axis = self.table.x_axis or self.table.y_axis
            return formatter(axis), None
        return formatter(self.table.x_axis), formatter(self.table.y_axis)

    def _map_review_labels(self) -> dict[str, str]:
        def axis_name(axis, fallback: str) -> str:
            if axis is None:
                return fallback
            return str(axis.name or fallback)

        return {
            "x_label": axis_name(self.table.x_axis, "X"),
            "y_label": axis_name(self.table.y_axis, "Y"),
            "value_units": str(self.table.cells[0].scale.units or ""),
        }

    def _active_table(self) -> ArrayTableWidget:
        return (self.source_table, self.result_table, self.changes_table)[
            self.tabs.currentIndex()
        ]

    def _active_calibration(self):
        index = self.tabs.currentIndex()
        if index == 0:
            data = self.source_data if self.source_data is not None else self.curve_source
            return data, self.source_table, "source"
        if index == 1:
            data = (
                self.result.map_data
                if self.result is not None
                else self.curve_result.curve_data if self.curve_result is not None else None
            )
            return data, self.result_table, "result"
        return None, self.changes_table, "changes"

    def _refresh_edit_actions(self, *_args) -> None:
        if not hasattr(self, "action_copy_selection"):
            return
        table = self._active_table()
        has_data = table.rowCount() > 0 and table.columnCount() > 0
        has_selection = bool(table.selectedIndexes())
        editable = has_data and table.editable
        tab_index = self.tabs.currentIndex()
        calibration_active = tab_index in (0, 1)
        self.action_copy_selection.setEnabled(has_selection)
        self.action_copy_table.setEnabled(has_data)
        self.action_paste.setEnabled(editable)
        for action in (
            self.action_fine_decrease,
            self.action_fine_increase,
            self.action_coarse_decrease,
            self.action_coarse_increase,
            self.action_set_value,
            self.action_add,
            self.action_subtract,
            self.action_multiply,
            self.action_divide,
            self.action_increase_percent,
            self.action_decrease_percent,
            self.action_interpolate_selection,
        ):
            action.setEnabled(editable and has_selection)
        self.copy_button.setEnabled(has_data)
        self.paste_button.setEnabled(editable)
        self.adjust_button.setEnabled(editable)
        self.anomaly_button.setEnabled(calibration_active and has_data)
        self.repair_button.setEnabled(calibration_active and editable and has_selection)
        self.smooth_button.setEnabled(calibration_active and editable)
        history = (
            self._source_history
            if tab_index == 0
            else self._result_history if tab_index == 1 else None
        )
        self.undo_button.setEnabled(bool(history is not None and history.can_undo))
        self.redo_button.setEnabled(bool(history is not None and history.can_redo))
        if tab_index == 2:
            self.edit_hint.setText("Read-only difference · copy is available")
        elif editable:
            self.edit_hint.setText("Local edits · double-click or type a value")
        else:
            self.edit_hint.setText("Read-only calibration")

    def copy_active_selection(self) -> bool:
        dimension = "3D" if self.table.shape()[1] > 1 else "1D"
        text = self._active_table().copy_selection_text(dimension)
        if not text:
            self.status_label.setText("Select one or more cells to copy.")
            return False
        QApplication.clipboard().setText(text)
        self.status_label.setText("Copied the selected Studio cells.")
        return True

    def copy_active_table(self) -> bool:
        table_type = str(self.table.definition.type).upper()
        if table_type not in {"1D", "2D", "3D"}:
            table_type = "3D" if self.snapshot.kind == "map" else "1D"
        active = self._active_table()
        if active.rowCount() < 1 or active.columnCount() < 1:
            return False
        QApplication.clipboard().setText(active.copy_table_text(table_type))
        self.status_label.setText("Copied the complete active Studio table.")
        return True

    def paste_active(self) -> bool:
        active = self._active_table()
        before = active.values()
        count = active.paste_values_text(QApplication.clipboard().text())
        if count < 1:
            self.status_label.setText(
                "Nothing was pasted. Select an editable Source or Result destination."
            )
            return False
        if np.array_equal(before, active.values()):
            # A synchronous document validator may reject and restore the edit while
            # ArrayTableWidget is still finishing its paste selection bookkeeping.
            # Preserve that useful validation error instead of replacing it with a
            # contradictory success message.
            if self.status_chip.text() != "CHECK INPUT":
                self.status_label.setText("The pasted values did not change the local table.")
            return False
        self.status_label.setText(f"Pasted {count} cells into the local Studio table.")
        return True

    def _step_active(self, *, coarse: bool, sign: int) -> bool:
        active = self._active_table()
        columns = max(1, active.columnCount())

        def step(value: float, row: int, column: int) -> float:
            cell = self.table.cells[row * columns + column]
            scale = cell.scale
            amount = scale.coarse_increment if coarse else scale.fine_increment
            target = value + sign * amount
            before_raw = int(round(scale.to_raw(value)))
            after_raw = int(round(scale.to_raw(target)))
            if before_raw == after_raw and amount != 0:
                slope = scale.to_real(1) - scale.to_real(0)
                raw_direction = sign if slope >= 0 else -sign
                candidate = before_raw + raw_direction
                if candidate < cell.storage_min or candidate > cell.storage_max:
                    return value
                return float(scale.to_real(candidate))
            after_raw = max(cell.storage_min, min(cell.storage_max, after_raw))
            return float(scale.to_real(after_raw))

        changed = active.transform_selected(step)
        if not changed:
            self.status_label.setText("The selected cells are already at that storage limit.")
        return changed

    def _prompt_adjustment(self, operation: str) -> bool:
        active = self._active_table()
        defaults = {
            "set": 0.0,
            "add": 0.0,
            "subtract": 0.0,
            "multiply": 1.0,
            "divide": 1.0,
            "increase_percent": 1.0,
            "decrease_percent": 1.0,
        }
        labels = {
            "set": "Set selected cells to:",
            "add": "Add to selected cells:",
            "subtract": "Subtract from selected cells:",
            "multiply": "Multiply selected cells by:",
            "divide": "Divide selected cells by:",
            "increase_percent": "Increase selected cells by percent:",
            "decrease_percent": "Decrease selected cells by percent:",
        }
        value, accepted = QInputDialog.getDouble(
            self,
            "Adjust selected cells",
            labels[operation],
            defaults[operation],
            -1.0e12,
            1.0e12,
            8,
        )
        if not accepted:
            return False
        if operation == "divide" and value == 0:
            return self._error("Division by zero is not allowed.")
        if operation == "set":
            changed = active.set_selected_value(value)
        else:
            transforms = {
                "add": lambda current, _row, _column: current + value,
                "subtract": lambda current, _row, _column: current - value,
                "multiply": lambda current, _row, _column: current * value,
                "divide": lambda current, _row, _column: current / value,
                "increase_percent": lambda current, _row, _column: current
                * (1.0 + value / 100.0),
                "decrease_percent": lambda current, _row, _column: current
                * (1.0 - value / 100.0),
            }
            changed = active.transform_selected(transforms[operation])
        if not changed:
            self.status_label.setText("The adjustment did not change any selected value.")
        return changed

    def _interpolate_active(self) -> bool:
        changed = self._active_table().interpolate_selected()
        if not changed:
            self.status_label.setText(
                "Interpolation needs a contiguous row, column, or rectangle with endpoints."
            )
        return changed

    def apply_display_settings(self, settings) -> None:
        """Apply the same palette and numeric metrics used by ordinary table editors."""
        self._display_settings = settings
        self.set_colormap(getattr(settings, "colormap", "rainbow"))
        for table in (self.source_table, self.result_table, self.changes_table):
            table.configure_display(
                font_size=int(getattr(settings, "font_size", 11)),
                density=str(getattr(settings, "table_density", "normal")),
                color_cells=bool(getattr(settings, "color_cells", True)),
            )
        self.splitter.updateGeometry()
        self.updateGeometry()

    def set_colormap(self, name: str) -> None:
        for table in (self.source_table, self.result_table, self.changes_table):
            table.set_colormap(name)
        self._colormap = self.source_table.colormap
        for legend in (self.source_legend, self.result_legend, self.changes_legend):
            legend.refresh()

    def refresh_theme(self) -> None:
        for table in (self.source_table, self.result_table, self.changes_table):
            table.refresh_colors()
        for legend in (self.source_legend, self.result_legend, self.changes_legend):
            legend.refresh()

    def _active_tab_changed(self, index: int) -> None:
        titles = ("Source calibration", "Generated result", "Changes from opening table")
        subtitles = (
            "Select a contiguous region to define the interpolation source.",
            "ROM-quantized preview on the opening table's complete grid.",
            "Signed difference between the generated result and opening table.",
        )
        states = ("SOURCE", "RESULT", "DELTA")
        if 0 <= index < len(titles):
            self.panel_title_label.setText(titles[index])
            self.panel_subtitle_label.setText(subtitles[index])
            state = states[index]
            kind = "neutral"
            if index == 1:
                mask = None
                if self.result is not None:
                    mask = self.result.extrapolated_mask
                elif self.curve_result is not None:
                    mask = self.curve_result.extrapolated_mask
                extrapolated = int(np.count_nonzero(mask)) if mask is not None else 0
                if extrapolated:
                    state = f"RESULT · {extrapolated} OUTSIDE"
                    kind = "warn"
                else:
                    kind = "ok"
            self.panel_state_chip.setText(state)
            self.panel_state_chip.set_kind(kind)
            (self.source_legend, self.result_legend, self.changes_legend)[index].refresh()
            self._refresh_edit_actions()

    def _source_selection_changed(self) -> None:
        indexes = self.source_table.selectedIndexes()
        if not indexes:
            if self.source_region is None:
                self.source_summary_label.setText("NO SOURCE CAPTURED")
                self.source_summary_label.set_kind("neutral")
            return
        rows = {index.row() for index in indexes}
        columns = {index.column() for index in indexes}
        if self.snapshot.kind == "curve":
            text = f"{len(columns)} POINTS SELECTED"
        else:
            text = f"{len(columns)} × {len(rows)} SELECTED"
        if self.source_summary_label.text() != text:
            self.source_summary_label.setText(text)
        if self.source_summary_label.property("chipKind") != "accent":
            self.source_summary_label.set_kind("accent")

    @staticmethod
    def _number_box() -> QDoubleSpinBox:
        box = QDoubleSpinBox()
        box.setDecimals(8)
        box.setRange(-1.0e12, 1.0e12)
        box.setSingleStep(1.0)
        return box

    def _load_snapshot(self, initial_selection=None) -> None:
        self.snapshot = snapshot_table(self.table)
        self._status_before_stale = None
        decimals = self.table.cells[0].scale.decimals()
        if self.snapshot.kind == "map":
            self.source_data = self.snapshot.as_map()
            self.curve_source = None
            self._source_history = UndoHistory(_calibration_equal)
            self._source_history.reset(self.source_data)
            self.source_table.set_values(
                self.source_data.z,
                x=self.source_data.x,
                y=self.source_data.y,
                editable=not self.snapshot.locked,
                decimals=decimals,
            )
            self.method_combo.clear()
            self.method_combo.addItem("Bilinear", "bilinear")
            self.method_combo.addItem("Shape-preserving PCHIP", "pchip")
            self.smooth_button.setText("Smooth table")
            self.target_count_label.setText(
                f"OPENING GRID · {self.source_data.columns} × {self.source_data.rows} CELLS"
            )
            self.expand_button.setText(
                "Expand Region to Destination "
                f"({self.source_data.columns} × {self.source_data.rows})"
            )
            self._set_axis_fields(self.source_data.x, self.source_data.y)
        else:
            self.curve_source = self.snapshot.as_curve()
            self.source_data = None
            self._source_history = UndoHistory(_calibration_equal)
            self._source_history.reset(self.curve_source)
            self.source_table.set_values(
                self.curve_source.values,
                x=self.curve_source.x,
                editable=not self.snapshot.locked,
                decimals=decimals,
            )
            self.method_combo.clear()
            self.method_combo.addItem("Linear", "linear")
            self.method_combo.addItem("Shape-preserving PCHIP", "pchip")
            self.smooth_button.setText("Smooth curve")
            self.target_count_label.setText(f"OPENING GRID · {self.curve_source.size} POINTS")
            self.expand_button.setText(
                f"Expand Region to Destination ({self.curve_source.size} Points)"
            )
            self._set_axis_fields(self.curve_source.x, None)
        self._configure_axis_inputs()
        self._set_curve_controls(self.snapshot.kind == "curve")
        self._target_mode_changed()
        self.source_region = self._region_from_logical_selection(initial_selection)
        if self.source_region is not None:
            self._select_source_region()
        self._clear_result()
        self._stale = False
        if self.source_region is None:
            self.source_summary_label.setText("NO SOURCE CAPTURED")
            self.source_summary_label.set_kind("neutral")
        self.status_label.setText("Select a source region or use Detect Padding.")
        self.status_chip.setText("READY")
        self.status_chip.set_kind("neutral")
        self.source_legend.refresh()
        self.splitter.updateGeometry()
        self.updateGeometry()
        self.refresh_stale_state()

    def _set_axis_fields(self, x: np.ndarray, y: np.ndarray | None) -> None:
        self._set_automatic_bounds(
            (float(x[0]), float(x[-1])),
            None if y is None else (float(y[0]), float(y[-1])),
        )
        self.target_x_text.setPlainText(", ".join(f"{value:.17g}" for value in x))
        if y is not None:
            self.target_y_text.setPlainText(", ".join(f"{value:.17g}" for value in y))

    def _set_automatic_bounds(
        self,
        x: tuple[float, float],
        y: tuple[float, float] | None,
    ) -> None:
        self._automatic_x_bounds = x
        self._automatic_y_bounds = y
        previous = self._updating_axis_fields
        self._updating_axis_fields = True
        try:
            self.x_min.setValue(x[0])
            self.x_max.setValue(x[1])
            if y is not None:
                self.y_min.setValue(y[0])
                self.y_max.setValue(y[1])
        finally:
            self._updating_axis_fields = previous

    def _automatic_bound_edited(self, axis: str, endpoint: int, value: float) -> None:
        if self._updating_axis_fields:
            return
        if axis == "x":
            bounds = list(self._automatic_x_bounds)
            bounds[endpoint] = float(value)
            self._automatic_x_bounds = bounds[0], bounds[1]
            self._resampling_settings_changed()
            return
        if self._automatic_y_bounds is None:
            return
        bounds = list(self._automatic_y_bounds)
        bounds[endpoint] = float(value)
        self._automatic_y_bounds = bounds[0], bounds[1]
        self._resampling_settings_changed()

    def _configure_axis_inputs(self) -> None:
        sx, _sy = self.table.shape()
        x_axis = self.table.x_axis
        if self.snapshot.kind == "curve" and sx == 1 and self.table.y_axis is not None:
            x_axis = self.table.y_axis

        def project(axis, boxes: tuple[QDoubleSpinBox, QDoubleSpinBox], label: QLabel) -> None:
            if axis is None or not axis.cells:
                return
            scale = axis.cells[0].scale
            for box in boxes:
                box.setDecimals(scale.decimals())
                box.setSingleStep(scale.fine_increment)
            detail = f"{axis.name} ({scale.units})" if scale.units else axis.name
            label.setToolTip(detail)

        previous = self._updating_axis_fields
        self._updating_axis_fields = True
        try:
            project(x_axis, (self.x_min, self.x_max), self.x_min_label)
            project(self.table.y_axis, (self.y_min, self.y_max), self.y_min_label)
        finally:
            self._updating_axis_fields = previous

    def _set_curve_controls(self, curve: bool) -> None:
        for widget in (self.y_min, self.y_max, self.y_min_label):
            widget.setVisible(not curve)
        self.target_y_label.setVisible(not curve and self.target_mode.currentData() == "custom")
        self.target_y_text.setVisible(not curve and self.target_mode.currentData() == "custom")
        self.expand_button.setEnabled(
            not self.snapshot.locked
            and self.snapshot.x_editable
            and (curve or self.snapshot.y_editable)
        )

    def _target_mode_changed(self, *_args) -> None:
        custom = self.target_mode.currentData() == "custom"
        self.target_stack.setCurrentIndex(1 if custom else 0)
        self.target_stack.updateGeometry()
        self.target_x_text.setVisible(custom)
        self.target_x_label.setVisible(custom)
        self.target_y_text.setVisible(custom and self.snapshot.kind == "map")
        self.target_y_label.setVisible(custom and self.snapshot.kind == "map")

    def _boundary_changed(self, *_args) -> None:
        visible = self.boundary_combo.currentData() == "linear"
        self.edge_limit.setVisible(visible)
        if self.edge_limit_label is not None:
            self.edge_limit_label.setVisible(visible)

    def _resampling_settings_changed(self, *_args) -> None:
        if self.result is None and self.curve_result is None:
            return
        self._clear_result()
        self.status_label.setText(
            "Resampling settings changed. Generate a new preview."
        )
        self.status_chip.setText("REGENERATE")
        self.status_chip.set_kind("warn")

    def _region_from_logical_selection(self, selection):
        if not selection:
            return None
        sx, _sy = self.table.shape()
        if self.snapshot.kind == "curve":
            offsets = sorted({int(y) * sx + int(x) for x, y in selection})
            if len(offsets) >= 2 and offsets == list(range(offsets[0], offsets[-1] + 1)):
                return offsets[0], offsets[-1]
            return None
        rows = sorted({int(y) for x, y in selection})
        columns = sorted({int(x) for x, y in selection})
        if len(selection) != len(rows) * len(columns):
            return None
        if rows != list(range(rows[0], rows[-1] + 1)) or columns != list(
            range(columns[0], columns[-1] + 1)
        ):
            return None
        if len(rows) < 2 or len(columns) < 2:
            return None
        return rows[0], rows[-1], columns[0], columns[-1]

    def _curve_source_region(self) -> tuple[int, int]:
        region = self.source_region
        if region is None or len(region) != 2:
            raise MapValidationError("No valid curve source region is selected.")
        return region

    def _map_source_region(self) -> tuple[int, int, int, int]:
        region = self.source_region
        if region is None or len(region) != 4:
            raise MapValidationError("No valid map source region is selected.")
        return region

    def _select_source_region(self) -> None:
        if self.snapshot.kind == "curve":
            start, stop = self._curve_source_region()
            self.source_table.select_rectangle(0, 0, start, stop)
        else:
            row0, row1, column0, column1 = self._map_source_region()
            self.source_table.select_rectangle(row0, row1, column0, column1)

    def _replace_source_region(
        self,
        region: tuple[int, int] | tuple[int, int, int, int],
    ) -> bool:
        previous = self.source_region
        self.source_region = region
        if previous == region or (self.result is None and self.curve_result is None):
            return False
        self._clear_result()
        return True

    def capture_source_region(self) -> bool:
        indexes = self.source_table.selectedIndexes()
        if self.snapshot.kind == "curve":
            columns = sorted({index.column() for index in indexes})
            if len(columns) < 2 or columns != list(range(columns[0], columns[-1] + 1)):
                return self._error("Select at least two contiguous curve points.")
            invalidated = self._replace_source_region((columns[0], columns[-1]))
        else:
            rows = sorted({index.row() for index in indexes})
            columns = sorted({index.column() for index in indexes})
            if (
                len(rows) < 2
                or len(columns) < 2
                or len(indexes) != len(rows) * len(columns)
                or rows != list(range(rows[0], rows[-1] + 1))
                or columns != list(range(columns[0], columns[-1] + 1))
            ):
                return self._error("Select one contiguous rectangle with at least 2 × 2 cells.")
            invalidated = self._replace_source_region(
                (rows[0], rows[-1], columns[0], columns[-1])
            )
        self.status_label.setText(
            "Source region changed. Generate a new preview."
            if invalidated
            else "Source region captured. The opening table remains the destination."
        )
        self.source_summary_label.setText(self._source_region_text())
        self.source_summary_label.set_kind("ok")
        self.status_chip.setText("REGENERATE" if invalidated else "SOURCE SET")
        self.status_chip.set_kind("warn" if invalidated else "ok")
        return True

    def detect_active_region(self) -> bool:
        try:
            region: tuple[int, int] | tuple[int, int, int, int]
            if self.snapshot.kind == "curve":
                assert self.curve_source is not None
                collapse_duplicate_curve(self.curve_source)
                x_keep = np.r_[0, np.flatnonzero(np.diff(self.curve_source.x) != 0) + 1]
                if not np.array_equal(x_keep, np.arange(x_keep.size)):
                    raise MapValidationError(
                        "Automatic detection supports consecutive trailing padding; select the "
                        "active region manually for this axis layout."
                    )
                region = (0, int(x_keep[-1]))
            else:
                assert self.source_data is not None
                collapse_duplicate_map(self.source_data)
                x_keep = np.r_[0, np.flatnonzero(np.diff(self.source_data.x) != 0) + 1]
                y_keep = np.r_[0, np.flatnonzero(np.diff(self.source_data.y) != 0) + 1]
                if not np.array_equal(x_keep, np.arange(x_keep.size)) or not np.array_equal(
                    y_keep, np.arange(y_keep.size)
                ):
                    raise MapValidationError(
                        "Automatic detection supports consecutive trailing padding; select the "
                        "active region manually for this axis layout."
                    )
                region = (0, int(y_keep[-1]), 0, int(x_keep[-1]))
            invalidated = self._replace_source_region(region)
            self._select_source_region()
            self.status_label.setText(
                "Source region changed. Generate a new preview."
                if invalidated
                else "Detected the unique non-padded source region."
            )
            self.source_summary_label.setText(self._source_region_text())
            self.source_summary_label.set_kind("ok")
            self.status_chip.setText("REGENERATE" if invalidated else "SOURCE SET")
            self.status_chip.set_kind("warn" if invalidated else "ok")
            return True
        except MapValidationError as exc:
            return self._error(str(exc))

    def _source_region_text(self) -> str:
        if self.source_region is None:
            return "NO SOURCE CAPTURED"
        if self.snapshot.kind == "curve":
            start, stop = self._curve_source_region()
            return f"SOURCE · {stop - start + 1} POINTS"
        row0, row1, column0, column1 = self._map_source_region()
        return f"SOURCE · {column1 - column0 + 1} × {row1 - row0 + 1}"

    def expand_to_full_grid(self) -> None:
        if self.source_table.selectedIndexes() and not self.capture_source_region():
            return
        if self.source_region is None and not self.detect_active_region():
            return
        if not self.expand_button.isEnabled():
            self._error("The destination axes are read-only; full-grid expansion is unavailable.")
            return
        if self.snapshot.kind == "curve":
            assert self.curve_source is not None
            start, stop = self._curve_source_region()
            x = self.curve_source.x[start : stop + 1]
            self._set_automatic_bounds((float(x[0]), float(x[-1])), None)
        else:
            assert self.source_data is not None
            row0, row1, column0, column1 = self._map_source_region()
            x = self.source_data.x[column0 : column1 + 1]
            y = self.source_data.y[row0 : row1 + 1]
            self._set_automatic_bounds(
                (float(x[0]), float(x[-1])),
                (float(y[0]), float(y[-1])),
            )
        self.target_mode.setCurrentIndex(0)
        self.generate_result()

    @staticmethod
    def _parse_axis(text: str, count: int, name: str) -> np.ndarray:
        tokens = [token for token in re.split(r"[,;\s]+", text.strip()) if token]
        try:
            values = np.asarray([float(token) for token in tokens], dtype=float)
        except ValueError as exc:
            raise MapValidationError(f"{name} contains an invalid number.") from exc
        if values.size != count:
            raise MapValidationError(f"{name} needs exactly {count} values.")
        return values

    def _target_axes(self) -> tuple[np.ndarray, np.ndarray | None]:
        x_count = self.snapshot.x.size
        y_count = None if self.snapshot.y is None else self.snapshot.y.size
        if self.target_mode.currentData() == "custom":
            x = self._parse_axis(self.target_x_text.toPlainText(), x_count, "Target X")
            y = (
                None
                if y_count is None
                else self._parse_axis(self.target_y_text.toPlainText(), y_count, "Target Y")
            )
        else:
            x = even_axis(*self._automatic_x_bounds, x_count)
            if y_count is None:
                y = None
            else:
                assert self._automatic_y_bounds is not None
                y = even_axis(*self._automatic_y_bounds, y_count)
        if not self.snapshot.x_editable:
            x = self.snapshot.x.copy()
        if y is not None and not self.snapshot.y_editable:
            assert self.snapshot.y is not None
            y = self.snapshot.y.copy()
        axis_preview = quantize_table_proposal(
            self.table,
            self.snapshot.values,
            x=x if self.snapshot.x_editable else None,
            y=y if y is not None and self.snapshot.y_editable else None,
        )
        return (
            x if axis_preview.x is None else axis_preview.x,
            y if axis_preview.y is None else axis_preview.y,
        )

    def _selected_source(self) -> CalibrationData:
        if self.source_region is None:
            if not self.detect_active_region():
                raise MapValidationError("No valid source region is selected.")
        if self.snapshot.kind == "curve":
            assert self.curve_source is not None
            start, stop = self._curve_source_region()
            return CurveData(
                self.curve_source.x[start : stop + 1],
                self.curve_source.values[start : stop + 1],
                self.curve_source.name,
            )
        assert self.source_data is not None
        row0, row1, column0, column1 = self._map_source_region()
        return MapData(
            self.source_data.x[column0 : column1 + 1],
            self.source_data.y[row0 : row1 + 1],
            self.source_data.z[row0 : row1 + 1, column0 : column1 + 1],
            self.source_data.name,
        )

    def generate_result(self) -> None:
        try:
            source = self._selected_source()
            target_x, target_y = self._target_axes()
            method = str(self.method_combo.currentData())
            boundary = str(self.boundary_combo.currentData())
            if isinstance(source, MapData):
                assert target_y is not None
                generated = resample_map(
                    source,
                    target_x,
                    target_y,
                    method=method,
                    boundary=boundary,
                    edge_limit=self.edge_limit.value(),
                )
                proposal = quantize_table_proposal(
                    self.table,
                    generated.map_data.z,
                    x=generated.map_data.x if self.snapshot.x_editable else None,
                    y=generated.map_data.y if self.snapshot.y_editable else None,
                )
                reference_proposal = quantize_table_proposal(
                    self.table,
                    generated.bilinear_reference.z,
                    x=generated.map_data.x if self.snapshot.x_editable else None,
                    y=generated.map_data.y if self.snapshot.y_editable else None,
                )
                qx = generated.map_data.x if proposal.x is None else proposal.x
                qy = generated.map_data.y if proposal.y is None else proposal.y
                result_map = MapData(qx, qy, proposal.values, generated.map_data.name)
                map_reference = MapData(
                    qx, qy, reference_proposal.values, generated.bilinear_reference.name
                )
                self.result = ResampleResult(
                    result_map,
                    generated.extrapolated_mask,
                    map_reference,
                    MapData(
                        qx,
                        qy,
                        result_map.z - map_reference.z,
                        "Difference vs bilinear",
                    ),
                    method,
                    boundary,
                )
                self.curve_result = None
                self._result_history = UndoHistory(_calibration_equal)
                self._result_history.reset(result_map)
            else:
                generated_curve = resample_curve(
                    source,
                    target_x,
                    method=method,
                    boundary=boundary,
                    edge_limit=self.edge_limit.value(),
                )
                proposal = quantize_table_proposal(
                    self.table,
                    generated_curve.curve_data.values,
                    x=generated_curve.curve_data.x if self.snapshot.x_editable else None,
                )
                reference_proposal = quantize_table_proposal(
                    self.table,
                    generated_curve.linear_reference.values,
                    x=generated_curve.curve_data.x if self.snapshot.x_editable else None,
                )
                qx = generated_curve.curve_data.x if proposal.x is None else proposal.x
                result_curve = CurveData(qx, proposal.values, generated_curve.curve_data.name)
                curve_reference = CurveData(
                    qx, reference_proposal.values, "Linear reference"
                )
                self.curve_result = CurveResampleResult(
                    result_curve,
                    generated_curve.extrapolated_mask,
                    curve_reference,
                    CurveData(
                        qx,
                        result_curve.values - curve_reference.values,
                        "Difference vs linear",
                    ),
                    method,
                    boundary,
                )
                self.result = None
                self._result_history = UndoHistory(_calibration_equal)
                self._result_history.reset(result_curve)
            self._result_source = source
            self._show_result()
            self.tabs.setCurrentIndex(1)
            self.status_label.setText(
                "Preview generated. Amber outlines mark extrapolated values."
            )
            self.status_chip.setText("PREVIEW")
            self.status_chip.set_kind("accent")
            self.generate_button.setText("Refresh Preview")
            self.refresh_stale_state()
        except (MapValidationError, ValueError) as exc:
            self._error(str(exc))

    def _show_result(self) -> None:
        decimals = self.table.cells[0].scale.decimals()
        if self.result is not None:
            self.result_table.set_values(
                self.result.map_data.z,
                x=self.result.map_data.x,
                y=self.result.map_data.y,
                editable=not self.snapshot.locked,
                decimals=decimals,
                mask=self.result.extrapolated_mask,
            )
            changes = self.result.map_data.z - self.snapshot.values
            self.changes_table.set_values(
                changes,
                x=self.result.map_data.x,
                y=self.result.map_data.y,
                decimals=decimals,
                difference=True,
                mask=self.result.extrapolated_mask,
            )
        elif self.curve_result is not None:
            self.result_table.set_values(
                self.curve_result.curve_data.values,
                x=self.curve_result.curve_data.x,
                editable=not self.snapshot.locked,
                decimals=decimals,
                mask=self.curve_result.extrapolated_mask.reshape(1, -1),
            )
            changes = self.curve_result.curve_data.values - self.snapshot.values.reshape(-1)
            self.changes_table.set_values(
                changes,
                x=self.curve_result.curve_data.x,
                decimals=decimals,
                difference=True,
                mask=self.curve_result.extrapolated_mask.reshape(1, -1),
            )
        enabled = self.result is not None or self.curve_result is not None
        self.result_legend.refresh()
        self.changes_legend.refresh()
        self.tabs.setTabEnabled(1, enabled)
        self.tabs.setTabEnabled(2, enabled)
        self.safety_button.setEnabled(enabled)
        self.visualize_button.setEnabled(True)
        self.apply_button.setEnabled(enabled and not self.snapshot.locked and not self._stale)
        self._active_tab_changed(self.tabs.currentIndex())

    def _clear_result(self) -> None:
        for dialog in tuple(self._visual_windows):
            if getattr(dialog, "_table", None) not in (
                self.result_table,
                self.changes_table,
            ):
                continue
            dialog.close()
            if dialog in self._visual_windows:
                self._remove_visual(dialog)
            dialog.deleteLater()
        self.result = None
        self.curve_result = None
        self._result_source = None
        self.result_table.setRowCount(0)
        self.result_table.setColumnCount(0)
        self.changes_table.setRowCount(0)
        self.changes_table.setColumnCount(0)
        self.tabs.setTabEnabled(1, False)
        self.tabs.setTabEnabled(2, False)
        self.safety_button.setEnabled(False)
        self.apply_button.setEnabled(False)
        self.generate_button.setText("Generate Preview")
        self._result_history.clear()
        self.result_legend.refresh()
        self.changes_legend.refresh()

    def _source_edited(self) -> None:
        if self._updating_table:
            return
        selection = self.source_table.selection_mask()
        try:
            values = self.source_table.values()
            proposal = quantize_table_proposal(self.table, values)
            if self.snapshot.kind == "map":
                assert self.source_data is not None
                synchronized = collapse_duplicate_map(self.source_data).synchronize_values(
                    proposal.values
                )
                # Mirrored physical padding cells are part of the same logical
                # calibration bin. Quantize the complete synchronized proposal once
                # more so every physical destination cell is storage-valid.
                proposal = quantize_table_proposal(self.table, synchronized)
                self.source_data = MapData(
                    self.source_data.x, self.source_data.y, proposal.values, self.source_data.name
                )
                self._source_history.record(self.source_data)
            else:
                assert self.curve_source is not None
                synchronized = collapse_duplicate_curve(
                    self.curve_source
                ).synchronize_values(np.asarray(proposal.values, dtype=float).reshape(-1))
                proposal = quantize_table_proposal(self.table, synchronized)
                self.curve_source = CurveData(
                    self.curve_source.x, proposal.values, self.curve_source.name
                )
                self._source_history.record(self.curve_source)
            self._show_source()
            if np.any(selection) and selection.shape == (
                self.source_table.rowCount(),
                self.source_table.columnCount(),
            ):
                self.source_table.select_mask(selection)
            self._clear_result()
            self.status_label.setText("Source changed locally; regenerate the result.")
        except (MapValidationError, ValueError) as exc:
            self._error(str(exc))
            self._show_source()

    def _result_edited(self) -> None:
        if self._updating_table:
            return
        try:
            values = self.result_table.values()
            current: CalibrationData
            if self.result is not None:
                proposal = quantize_table_proposal(self.table, values)
                synchronized = collapse_duplicate_map(
                    self.result.map_data
                ).synchronize_values(proposal.values)
                proposal = quantize_table_proposal(self.table, synchronized)
                current = MapData(
                    self.result.map_data.x,
                    self.result.map_data.y,
                    proposal.values,
                    self.result.map_data.name,
                )
            elif self.curve_result is not None:
                proposal = quantize_table_proposal(self.table, values)
                synchronized = collapse_duplicate_curve(
                    self.curve_result.curve_data
                ).synchronize_values(np.asarray(proposal.values, dtype=float).reshape(-1))
                proposal = quantize_table_proposal(self.table, synchronized)
                current = CurveData(
                    self.curve_result.curve_data.x,
                    proposal.values,
                    self.curve_result.curve_data.name,
                )
            else:
                return
            self._install_result_state(current, record=True)
            self._sync_result_tables()
            self.status_label.setText("Result edit quantized to the destination storage scale.")
        except (MapValidationError, ValueError) as exc:
            self._error(str(exc))
            self._sync_result_tables()

    def _show_source(self) -> None:
        self._updating_table = True
        try:
            if self.source_data is not None:
                self.source_table.update_values(self.source_data.z)
            else:
                assert self.curve_source is not None
                self.source_table.update_values(self.curve_source.values)
            self.source_legend.refresh()
        finally:
            self._updating_table = False

    def undo_local(self) -> None:
        index = self.tabs.currentIndex()
        if index == 2:
            return
        if index == 0:
            state = self._source_history.undo()
            if state is None:
                return
            if isinstance(state, MapData):
                self.source_data = state
            else:
                self.curve_source = state
            self._show_source()
            self._clear_result()
        else:
            state = self._result_history.undo()
            if state is None:
                return
            self._restore_result_state(state)
        self._refresh_edit_actions()

    def redo_local(self) -> None:
        index = self.tabs.currentIndex()
        if index == 2:
            return
        if index == 0:
            state = self._source_history.redo()
            if state is None:
                return
            if isinstance(state, MapData):
                self.source_data = state
            else:
                self.curve_source = state
            self._show_source()
            self._clear_result()
        else:
            state = self._result_history.redo()
            if state is not None:
                self._restore_result_state(state)
        self._refresh_edit_actions()

    def _restore_result_state(self, state) -> None:
        self._install_result_state(state, record=False)
        self._sync_result_tables()

    def _install_result_state(self, state, *, record: bool) -> None:
        """Replace the Result model while preserving its resampling provenance."""
        if isinstance(state, MapData) and self.result is not None:
            previous = self.result
            self.result = ResampleResult(
                state,
                previous.extrapolated_mask,
                previous.bilinear_reference,
                MapData(
                    state.x,
                    state.y,
                    state.z - previous.bilinear_reference.z,
                    "Difference vs bilinear",
                ),
                previous.method,
                previous.boundary,
            )
        elif isinstance(state, CurveData) and self.curve_result is not None:
            previous_curve = self.curve_result
            self.curve_result = CurveResampleResult(
                state,
                previous_curve.extrapolated_mask,
                previous_curve.linear_reference,
                CurveData(
                    state.x,
                    state.values - previous_curve.linear_reference.values,
                    "Difference vs linear",
                ),
                previous_curve.method,
                previous_curve.boundary,
            )
        else:
            raise MapValidationError("Result history does not match the active calibration.")
        if record:
            self._result_history.record(state)

    def _sync_result_tables(self) -> None:
        """Update Result and Changes in place so local edits remain responsive."""
        self._updating_table = True
        try:
            if self.result is not None:
                values = self.result.map_data.z
                changes = values - self.snapshot.values
                self.result_table.update_values(values, mask=self.result.extrapolated_mask)
                self.changes_table.update_values(
                    changes, mask=self.result.extrapolated_mask
                )
            elif self.curve_result is not None:
                values = self.curve_result.curve_data.values
                changes = values - self.snapshot.values.reshape(-1)
                self.result_table.update_values(
                    values,
                    mask=self.curve_result.extrapolated_mask.reshape(1, -1),
                )
                self.changes_table.update_values(
                    changes,
                    mask=self.curve_result.extrapolated_mask.reshape(1, -1),
                )
        finally:
            self._updating_table = False
        self.result_legend.refresh()
        self.changes_legend.refresh()
        self._active_tab_changed(self.tabs.currentIndex())

    def detect_active_anomalies(self) -> None:
        try:
            before, table, role = self._active_calibration()
            if role == "changes" or before is None:
                return
            if isinstance(before, MapData):
                anomaly_mask = detect_anomalies(before).mask
            else:
                anomaly_mask = detect_curve_anomalies(before).mask
            mask = anomaly_mask if anomaly_mask.ndim == 2 else anomaly_mask.reshape(1, -1)
            table.select_mask(mask)
            self.status_label.setText(
                f"Selected {int(np.count_nonzero(anomaly_mask))} suspicious values; no data changed."
            )
            self._refresh_edit_actions()
        except MapValidationError as exc:
            self._error(str(exc))

    def repair_active_selection(self) -> None:
        try:
            before, table, role = self._active_calibration()
            if role == "changes" or before is None or not table.editable:
                return
            mask = table.selection_mask()
            proposed = (
                repair_selected_region(before, mask)
                if isinstance(before, MapData)
                else repair_curve_selection(before, mask.reshape(-1))
            )
            proposed = self._quantized_transform(proposed)
            dialog = SmoothingPreviewDialog(
                before,
                proposed,
                self,
                colormap=self._colormap,
                display_settings=self._display_settings,
                decimals=self.table.cells[0].scale.decimals(),
                operation_label="repair",
            )
            try:
                accepted = dialog.exec() == QDialog.DialogCode.Accepted
            finally:
                dialog.deleteLater()
            if not accepted:
                return
            self._accept_active_transform(proposed, role)
        except MapValidationError as exc:
            self._error(str(exc))

    def smooth_active(self) -> None:
        before, table, role = self._active_calibration()
        if role == "changes" or before is None or not table.editable:
            return
        answer = QMessageBox.warning(
            self,
            "Smooth entire calibration?",
            "Whole-table smoothing changes calibration values. Review every proposed difference.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            proposed = (
                smooth_entire_table(before)
                if isinstance(before, MapData)
                else smooth_entire_curve(before)
            )
            proposed = self._quantized_transform(proposed)
            dialog = SmoothingPreviewDialog(
                before,
                proposed,
                self,
                colormap=self._colormap,
                display_settings=self._display_settings,
                decimals=self.table.cells[0].scale.decimals(),
                operation_label="smoothing",
            )
            try:
                accepted = dialog.exec() == QDialog.DialogCode.Accepted
            finally:
                dialog.deleteLater()
            if not accepted:
                return
            self._accept_active_transform(proposed, role)
        except MapValidationError as exc:
            self._error(str(exc))

    def _quantized_transform(self, proposed):
        if isinstance(proposed, MapData):
            proposal = quantize_table_proposal(self.table, proposed.z)
            return MapData(proposed.x, proposed.y, proposal.values, proposed.name)
        proposal = quantize_table_proposal(self.table, proposed.values)
        return CurveData(proposed.x, proposal.values, proposed.name)

    def _accept_active_transform(self, proposed, role: str) -> None:
        if role == "source":
            self._accept_source_transform(proposed)
        elif role == "result":
            self._accept_result_transform(proposed)

    def _accept_source_transform(self, proposed) -> None:
        if isinstance(proposed, MapData):
            self.source_data = proposed
            self._source_history.record(self.source_data)
        else:
            self.curve_source = proposed
            self._source_history.record(self.curve_source)
        self._show_source()
        self._clear_result()
        self.status_label.setText("Applied the reviewed transformation locally; regenerate to apply.")

    def _accept_result_transform(self, proposed) -> None:
        self._install_result_state(proposed, record=True)
        self._sync_result_tables()
        self.status_label.setText("Applied the reviewed transformation to the local Result.")

    def visualize_active(self) -> None:
        index = self.tabs.currentIndex()
        map_review_labels = self._map_review_labels()
        dialog: QDialog
        if index == 2 and self.result is not None:
            map_changes = MapData(
                self.result.map_data.x,
                self.result.map_data.y,
                self.result.map_data.z - self.snapshot.values,
                "Changes from opening table",
            )
            dialog = MapReviewDialog(
                map_changes,
                "Map Studio changes review",
                self.changes_table,
                self.result.extrapolated_mask,
                self,
                colormap=self._colormap,
                **map_review_labels,
            )
        elif index == 2 and self.curve_result is not None:
            curve_changes = CurveData(
                self.curve_result.curve_data.x,
                self.curve_result.curve_data.values - self.snapshot.values.reshape(-1),
                "Changes from opening table",
            )
            dialog = CurveReviewDialog(
                curve_changes,
                "Map Studio curve changes",
                self.curve_result.extrapolated_mask,
                self,
                colormap=self._colormap,
                table=self.changes_table,
            )
        elif index == 1 and self.result is not None:
            dialog = MapReviewDialog(
                self.result.map_data,
                "Map Studio result review",
                self.result_table,
                self.result.extrapolated_mask,
                self,
                colormap=self._colormap,
                **map_review_labels,
            )
        elif index == 1 and self.curve_result is not None:
            dialog = CurveReviewDialog(
                self.curve_result.curve_data,
                "Map Studio curve result",
                self.curve_result.extrapolated_mask,
                self,
                colormap=self._colormap,
                table=self.result_table,
            )
        elif self.source_data is not None:
            dialog = MapReviewDialog(
                self.source_data,
                "Map Studio source review",
                self.source_table,
                parent=self,
                colormap=self._colormap,
                **map_review_labels,
            )
        else:
            assert self.curve_source is not None
            dialog = CurveReviewDialog(
                self.curve_source,
                "Map Studio curve source",
                parent=self,
                colormap=self._colormap,
                table=self.source_table,
            )
        self._visual_windows.append(dialog)
        dialog.finished.connect(lambda *_args, item=dialog: self._remove_visual(item))
        dialog.show()

    def _remove_visual(self, dialog) -> None:
        if dialog in self._visual_windows:
            self._visual_windows.remove(dialog)

    def show_safety_report(self) -> None:
        if self.result is not None:
            source = self._result_source
            assert isinstance(source, MapData)
            text = build_safety_report(
                source,
                self.result.map_data,
                self.result.bilinear_reference,
                self.result.extrapolated_mask,
            ).to_text()
        elif self.curve_result is not None:
            delta = self.curve_result.curve_data.values - self.snapshot.values.reshape(-1)
            text = "\n".join(
                [
                    "Map Studio curve safety report",
                    f"Changed points: {int(np.count_nonzero(delta))}",
                    f"Extrapolated points: {self.curve_result.extrapolated_points}",
                    f"Maximum absolute change: {float(np.max(np.abs(delta))):.8g}",
                    f"RMS change: {float(np.sqrt(np.mean(np.square(delta)))):.8g}",
                    "This is a numerical review aid, not an engine-safety determination.",
                ]
            )
        else:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Numerical safety report")
        layout = QVBoxLayout(dialog)
        view = QTextEdit()
        view.setReadOnly(True)
        view.setPlainText(text)
        layout.addWidget(view)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        buttons.clicked.connect(dialog.accept)
        layout.addWidget(buttons)
        dialog.resize(620, 500)
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()

    def is_stale(self) -> bool:
        return fingerprint_table(self.table) != self.snapshot.fingerprint

    def refresh_stale_state(self) -> None:
        stale = self.is_stale()
        was_stale = self._stale
        has_result = self.result is not None or self.curve_result is not None
        self._stale = stale
        self.apply_button.setEnabled(has_result and not self.snapshot.locked and not stale)
        if stale:
            if not was_stale:
                self._status_before_stale = (
                    self.status_label.text(),
                    self.status_chip.text(),
                    str(self.status_chip.property("chipKind") or "neutral"),
                )
            self.status_label.setText(
                "The opening table changed after Studio loaded it. Reload before applying."
            )
            self.status_chip.setText("STALE")
            self.status_chip.set_kind("warn")
        elif was_stale and self._status_before_stale is not None:
            label, chip, kind = self._status_before_stale
            self.status_label.setText(label)
            self.status_chip.setText(chip)
            self.status_chip.set_kind(kind)
            self._status_before_stale = None

    def handle_rom_reloaded(self) -> None:
        """Reconcile this snapshot workspace after its owning ROM reloads from disk.

        Clean Studio documents can safely adopt the new opening table immediately.  Local Source
        edits or a generated Result are preserved verbatim and merely become stale when their
        opening-table fingerprint no longer matches, so disk reload never destroys Studio work.
        """
        if self.has_local_changes():
            self.refresh_stale_state()
            return

        selection = None
        if self.source_region is not None:
            sx, _sy = self.table.shape()
            if self.snapshot.kind == "curve":
                start, stop = self._curve_source_region()
                selection = [(offset % sx, offset // sx) for offset in range(start, stop + 1)]
            else:
                row0, row1, column0, column1 = self._map_source_region()
                selection = [
                    (column, row)
                    for row in range(row0, row1 + 1)
                    for column in range(column0, column1 + 1)
                ]
        self._load_snapshot(selection)

    def request_apply(self) -> None:
        self.refresh_stale_state()
        if self._stale:
            return
        try:
            if self.result is not None:
                proposal = quantize_table_proposal(
                    self.table,
                    self.result.map_data.z,
                    x=self.result.map_data.x if self.snapshot.x_editable else None,
                    y=self.result.map_data.y if self.snapshot.y_editable else None,
                )
            elif self.curve_result is not None:
                proposal = quantize_table_proposal(
                    self.table,
                    self.curve_result.curve_data.values,
                    x=self.curve_result.curve_data.x if self.snapshot.x_editable else None,
                )
            else:
                return
            self.applyRequested.emit(proposal)
        except MapValidationError as exc:
            self._error(str(exc))

    def accept_applied(self, *, changed: bool = True) -> None:
        self._load_snapshot()
        if changed:
            self.status_label.setText("Applied to the opening table as one undoable operation.")
            self.status_chip.setText("APPLIED")
        else:
            self.status_label.setText(
                "The preview already matches the opening table; no undo operation was created."
            )
            self.status_chip.setText("NO CHANGE")
        self.status_chip.set_kind("ok")

    def has_local_changes(self) -> bool:
        if self.result is not None or self.curve_result is not None:
            return True
        if self.snapshot.kind == "map":
            return self.source_data is not None and not _map_equal(
                self.source_data, self.snapshot.as_map()
            )
        return self.curve_source is not None and not _curve_equal(
            self.curve_source, self.snapshot.as_curve()
        )

    def can_close(self) -> bool:
        if not self.has_local_changes():
            return True
        return QMessageBox.question(
            self,
            "Discard local Map Studio work?",
            "Close Map Studio and discard its unapplied result and local history?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) == QMessageBox.StandardButton.Yes

    def reload_source(self) -> None:
        if self.has_local_changes():
            answer = QMessageBox.question(
                self,
                "Discard local Map Studio work?",
                "Reloading discards local Source edits, the generated Result, and local history.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._load_snapshot()

    def _error(self, message: str) -> bool:
        self.status_label.setText(message)
        self.status_chip.setText("CHECK INPUT")
        self.status_chip.set_kind("warn")
        return False
