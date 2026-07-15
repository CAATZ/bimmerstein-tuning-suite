from __future__ import annotations

from typing import Sequence

from ecueditor.core.comms.protocol.base import MemoryRead, Protocol
from ecueditor.core.comms.transport.base import Transport
from ecueditor.core.errors import CommsError, CommsTimeout


class ConnectionManager:
    MAX_RETRIES = 2

    def __init__(
        self,
        transport: Transport,
        protocol: Protocol,
        module_address: int | None = None,
    ) -> None:
        self._transport = transport
        self._protocol = protocol
        self._module = (
            getattr(protocol, "MODULE_ECU", 0x12)
            if module_address is None
            else int(module_address)
        )
        self._ecu_id: str | None = None

    def open(self, port: str) -> None:
        self._transport.open(port, self._protocol.serial_params())

    def init(self) -> str:
        params = self._protocol.serial_params()
        last: Exception | None = None
        for _ in range(self.MAX_RETRIES + 1):
            try:
                response = self._exchange(
                    self._protocol.build_init(),
                    params.connect_timeout_ms,
                    params.inter_byte_timeout_ms,
                )
                ecu_id = self._protocol.parse_init(response)
                if not isinstance(ecu_id, str) or not ecu_id.strip():
                    raise CommsError(
                        f"protocol {type(self._protocol).__name__} returned "
                        f"invalid ECU ID {ecu_id!r}"
                    )
                self._ecu_id = ecu_id
                return self._ecu_id
            except (CommsTimeout, CommsError) as exc:
                last = exc
                self._transport.flush_input()
        raise last or CommsTimeout("init failed after retries")

    def read_memory(self, reads: Sequence[MemoryRead]) -> list[bytes]:
        params = self._protocol.serial_params()
        output: list[bytes] = []
        for read in reads:
            frame = self._protocol.build_read(self._module, [read])
            response = self._exchange_with_retry(
                frame, params.response_timeout_ms, params.inter_byte_timeout_ms,
            )
            chunks = self._protocol.parse_read(response, [read])
            try:
                chunk_count = len(chunks)
            except (TypeError, AttributeError) as exc:
                raise CommsError(
                    f"protocol returned a non-sequence payload for read {read!r}"
                ) from exc
            if chunk_count != 1:
                raise CommsError(
                    f"protocol returned {chunk_count} payloads for one read {read!r}"
                )
            chunk = chunks[0]
            if not isinstance(chunk, (bytes, bytearray, memoryview)):
                raise CommsError(
                    f"protocol returned a non-bytes payload for read {read!r}"
                )
            payload = bytes(chunk)
            if len(payload) != read.length:
                raise CommsError(
                    f"protocol returned {len(payload)} bytes for {read.length}-byte read {read!r}"
                )
            output.append(payload)
        return output

    def telegram_setup(self, entries: Sequence[tuple[int, int]]) -> None:
        if not self.supports_telegram:
            raise CommsError(
                f"protocol {type(self._protocol).__name__} does not support telegram polling"
            )
        params = self._protocol.serial_params()
        frame = self._protocol.build_telegram_setup(self._module, entries)  # type: ignore[attr-defined]
        response = self._exchange_with_retry(
            frame, params.response_timeout_ms, params.inter_byte_timeout_ms,
        )
        self._protocol.parse_telegram_setup_ack(response)  # type: ignore[attr-defined]

    def telegram_poll(self, expected_length: int = 0) -> bytes:
        if not self.supports_telegram:
            raise CommsError(
                f"protocol {type(self._protocol).__name__} does not support telegram polling"
            )
        params = self._protocol.serial_params()
        frame = self._protocol.build_telegram_poll(self._module)  # type: ignore[attr-defined]
        response = self._exchange_with_retry(
            frame, params.response_timeout_ms, params.inter_byte_timeout_ms,
        )
        return self._protocol.parse_telegram_poll(  # type: ignore[attr-defined]
            response, expected_length=expected_length,
        )

    def reset(self) -> None:
        params = self._protocol.serial_params()
        self._exchange(
            self._protocol.build_reset(self._module),
            params.response_timeout_ms,
            params.inter_byte_timeout_ms,
        )

    def close(self) -> None:
        self._transport.close()
        self._ecu_id = None

    @property
    def ecu_id(self) -> str | None:
        return self._ecu_id

    @property
    def ident(self) -> bytes:
        return getattr(self._protocol, "last_ident", b"")

    @property
    def cal_id(self) -> str:
        derive = getattr(self._protocol, "cal_id_from_ident", None)
        return derive(self.ident) if derive is not None else ""

    @property
    def supports_telegram(self) -> bool:
        return all(callable(getattr(self._protocol, name, None)) for name in (
            "build_telegram_setup", "parse_telegram_setup_ack",
            "build_telegram_poll", "parse_telegram_poll",
        ))

    @property
    def telegram_max_entries(self) -> int:
        return max(1, int(getattr(self._protocol, "MAX_TELEGRAM_ENTRIES", 255)))

    def _recv_frame(self, first_byte_ms: int, inter_byte_ms: int) -> bytes:
        header_size = max(1, int(getattr(self._protocol, "RESPONSE_HEADER_SIZE", 2)))
        header = self._transport.read(header_size, first_byte_ms)
        length_reader = getattr(self._protocol, "response_length", None)
        total = length_reader(header) if callable(length_reader) else header[1]
        if total <= header_size:
            raise CommsError(f"implausible response length {total} for {header_size}-byte header")
        rest = self._transport.read(total - header_size, inter_byte_ms)
        return header + rest

    def _exchange(self, frame: bytes, first_byte_ms: int, inter_byte_ms: int) -> bytes:
        self._transport.flush_input()
        self._transport.write(frame)
        return self._recv_frame(first_byte_ms, inter_byte_ms)

    def _exchange_with_retry(
        self, frame: bytes, first_byte_ms: int, inter_byte_ms: int,
    ) -> bytes:
        last: Exception | None = None
        for _ in range(self.MAX_RETRIES + 1):
            try:
                response = self._exchange(frame, first_byte_ms, inter_byte_ms)
            except CommsTimeout as exc:
                last = exc
                continue
            validator = getattr(self._protocol, "validate_response", None)
            if callable(validator) and not validator(response):
                last = CommsError(f"bad response framing: {response.hex()}")
                continue
            return response
        raise last or CommsTimeout("no response after retries")
