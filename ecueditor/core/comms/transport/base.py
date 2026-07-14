from __future__ import annotations
import typing
from dataclasses import dataclass

@dataclass(frozen=True)
class SerialParams:
    baud: int
    databits: int
    stopbits: int
    parity: str                        # "even" / "odd" / "none"
    connect_timeout_ms: int            # init/wake first-byte wait (cold ECU)
    response_timeout_ms: int           # normal-command first-byte wait (read/reset/poll)
    inter_byte_timeout_ms: int         # gap once a reply has started
    write_timeout_ms: int              # port write timeout

@typing.runtime_checkable
class Transport(typing.Protocol):
    def open(self, port: str, params: SerialParams) -> None: ...
    def write(self, data: bytes) -> None: ...
    def read(self, n: int, timeout_ms: int) -> bytes: ...   # raises CommsTimeout if short
    def flush_input(self) -> None: ...
    def close(self) -> None: ...
    @property
    def is_open(self) -> bool: ...

from ecueditor.core.errors import CommsError

def _try_d2xx() -> "Transport | None":
    try:
        import ftd2xx  # type: ignore
        if ftd2xx.listDevices():
            from ecueditor.core.comms.transport.d2xx import D2XXTransport
            return D2XXTransport()
    except Exception:                    # noqa: BLE001 - any import/enumeration failure => skip
        return None
    return None

def _try_pyserial() -> "Transport | None":
    try:
        import serial  # noqa: F401
    except ImportError:
        return None
    from ecueditor.core.comms.transport.pyserial import PySerialTransport
    return PySerialTransport()

def open_best_transport() -> "Transport":
    """D2XX if an FTDI device is present and ftd2xx imports; else pyserial; else CommsError."""
    for probe in (_try_d2xx, _try_pyserial):
        t = probe()
        if t is not None:
            return t
    raise CommsError("no serial backend available; install pyserial or ftd2xx, "
                     "or use ReplayTransport for tests")

def list_ports() -> list[str]:
    ports: list[str] = []
    try:
        import ftd2xx  # type: ignore
        devs = ftd2xx.listDevices() or []
        ports += [f"FTDI:{i}" for i in range(len(devs))]
    except Exception:                    # noqa: BLE001
        pass
    try:
        from serial.tools import list_ports as _lp  # type: ignore
        ports += [p.device for p in _lp.comports()]
    except Exception:                    # noqa: BLE001
        pass
    return ports
