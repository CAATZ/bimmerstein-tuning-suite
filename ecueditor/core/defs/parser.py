from __future__ import annotations
import re
from pathlib import Path
from xml.etree import ElementTree as ET
from ecueditor.core.defs.model import RomId, RomDefinition
from ecueditor.core.memory import image_size_compatible, probe_offset, model_for_match
from ecueditor.core.memory.base import MemoryModel
from ecueditor.core.errors import DefinitionError


def _hexint(v: str | None) -> int | None:
    if v is None: return None
    try: return int(v, 16)
    except ValueError:
        try: return int(v)
        except ValueError: return None


def _dec(v: str | None) -> int | None:
    if v is None: return None
    try: return int(v)
    except ValueError: return None


def _filesize(v: str | None) -> int | None:
    if not v: return None
    s = v.strip().lower()
    if s.endswith("kb"): return int(s[:-2]) * 1024
    if s.endswith("mb"): return int(s[:-2]) * 1024 * 1024
    if s.endswith("b"):  return int(s[:-1])
    try: return int(s)
    except ValueError: return None


def _load_root(path: str | Path) -> ET.Element:
    try:
        raw = Path(path).read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError as exc:
        raise DefinitionError(f"definition file not found: {path}") from exc
    raw = re.sub(r"<!DOCTYPE.*?\]>", "", raw, flags=re.DOTALL)
    raw = re.sub(r"&(?!(amp|lt|gt|quot|apos);)", "&amp;", raw)
    try:
        return ET.fromstring(raw)
    except ET.ParseError as exc:
        raise DefinitionError(f"cannot parse {path}: {exc}") from exc


class DefinitionDocument:
    def __init__(self, path: str | Path, root: ET.Element) -> None:
        self.path = Path(path)
        self._root = root
        self._rom_nodes = list(root.iter("rom"))

    @property
    def rom_ids(self) -> list[RomId]:
        out: list[RomId] = []
        for rom in self._rom_nodes:
            rid = rom.find("romid")
            if rid is None: continue
            mm = rid.find("memmodel")
            out.append(RomId(
                xmlid=rid.findtext("xmlid") or "",
                internal_id_address=_hexint(rid.findtext("internalidaddress")),
                internal_id_string=rid.findtext("internalidstring"),
                ecuid=rid.findtext("ecuid"),
                filesize=_filesize(rid.findtext("filesize")),
                memmodel=mm.text if mm is not None else None,
                memmodel_endian=mm.get("endian") if mm is not None else None,
                no_ram_offset=rid.find("noramoffset") is not None,
            ))
        return out

    def find_matching(self, image: bytes) -> tuple[RomId, MemoryModel] | None:
        size = len(image)
        for r in self.rom_ids:
            if not image_size_compatible(r, size):
                continue
            probe = probe_offset(r, size)              # framing-correct probe location (fo() or raw)
            if r.matches(image, probe=probe):
                return r, model_for_match(r, size)
        return None

    def resolve(self, xmlid: str) -> "RomDefinition":
        from ecueditor.core.defs.inheritance import resolve_rom
        by_xid: dict[str, list] = {}
        for rom in self._rom_nodes:
            rid = rom.find("romid")
            xid = rid.findtext("xmlid") if rid is not None else None
            if xid: by_xid.setdefault(xid, []).append(rom)
        if xmlid not in by_xid:
            raise DefinitionError(f"no <rom> with xmlid {xmlid!r}")
        return resolve_rom(by_xid, xmlid)


def parse_definition_file(path: str | Path) -> DefinitionDocument:
    return DefinitionDocument(path, _load_root(path))
