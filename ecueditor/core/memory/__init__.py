from __future__ import annotations
from ecueditor.core.memory.base import MemoryModel
from ecueditor.core.memory.direct import DirectMemoryModel
from ecueditor.core.memory.ms41_fullread import MS41FullReadModel
from ecueditor.core.defs.model import RomId

_FULL = 0x40000
_CAL = 0x6000

def _is_24kb_framed(romid: RomId) -> bool:
    # A romid whose declared filesize is the 24 KB CAL uses CAL-relative storageaddresses.
    return romid.filesize == _CAL

def model_for_match(romid: RomId, image_size: int) -> MemoryModel:
    if _is_24kb_framed(romid) and image_size != _CAL:
        return MS41FullReadModel()     # CAL-relative SA in a 256 KB image needs fo()
    return DirectMemoryModel()          # 24 KB image, or a 256 KB-framed romid with raw offsets

def probe_offset(romid: RomId, image_size: int) -> int:
    addr = romid.internal_id_address or 0
    if _is_24kb_framed(romid) and image_size != _CAL:
        return (0x10000 + addr) ^ 0x4000   # probe the id bytes at fo(addr) in a full read
    return addr                             # raw probe otherwise
