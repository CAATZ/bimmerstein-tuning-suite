from __future__ import annotations
from collections import Counter, defaultdict
from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Sequence
from ecueditor.core.defs.parser import DefinitionDocument
from ecueditor.core.defs.model import RomId, RomDefinition
from ecueditor.core.defs.importers import romraider_xml  # noqa: F401  (register native importer)
from ecueditor.core.defs.importers.base import DefinitionImporter
from ecueditor.core.memory import model_for_match
from ecueditor.core.memory.base import MemoryModel
from ecueditor.core.memory.linear_offset import LinearOffsetMemoryModel
from ecueditor.core.plugins.registry import IMPORTERS
from ecueditor.core.errors import DefinitionError

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class DocumentStatus:
    path: Path
    ok: bool
    error: str = ""
    rom_count: int = 0


@dataclass(frozen=True)
class ResolvedDefinitionSection:
    key: str
    label: str
    definition: RomDefinition
    memory_model: MemoryModel


@dataclass(frozen=True)
class _LinearFraming:
    partial_size: int
    full_size: int
    offset: int
    partial_memmodel: str | None
    full_memmodel: str | None
    support: int


def _format_image_size(size: int) -> str:
    if size % (1024 * 1024) == 0:
        return f"{size // (1024 * 1024)} MB"
    if size % 1024 == 0:
        return f"{size // 1024} KB"
    return f"{size:,} bytes"


class DefinitionLibrary:
    def __init__(self, paths: Sequence[str | Path], *, strict: bool = False) -> None:
        self._paths = [Path(p) for p in paths]
        self._docs: list[DefinitionDocument] = []
        self._statuses: list[DocumentStatus] = []
        self._linear_framing_cache: dict[DefinitionDocument, tuple[_LinearFraming, ...]] = {}
        for p in self._paths:
            try:
                doc = self._importer_for(p).import_document(p)
                if not isinstance(doc, DefinitionDocument):
                    raise TypeError(
                        "definition importer must return a DefinitionDocument, "
                        f"not {type(doc).__name__}"
                    )
                rom_count = len(doc.rom_ids)
            except (Exception, SystemExit) as exc:  # any plugin import failure is a per-path fact
                if strict:
                    raise DefinitionError(str(exc)) from exc
                self._statuses.append(DocumentStatus(path=p, ok=False, error=str(exc)))
                continue
            self._docs.append(doc)
            self._statuses.append(DocumentStatus(path=p, ok=True, rom_count=rom_count))

    def document_statuses(self) -> list[DocumentStatus]:
        return list(self._statuses)

    def _importer_for(self, path: Path) -> DefinitionImporter:
        for key in IMPORTERS.keys():
            try:
                imp = IMPORTERS.get(key)()
                if imp.can_import(path):
                    return imp
            except (Exception, SystemExit) as exc:
                _log.warning(
                    "definition importer %r failed to probe %s: %s",
                    key,
                    path,
                    exc,
                    exc_info=exc,
                )
        raise DefinitionError(f"no importer can load {path}")

    def match(self, image: bytes) -> tuple[DefinitionDocument, RomId, MemoryModel] | None:
        matches = self.matches(image)
        return matches[0] if matches else None

    def matches(self, image: bytes) -> list[tuple[DefinitionDocument, RomId, MemoryModel]]:
        out: list[tuple[DefinitionDocument, RomId, MemoryModel]] = []
        for doc in self._docs:                       # priority = list order
            out.extend((doc, rid, model) for rid, model in doc.find_matches(image))
        return out

    def _linear_framings(self, doc: DefinitionDocument) -> tuple[_LinearFraming, ...]:
        """Infer only strongly corroborated linear partial-to-full address mappings.

        Duplicate XML IDs at two declared sizes establish candidate framings. Same-name,
        concrete tables then have to overwhelmingly prove one in-bounds address delta. Evidence
        is pooled by size/memory-model pair so a well-described ROM can prove the framing used by
        a sparse sibling ROM from the same definition document.
        """
        cached = self._linear_framing_cache.get(doc)
        if cached is not None:
            return cached

        grouped: dict[str, list[RomId]] = defaultdict(list)
        for rid in doc.rom_ids:
            if rid.xmlid and rid.filesize is not None and rid.internal_id_string:
                grouped[rid.xmlid].append(rid)

        evidence: dict[
            tuple[int, int, str | None, str | None], Counter[int]
        ] = defaultdict(Counter)
        for variants in grouped.values():
            for partial in variants:
                for full in variants:
                    if partial.filesize is None or full.filesize is None:
                        continue
                    if partial.filesize >= full.filesize:
                        continue
                    if partial.internal_id_string != full.internal_id_string:
                        continue
                    partial_def = doc.resolve_match(partial)
                    full_def = doc.resolve_match(full)
                    key = (
                        partial.filesize,
                        full.filesize,
                        partial.memmodel,
                        full.memmodel,
                    )
                    for name in partial_def.tables.keys() & full_def.tables.keys():
                        partial_address = partial_def.tables[name].storage_address
                        full_address = full_def.tables[name].storage_address
                        if partial_address is None or full_address is None:
                            continue
                        delta = full_address - partial_address
                        if delta <= 0 or delta + partial.filesize > full.filesize:
                            continue
                        evidence[key][delta] += 1

        relationships: list[_LinearFraming] = []
        for key, counts in evidence.items():
            ranked = counts.most_common(2)
            if not ranked:
                continue
            offset, support = ranked[0]
            total = sum(counts.values())
            # Sparse/conflicting documents stay in the safe single-section path.
            if support < 3 or support * 5 < total * 4:
                continue
            if len(ranked) > 1 and ranked[1][1] == support:
                continue
            partial_size, full_size, partial_memmodel, full_memmodel = key
            relationships.append(_LinearFraming(
                partial_size=partial_size,
                full_size=full_size,
                offset=offset,
                partial_memmodel=partial_memmodel,
                full_memmodel=full_memmodel,
                support=support,
            ))

        result = tuple(sorted(
            relationships,
            key=lambda item: (-item.support, item.partial_size, item.full_size, item.offset),
        ))
        self._linear_framing_cache[doc] = result
        return result

    def _linear_framing_for(
        self, doc: DefinitionDocument, rid: RomId,
    ) -> _LinearFraming | None:
        for framing in self._linear_framings(doc):
            if (
                rid.filesize == framing.partial_size
                and rid.memmodel == framing.partial_memmodel
            ) or (
                rid.filesize == framing.full_size
                and rid.memmodel == framing.full_memmodel
            ):
                return framing
        return None

    @staticmethod
    def _paired_variant(
        doc: DefinitionDocument,
        rid: RomId,
        *,
        filesize: int,
        memmodel: str | None,
    ) -> RomId | None:
        return next((
            candidate
            for candidate in doc.rom_ids
            if candidate.xmlid == rid.xmlid
            and candidate.internal_id_string == rid.internal_id_string
            and candidate.filesize == filesize
            and candidate.memmodel == memmodel
        ), None)

    def resolve_sections(self, image: bytes) -> list[ResolvedDefinitionSection]:
        """Resolve the best match for each address framing present in one image."""
        matches = self.matches(image)
        if not matches:
            return []

        def section_key(rid: RomId) -> str:
            if rid.filesize == 0x6000:
                return "partial"
            if rid.filesize == 0x40000 and len(image) == 0x40000:
                return "full"
            return "rom"

        first_doc, first_rid, first_model = matches[0]
        first_key = section_key(first_rid)
        chosen: list[tuple[DefinitionDocument, RomId, MemoryModel, str]]
        if first_key in {"partial", "full"}:
            selected: dict[str, tuple[DefinitionDocument, RomId, MemoryModel]] = {}
            for match in matches:
                key = section_key(match[1])
                if key in {"partial", "full"}:
                    selected.setdefault(key, match)
            chosen = [
                (*selected[key], key)
                for key in ("partial", "full")
                if key in selected
            ]
        else:
            framing = self._linear_framing_for(first_doc, first_rid)
            if framing is None:
                chosen = [(first_doc, first_rid, first_model, "rom")]
            elif first_rid.filesize == framing.partial_size:
                chosen = [(first_doc, first_rid, first_model, "partial")]
            else:
                partial = self._paired_variant(
                    first_doc,
                    first_rid,
                    filesize=framing.partial_size,
                    memmodel=framing.partial_memmodel,
                )
                chosen = []
                if partial is not None:
                    partial_model: MemoryModel = LinearOffsetMemoryModel(framing.offset)
                    chosen.append((
                        first_doc,
                        partial,
                        partial_model,
                        "partial",
                    ))
                chosen.append((first_doc, first_rid, first_model, "full"))

        return [
            ResolvedDefinitionSection(
                key=key,
                label=(
                    "ROM tables"
                    if key == "rom"
                    else f"{key.title()} BIN ({_format_image_size(rid.filesize or len(image))})"
                ),
                definition=doc.resolve_match(rid),
                memory_model=model,
            )
            for doc, rid, model, key in chosen
        ]

    def resolve_for(self, image: bytes) -> RomDefinition | None:
        m = self.match(image)
        if m is None:
            return None
        doc, rid, _ = m
        return doc.resolve_match(rid)

    def force_load(self, image: bytes, xmlid: str) -> tuple[RomDefinition, MemoryModel]:
        for doc in self._docs:
            rid = next((r for r in doc.rom_ids if r.xmlid == xmlid), None)
            if rid is not None:
                return doc.resolve_match(rid), model_for_match(rid, len(image))
        raise DefinitionError(f"no definition with xmlid {xmlid!r} in library")
