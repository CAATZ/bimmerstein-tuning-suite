from __future__ import annotations
import importlib.util
from pathlib import Path
from typing import Callable, Generic, TypeVar

T = TypeVar("T")

class Registry(Generic[T]):
    def __init__(self, kind: str) -> None:
        self.kind = kind
        self._items: dict[str, Callable[..., T]] = {}
    def register(self, key: str, factory: Callable[..., T]) -> None:
        self._items[key] = factory
    def get(self, key: str) -> Callable[..., T]:
        if key not in self._items:
            raise KeyError(f"no {self.kind} registered as {key!r}")
        return self._items[key]
    def keys(self) -> list[str]:
        return list(self._items)

PROTOCOLS: Registry = Registry("protocol")
TRANSPORTS: Registry = Registry("transport")
CHECKSUMS: Registry = Registry("checksum")
IMPORTERS: Registry = Registry("importer")
ANALYSES: Registry = Registry("analysis")
EXTERNALS: Registry = Registry("external")
MEMORY_MODELS: Registry = Registry("memory_model")

_BY_NAME = {"protocols": PROTOCOLS, "transports": TRANSPORTS, "checksums": CHECKSUMS,
            "importers": IMPORTERS, "analyses": ANALYSES, "externals": EXTERNALS,
            "memory_models": MEMORY_MODELS}

def register(kind: str, key: str):
    def deco(cls: type) -> type:
        _BY_NAME[kind].register(key, cls)
        return cls
    return deco

def load_plugins(plugins_dir: str | Path) -> list[str]:
    loaded: list[str] = []
    d = Path(plugins_dir)
    if not d.is_dir():
        return loaded
    for py in sorted(d.glob("*.py")):
        if py.name.startswith("_"):
            continue
        spec = importlib.util.spec_from_file_location(f"ecueditor_plugin_{py.stem}", py)
        if spec and spec.loader:
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            loaded.append(py.stem)
    return loaded
