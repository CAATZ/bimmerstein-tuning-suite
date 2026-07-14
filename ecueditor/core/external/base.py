from __future__ import annotations
import typing
from dataclasses import dataclass
from typing import Mapping

@dataclass(frozen=True)
class ExternalDataItem:
    id: str
    name: str
    units: str

@typing.runtime_checkable
class ExternalDataSource(typing.Protocol):
    name: str
    def items(self) -> list[ExternalDataItem]: ...       # channels this source exposes
    def read(self) -> Mapping[str, float]: ...            # item id -> current value (polled alongside ECU)
