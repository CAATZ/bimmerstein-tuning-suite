"""Dedicated 1D-scalar frame (spec §5, B5): big value + steppers over hidden-grid plumbing."""
from __future__ import annotations
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                               QLineEdit)
from PySide6.QtCore import Qt, QEvent
from ecueditor.ui.design.fonts import numeric_font
from ecueditor.ui.design.theme_manager import current_theme
from ecueditor.ui.editor import edit_ops
from ecueditor.ui.editor.table_model import TableGridModel
from ecueditor.ui.editor.table_grid import TableGridWidget
from ecueditor.ui.editor.table_menubar import TableMenuBar
from ecueditor.ui.editor.frames.header import FrameHeader


class ScalarFrame(QWidget):
    def __init__(self, table, parent=None, roms_provider=None) -> None:
        super().__init__(parent)
        self.setObjectName("tableFrame")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._table = table
        self.header = FrameHeader(table.definition)
        model = TableGridModel(table)
        self.grid = TableGridWidget(model)
        self.grid.setVisible(False)                       # plumbing only
        self.grid.selectionModel().setCurrentIndex(
            model.index(0, 0), self.grid.selectionModel().SelectionFlag.SelectCurrent)
        # mockup: the scalar frame has NO verb bar -- keep the menubar constructed (for its
        # action plumbing/tests: action_undo_all etc.) but never add it to the visible layout.
        self.menubar = TableMenuBar(self.grid, parent=self, roms_provider=roms_provider)

        self._value = QLabel(); self._value.setFont(numeric_font(26))
        self._unit = QLabel(table.cells[0].scale.units)
        self._unit.setStyleSheet(f"color: {current_theme().text_dim};")
        self._edit = QLineEdit(); self._edit.setFont(numeric_font(26))
        self._edit.setVisible(False); self._edit.setMaximumWidth(180)
        self._edit.editingFinished.connect(self._commit_edit)

        scale = table.cells[0].scale
        fine, coarse_inc = scale.fine_increment, scale.coarse_increment
        row = QHBoxLayout(); row.setContentsMargins(14, 10, 14, 12); row.setSpacing(8)
        row.addWidget(self._value); row.addWidget(self._edit)
        row.addWidget(self._unit, 0, Qt.AlignmentFlag.AlignBottom)
        row.addStretch(1)
        # spec §5: the actual fine/coarse step VALUES are displayed on the steppers
        for label, coarse, sign in ((f"− {coarse_inc:g}", True, -1), (f"− {fine:g}", False, -1),
                                    (f"+ {fine:g}", False, +1), (f"+ {coarse_inc:g}", True, +1)):
            btn = QPushButton(label)
            btn.clicked.connect(lambda _c=False, c=coarse, s=sign: self.bump(coarse=c, sign=s))
            row.addWidget(btn)

        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(0)
        lay.addWidget(self.header)
        host = QWidget(); host.setLayout(row); lay.addWidget(host)
        lay.addWidget(self.grid)                          # hidden, but must be parented
        lay.addStretch(1)

        model.dataChanged.connect(lambda *_a: self._sync())
        model.modelReset.connect(self._sync)
        self._value.installEventFilter(self)
        self._sync()

    # --- behavior ---------------------------------------------------------------
    def bump(self, *, coarse: bool, sign: int) -> None:
        model = self.grid.model()
        idx = [model.index(0, 0)]
        fn = {(False, +1): edit_ops.increment_fine, (False, -1): edit_ops.decrement_fine,
              (True, +1): edit_ops.increment_coarse, (True, -1): edit_ops.decrement_coarse}[
                  (coarse, sign)]
        fn(model, idx)

    def eventFilter(self, obj, event):
        if obj is self._value and event.type() == QEvent.Type.MouseButtonDblClick:
            self._begin_edit()
            return True
        return super().eventFilter(obj, event)

    def _begin_edit(self) -> None:
        self._edit.setText(self._value.text())
        self._value.setVisible(False); self._edit.setVisible(True)
        self._edit.setFocus(); self._edit.selectAll()

    def _commit_edit(self) -> None:
        model = self.grid.model()
        try:
            model.setData(model.index(0, 0), float(self._edit.text()), Qt.ItemDataRole.EditRole)
        except ValueError:
            pass                                          # non-numeric: keep the old value
        self._edit.setVisible(False); self._value.setVisible(True)

    def _sync(self) -> None:
        cell = self._table.cell_at(0, 0)
        self._value.setText(cell.scale.format_value(cell.real()))

    def value_text(self) -> str: return self._value.text()
    def unit_text(self) -> str: return self._unit.text()
