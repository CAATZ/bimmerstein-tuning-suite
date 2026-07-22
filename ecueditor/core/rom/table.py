from __future__ import annotations
import logging
from collections.abc import Iterator
from ecueditor.core.rom.cell import DataCell
from ecueditor.core.rom import storage
from ecueditor.core.scaling.scale import Scale
from ecueditor.core.defs.model import TableDef, AxisDef, ScaleDef
from ecueditor.core.memory.base import MemoryModel
from ecueditor.core.errors import TableError

log = logging.getLogger(__name__)

def _scale_from(sd: ScaleDef | None) -> Scale:
    if sd is None:
        return Scale()
    return Scale(units=sd.units, expression=sd.expression, to_byte=sd.to_byte,
                 format=sd.format, fine_increment=sd.fine_increment,
                 coarse_increment=sd.coarse_increment)

class Table:
    def __init__(self, definition: TableDef, cells: list[DataCell],
                 x_axis: "Table | None" = None, y_axis: "Table | None" = None) -> None:
        self.definition = definition
        self.name = definition.name
        self.logparam = definition.logparam          # mirrors TableDef.logparam (for the live overlay)
        self.cells = cells
        self.x_axis = x_axis
        self.y_axis = y_axis
    def shape(self) -> tuple[int, int]:
        if self.definition.type == "2D":
            count = len(self.cells)
            if self.definition.y_axis is not None and self.definition.x_axis is None:
                return (1, count)
            return (count, 1)
        return (self.definition.size_x or 1, self.definition.size_y or 1)
    def cell_at(self, x: int, y: int) -> DataCell:
        sx, _sy = self.shape()
        return self.cells[y * sx + x]
    def is_changed(self) -> bool:
        if any(c.is_changed() for c in self.cells):
            return True
        if self.x_axis is not None and self.x_axis.is_changed():
            return True
        if self.y_axis is not None and self.y_axis.is_changed():
            return True
        return False
    def needs_write(self) -> bool:
        if any(c.needs_write() for c in self.cells):
            return True
        if self.x_axis is not None and self.x_axis.needs_write():
            return True
        if self.y_axis is not None and self.y_axis.needs_write():
            return True
        return False
    def undo_all(self) -> None:
        for c in self.cells: c.undo()
        if self.x_axis is not None: self.x_axis.undo_all()
        if self.y_axis is not None: self.y_axis.undo_all()
    def set_revert_point(self, *, pending_write: bool = True) -> None:
        for c in self.cells: c.set_revert_point(pending_write=pending_write)
        if self.x_axis is not None:
            self.x_axis.set_revert_point(pending_write=pending_write)
        if self.y_axis is not None:
            self.y_axis.set_revert_point(pending_write=pending_write)

    # ---- Clipboard: RomRaider [TableND] tab-delimited format (golden format pinned by the tests) ----
    def _header(self) -> str:
        return {"1D": "[Table1D]", "2D": "[Table2D]", "3D": "[Table3D]"}.get(self.definition.type, "[Table1D]")

    def to_text(self) -> str:
        sx, sy = self.shape()
        fmt = self.cells[0].scale.format_value if self.cells else (lambda v: "")
        rows: list[str] = [self._header()]
        if self.definition.type == "3D":
            # Always emit the FULL sy x sx grid, even when an axis was omitted at build time
            # (label-only "static" axes in real defs): x-axis header row only when the X axis
            # exists, per-row Y prefix only when the Y axis exists. Never collapse to one row.
            if self.x_axis:
                rows.append("\t" + "\t".join(self.x_axis.cells[i].scale.format_value(self.x_axis.cells[i].real())
                                             for i in range(sx)))
            for r in range(sy):
                line: list[str] = []
                if self.y_axis:
                    yv = self.y_axis.cells[r]
                    line.append(yv.scale.format_value(yv.real()))
                line += [fmt(self.cell_at(c, r).real()) for c in range(sx)]
                rows.append("\t".join(line))
        elif self.definition.type == "2D" and (axis := self.x_axis or self.y_axis):
            rows.append("\t".join(
                cell.scale.format_value(cell.real()) for cell in axis.cells
            ))
            rows.append("\t".join(fmt(cell.real()) for cell in self.cells))
        else:                                   # 1D / 2D data row(s), no axis header
            n = max(sx, sy)
            rows.append("\t".join(fmt(self.cells[i].real()) for i in range(n)))
        return "\n".join(rows)

    @staticmethod
    def _paste_cell(cell: DataCell, text: str) -> None:
        # Idempotent paste: to_text formats at display precision (lossy), so round-tripping an
        # unchanged value via to_raw can land on a neighbour byte and spuriously mark the cell
        # changed (drives the red/blue edit shading). Skip a value that displays identically.
        val = float(text)
        if cell.scale.format_value(val) != cell.scale.format_value(cell.real()):
            cell.set_real(val)

    def paste_text(self, text: str, anchor: int = 0) -> None:
        lines = text.splitlines()
        table_header = bool(lines and lines[0].startswith("[Table"))
        if table_header:
            tail = lines[0].partition("]")[2]
            lines = lines[1:]
            tail = tail.lstrip("\t ")
            if tail:
                lines.insert(0, tail)
        axis_table = table_header or bool(lines and lines[0].startswith("\t"))
        if axis_table and self.definition.type == "3D" \
                and (self.x_axis or self.y_axis) and len(lines) >= 2:
            # Full-grid 3D paste, tolerant of the degraded shapes to_text emits: the x-axis
            # header line exists only when the X axis does; the leading per-row y value only
            # when the Y axis does. (A 3D table with NEITHER axis roundtrips via the flat
            # branch below, since its grid carries no header row and no row prefixes.)
            sx, sy = self.shape()
            if self.x_axis:
                axis_values = lines[0].split("\t")
                if axis_values and axis_values[0] == "":
                    axis_values = axis_values[1:]
                for cell, value in zip(self.x_axis.cells, axis_values):
                    if value not in ("", "x"):
                        self._paste_cell(cell, value)
            data_lines = lines[1:] if self.x_axis else lines
            for r, line in enumerate(data_lines[:sy]):
                vals = line.split("\t")
                if self.y_axis:
                    if vals and vals[0] not in ("", "x"):
                        self._paste_cell(self.y_axis.cell_at(r, 0), vals[0])
                    vals = vals[1:]
                for c, v in enumerate(vals[:sx]):
                    if v not in ("", "x"):
                        self._paste_cell(self.cell_at(c, r), v)
        elif table_header and self.definition.type == "2D" \
                and (axis := self.x_axis or self.y_axis) \
                and len(lines) >= 2:
            for cell, value in zip(axis.cells, lines[0].split("\t")):
                if value not in ("", "x"):
                    self._paste_cell(cell, value)
            for cell, value in zip(self.cells, lines[1].split("\t")):
                if value not in ("", "x"):
                    self._paste_cell(cell, value)
        else:
            flat = [v for line in lines for v in line.split("\t")]
            for i, v in enumerate(flat):
                idx = anchor + i
                if idx < len(self.cells) and v not in ("", "x"):
                    self._paste_cell(self.cells[idx], v)

    def write_back(self, image: bytearray, memory_model: MemoryModel, endian_default: str) -> None:
        _write_table(self, image, memory_model, endian_default)

    def resync(self, image: bytearray, memory_model: MemoryModel, endian_default: str) -> None:
        """Re-read every cell with real storage backing (own cells + x_axis/y_axis sub-table
        cells; static/label axes with no storageaddress are skipped, mirroring write_back) from
        `image`. A cell whose `raw` no longer matches the bytes now in `image` gets BOTH `raw`
        and `original` set to that value.

        Called by RomImage.flush() after the write-back pass so that two live Table objects
        aliasing the same storage bytes end up agreeing:
          - a cell the caller edited and just wrote is already byte-identical to `image` ->
            untouched -> `original` still holds the load-time value -> is_changed() stays True.
          - an aliased twin cell nobody edited differs from the now-updated `image` -> both
            `raw` and `original` move to the new bytes -> it reads correctly and as unchanged.
          - on a same-byte conflict between two live tables edited to different values, the
            write-back pass's last writer (materialization order, i.e. dict/definition order)
            wins in `image`; this pass then pulls every other table's cell up to that winning
            value (its raw/original both become the winner, so it stops reading as changed).
            Deterministic, order-derived from RomImage._tables iteration order.
        Cells over non-aliased addresses are never perturbed: after write-back their raw is
        already byte-identical to image, so the "differs" check never fires for them.
        """
        _resync_table(self, image, memory_model, endian_default)

class Table1D(Table): ...
class Table2D(Table): ...
class Table3D(Table): ...

class SwitchTable(Table):
    def active_state(self) -> str | None:
        cur = bytes(c.raw for c in self.cells)
        for name, hexdata in self.definition.states:
            if bytes(int(b, 16) for b in hexdata.split()) == cur:
                return name
        return None

class BitwiseSwitchTable(Table):
    def _mask(self, name: str) -> int:
        pos = dict(self.definition.bits)[name]
        return 1 << pos
    def bit_value(self, name: str) -> bool:
        return bool(self.cells[0].raw & self._mask(name))
    def set_bit(self, name: str, on: bool) -> None:
        m = self._mask(name)
        self.cells[0].set_raw((self.cells[0].raw | m) if on else (self.cells[0].raw & ~m), clamp=False)


# ---------------------------------------------------------------------------
# build_table / _write_table — cell materialization and its inverse.
# ---------------------------------------------------------------------------

def _little_endian(endian: str | None, endian_default: str) -> bool:
    return (endian or endian_default) == "little"

def _read_cells(image, memory_model: MemoryModel, sa: int, count: int, storage_type: str,
                little_endian: bool, scale: Scale) -> list[DataCell]:
    width = storage.storage_width(storage_type)
    lo, hi = storage.storage_bounds(storage_type)
    cells: list[DataCell] = []
    for i in range(count):
        raw = storage.read_int(image, memory_model.file_offset(sa + i * width), storage_type, little_endian)
        cells.append(DataCell(raw=raw, original=raw, scale=scale, storage_min=lo, storage_max=hi))
    return cells


def _physical_data_indices(definition: TableDef) -> list[int]:
    """Return logical row-major indices in their physical ROM storage order.

    RomRaider applies ``flipy`` to the column coordinate and ``flipx`` to the
    row coordinate before an optional X/Y swap. The counterintuitive attribute
    names are retained for byte-compatible populate/save behavior.
    """
    sx = definition.size_x or 1
    sy = definition.size_y or 1
    if definition.type != "3D":
        count = sx if definition.type == "1D" else max(sx, sy)
        return list(range(count))

    i_max = sx if definition.swap_xy else sy
    j_max = sy if definition.swap_xy else sx
    indices: list[int] = []
    for i in range(i_max):
        for j in range(j_max):
            x = j_max - j - 1 if definition.flip_y else j
            y = i_max - i - 1 if definition.flip_x else i
            if definition.swap_xy:
                x, y = y, x
            indices.append(y * sx + x)
    return indices


def _logical_data_cells(definition: TableDef, physical_cells: list[DataCell]) -> list[DataCell]:
    indices = _physical_data_indices(definition)
    if indices == list(range(len(physical_cells))):
        return physical_cells
    logical: list[DataCell | None] = [None] * len(physical_cells)
    for physical_index, logical_index in enumerate(indices):
        logical[logical_index] = physical_cells[physical_index]
    if any(cell is None for cell in logical):
        raise TableError(f"invalid storage layout for table {definition.name!r}")
    return [cell for cell in logical if cell is not None]

def _write_cells(image: bytearray, memory_model: MemoryModel, sa: int, cells: list[DataCell],
                 storage_type: str, little_endian: bool) -> None:
    width = storage.storage_width(storage_type)
    for i, cell in enumerate(cells):
        if not cell.needs_write():
            # Unedited cells are byte-identical to the image by construction (they were read
            # from it). Skipping them is a no-op in the common case, but is load-bearing when
            # two tables alias the same storage bytes (e.g. RomRaider SS1v2 "MAF" and
            # "MAF (1024 kg/hr Mode)" both cover 0x2AD6-0x2CD6): without this guard, whichever
            # aliased table flushes last would overwrite the other's edit with its own stale,
            # unedited copy of the shared bytes.
            continue
        storage.write_int(
            image, memory_model.file_offset(sa + i * width), cell.raw,
            storage_type, little_endian,
        )

def _build_axis(axis: AxisDef | None, role: str, definition: TableDef, image,
                memory_model: MemoryModel, endian_default: str) -> "Table1D | None":
    if axis is None:
        return None
    table_dim = definition.size_x if role == "X" else definition.size_y
    scale = _scale_from(axis.scale)
    axis_name = axis.name or f"{definition.name} {role} Axis"

    if axis.static_values is not None:
        values = axis.static_values
        if not all(isinstance(v, float) for v in values):
            # Prose (or mixed numeric/prose) static values: axis Table cells are integer-raw
            # DataCells, which a string label can't be. No axis sub-Table is built here -- the
            # labels remain available only on the definition (TableDef.x_axis/y_axis
            # .static_values), which the UI reads directly for grid headers.
            log.debug("axis %s of table %r has non-numeric static_values %r -- omitting axis "
                      "sub-Table (labels stay on the definition)", role, definition.name, values)
            return None
        # Deliberate relaxation of DataCell's int contract: static/label axes store the literal
        # float breakpoint as raw==real, read-only via storage_min==storage_max, and are excluded
        # from write_back (pending a possible future contract widening to int|float).
        cells = [DataCell(raw=v, original=v, scale=scale, storage_min=v, storage_max=v)  # type: ignore[arg-type]
                 for v in values]
        axis_def = TableDef(name=axis_name, type="1D", category=None, storage_address=None,
                            storage_type=axis.storage_type, endian=axis.endian, size_x=len(values), size_y=1,
                            scale=axis.scale, x_axis=None, y_axis=None, logparam=axis.logparam)
        return Table1D(axis_def, cells)

    if axis.storage_address is None:
        # Real defs pervasively use a "Static X/Y Axis" purely as a prose row/column label
        # (e.g. 'Byte 4' Y-axis: "Convert from Decimal to Binary..."), with no numeric <data>
        # and no storageaddress -- inheritance.py already treats that as a normal condition
        # (static_values ends up None). There is no axis to materialize; the label itself
        # remains available via TableDef.description/name. A normal condition, hence debug.
        log.debug("axis %s of table %r has no storage_address and no numeric static_values "
                  "(label-only axis) -- omitting axis", role, definition.name)
        return None

    size = axis.size if axis.size is not None else (table_dim or 1)
    storage_type = axis.storage_type or "uint8"
    little = _little_endian(axis.endian or definition.endian, endian_default)
    cells = _read_cells(image, memory_model, axis.storage_address, size, storage_type, little, scale)
    # Resolved endian is baked into the synthetic definition so _write_table doesn't need to re-derive it.
    axis_def = TableDef(name=axis_name, type="1D", category=None, storage_address=axis.storage_address,
                        storage_type=storage_type, endian="little" if little else "big", size_x=size, size_y=1,
                        scale=axis.scale, x_axis=None, y_axis=None, logparam=axis.logparam)
    return Table1D(axis_def, cells)

def _switch_byte_count(definition: TableDef) -> int:
    if not definition.states:
        return 1
    return len(definition.states[0][1].split())

def _build_switch_table(definition: TableDef, image, memory_model: MemoryModel,
                        endian_default: str) -> SwitchTable:
    if definition.storage_address is None:
        raise TableError(f"switch table {definition.name!r} has no storage_address")
    storage_type = definition.storage_type or "uint8"
    little = _little_endian(definition.endian, endian_default)
    width = storage.storage_width(storage_type)
    count = max(1, _switch_byte_count(definition) // width)
    scale = _scale_from(definition.scale)
    cells = _read_cells(
        image, memory_model, definition.storage_address, count, storage_type, little, scale
    )
    return SwitchTable(definition, cells)

def _build_bitwise_table(definition: TableDef, image, memory_model: MemoryModel,
                         endian_default: str) -> BitwiseSwitchTable:
    if definition.storage_address is None:
        raise TableError(f"bitwise switch table {definition.name!r} has no storage_address")
    storage_type = definition.storage_type or "uint8"
    little = _little_endian(definition.endian, endian_default)
    scale = _scale_from(definition.scale)
    cells = _read_cells(image, memory_model, definition.storage_address, 1, storage_type, little, scale)
    return BitwiseSwitchTable(definition, cells)

_DATA_TABLE_CLASSES = {"1D": Table1D, "2D": Table2D, "3D": Table3D}

def build_table(definition: TableDef, image, memory_model: MemoryModel,
                endian_default: str) -> Table:
    if definition.type == "Switch":
        return _build_switch_table(definition, image, memory_model, endian_default)
    if definition.type == "BitwiseSwitch":
        return _build_bitwise_table(definition, image, memory_model, endian_default)

    cls = _DATA_TABLE_CLASSES.get(definition.type)
    if cls is None:
        raise TableError(f"unknown table type {definition.type!r}")
    if definition.storage_address is None:
        raise TableError(f"table {definition.name!r} has no storage_address")

    storage_type = definition.storage_type or "uint8"
    little = _little_endian(definition.endian, endian_default)
    scale = _scale_from(definition.scale)
    sx = definition.size_x or 1
    sy = definition.size_y or 1
    if definition.type == "3D":
        count = sx * sy
    elif definition.type == "2D":
        count = max(sx, sy)
    else:  # 1D
        count = sx

    physical_cells = _read_cells(
        image, memory_model, definition.storage_address, count, storage_type, little, scale
    )
    cells = _logical_data_cells(definition, physical_cells)
    x_axis = _build_axis(definition.x_axis, "X", definition, image, memory_model, endian_default)
    y_axis = _build_axis(definition.y_axis, "Y", definition, image, memory_model, endian_default)
    return cls(definition, cells, x_axis, y_axis)

def _storage_groups(table: Table, endian_default: str
                    ) -> Iterator[tuple[list[DataCell], int, str, bool]]:
    """Yield (cells, storage_address, storage_type, little_endian) for `table`'s own cells and
    each axis sub-table that has real storage backing. Static/label axes (storage_address is
    None -- see _build_axis) are skipped: there is nothing in `image` to write to or re-read
    from. Shared by _write_table and _resync_table so the two passes traverse identically.
    """
    definition = table.definition
    if definition.storage_address is not None:
        storage_type = definition.storage_type or "uint8"
        little = _little_endian(definition.endian, endian_default)
        indices = _physical_data_indices(definition)
        yield [table.cells[index] for index in indices], definition.storage_address, storage_type, little

    for axis in (table.x_axis, table.y_axis):
        if axis is None:
            continue
        axis_def = axis.definition
        if axis_def.storage_address is None:
            continue  # static axis: read-only, no image bytes back it
        axis_storage_type = axis_def.storage_type or "uint8"
        axis_little = _little_endian(axis_def.endian, endian_default)
        yield axis.cells, axis_def.storage_address, axis_storage_type, axis_little

def iter_storage_cells(table: Table, endian_default: str
                       ) -> Iterator[tuple[DataCell, int, str, bool]]:
    """Yield every storage-backed cell with its exact storage address and encoding.

    Axis cells are included. Static axes are omitted because they have no ROM storage.
    Keeping the cell's own encoding is essential when two definitions alias the same bytes
    but expose them through different scales or storage shapes.
    """
    for cells, storage_address, storage_type, little in _storage_groups(table, endian_default):
        width = storage.storage_width(storage_type)
        for index, cell in enumerate(cells):
            yield cell, storage_address + index * width, storage_type, little

def _write_table(table: Table, image: bytearray, memory_model: MemoryModel, endian_default: str) -> None:
    for cells, sa, storage_type, little in _storage_groups(table, endian_default):
        _write_cells(image, memory_model, sa, cells, storage_type, little)

def _resync_cells(image, memory_model: MemoryModel, sa: int, cells: list[DataCell],
                  storage_type: str, little_endian: bool) -> None:
    width = storage.storage_width(storage_type)
    for i, cell in enumerate(cells):
        value = storage.read_int(image, memory_model.file_offset(sa + i * width), storage_type, little_endian)
        if value != cell.raw:
            cell.raw = value
            cell.original = value
            cell.mark_written()

def _resync_table(table: Table, image: bytearray, memory_model: MemoryModel, endian_default: str) -> None:
    for cells, sa, storage_type, little in _storage_groups(table, endian_default):
        _resync_cells(image, memory_model, sa, cells, storage_type, little)
