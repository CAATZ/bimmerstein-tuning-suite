from __future__ import annotations
from collections.abc import Callable
from dataclasses import dataclass, field
from ecueditor.core.scaling.scale import Scale

@dataclass
class DataCell:
    raw: int
    original: int
    scale: Scale
    storage_min: int
    storage_max: int
    # ``original`` is the user-facing Undo All / change-border baseline.  Moving that
    # baseline before Save must not make the accepted bytes disappear from the write pass.
    # This flag keeps that storage obligation separate from the visible revert-point state.
    pending_write: bool = False
    _change_callback: Callable[["DataCell"], None] | None = field(
        default=None, repr=False, compare=False
    )

    def real(self) -> float:
        return self.scale.to_real(self.raw)

    def set_real(self, value: float) -> None:
        self.set_raw(round(self.scale.to_raw(value)), clamp=True)

    def set_raw(self, value: int, *, clamp: bool = True) -> None:
        v = int(value)
        if clamp:
            v = max(self.storage_min, min(self.storage_max, v))
        if v == self.raw:
            return
        self.raw = v
        if self._change_callback is not None:
            self._change_callback(self)

    def bind_change_callback(self, callback: Callable[["DataCell"], None]) -> None:
        """Bind this storage-backed cell to its owning ROM's live alias coordinator."""
        self._change_callback = callback

    def sync_raw_from_storage(self, value: int) -> None:
        """Accept an aliased working-ROM update without recursively writing it again."""
        self.raw = int(value)

    def is_changed(self) -> bool:
        return self.raw != self.original

    def needs_write(self) -> bool:
        return self.pending_write or self.is_changed()

    def undo(self) -> None:
        self.set_raw(self.original, clamp=False)

    def set_revert_point(self, *, pending_write: bool = True) -> None:
        if pending_write and self.is_changed():
            self.pending_write = True
        self.original = self.raw
        if not pending_write:
            self.pending_write = False

    def mark_written(self) -> None:
        self.pending_write = False
