from __future__ import annotations
import time
from typing import Any, Callable
from ecueditor.core.comms.transport.base import SerialParams
from ecueditor.core.comms.transport.halfduplex import HalfDuplexTransport
from ecueditor.core.errors import CommsError
from ecueditor.core.plugins.registry import register

_PARITY = {"even": "E", "odd": "O", "none": "N"}

@register("transports", "pyserial")
class PySerialTransport(HalfDuplexTransport):
    name = "pyserial"

    def __init__(self, *, strip_echo: bool = True,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        super().__init__(strip_echo=strip_echo, sleep=sleep)
        self._dev: Any = None

    def _open_device(self, port: str, params: SerialParams) -> None:
        try:
            import serial  # type: ignore
        except ImportError as exc:                       # pragma: no cover - env dependent
            raise CommsError("pyserial not installed (pip install pyserial)") from exc
        self._dev = serial.Serial(
            port=port, baudrate=params.baud, bytesize=params.databits,
            stopbits=params.stopbits, parity=_PARITY[params.parity],
            timeout=params.response_timeout_ms / 1000.0,
            write_timeout=params.write_timeout_ms / 1000.0,
            dsrdtr=False,      # disable DSR/DTR flow control — can hang the OS on K-line adapters
            rtscts=False,      # disable RTS/CTS hardware flow control
        )
        try:
            self._dev.setDTR(False)
            self._dev.setRTS(False)
        except Exception:                                # pragma: no cover - adapter dependent
            pass

    def _raw_write(self, data: bytes) -> None:
        self._dev.write(bytes(data))

    def _raw_read(self, n: int) -> bytes:
        return bytes(self._dev.read(n))

    def _set_read_timeout(self, timeout_ms: int) -> None:
        self._dev.timeout = timeout_ms / 1000.0

    def _raw_flush(self) -> None:
        self._dev.flush()

    def _raw_reset_input(self) -> None:
        if self._dev is not None:
            self._dev.reset_input_buffer()

    def _close_device(self) -> None:
        if self._dev is not None:
            self._dev.close()
            self._dev = None

    @property
    def is_open(self) -> bool:
        return self._dev is not None and bool(self._dev.is_open)
