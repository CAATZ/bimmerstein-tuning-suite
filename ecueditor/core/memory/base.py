from __future__ import annotations
from typing import Protocol, runtime_checkable

@runtime_checkable
class MemoryModel(Protocol):
    name: str
    def file_offset(self, storage_address: int) -> int: ...
