from __future__ import annotations
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

@dataclass(frozen=True)
class RegionStatus:
    name: str        # "Boot" | "Program" | "Calibration" | "Verify switch"
    status: str      # "ok" | "mismatch" | "n/a" | "on" | "off" | "unknown"
    detail: str = ""

@dataclass(frozen=True)
class ChecksumReport:
    regions: tuple[RegionStatus, ...]

    @property
    def ok(self) -> bool:
        return not any(r.status == "mismatch" for r in self.regions)

@runtime_checkable
class ChecksumManager(Protocol):
    name: str
    def validate(self, data: bytes) -> tuple[bool, list[str]]: ...
    def update(self, data: bytearray) -> list[str]: ...
    def report(self, data: bytes) -> ChecksumReport: ...
