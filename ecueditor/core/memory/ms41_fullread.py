from __future__ import annotations
from ecueditor.core.plugins.registry import register

@register("memory_models", "ms41_fullread")
class MS41FullReadModel:
    name = "ms41_fullread"
    def file_offset(self, storage_address: int) -> int:
        return (0x10000 + storage_address) ^ 0x4000
