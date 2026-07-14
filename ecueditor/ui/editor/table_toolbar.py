from __future__ import annotations
from PySide6.QtWidgets import QToolBar, QComboBox, QLineEdit, QLabel
from PySide6.QtGui import QAction, QDoubleValidator, QKeySequence
from PySide6.QtCore import Qt
from ecueditor.ui.design.icons import icon
from ecueditor.ui.editor import edit_ops
from ecueditor.ui.editor.table_grid import TableGridWidget

class TableToolBar(QToolBar):
    def __init__(self, parent=None) -> None:
        super().__init__("Table", parent)
        self.setObjectName("table_toolbar")
        self._grid: TableGridWidget | None = None
        self.action_inc_fine = self.addAction("Fine +")
        self.action_dec_fine = self.addAction("Fine -")
        self.action_inc_coarse = self.addAction("Coarse +")
        self.action_dec_coarse = self.addAction("Coarse -")
        self.action_inc_coarse.setShortcut(QKeySequence("+")); self.action_dec_coarse.setShortcut(QKeySequence("_"))
        self.addSeparator()
        self.addWidget(QLabel("Set:"))
        self.set_value_edit = QLineEdit(); self.set_value_edit.setValidator(QDoubleValidator())
        self.set_value_edit.setFixedWidth(70); self.addWidget(self.set_value_edit)
        self.action_set = self.addAction("Set")
        self.addSeparator()
        self.addWidget(QLabel("Math:"))
        self.math_operand_edit = QLineEdit(); self.math_operand_edit.setValidator(QDoubleValidator())
        self.math_operand_edit.setObjectName("mathOperand")
        self.math_operand_edit.setPlaceholderText("value")
        self.math_operand_edit.setToolTip("Operand for +, −, ×, ÷, +%, and −%")
        self.math_operand_edit.setFixedWidth(58); self.addWidget(self.math_operand_edit)
        self.multiply_edit = self.math_operand_edit  # compatibility alias for the original surface
        self.action_add = self.addAction("+")
        self.action_subtract = self.addAction("−")
        self.action_multiply = self.addAction("×"); self.action_multiply.setShortcut(QKeySequence("*"))
        self.action_divide = self.addAction("÷")
        self.action_increase_percent = self.addAction("+%")
        self.action_decrease_percent = self.addAction("−%")
        self.action_add.setToolTip("Add value to selected cells")
        self.action_subtract.setToolTip("Subtract value from selected cells")
        self.action_multiply.setToolTip("Multiply selected cells (*)")
        self.action_divide.setToolTip("Divide selected cells")
        self.action_increase_percent.setToolTip("Increase selected cells by percent")
        self.action_decrease_percent.setToolTip("Decrease selected cells by percent")
        self.addSeparator()
        self.action_color = QAction("Color", self, checkable=True, checked=True)
        self.action_color.setIcon(icon("color")); self.action_color.setToolTip("Heatmap colors")
        self.addAction(self.action_color)
        self.action_enable3d = self.addAction("3D")
        self.action_enable3d.setIcon(icon("cube")); self.action_enable3d.setToolTip("3D surface")
        self.addSeparator()
        self.addWidget(QLabel("Scale:")); self.scale_combo = QComboBox(); self.addWidget(self.scale_combo)

        for act in (self.action_color, self.action_enable3d):
            btn = self.widgetForAction(act)
            if btn is not None:
                btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)

        self.action_inc_fine.triggered.connect(lambda: self._op(edit_ops.increment_fine))
        self.action_dec_fine.triggered.connect(lambda: self._op(edit_ops.decrement_fine))
        self.action_inc_coarse.triggered.connect(lambda: self._op(edit_ops.increment_coarse))
        self.action_dec_coarse.triggered.connect(lambda: self._op(edit_ops.decrement_coarse))
        self.action_set.triggered.connect(self._on_set)
        self.set_value_edit.returnPressed.connect(self._on_set)     # Enter triggers Set (fact base 1.5)
        self.action_add.triggered.connect(lambda: self._on_math(edit_ops.add))
        self.action_subtract.triggered.connect(lambda: self._on_math(edit_ops.subtract))
        self.action_multiply.triggered.connect(lambda: self._on_math(edit_ops.multiply))
        self.action_divide.triggered.connect(lambda: self._on_math(edit_ops.divide, reject_zero=True))
        self.action_increase_percent.triggered.connect(
            lambda: self._on_math(edit_ops.increase_percent))
        self.action_decrease_percent.triggered.connect(
            lambda: self._on_math(edit_ops.decrease_percent))
        self.math_operand_edit.textChanged.connect(lambda: self._set_math_invalid(False))
        self.action_color.toggled.connect(self._on_color)
        self.scale_combo.currentIndexChanged.connect(self._on_scale)

    def bind(self, grid: TableGridWidget | None) -> None:
        self._grid = grid
        self.scale_combo.blockSignals(True); self.scale_combo.clear()
        if grid is not None:
            for a in (self.action_inc_fine, self.action_dec_fine, self.action_inc_coarse,
                      self.action_dec_coarse, self.action_add, self.action_subtract,
                      self.action_multiply, self.action_divide, self.action_increase_percent,
                      self.action_decrease_percent):
                grid.addAction(a)          # +, _, and * fire when the grid has keyboard focus
            for sc in grid.model().scales:
                self.scale_combo.addItem(sc.units or "raw value")
            self.scale_combo.setCurrentIndex(grid.model()._scale_ix)
            self.action_color.setChecked(grid.model()._color_cells)
            self._refresh_increment_tooltips()
        self.scale_combo.blockSignals(False)
        self.setEnabled(grid is not None)

    def _refresh_increment_tooltips(self) -> None:
        if self._grid is None:
            return
        scale = self._grid.model().current_scale
        units = f" {scale.units}" if scale.units else ""
        fine = f"{scale.fine_increment:g}{units}"
        coarse = f"{scale.coarse_increment:g}{units}"
        self.action_inc_fine.setToolTip(
            f"Increase selected cells by fine step ({fine})",
        )
        self.action_dec_fine.setToolTip(
            f"Decrease selected cells by fine step ({fine})",
        )
        self.action_inc_coarse.setToolTip(
            f"Increase selected cells by coarse step ({coarse}). Shortcut: +",
        )
        self.action_dec_coarse.setToolTip(
            f"Decrease selected cells by coarse step ({coarse}). Shortcut: _",
        )

    def _sel(self):
        return self._grid.selected_indexes() if self._grid else []

    def _op(self, fn) -> None:
        if self._grid:
            fn(self._grid.model(), self._sel())

    def _on_set(self) -> None:
        if self._grid and self.set_value_edit.text():
            edit_ops.set_value(self._grid.model(), self._sel(), float(self.set_value_edit.text()))

    def _on_math(self, operation, *, reject_zero: bool = False) -> None:
        if not self._grid or not self.math_operand_edit.text():
            return
        operand = float(self.math_operand_edit.text())
        if reject_zero and operand == 0:
            self._set_math_invalid(True)
            self.math_operand_edit.setToolTip("Division by zero is not allowed")
            return
        self._set_math_invalid(False)
        operation(self._grid.model(), self._sel(), operand)

    def _set_math_invalid(self, invalid: bool) -> None:
        self.math_operand_edit.setProperty("invalid", invalid)
        if not invalid:
            self.math_operand_edit.setToolTip("Operand for +, −, ×, ÷, +%, and −%")
        self.math_operand_edit.style().unpolish(self.math_operand_edit)
        self.math_operand_edit.style().polish(self.math_operand_edit)

    def _on_color(self, on: bool) -> None:
        if self._grid:
            self._grid.set_color_cells(on)

    def _on_scale(self, ix: int) -> None:
        if self._grid and ix >= 0:
            self._grid.model().set_scale(ix)
            self._refresh_increment_tooltips()
