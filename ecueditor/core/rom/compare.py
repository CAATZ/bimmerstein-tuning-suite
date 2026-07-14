from __future__ import annotations
from dataclasses import dataclass, field
from ecueditor.core.rom.table import Table
from ecueditor.core.rom.image import RomImage

def compare_tables(a: Table, b: Table) -> list[tuple[int, float, float]]:
    out: list[tuple[int, float, float]] = []
    for i, (ca, cb) in enumerate(zip(a.cells, b.cells)):
        ra, rb = ca.real(), cb.real()
        if ra != rb:
            out.append((i, ra, rb))
    return out

@dataclass
class ImageComparison:
    equal: list[str] = field(default_factory=list)
    different: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)   # in a, not in b

def compare_images(a: RomImage, b: RomImage) -> ImageComparison:
    cmp = ImageComparison()
    for name, ta in a.tables.items():
        tb = b.tables.get(name)
        if tb is None:
            cmp.missing.append(name)
        elif compare_tables(ta, tb):
            cmp.different.append(name)
        else:
            cmp.equal.append(name)
    return cmp
