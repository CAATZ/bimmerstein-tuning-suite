from __future__ import annotations
from ecueditor.core.plugins.registry import register

@register("memory_models", "direct")
class DirectMemoryModel:
    name = "direct"
    def file_offset(self, storage_address: int) -> int:
        return storage_address
