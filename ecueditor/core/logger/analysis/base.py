from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, runtime_checkable, TYPE_CHECKING
if TYPE_CHECKING:
    from ecueditor.core.logger.engine import Sample
    from ecueditor.core.rom.image import RomImage

@dataclass
class AnalysisResult:
    kind: str
    x_label: str
    y_label: str
    points: tuple[tuple[float, float], ...]
    fit_x: tuple[float, ...] = ()
    fit_y: tuple[float, ...] = ()
    corrections: Mapping[str, float] = field(default_factory=dict)
    sample_count: int = 0
    notes: tuple[str, ...] = ()

@runtime_checkable
class AnalysisTab(Protocol):
    id: str
    title: str
    required_channels: tuple[str, ...]
    def accept(self, sample: "Sample") -> None: ...
    def result(self) -> AnalysisResult: ...
    def apply_to_rom(self, rom: "RomImage") -> list[str]: ...

class AnalysisRegistry:
    """Standalone id->class registry used by tests and the UI; the global ANALYSES registry is populated
    separately via @register. Register a class (keyed by cls.id), list ids, and build an instance."""
    def __init__(self) -> None:
        self._by_id: dict[str, type[Any]] = {}
    def register(self, cls: type[Any]) -> type[Any]:
        self._by_id[cls.id] = cls
        return cls
    def ids(self) -> list[str]:
        return list(self._by_id)
    def build(self, tab_id: str, *args: Any, **kwargs: Any) -> AnalysisTab:
        if tab_id not in self._by_id:
            raise KeyError(f"no analysis registered as {tab_id!r}")
        return self._by_id[tab_id](*args, **kwargs)
