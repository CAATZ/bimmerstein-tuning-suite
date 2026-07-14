from __future__ import annotations
import typing
from dataclasses import dataclass
from typing import Sequence
from ecueditor.core.comms.transport.base import SerialParams

@dataclass(frozen=True)
class MemoryRead:
    address: int
    length: int

@typing.runtime_checkable
class Protocol(typing.Protocol):
    id: str
    def serial_params(self) -> SerialParams: ...
    def build_init(self) -> bytes: ...
    def parse_init(self, response: bytes) -> str: ...        # -> ECU-ID string
    def build_read(self, module_addr: int, reads: Sequence[MemoryRead]) -> bytes: ...
    def parse_read(self, response: bytes, reads: Sequence[MemoryRead]) -> list[bytes]: ...
    def build_reset(self, module_addr: int) -> bytes: ...
