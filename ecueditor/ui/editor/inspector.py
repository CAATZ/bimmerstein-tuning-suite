"""Docked cell inspector (spec §4): live facts for the selected cell."""
from __future__ import annotations
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QFormLayout, QLabel
from ecueditor.core.rom.storage import storage_width
from ecueditor.core.rom.table import SwitchTable, BitwiseSwitchTable
from ecueditor.ui.design.fonts import numeric_font

_ROWS = ("Description", "Real", "Raw", "Address", "File offset", "Scaling",
         "Original", "Range", "Endian")


class CellInspectorPanel(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("cellInspectorPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        form = QFormLayout(self)
        form.setContentsMargins(8, 8, 8, 8)
        self._values: dict[str, QLabel] = {}
        for row in _ROWS:
            lab = QLabel("—"); lab.setFont(numeric_font(10))
            if row == "Description":
                lab.setFont(self.font())
                lab.setWordWrap(True)
            lab.setTextInteractionFlags(lab.textInteractionFlags()
                                        | lab.textInteractionFlags().TextSelectableByMouse)
            self._values[row] = lab
            form.addRow(row, lab)
        self._doc = None
        self._table = None
        self._rom = None
        self._grid = None

    # --- wiring ---------------------------------------------------------------
    def set_document(self, doc) -> None:
        grid = getattr(doc, "grid", None) if doc is not None else None
        table = getattr(doc, "table", None) if doc is not None else None
        if table is None and grid is not None:
            table = grid.model().table
        rom = getattr(doc, "rom", None) if doc is not None else None
        self._doc = doc if table is not None and rom is not None else None
        self._table = table if self._doc is not None else None
        self._rom = rom if self._doc is not None else None
        self._grid = grid if self._doc is not None else None
        self.clear()
        if self._doc is not None:
            self._show_table_metadata()

    def clear(self) -> None:
        for lab in self._values.values():
            lab.setText("—")

    def value_text(self, row_label: str) -> str:
        return self._values[row_label].text()

    def _set_address(self, address: int | None) -> None:
        if self._doc is None or address is None:
            self._values["Address"].setText("—")
            self._values["File offset"].setText("—")
            return
        self._values["Address"].setText(f"0x{address:04X}")
        try:
            offset = self._rom.memory_model_for(self._table).file_offset(address)
        except Exception:  # noqa: BLE001 — not every imported memory model maps every address
            self._values["File offset"].setText("—")
        else:
            self._values["File offset"].setText(f"0x{offset:04X}")

    @staticmethod
    def _raw_text(table) -> str:
        width = storage_width(table.definition.storage_type or "uint8")
        return " ".join(f"{cell.raw:0{width * 2}X}" for cell in table.cells)

    def _show_table_metadata(self) -> None:
        """Show definition-level facts before (or without) a grid-cell selection."""
        if self._doc is None:
            return
        table, rom = self._table, self._rom
        tdef = table.definition
        description = (tdef.description or "").strip() or "Not provided by definition."
        self._values["Description"].setText(description)
        self._values["Description"].setToolTip(description)
        self._set_address(tdef.storage_address)
        self._values["Endian"].setText(tdef.endian or rom.endian_default_for(table))

        if table.cells:
            scale = table.cells[0].scale
            self._values["Scaling"].setText(scale.expression)

        if isinstance(table, SwitchTable):
            self._values["Raw"].setText(self._raw_text(table))
            self._values["Real"].setText(table.active_state() or "Unknown state")
        elif isinstance(table, BitwiseSwitchTable):
            self._values["Raw"].setText(self._raw_text(table))
            active = [name for name, _position in tdef.bits if table.bit_value(name)]
            self._values["Real"].setText(", ".join(active) or "No bits enabled")
        elif len(table.cells) == 1:
            cell = table.cells[0]
            self._values["Raw"].setText(self._raw_text(table))
            self._values["Real"].setText(
                f"{cell.scale.format_value(cell.real())} {cell.scale.units}".rstrip()
            )

    # --- display ----------------------------------------------------------------
    def show_index(self, index) -> None:
        if self._doc is None or index is None or not index.isValid():
            self.clear()
            if self._doc is not None:
                self._show_table_metadata()
            return
        model = self._grid.model()
        table, rom = model.table, self._rom
        x, y = model.cell_xy(index)
        cell = table.cell_at(x, y)
        tdef = table.definition
        scale = cell.scale
        self._values["Real"].setText(f"{scale.format_value(cell.real())} {scale.units}")
        self._values["Raw"].setText(f"{cell.raw} (0x{cell.raw:02X})")
        # Linear cell index derived from the model index (matches Table.cell_at(x,y) =
        # cells[y*size_x+x]) -- NOT table.cells.index(cell): DataCell is a value-equal
        # dataclass, so .index(cell) would return the FIRST cell that compares equal by
        # value, which is wrong for any cell whose raw/original/scale duplicate an earlier
        # cell (common in flat table regions of real ROMs).
        sx = tdef.size_x or 1
        i = y * sx + x
        if tdef.storage_address is not None:
            addr = tdef.storage_address + i * storage_width(tdef.storage_type or "uint8")
            self._set_address(addr)
        else:
            self._set_address(None)
        self._values["Scaling"].setText(scale.expression)
        delta = cell.real() - scale.to_real(cell.original)
        self._values["Original"].setText(
            f"{scale.format_value(scale.to_real(cell.original))}"
            + (f"  ({delta:+.2f})" if cell.is_changed() else ""))
        lo, hi = scale.to_real(cell.storage_min), scale.to_real(cell.storage_max)
        lo, hi = min(lo, hi), max(lo, hi)
        self._values["Range"].setText(f"{scale.format_value(lo)} … {scale.format_value(hi)} {scale.units}")
        self._values["Endian"].setText(tdef.endian or rom.endian_default_for(table))
