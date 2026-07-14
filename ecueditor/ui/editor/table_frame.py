from __future__ import annotations
from PySide6.QtWidgets import QWidget, QVBoxLayout
from ecueditor.core.rom.table import SwitchTable, BitwiseSwitchTable

class _TablePanel(QWidget):
    def __init__(self, table, parent=None, roms_provider=None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self); lay.setContentsMargins(8, 8, 8, 8); lay.setSpacing(0)
        if isinstance(table, SwitchTable):
            self.grid = None
            from ecueditor.ui.editor.frames.switch_frame import SwitchFrame
            self.body = SwitchFrame(table)
            lay.addWidget(self.body)
        elif isinstance(table, BitwiseSwitchTable):
            self.grid = None
            from ecueditor.ui.editor.frames.switch_frame import BitwiseSwitchFrame
            self.body = BitwiseSwitchFrame(table)
            lay.addWidget(self.body)
        elif not isinstance(table, BitwiseSwitchTable) and table.shape() == (1, 1):
            from ecueditor.ui.editor.frames.scalar_frame import ScalarFrame
            frame = ScalarFrame(table, roms_provider=roms_provider)
            self.grid = frame.grid
            self.menubar = frame.menubar
            self.frame = frame
            self.body = frame
            lay.addWidget(frame)
        else:
            from ecueditor.ui.editor.frames.grid_frame import GridTableFrame
            frame = GridTableFrame(table, roms_provider=roms_provider)
            self.grid = frame.grid
            self.menubar = frame.menubar        # REQUIRED: existing tests read doc.menubar
            self.frame = frame
            self.body = frame
            lay.addWidget(frame)

class TableDocument(_TablePanel):
    """Table panel hosted inside the V2 internal-window workspace."""
    def __init__(self, rom, table, title: str, parent=None, roms_provider=None) -> None:
        super().__init__(table, parent=parent, roms_provider=roms_provider)
        self.rom = rom
        self.table = table
        self.title = title
