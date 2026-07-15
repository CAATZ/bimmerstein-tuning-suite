from __future__ import annotations
import typing
import warnings
from collections.abc import Sequence
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


@typing.runtime_checkable
class DiscoverableTransportFactory(typing.Protocol):
    """Optional registry convention that makes a transport reachable from the logger UI."""

    def __call__(self) -> Transport: ...
    def claims_port(self, port: str) -> bool: ...
    def list_ports(self) -> Sequence[str]: ...

from ecueditor.core.errors import CommsError


def _plugin_failure_detail(exc: BaseException) -> str:
    return str(exc) or type(exc).__name__

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

def open_best_transport(port: str | None = None) -> "Transport":
    """Return the backend that owns *port*.

    Registered factories may claim their own explicit port namespaces first.
    Otherwise ``FTDI:n`` belongs to D2XX and every other named port belongs to
    pyserial. With no port (legacy/probing callers), retain the historical
    D2XX-then-pyserial preference.
    """
    if port:
        plugin_transport = _registered_transport_for_port(port)
        if plugin_transport is not None:
            return plugin_transport
        is_ftdi = port.upper().startswith("FTDI:")
        transport = _try_d2xx() if is_ftdi else _try_pyserial()
        if transport is not None:
            return transport
        backend = "D2XX/ftd2xx" if is_ftdi else "pyserial"
        raise CommsError(f"selected port {port!r} requires the {backend} backend")
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
    from ecueditor.core.plugins.registry import TRANSPORTS
    for key in TRANSPORTS.keys():
        try:
            factory = TRANSPORTS.get(key)
            discover = getattr(factory, "list_ports", None)
            if not callable(discover):
                continue
            ports.extend(str(port) for port in discover())
        except (Exception, SystemExit) as exc:  # noqa: BLE001 - isolate optional plugin code
            warnings.warn(
                f"Transport plugin {key!r} failed to list ports and was skipped: "
                f"{_plugin_failure_detail(exc)}",
                RuntimeWarning,
                stacklevel=2,
            )
    return list(dict.fromkeys(ports))


def _registered_transport_for_port(port: str) -> Transport | None:
    from ecueditor.core.plugins.registry import TRANSPORTS

    claimants: list[tuple[str, typing.Callable[..., Transport]]] = []
    for key in TRANSPORTS.keys():
        try:
            factory = TRANSPORTS.get(key)
            claim = getattr(factory, "claims_port", None)
            if not callable(claim):
                continue
            claimed = bool(claim(port))
        except (Exception, SystemExit) as exc:  # noqa: BLE001 - isolate optional plugin code
            warnings.warn(
                f"Transport plugin {key!r} failed while checking {port!r} and was skipped: "
                f"{_plugin_failure_detail(exc)}",
                RuntimeWarning,
                stacklevel=2,
            )
            continue
        if claimed:
            claimants.append((key, factory))
    if len(claimants) > 1:
        names = ", ".join(key for key, _factory in claimants)
        raise CommsError(f"port {port!r} is claimed by multiple transport plugins: {names}")
    if not claimants:
        return None
    key, factory = claimants[0]
    try:
        transport = factory()
        conforms = isinstance(transport, Transport)
    except (Exception, SystemExit) as exc:  # noqa: BLE001 - normalize plugin construction failures
        raise CommsError(
            f"transport plugin {key!r} could not be created: {_plugin_failure_detail(exc)}"
        ) from exc
    if not conforms:
        raise CommsError(f"transport plugin {key!r} does not implement the Transport contract")
    return transport
