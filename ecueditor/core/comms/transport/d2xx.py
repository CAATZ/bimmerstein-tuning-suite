from __future__ import annotations
import time
from typing import Any, Callable
from ecueditor.core.comms.transport.base import SerialParams
from ecueditor.core.comms.transport.halfduplex import HalfDuplexTransport
from ecueditor.core.errors import CommsError
from ecueditor.core.plugins.registry import register

_PARITY = {"none": 0, "odd": 1, "even": 2}
_STOP = {1: 0, 2: 2}                                  # FTDI: FT_STOP_BITS_1=0, FT_STOP_BITS_2=2

@register("transports", "d2xx")
class D2XXTransport(HalfDuplexTransport):
    name = "d2xx"

    def __init__(self, *, strip_echo: bool = True,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        super().__init__(strip_echo=strip_echo, sleep=sleep)
        self._dev: Any = None
        self._write_timeout_ms: int = 3000

    def _open_device(self, port: str, params: SerialParams) -> None:
        try:
            import ftd2xx  # type: ignore
        except ImportError as exc:                       # pragma: no cover - env dependent
            raise CommsError("ftd2xx not installed (pip install ftd2xx)") from exc
        if not port.upper().startswith("FTDI:"):
            raise CommsError(f"D2XX requires an FTDI:n port, got {port!r}")
        try:
            index = int(port.split(":", 1)[1])
        except (IndexError, ValueError) as exc:
            raise CommsError(f"invalid FTDI port {port!r}") from exc
        self._dev = ftd2xx.open(index)
        self._dev.setBaudRate(params.baud)
        self._dev.setDataCharacteristics(params.databits, _STOP[params.stopbits], _PARITY[params.parity])
        self._write_timeout_ms = params.write_timeout_ms
        # NB: the 5 ms echo budget (halfduplex.ECHO_READ_MS) assumes an FT232 latency timer of
        # ~1-2 ms (standard for BMW diag/flash tools); the default 16 ms slows every partial read.
        self._dev.setTimeouts(params.response_timeout_ms, params.write_timeout_ms)
        try:
            self._dev.setLatencyTimer(2)   # FT232 default is 16 ms; the 5 ms echo-read budget
                                           # (halfduplex.ECHO_READ_MS) needs ~1-2 ms. Best-effort:
                                           # a chip that rejects it still works (just slower).
        except Exception:                  # pragma: no cover - adapter dependent
            pass

    def _raw_write(self, data: bytes) -> None:
        self._dev.write(bytes(data))

    def _raw_read(self, n: int) -> bytes:
        return bytes(self._dev.read(n))

    def _set_read_timeout(self, timeout_ms: int) -> None:
        self._dev.setTimeouts(timeout_ms, self._write_timeout_ms)

    def _raw_reset_input(self) -> None:
        if self._dev is not None:
            self._dev.purge()

    def _close_device(self) -> None:
        if self._dev is not None:
            self._dev.close()
            self._dev = None

    @property
    def is_open(self) -> bool:
        return self._dev is not None
