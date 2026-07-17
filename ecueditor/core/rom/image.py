from __future__ import annotations
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable, TypeVar
from ecueditor.core.defs.library import DefinitionLibrary, ResolvedDefinitionSection
from ecueditor.core.defs.model import RomDefinition, TableDef
from ecueditor.core.memory.base import MemoryModel
from ecueditor.core.memory import model_for_match, probe_offset
from ecueditor.core.rom.cell import DataCell
from ecueditor.core.rom import storage
from ecueditor.core.rom.table import Table, build_table, iter_storage_cells
from ecueditor.core.errors import (
    ChecksumError,
    DefinitionError,
    ECUEditorError,
    NoMatchingRomError,
)
from ecueditor.core.checksum.base import ChecksumManager

if TYPE_CHECKING:
    from ecueditor.core.settings import EditorSettings

_T = TypeVar("_T")


def _run_checksum_operation(
    manager: ChecksumManager,
    operation: str,
    callback: Callable[[], _T],
) -> _T:
    """Normalize optional checksum runtime failures without swallowing user interrupts."""
    try:
        return callback()
    except ChecksumError:
        raise
    except (Exception, SystemExit) as exc:
        detail = str(exc) or type(exc).__name__
        raise ChecksumError(
            f"checksum manager {type(manager).__name__} failed during {operation}: {detail}"
        ) from exc


def _read_bytes(path) -> bytearray:
    try:
        return bytearray(Path(path).read_bytes())
    except FileNotFoundError as exc:
        raise ECUEditorError(f"ROM file not found: {path}") from exc


def _atomic_write_bytes(target: Path, payload: bytes) -> None:
    """Durably stage ``payload`` beside ``target``, then atomically replace it."""
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except BaseException:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise


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
                 memory_model: MemoryModel, path: Path | None,
                 *, force_loaded: bool = False,
                 sections: tuple[ResolvedDefinitionSection, ...] | None = None) -> None:
        self.data = data
        self.definition = definition
        self.memory_model = memory_model
        self.path = path
        self.checksum_manager: ChecksumManager | None = None       # bound by _bind_checksum() below
        self.sections = sections or (
            ResolvedDefinitionSection(
                key=self._section_key(definition, len(data)),
                label=self._section_label(definition, len(data)),
                definition=definition,
                memory_model=memory_model,
            ),
        )
        self._tables: dict[str, Table] = {}       # preferred name lookup (first section wins)
        self._tables_by_key: dict[tuple[str, str], Table] = {}
        self._table_keys_by_object: dict[int, tuple[str, str]] = {}
        self._tables_initialized = False
        self._endian_default = definition.romid.memmodel_endian or "little"
        self._cell_bindings: dict[int, _CellBinding] = {}
        self._byte_bindings: dict[int, list[_CellBinding]] = {}
        self._force_loaded = force_loaded

    @staticmethod
    def _section_key(definition: RomDefinition, image_size: int) -> str:
        if definition.romid.filesize == 0x6000:
            return "partial"
        if definition.romid.filesize == image_size == 0x40000:
            return "full"
        return "rom"

    @classmethod
    def _section_label(cls, definition: RomDefinition, image_size: int) -> str:
        return {
            "partial": "Partial BIN (24 KB)",
            "full": "Full BIN (256 KB)",
            "rom": "ROM tables",
        }[cls._section_key(definition, image_size)]

    @classmethod
    def open(cls, path, library: DefinitionLibrary,
             *, settings: "EditorSettings | None" = None) -> "RomImage":
        data = _read_bytes(path)
        sections = tuple(library.resolve_sections(bytes(data)))
        if not sections:
            raise NoMatchingRomError(f"no definition matches {path}")
        primary = sections[0]
        rom = cls(
            data,
            primary.definition,
            primary.memory_model,
            Path(path),
            sections=sections,
        )
        rom._bind_checksum(settings)
        return rom

    @classmethod
    def force_open(cls, path, library: DefinitionLibrary, xmlid: str,
                   *, settings: "EditorSettings | None" = None) -> "RomImage":
        data = _read_bytes(path)
        definition, model = library.force_load(bytes(data), xmlid)
        rom = cls(data, definition, model, Path(path), force_loaded=True)
        rom._bind_checksum(settings)
        return rom

    def _bind_checksum(self, settings: "EditorSettings | None" = None) -> None:
        from ecueditor.core.checksum.builtins.ms41 import MS41Checksum
        from ecueditor.core.plugins.registry import CHECKSUMS
        override = None
        if settings is not None:
            override = settings.checksum_override.get(self.definition.romid.xmlid) or None
        # A settings override wins last; otherwise honor a declared checksum type. If neither
        # exists, the native MS41 manager is bound below for both supported framings.
        ctype = override or self.definition.checksum_type
        if ctype:
            source = "settings override" if override else "definition"
            try:
                factory = CHECKSUMS.get(ctype)
            except KeyError as exc:
                raise ChecksumError(
                    f"{source} names unregistered checksum type {ctype!r}"
                ) from exc
            try:
                manager = factory()
                if not isinstance(manager, ChecksumManager):
                    raise TypeError("does not implement the ChecksumManager contract")
            except (Exception, SystemExit) as exc:
                detail = str(exc) or type(exc).__name__
                raise ChecksumError(
                    f"{source} checksum type {ctype!r} failed to initialize: {detail}"
                ) from exc
            self.checksum_manager = manager
            return
        if not self._uses_native_ms41_checksum():
            # Imported ECU families without an explicit checksum plugin remain editable and
            # save byte-for-byte. Applying MS41 correction to arbitrary image sizes is unsafe.
            self.checksum_manager = None
            return
        # (2) Bind the MS41 manager for BOTH framings — it handles 256 KB (boot+program+cal) and
        #     24 KB (cal table only) internally. For an MS41.3 full read, skip the unverified program CRC.
        correct_program = not self._is_ms41_3_full_read()
        self.checksum_manager = MS41Checksum(correct_program=correct_program)

    def _uses_native_ms41_checksum(self) -> bool:
        size = len(self.data)
        if size == 0x6000:
            return self.definition.romid.filesize == 0x6000
        if size == 0x40000:
            return any(
                section.definition.romid.filesize in {0x6000, 0x40000}
                for section in self.sections
            )
        return False

    def _is_ms41_3_full_read(self) -> bool:
        # MS41.3 == the SS1v2 / SHINDE1 firmware family; only relevant for a 256 KB full read.
        if self.memory_model.name != "ms41_fullread":
            return False
        rid = self.definition.romid
        return (rid.ecuid or "").upper() == "SHINDE1" or rid.xmlid in {"SS1v2", "SS1v0"}

    def checksum_status(self) -> tuple[bool, list[str]]:
        manager = self.checksum_manager
        if manager is None:
            return True, ["no checksum manager bound"]
        return _run_checksum_operation(
            manager,
            "validate",
            lambda: manager.validate(bytes(self.data)),
        )

    def checksum_report(self):
        """Return per-region checksum status, or ``None`` when no manager is bound."""
        manager = self.checksum_manager
        if manager is None:
            return None
        return _run_checksum_operation(
            manager,
            "report",
            lambda: manager.report(bytes(self.data)),
        )

    @property
    def endian_default(self) -> str:
        """The ROM-wide endian default; per-table/axis definition endian overrides win."""
        return self._endian_default

    @property
    def tables(self) -> dict[str, Table]:
        self._ensure_tables()
        return self._tables

    @property
    def table_definitions(self) -> dict[str, TableDef]:
        """Preferred name lookup across sections; first section wins duplicates."""
        out: dict[str, TableDef] = {}
        for section in self.sections:
            for name, definition in section.definition.tables.items():
                if definition.storage_address is not None:
                    out.setdefault(name, definition)
        return out

    def section_definitions(self, section: str) -> dict[str, TableDef]:
        for item in self.sections:
            if item.key == section:
                return {
                    name: definition
                    for name, definition in item.definition.tables.items()
                    if definition.storage_address is not None
                }
        raise KeyError(section)

    def _ensure_tables(self) -> None:
        if self._tables_initialized:
            return
        for section in self.sections:
            endian_default = section.definition.romid.memmodel_endian or "little"
            for name, tdef in section.definition.tables.items():
                if tdef.storage_address is None:
                    continue
                key = (section.key, name)
                table = build_table(
                    tdef, self.data, section.memory_model, endian_default
                )
                self._tables_by_key[key] = table
                self._table_keys_by_object[id(table)] = key
                self._tables.setdefault(name, table)
        self._tables_initialized = True
        self._bind_live_storage_aliases()

    def section_tables(self, section: str) -> dict[str, Table]:
        self._ensure_tables()
        return {
            name: table
            for (section_key, name), table in self._tables_by_key.items()
            if section_key == section
        }

    def table(self, name: str, *, section: str | None = None) -> Table:
        self._ensure_tables()
        if section is None:
            return self._tables[name]
        return self._tables_by_key[(section, name)]

    def table_key(self, table: Table) -> tuple[str, str]:
        self._ensure_tables()
        return self._table_keys_by_object[id(table)]

    def memory_model_for(self, table: Table) -> MemoryModel:
        section_key, _name = self.table_key(table)
        return next(s.memory_model for s in self.sections if s.key == section_key)

    def endian_default_for(self, table: Table) -> str:
        section_key, _name = self.table_key(table)
        section = next(s for s in self.sections if s.key == section_key)
        return section.definition.romid.memmodel_endian or "little"

    def _bind_live_storage_aliases(self) -> None:
        """Index live DataCells by physical ROM bytes, following RomRaider's byte mapping."""
        for owner in self._tables_by_key.values():
            memory_model = self.memory_model_for(owner)
            endian_default = self.endian_default_for(owner)
            for cell, storage_address, storage_type, little in iter_storage_cells(
                owner, endian_default
            ):
                file_offset = memory_model.file_offset(storage_address)
                width = storage.storage_width(storage_type)
                binding = _CellBinding(
                    cell, owner, file_offset, storage_type, little, width
                )
                self._cell_bindings[id(cell)] = binding
                for byte_offset in range(file_offset, file_offset + width):
                    self._byte_bindings.setdefault(byte_offset, []).append(binding)
                cell.bind_change_callback(self._on_live_cell_changed)
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
        return any(t.needs_write() for t in self._tables_by_key.values())

    def _reload_validation_section(
        self, image_size: int
    ) -> ResolvedDefinitionSection:
        """Choose a section whose native framing can identify this on-disk image.

        A combined full-BIN view deliberately keeps the partial section first for preferred
        table-name lookup.  That section's declared size is not necessarily the image size,
        though, so reload identity checks must use the native full section when one exists.
        The MS41 24 KB-in-256 KB framing remains a supported fallback through
        ``model_for_match``.
        """
        sections = sorted(
            self.sections,
            key=lambda section: section.definition.romid.filesize != image_size,
        )
        for section in sections:
            try:
                fresh_model = model_for_match(section.definition.romid, image_size)
            except DefinitionError:
                continue
            if fresh_model.name == section.memory_model.name:
                return section
        raise ECUEditorError(
            "ROM on disk is incompatible with the loaded definition sections; "
            "reopen it as a separate ROM"
        )

    def reload_from_disk(self) -> None:
        """Replace the working image with a compatible copy reread from ``path``.

        Reload is deliberately in-place: open table models, 3D views, Map Studio documents,
        and the live storage-alias index all retain references to this RomImage and its Table /
        DataCell graph.  The on-disk image must therefore still match the loaded RomId, byte
        length, and framing-derived memory model.  An incompatible replacement is rejected
        before any live state is changed and must be opened as a separate ROM instead.
        """
        if self.path is None:
            raise ECUEditorError("ROM has no source file to reload")

        fresh = _read_bytes(self.path)
        if len(fresh) != len(self.data):
            raise ECUEditorError(
                "ROM file size changed from "
                f"{len(self.data)} to {len(fresh)} bytes; reopen it as a separate ROM"
            )

        validation_section = self._reload_validation_section(len(fresh))
        romid = validation_section.definition.romid
        probe = probe_offset(romid, len(fresh))
        if not self._force_loaded and not romid.matches(bytes(fresh), probe=probe):
            raise ECUEditorError(
                f"ROM on disk no longer matches the loaded definition {romid.xmlid!r}; "
                "reopen it as a separate ROM"
            )

        # Preserve the bytearray identity as well as every materialized Table/DataCell identity.
        # Table.resync() adopts changed raw bytes; set_revert_point(False) is also required for
        # the subtle case where a local edit already equals the newly read disk value.
        self.data[:] = fresh
        for table in self._tables_by_key.values():
            table.resync(
                self.data, self.memory_model_for(table), self.endian_default_for(table)
            )
            table.set_revert_point(pending_write=False)

    def flush(self) -> None:
        """Materialize pending writes, then defensively re-read any externally changed bytes.

        Normal edits already update ``self.data`` and all live aliases immediately. The second
        pass remains as a safety net for callers that mutate the working bytearray directly.
        """
        for t in self._tables_by_key.values():
            t.write_back(self.data, self.memory_model_for(t), self.endian_default_for(t))
        for t in self._tables_by_key.values():
            t.resync(self.data, self.memory_model_for(t), self.endian_default_for(t))

    def save(self, path=None) -> list[str]:
        target = Path(path) if path else self.path
        if target is None:
            raise ValueError("no path to save to")

        # Nothing below mutates the live working image or any cell baseline until the atomic
        # replacement succeeds. This preserves the only pending-write marker on every failure
        # path, including a checksum plugin that mutates its input before raising.
        candidate = bytearray(self.data)
        for table in self._tables_by_key.values():
            table.write_back(
                candidate, self.memory_model_for(table), self.endian_default_for(table)
            )
        notes: list[str] = []
        manager = self.checksum_manager
        if manager is not None:
            notes += _run_checksum_operation(
                manager,
                "update",
                lambda: manager.update(candidate),
            )
        if len(candidate) != len(self.data):
            raise ChecksumError(
                f"checksum manager changed ROM image size from {len(self.data):,} "
                f"to {len(candidate):,} bytes"
            )
        _atomic_write_bytes(target, bytes(candidate))

        self.data[:] = candidate
        for table in self._tables_by_key.values():
            table.resync(
                self.data, self.memory_model_for(table), self.endian_default_for(table)
            )
        # The durable replacement is complete; reset visual and storage-dirty baselines.
        for t in self._tables_by_key.values():
            t.set_revert_point(pending_write=False)
        self.path = target
        return notes
