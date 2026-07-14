from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from ecueditor.core.defs.library import DefinitionLibrary
from ecueditor.core.defs.model import RomDefinition
from ecueditor.core.memory.base import MemoryModel
from ecueditor.core.rom.cell import DataCell
from ecueditor.core.rom import storage
from ecueditor.core.rom.table import Table, build_table, iter_storage_cells
from ecueditor.core.errors import NoMatchingRomError, ECUEditorError, ChecksumError
from ecueditor.core.checksum.base import ChecksumManager


def _read_bytes(path) -> bytearray:
    try:
        return bytearray(Path(path).read_bytes())
    except FileNotFoundError as exc:
        raise ECUEditorError(f"ROM file not found: {path}") from exc


@dataclass(frozen=True)
class _CellBinding:
    cell: DataCell
    owner: Table
    file_offset: int
    storage_type: str
    little_endian: bool
    width: int

class RomImage:
    def __init__(self, data: bytearray, definition: RomDefinition,
                 memory_model: MemoryModel, path: Path | None) -> None:
        self.data = data
        self.definition = definition
        self.memory_model = memory_model
        self.path = path
        self.checksum_manager: ChecksumManager | None = None       # bound by _bind_checksum() below
        self._tables: dict[str, Table] = {}
        self._endian_default = definition.romid.memmodel_endian or "little"
        self._cell_bindings: dict[int, _CellBinding] = {}
        self._byte_bindings: dict[int, list[_CellBinding]] = {}
        self._live_aliases_bound = False

    @classmethod
    def open(cls, path, library: DefinitionLibrary) -> "RomImage":
        data = _read_bytes(path)
        match = library.match(bytes(data))
        if match is None:
            raise NoMatchingRomError(f"no definition matches {path}")
        doc, rid, model = match
        definition = doc.resolve(rid.xmlid)
        rom = cls(data, definition, model, Path(path))
        rom._bind_checksum()
        return rom

    @classmethod
    def force_open(cls, path, library: DefinitionLibrary, xmlid: str) -> "RomImage":
        data = _read_bytes(path)
        definition, model = library.force_load(bytes(data), xmlid)
        rom = cls(data, definition, model, Path(path))
        rom._bind_checksum()
        return rom

    def _bind_checksum(self) -> None:
        from ecueditor.core.checksum.builtins.ms41 import MS41Checksum
        from ecueditor.core.plugins.registry import CHECKSUMS
        # (1) A declared <checksum type> wins (imported non-MS41 defs); MS41 defs have none.
        ctype = self.definition.checksum_type
        if ctype:
            try:
                factory = CHECKSUMS.get(ctype)
            except KeyError as exc:
                raise ChecksumError(f"unregistered checksum type {ctype!r}") from exc
            self.checksum_manager = factory()
            return
        # (2) Bind the MS41 manager for BOTH framings — it handles 256 KB (boot+program+cal) and
        #     24 KB (cal table only) internally. For an MS41.3 full read, skip the unverified program CRC.
        correct_program = not self._is_ms41_3_full_read()
        self.checksum_manager = MS41Checksum(correct_program=correct_program)
        # (3) per-ROM user override is applied by the caller (EditorSettings.checksum_override) if set.

    def _is_ms41_3_full_read(self) -> bool:
        # MS41.3 == the SS1v2 / SHINDE1 firmware family; only relevant for a 256 KB full read.
        if self.memory_model.name != "ms41_fullread":
            return False
        rid = self.definition.romid
        return (rid.ecuid or "").upper() == "SHINDE1" or rid.xmlid in {"SS1v2", "SS1v0"}

    def checksum_status(self) -> tuple[bool, list[str]]:
        if self.checksum_manager is None:
            return True, ["no checksum manager bound"]
        return self.checksum_manager.validate(bytes(self.data))

    def checksum_report(self):
        """Return per-region checksum status, or ``None`` when no manager is bound."""
        if self.checksum_manager is None:
            return None
        return self.checksum_manager.report(bytes(self.data))

    @property
    def endian_default(self) -> str:
        """The ROM-wide endian default; per-table/axis definition endian overrides win."""
        return self._endian_default

    @property
    def tables(self) -> dict[str, Table]:
        if not self._tables:
            for name, tdef in self.definition.tables.items():
                if tdef.storage_address is None:
                    continue
                self._tables[name] = build_table(tdef, self.data, self.memory_model,
                                                 self._endian_default)
        if not self._live_aliases_bound:
            self._bind_live_storage_aliases()
        return self._tables

    def table(self, name: str) -> Table:
        return self.tables[name]

    def _bind_live_storage_aliases(self) -> None:
        """Index live DataCells by physical ROM bytes, following RomRaider's byte mapping."""
        for owner in self._tables.values():
            for cell, storage_address, storage_type, little in iter_storage_cells(
                owner, self._endian_default
            ):
                file_offset = self.memory_model.file_offset(storage_address)
                width = storage.storage_width(storage_type)
                binding = _CellBinding(
                    cell, owner, file_offset, storage_type, little, width
                )
                self._cell_bindings[id(cell)] = binding
                for byte_offset in range(file_offset, file_offset + width):
                    self._byte_bindings.setdefault(byte_offset, []).append(binding)
                cell.bind_change_callback(self._on_live_cell_changed)
        self._live_aliases_bound = True

    def _on_live_cell_changed(self, cell: DataCell) -> None:
        """Write one edit to working bytes and immediately re-read every overlapping alias."""
        source = self._cell_bindings.get(id(cell))
        if source is None:
            return
        storage.write_int(
            self.data, source.file_offset, cell.raw,
            source.storage_type, source.little_endian,
        )

        peers: dict[int, _CellBinding] = {}
        for byte_offset in range(source.file_offset, source.file_offset + source.width):
            for binding in self._byte_bindings.get(byte_offset, ()):
                peers[id(binding.cell)] = binding
        for binding in peers.values():
            value = storage.read_int(
                self.data, binding.file_offset,
                binding.storage_type, binding.little_endian,
            )
            if binding.cell.raw != value:
                # Preserve each alias's own original/revert baseline, exactly as RomRaider's
                # updateBinValueFromMemory() does after a shared-address edit.
                binding.cell.sync_raw_from_storage(value)

    def storage_aliases(self, table: Table) -> frozenset[Table]:
        """Return top-level tables whose storage overlaps ``table`` or any of its axes."""
        # Accessing ``tables`` ensures the complete ROM-wide byte index exists.
        _ = self.tables
        byte_offsets: set[int] = set()
        for binding in self._cell_bindings.values():
            if binding.owner is table:
                byte_offsets.update(range(binding.file_offset, binding.file_offset + binding.width))
        owners = {
            binding.owner
            for byte_offset in byte_offsets
            for binding in self._byte_bindings.get(byte_offset, ())
        }
        return frozenset(owners)

    def is_dirty(self) -> bool:
        return any(t.needs_write() for t in self._tables.values())

    def flush(self) -> None:
        """Materialize pending writes, then defensively re-read any externally changed bytes.

        Normal edits already update ``self.data`` and all live aliases immediately. The second
        pass remains as a safety net for callers that mutate the working bytearray directly.
        """
        for t in self._tables.values():
            t.write_back(self.data, self.memory_model, self._endian_default)
        for t in self._tables.values():
            t.resync(self.data, self.memory_model, self._endian_default)

    def save(self, path=None) -> list[str]:
        self.flush()
        notes: list[str] = []
        if self.checksum_manager is not None:
            notes += self.checksum_manager.update(self.data)
        target = Path(path) if path else self.path
        if target is None:
            raise ValueError("no path to save to")
        target.write_bytes(bytes(self.data))
        # reset revert points so a re-save is clean
        for t in self._tables.values():
            t.set_revert_point(pending_write=False)
        self.path = target
        return notes
