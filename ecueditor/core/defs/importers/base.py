from __future__ import annotations
from pathlib import Path
from typing import Protocol, runtime_checkable
from ecueditor.core.defs.parser import DefinitionDocument

@runtime_checkable
class DefinitionImporter(Protocol):
    name: str
    def can_import(self, path: str | Path) -> bool: ...
    def import_document(self, path: str | Path) -> DefinitionDocument: ...
