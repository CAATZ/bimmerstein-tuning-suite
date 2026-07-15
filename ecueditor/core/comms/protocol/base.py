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
class _ProtocolContract(typing.Protocol):
    id: str
    def serial_params(self) -> SerialParams: ...
    def build_init(self) -> bytes: ...
    def parse_init(self, response: bytes) -> str: ...        # -> ECU-ID string
    def build_read(self, module_addr: int, reads: Sequence[MemoryRead]) -> bytes: ...
    def parse_read(self, response: bytes, reads: Sequence[MemoryRead]) -> list[bytes]: ...
    def build_reset(self, module_addr: int) -> bytes: ...
    def response_length(self, header: bytes) -> int: ...
    def validate_response(self, response: bytes) -> bool: ...


# ``typing`` treats a runtime-checkable class literally named ``Protocol`` as its
# own marker base and gives it an empty member set.  Keep the public API name while
# backing it with a distinctly named structural contract so isinstance checks are
# meaningful on every supported Python version.
Protocol = _ProtocolContract


def create_registered_protocol(protocol_id: str) -> Protocol:
    """Build and validate a registered protocol without leaking plugin failures.

    A protocol plugin is third-party code even after its module loaded successfully.
    Normal exceptions and ``SystemExit`` from its factory become ``CommsError`` so the
    logger can report a failed connection instead of terminating the GUI.  An explicit
    ``KeyboardInterrupt`` remains a user interrupt and is never swallowed.
    """
    from ecueditor.core.errors import CommsError
    from ecueditor.core.plugins.registry import PROTOCOLS

    try:
        factory = PROTOCOLS.get(protocol_id)
    except KeyError as exc:
        raise CommsError(f"no protocol plugin registered as {protocol_id!r}") from exc
    try:
        candidate: object = factory()
        conforms = isinstance(candidate, Protocol)
    except (Exception, SystemExit) as exc:  # noqa: BLE001 - isolate optional plugin code
        detail = str(exc) or type(exc).__name__
        raise CommsError(
            f"protocol plugin {protocol_id!r} could not be created: {detail}"
        ) from exc
    if not conforms:
        raise CommsError(
            f"protocol plugin {protocol_id!r} does not implement the Protocol contract"
        )
    return typing.cast(Protocol, candidate)
