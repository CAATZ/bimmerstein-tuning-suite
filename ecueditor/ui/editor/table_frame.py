from __future__ import annotations
from PySide6.QtWidgets import QWidget, QVBoxLayout
from ecueditor.core.rom.table import SwitchTable, BitwiseSwitchTable
from ecueditor.ui.editor.table_grid import TableGridWidget
from ecueditor.ui.editor.table_menubar import TableMenuBar

class _TablePanel(QWidget):
    def __init__(self, table, parent=None, roms_provider=None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self); lay.setContentsMargins(8, 8, 8, 8); lay.setSpacing(0)
        self.grid: TableGridWidget | None = None
        self.menubar: TableMenuBar | None = None
        self.frame: QWidget | None = None
        self.body: QWidget
        if isinstance(table, SwitchTable):
            from ecueditor.ui.editor.frames.switch_frame import SwitchFrame
            self.body = SwitchFrame(table)
            lay.addWidget(self.body)
        elif isinstance(table, BitwiseSwitchTable):
            from ecueditor.ui.editor.frames.switch_frame import BitwiseSwitchFrame
            self.body = BitwiseSwitchFrame(table)
            lay.addWidget(self.body)
        elif not isinstance(table, BitwiseSwitchTable) and table.shape() == (1, 1):
            from ecueditor.ui.editor.frames.scalar_frame import ScalarFrame
            scalar_frame = ScalarFrame(table, roms_provider=roms_provider)
            self.grid = scalar_frame.grid
            self.menubar = scalar_frame.menubar
            self.frame = scalar_frame
            self.body = scalar_frame
            lay.addWidget(scalar_frame)
        else:
            from ecueditor.ui.editor.frames.grid_frame import GridTableFrame
            grid_frame = GridTableFrame(table, roms_provider=roms_provider)
            self.grid = grid_frame.grid
            self.menubar = grid_frame.menubar   # REQUIRED: existing tests read doc.menubar
            self.frame = grid_frame
            self.body = grid_frame
            lay.addWidget(grid_frame)

class TableDocument(_TablePanel):
    """Table panel hosted inside the V2 internal-window workspace."""
    def __init__(self, rom, table, title: str, parent=None, roms_provider=None) -> None:
        super().__init__(table, parent=parent, roms_provider=roms_provider)
        self.rom = rom
        self.table = table
        self.title = title
