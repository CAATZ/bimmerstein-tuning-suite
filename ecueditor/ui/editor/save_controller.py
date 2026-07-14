from __future__ import annotations
from pathlib import Path

def save_rom(rom, path: Path) -> list[str]:
    """Flush cells, recompute checksums, write bytes. Returns checksum notes."""
    return rom.save(Path(path))
