from __future__ import annotations

from dataclasses import dataclass

from ecueditor.core.plugins.registry import register


@register("memory_models", "linear_offset")
@dataclass
class LinearOffsetMemoryModel:
    """Map a partial definition's addresses into a proven linear region of a full BIN."""

    offset: int
    name: str = "linear_offset"

    def file_offset(self, storage_address: int) -> int:
        return self.offset + storage_address
