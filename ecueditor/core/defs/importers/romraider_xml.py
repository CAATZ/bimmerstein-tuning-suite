from __future__ import annotations
from pathlib import Path
from ecueditor.core.plugins.registry import register
from ecueditor.core.defs.parser import parse_definition_file, DefinitionDocument

@register("importers", "romraider_xml")
class RomRaiderXmlImporter:
    name = "romraider_xml"
    def can_import(self, path: str | Path) -> bool:
        return str(path).lower().endswith(".xml")
    def import_document(self, path: str | Path) -> DefinitionDocument:
        return parse_definition_file(path)
