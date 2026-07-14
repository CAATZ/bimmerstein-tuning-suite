from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence
from ecueditor.core.defs.parser import DefinitionDocument
from ecueditor.core.defs.model import RomId, RomDefinition
from ecueditor.core.defs.importers import romraider_xml  # noqa: F401  (register native importer)
from ecueditor.core.defs.importers.base import DefinitionImporter
from ecueditor.core.memory import model_for_match
from ecueditor.core.memory.base import MemoryModel
from ecueditor.core.plugins.registry import IMPORTERS
from ecueditor.core.errors import DefinitionError


@dataclass(frozen=True)
class DocumentStatus:
    path: Path
    ok: bool
    error: str = ""
    rom_count: int = 0


class DefinitionLibrary:
    def __init__(self, paths: Sequence[str | Path], *, strict: bool = False) -> None:
        self._paths = [Path(p) for p in paths]
        self._docs: list[DefinitionDocument] = []
        self._statuses: list[DocumentStatus] = []
        for p in self._paths:
            try:
                doc = self._importer_for(p).import_document(p)
            except Exception as exc:  # noqa: BLE001 — any import failure is a per-path fact
                if strict:
                    raise DefinitionError(str(exc)) from exc
                self._statuses.append(DocumentStatus(path=p, ok=False, error=str(exc)))
                continue
            self._docs.append(doc)
            self._statuses.append(DocumentStatus(path=p, ok=True, rom_count=len(doc.rom_ids)))

    def document_statuses(self) -> list[DocumentStatus]:
        return list(self._statuses)

    def _importer_for(self, path: Path) -> DefinitionImporter:
        for key in IMPORTERS.keys():
            imp = IMPORTERS.get(key)()
            if imp.can_import(path):
                return imp
        raise DefinitionError(f"no importer can load {path}")

    def match(self, image: bytes) -> tuple[DefinitionDocument, RomId, MemoryModel] | None:
        for doc in self._docs:                       # priority = list order
            hit = doc.find_matching(image)            # framing-aware -> (RomId, MemoryModel)
            if hit is not None:
                rid, model = hit
                return doc, rid, model
        return None

    def resolve_for(self, image: bytes) -> RomDefinition | None:
        m = self.match(image)
        if m is None:
            return None
        doc, rid, _ = m
        return doc.resolve(rid.xmlid)

    def force_load(self, image: bytes, xmlid: str) -> tuple[RomDefinition, MemoryModel]:
        for doc in self._docs:
            rid = next((r for r in doc.rom_ids if r.xmlid == xmlid), None)
            if rid is not None:
                return doc.resolve(xmlid), model_for_match(rid, len(image))
        raise DefinitionError(f"no definition with xmlid {xmlid!r} in library")
