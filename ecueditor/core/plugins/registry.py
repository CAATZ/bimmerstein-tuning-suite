from __future__ import annotations
import hashlib
import importlib.util
import os
import sys
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Generic, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class PluginLoadFailure:
    path: Path
    message: str

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

def load_plugins(
    plugins_dir: str | Path,
    *,
    on_error: Callable[[PluginLoadFailure], None] | None = None,
) -> list[str]:
    loaded: list[str] = []
    d = Path(plugins_dir)
    if not d.is_dir():
        return loaded
    for py in sorted(d.glob("*.py")):
        if py.name.startswith("_"):
            continue
        canonical_path = os.path.normcase(str(py.resolve())).encode("utf-8")
        module_name = f"ecueditor_plugin_{hashlib.sha256(canonical_path).hexdigest()[:16]}"
        spec = importlib.util.spec_from_file_location(module_name, py)
        if spec and spec.loader:
            module = importlib.util.module_from_spec(spec)
            snapshots = {name: dict(registry._items) for name, registry in _BY_NAME.items()}
            previous_module = sys.modules.get(module_name)
            # Standard import machinery does this before executing a module. Some
            # decorators (notably dataclasses with postponed annotations) require
            # their defining module to be discoverable during class creation.
            sys.modules[module_name] = module
            try:
                spec.loader.exec_module(module)
            except (Exception, SystemExit) as exc:  # noqa: BLE001 - isolate plugin startup
                if previous_module is None:
                    sys.modules.pop(module_name, None)
                else:
                    sys.modules[module_name] = previous_module
                for name, registry in _BY_NAME.items():
                    registry._items.clear()
                    registry._items.update(snapshots[name])
                failure = PluginLoadFailure(py.resolve(), str(exc) or type(exc).__name__)
                if on_error is not None:
                    on_error(failure)
                warnings.warn(
                    f"Plugin {py.name} failed to load and was skipped: {failure.message}",
                    RuntimeWarning,
                    stacklevel=2,
                )
            else:
                loaded.append(py.stem)
    return loaded
