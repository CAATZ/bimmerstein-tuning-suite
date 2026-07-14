from __future__ import annotations
from dataclasses import replace
from PySide6.QtWidgets import (QDialog, QFormLayout, QVBoxLayout, QTabWidget, QWidget,
                               QSpinBox, QComboBox, QDialogButtonBox)
from PySide6.QtCore import Signal
from ecueditor.core.settings import EditorSettings, save_settings

class SettingsDialog(QDialog):
    settings_changed = Signal(object)     # emits an EditorSettings

    def __init__(self, settings: EditorSettings, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self._settings = settings
        lay = QVBoxLayout(self)
        tabs = QTabWidget(); lay.addWidget(tabs)

        appearance = QWidget(); form = QFormLayout(appearance)
        self.combo_theme = QComboBox(); self.combo_theme.addItems(["Dark", "Light", "System"])
        self.combo_theme.setCurrentText(settings.theme.capitalize())
        self.combo_colormap = QComboBox()
        self.combo_colormap.addItem("Viridis (perceptual)", "viridis")
        self.combo_colormap.addItem("Classic Rainbow", "rainbow")
        ix = self.combo_colormap.findData(settings.colormap)
        self.combo_colormap.setCurrentIndex(max(0, ix))
        self.combo_editor_window_size = QComboBox()
        for label, value in (("Small", "small"), ("Medium", "medium"), ("Large", "large")):
            self.combo_editor_window_size.addItem(label, value)
        ix = self.combo_editor_window_size.findData(settings.editor_window_size)
        if ix < 0:
            ix = self.combo_editor_window_size.findData("medium")
        self.combo_editor_window_size.setCurrentIndex(ix)
        self.combo_table_density = QComboBox()
        for label, value in (("Normal", "normal"), ("Compact", "compact")):
            self.combo_table_density.addItem(label, value)
        ix = self.combo_table_density.findData(settings.table_density)
        if ix < 0:
            ix = self.combo_table_density.findData("normal")
        self.combo_table_density.setCurrentIndex(ix)
        self.spin_font_size = QSpinBox(); self.spin_font_size.setRange(7, 24)
        self.spin_font_size.setValue(settings.font_size)
        self.spin_cell_width = QSpinBox(); self.spin_cell_width.setRange(8, 300)
        self.spin_cell_width.setValue(settings.cell_width)
        self.spin_cell_height = QSpinBox(); self.spin_cell_height.setRange(8, 300)
        self.spin_cell_height.setValue(settings.cell_height)
        form.addRow("Theme", self.combo_theme)
        form.addRow("Heatmap", self.combo_colormap)
        form.addRow("Editor Window Size", self.combo_editor_window_size)
        form.addRow("Table Density", self.combo_table_density)
        form.addRow("Value Font Size", self.spin_font_size)
        form.addRow("Cell Width", self.spin_cell_width)
        form.addRow("Cell Height", self.spin_cell_height)
        tabs.addTab(appearance, "Appearance")

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject); lay.addWidget(bb)

    def to_settings(self) -> EditorSettings:
        return replace(self._settings,
                       theme=self.combo_theme.currentText().lower(),
                       colormap=self.combo_colormap.currentData(),
                       editor_window_size=self.combo_editor_window_size.currentData(),
                       table_density=self.combo_table_density.currentData(),
                       font_size=self.spin_font_size.value(),
                       cell_width=self.spin_cell_width.value(),
                       cell_height=self.spin_cell_height.value())

    def accept(self) -> None:
        out = self.to_settings()
        save_settings(out)
        self.settings_changed.emit(out)
        super().accept()
