from __future__ import annotations
from ecueditor.core.memory.base import MemoryModel
from ecueditor.core.memory.direct import DirectMemoryModel
from ecueditor.core.memory.linear_offset import LinearOffsetMemoryModel
from ecueditor.core.memory.ms41_fullread import MS41FullReadModel
from ecueditor.core.defs.model import RomId
from ecueditor.core.errors import DefinitionError

_FULL = 0x40000
_CAL = 0x6000


def image_size_compatible(romid: RomId, image_size: int) -> bool:
    """Whether ``image_size`` is safe to address with this RomId framing.

    Imported definitions may describe arbitrary image sizes.  Their declared
    size must match exactly.  The sole framing exception is the native MS41
    convention where a 24 KB CAL-relative definition can address a 256 KB full
    read through ``fo()``.  A missing filesize retains the legacy importer
    contract: no size constraint and direct addressing, with every concrete
    table/axis access still protected by ROM storage bounds checks.
    """
    if image_size <= 0:
        return False
    declared = romid.filesize
    if declared is None:
        return True
    return image_size == declared or (declared == _CAL and image_size == _FULL)


def _require_compatible_image_size(romid: RomId, image_size: int) -> None:
    if image_size <= 0:
        raise DefinitionError("ROM image size must be positive")
    if not image_size_compatible(romid, image_size):
        declared = romid.filesize
        raise DefinitionError(
            f"incompatible ROM image size {image_size:,} bytes for definition "
            f"{romid.xmlid!r}, which declares {declared:,} bytes"
        )

def _is_24kb_framed(romid: RomId) -> bool:
    # A romid whose declared filesize is the 24 KB CAL uses CAL-relative storageaddresses.
    return romid.filesize == _CAL

def model_for_match(romid: RomId, image_size: int) -> MemoryModel:
    _require_compatible_image_size(romid, image_size)
    if _is_24kb_framed(romid) and image_size == _FULL:
        return MS41FullReadModel()     # CAL-relative SA in a 256 KB image needs fo()
    return DirectMemoryModel()          # 24 KB image, or a 256 KB-framed romid with raw offsets

def probe_offset(romid: RomId, image_size: int) -> int:
    _require_compatible_image_size(romid, image_size)
    addr = romid.internal_id_address or 0
    if _is_24kb_framed(romid) and image_size == _FULL:
        return (0x10000 + addr) ^ 0x4000   # probe the id bytes at fo(addr) in a full read
    return addr                             # raw probe otherwise


__all__ = [
    "DirectMemoryModel",
    "LinearOffsetMemoryModel",
    "MS41FullReadModel",
    "MemoryModel",
    "image_size_compatible",
    "model_for_match",
    "probe_offset",
]
