from __future__ import annotations
from typing import Sequence
from ecueditor.core.comms.protocol.base import MemoryRead, Protocol
from ecueditor.core.comms.protocol.ds2 import ds2_validate
from ecueditor.core.comms.transport.base import Transport
from ecueditor.core.errors import CommsError, CommsTimeout

class ConnectionManager:
    MAX_RETRIES = 2

    def __init__(self, transport: Transport, protocol: Protocol) -> None:
        self._transport = transport
        self._protocol = protocol
        self._module = getattr(protocol, "MODULE_ECU", 0x12)
        self._ecu_id: str | None = None

    def open(self, port: str) -> None:
        self._transport.open(port, self._protocol.serial_params())

    def init(self) -> str:
        p = self._protocol.serial_params()
        last: Exception | None = None
        for _ in range(self.MAX_RETRIES + 1):
            try:
                resp = self._exchange(self._protocol.build_init(),
                                      p.connect_timeout_ms, p.inter_byte_timeout_ms)
                self._ecu_id = self._protocol.parse_init(resp)
                return self._ecu_id
            except (CommsTimeout, CommsError) as exc:
                last = exc
                self._transport.flush_input()          # settle before the next wake attempt
                continue
        raise last or CommsTimeout("init failed after retries")

    def read_memory(self, reads: Sequence[MemoryRead]) -> list[bytes]:
        p = self._protocol.serial_params()
        out: list[bytes] = []
        for r in reads:
            frame = self._protocol.build_read(self._module, [r])
            resp = self._exchange_with_retry(frame, p.response_timeout_ms, p.inter_byte_timeout_ms)
            out += self._protocol.parse_read(resp, [r])
        return out

    def telegram_setup(self, entries: Sequence[tuple[int, int]]) -> None:
        if not self.supports_telegram:
            raise CommsError(f"protocol {type(self._protocol).__name__} does not support telegram polling")
        p = self._protocol.serial_params()
        frame = self._protocol.build_telegram_setup(self._module, entries)  # type: ignore[attr-defined]
        resp = self._exchange_with_retry(frame, p.response_timeout_ms, p.inter_byte_timeout_ms)
        self._protocol.parse_telegram_setup_ack(resp)  # type: ignore[attr-defined]

    def telegram_poll(self, expected_length: int = 0) -> bytes:
        if not self.supports_telegram:
            raise CommsError(f"protocol {type(self._protocol).__name__} does not support telegram polling")
        p = self._protocol.serial_params()
        frame = self._protocol.build_telegram_poll(self._module)  # type: ignore[attr-defined]
        resp = self._exchange_with_retry(frame, p.response_timeout_ms, p.inter_byte_timeout_ms)
        return self._protocol.parse_telegram_poll(  # type: ignore[attr-defined]
            resp, expected_length=expected_length,
        )

    def reset(self) -> None:
        p = self._protocol.serial_params()
        self._exchange(self._protocol.build_reset(self._module), p.response_timeout_ms, p.inter_byte_timeout_ms)

    def close(self) -> None:
        self._transport.close()
        self._ecu_id = None

    @property
    def ecu_id(self) -> str | None:
        return self._ecu_id

    @property
    def ident(self) -> bytes:
        # (Phase 8c, spec §9.2) full DS2 identification block from the last init; b"" before init
        # or for protocols that don't retain it.
        return getattr(self._protocol, "last_ident", b"")

    @property
    def cal_id(self) -> str:
        # Derived from the ident block by the protocol; "" when the protocol lacks the API or the
        # CAL-ID offset is undetermined (chip then shows "—").
        derive = getattr(self._protocol, "cal_id_from_ident", None)
        return derive(self.ident) if derive is not None else ""

    @property
    def supports_telegram(self) -> bool:
        return all(callable(getattr(self._protocol, name, None)) for name in (
            "build_telegram_setup", "parse_telegram_setup_ack",
            "build_telegram_poll", "parse_telegram_poll",
        ))

    # -- internals -----------------------------------------------------------
    def _recv_frame(self, first_byte_ms: int, inter_byte_ms: int) -> bytes:
        header = self._transport.read(2, first_byte_ms)      # [addr][len] — waits for the reply onset
        total = header[1]
        if total < 4:
            raise CommsError(f"implausible DS2 len byte {total}")
        rest = self._transport.read(total - 2, inter_byte_ms)  # the reply is streaming now
        return header + rest

    def _exchange(self, frame: bytes, first_byte_ms: int, inter_byte_ms: int) -> bytes:
        self._transport.flush_input()
        self._transport.write(frame)
        return self._recv_frame(first_byte_ms, inter_byte_ms)

    def _exchange_with_retry(self, frame: bytes, first_byte_ms: int, inter_byte_ms: int) -> bytes:
        last: Exception | None = None
        for _ in range(self.MAX_RETRIES + 1):
            try:
                resp = self._exchange(frame, first_byte_ms, inter_byte_ms)
            except CommsTimeout as exc:
                last = exc
                continue
            if not ds2_validate(resp):
                last = CommsError(f"bad response framing: {resp.hex()}")
                continue
            return resp
        raise last or CommsTimeout("no response after retries")
