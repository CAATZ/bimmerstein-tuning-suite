from __future__ import annotations

from typing import Sequence
from ecueditor.core.comms.transport.base import SerialParams
from ecueditor.core.comms.protocol.base import MemoryRead
from ecueditor.core.errors import CommsError
from ecueditor.core.plugins.registry import register

def ds2_checksum(frame_without_cksum: bytes) -> int:
    """XOR of every byte (fact base §5.2 / ms41id.frame running-XOR)."""
    x = 0
    for b in frame_without_cksum:
        x ^= b
    return x

def ds2_frame(module_addr: int, payload: bytes) -> bytes:
    """[addr][len][payload...][xor]; len = TOTAL frame byte count (ms41id: 1 + len(data) + 2)."""
    length = 1 + len(payload) + 2       # addr(1) + payload(n) + (len byte + cksum byte)
    body = bytes([module_addr & 0xFF, length]) + bytes(payload)
    return body + bytes([ds2_checksum(body)])

def ds2_validate(frame: bytes) -> bool:
    """True iff the len field equals the frame length and the trailing XOR matches."""
    if len(frame) < 4:
        return False
    if frame[1] != len(frame):
        return False
    return ds2_checksum(frame[:-1]) == frame[-1]

@register("protocols", "DS2")
class DS2Protocol:
    """BMW DS2 (K-line), including definition-driven logger address polling."""
    id = "DS2"
    MODULE_ECU = 0x12
    CMD_INIT = 0x00
    CMD_READ_MEMORY = 0x06
    CMD_RESET = 0x43
    CMD_TELEGRAM = 0x0B
    TELEGRAM_SETUP = 0x01
    TELEGRAM_POLL = 0x00
    MAX_TELEGRAM_ENTRIES = 49  # 6 + 5*n must fit the one-byte DS2 total length
    STATUS_OK = 0xA0
    ECU_ID_LEN = 7                        # ident payload[:7] (part number / custom id; see caveat)

    def serial_params(self) -> SerialParams:
        # logger def 9600 8E; TX 8E2 (2 stop bits) matches the hardware-proven Flasher (safe
        # superset — the ECU's literal framing is 8E1, RX-agnostic). Timeouts ported from the
        # Flasher: 1.5-2 s first-byte, 0.6 s inter-byte, 3 s write (the old flat 55 ms is too tight).
        return SerialParams(baud=9600, databits=8, stopbits=2, parity="even",
                            connect_timeout_ms=2000, response_timeout_ms=1500,
                            inter_byte_timeout_ms=600, write_timeout_ms=3000)

    def build_init(self) -> bytes:
        return ds2_frame(self.MODULE_ECU, bytes([self.CMD_INIT]))     # -> 12 04 00 16

    # (Phase 8c, spec §9.2) No byte-verified CAL-ID / software-version slice exists in the DS2
    # cmd-0x00 identification payload: the ms41-identity reference (ISN_IDENTIFICATION_REFERENCE
    # §4/§5) names only part#/HW-coding/coding-block/index-date/ISN-serial, and the ms41-cal-defs
    # DS2 logger reference resolves ECU identity by a SINGLE ECU-ID (= payload[:7]). Deriving an
    # offset from the reference corpus (Capture Serial 3) was ambiguous, and global-constraints-8c
    # forbids guessing it. So CAL_ID_SLICE stays None (cal_id_from_ident -> "" -> chip "—") until
    # the offset is pinned. Backlog: Task-13 "CAL-ID offset open question".
    CAL_ID_SLICE: slice | None = None

    def parse_init(self, response: bytes) -> str:
        if not ds2_validate(response):
            raise CommsError(f"bad DS2 init response framing: {response.hex()}")
        if response[2] != self.STATUS_OK:
            raise CommsError(f"DS2 init status {response[2:3].hex()} != 0xA0")
        payload = response[3:-1]          # strip addr, len, status, xor
        self._last_ident = bytes(payload)  # (Phase 8c) full ident block retained, not just [:7]
        ecu_id = payload[: self.ECU_ID_LEN].decode("latin-1")
        return ecu_id.rstrip("\x00").strip()

    @property
    def last_ident(self) -> bytes:
        return getattr(self, "_last_ident", b"")

    def cal_id_from_ident(self, ident: bytes) -> str:
        sl = self.CAL_ID_SLICE
        if sl is None:                     # Step-1 ambiguous: plumbing only, chip shows "—"
            return ""
        if len(ident) < sl.stop:           # ident too short to hold the CAL-ID slice
            return ""
        return ident[sl].decode("latin-1").rstrip("\x00").strip()

    def build_read(self, module_addr: int, reads: Sequence[MemoryRead]) -> bytes:
        reads = list(reads)
        if not reads:
            raise CommsError("build_read requires at least one MemoryRead")
        start = reads[0].address
        num_bytes = sum(r.length for r in reads)
        payload = bytes([self.CMD_READ_MEMORY, num_bytes & 0xFF,
                         (start >> 8) & 0xFF, start & 0xFF])
        return ds2_frame(module_addr, payload)

    def parse_read(self, response: bytes, reads: Sequence[MemoryRead]) -> list[bytes]:
        if not ds2_validate(response):
            raise CommsError(f"bad DS2 read response framing: {response.hex()}")
        if response[2] != self.STATUS_OK:
            raise CommsError(f"DS2 read status 0x{response[2]:02X} != 0xA0")
        payload = response[3:-1]
        need = sum(r.length for r in reads)
        if len(payload) < need:
            raise CommsError(f"DS2 read payload {len(payload)} < requested {need}")
        out: list[bytes] = []
        off = 0
        for r in reads:
            out.append(bytes(payload[off:off + r.length]))
            off += r.length
        return out

    def build_reset(self, module_addr: int) -> bytes:
        return ds2_frame(module_addr, bytes([self.CMD_RESET]))

    def build_telegram_setup(self, module_addr: int, entries: Sequence[tuple[int, int]]) -> bytes:
        entries = list(entries)
        if not entries:
            raise CommsError("telegram setup requires at least one logger address")
        if len(entries) > self.MAX_TELEGRAM_ENTRIES:
            raise CommsError(
                f"telegram setup has {len(entries)} addresses; maximum is {self.MAX_TELEGRAM_ENTRIES}"
            )
        encoded = bytearray([self.CMD_TELEGRAM, self.TELEGRAM_SETUP, len(entries)])
        for type_flag, address in entries:
            if not 0 <= address <= 0xFFFFFFFF:
                raise CommsError(f"telegram address out of range: {address}")
            encoded.append(type_flag & 0xFF)
            encoded.extend(address.to_bytes(4, "big"))
        return ds2_frame(module_addr, bytes(encoded))

    def parse_telegram_setup_ack(self, response: bytes) -> None:
        if not ds2_validate(response):
            raise CommsError(f"bad DS2 telegram setup ack framing: {response.hex()}")
        if response[2] != self.STATUS_OK:
            raise CommsError(f"DS2 telegram setup status 0x{response[2]:02X} != 0xA0")

    def build_telegram_poll(self, module_addr: int) -> bytes:
        return ds2_frame(module_addr, bytes([self.CMD_TELEGRAM, self.TELEGRAM_POLL]))

    def parse_telegram_poll(self, response: bytes, expected_length: int = 0) -> bytes:
        if not ds2_validate(response):
            raise CommsError(f"bad DS2 telegram poll framing: {response.hex()}")
        if response[2] != self.STATUS_OK:
            raise CommsError(f"DS2 telegram poll status 0x{response[2]:02X} != 0xA0")
        payload = response[3:-1]
        if len(payload) < expected_length:
            raise CommsError(f"DS2 telegram payload {len(payload)} < {expected_length}")
        return bytes(payload)
