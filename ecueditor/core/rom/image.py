from __future__ import annotations
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable, TypeVar
from ecueditor.core.defs.library import DefinitionLibrary
from ecueditor.core.defs.model import RomDefinition
from ecueditor.core.memory.base import MemoryModel
from ecueditor.core.memory import model_for_match, probe_offset
from ecueditor.core.rom.cell import DataCell
from ecueditor.core.rom import storage
from ecueditor.core.rom.table import Table, build_table, iter_storage_cells
from ecueditor.core.errors import NoMatchingRomError, ECUEditorError, ChecksumError
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
                 *, force_loaded: bool = False) -> None:
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
        self._force_loaded = force_loaded

    @classmethod
    def open(cls, path, library: DefinitionLibrary,
             *, settings: "EditorSettings | None" = None) -> "RomImage":
        data = _read_bytes(path)
        match = library.match(bytes(data))
        if match is None:
            raise NoMatchingRomError(f"no definition matches {path}")
        doc, rid, model = match
        definition = doc.resolve(rid.xmlid)
        rom = cls(data, definition, model, Path(path))
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
        # (2) Bind the MS41 manager for BOTH framings — it handles 256 KB (boot+program+cal) and
        #     24 KB (cal table only) internally. For an MS41.3 full read, skip the unverified program CRC.
        correct_program = not self._is_ms41_3_full_read()
        self.checksum_manager = MS41Checksum(correct_program=correct_program)

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

        romid = self.definition.romid
        probe = probe_offset(romid, len(fresh))
        if not self._force_loaded and not romid.matches(bytes(fresh), probe=probe):
            raise ECUEditorError(
                f"ROM on disk no longer matches the loaded definition {romid.xmlid!r}; "
                "reopen it as a separate ROM"
            )
        fresh_model = model_for_match(romid, len(fresh))
        if fresh_model.name != self.memory_model.name:
            raise ECUEditorError(
                "ROM on disk requires a different memory model; reopen it as a separate ROM"
            )

        # Preserve the bytearray identity as well as every materialized Table/DataCell identity.
        # Table.resync() adopts changed raw bytes; set_revert_point(False) is also required for
        # the subtle case where a local edit already equals the newly read disk value.
        self.data[:] = fresh
        for table in self._tables.values():
            table.resync(self.data, self.memory_model, self._endian_default)
            table.set_revert_point(pending_write=False)

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
        target = Path(path) if path else self.path
        if target is None:
            raise ValueError("no path to save to")

        # Nothing below mutates the live working image or any cell baseline until the atomic
        # replacement succeeds. This preserves the only pending-write marker on every failure
        # path, including a checksum plugin that mutates its input before raising.
        candidate = bytearray(self.data)
        for table in self._tables.values():
            table.write_back(candidate, self.memory_model, self._endian_default)
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
        for table in self._tables.values():
            table.resync(self.data, self.memory_model, self._endian_default)
        # The durable replacement is complete; reset visual and storage-dirty baselines.
        for t in self._tables.values():
            t.set_revert_point(pending_write=False)
        self.path = target
        return notes
