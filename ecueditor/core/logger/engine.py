from __future__ import annotations
import threading
import time
from dataclasses import dataclass
from typing import Callable, Literal, Mapping, Sequence
from ecueditor.core.comms.connection import ConnectionManager
from ecueditor.core.comms.protocol.base import MemoryRead
from ecueditor.core.loggerdef.channel import LoggerChannel, address_class
from ecueditor.core.loggerdef.parser import LoggerDefinition
from ecueditor.core.errors import CommsError
from ecueditor.core.rom.storage import storage_width
from ecueditor.core.logger.telegram import (
    decode_telegram_payload,
    is_address_list_channel,
    telegram_entries,
    telegram_slots,
)

PollMode = Literal["auto", "batch", "memory"]

@dataclass(frozen=True)
class Sample:
    timestamp_ms: float
    values: Mapping[str, float]


@dataclass(frozen=True)
class SelectionReport:
    requested: tuple[str, ...]
    selected: tuple[str, ...]
    unavailable: Mapping[str, str]


@dataclass(frozen=True)
class _BlockItem:
    channel: LoggerChannel
    offset: int
    length: int


@dataclass(frozen=True)
class _ReadBlock:
    read: MemoryRead
    items: tuple[_BlockItem, ...]


def _memory_blocks(
    selected: Sequence[tuple[LoggerChannel, MemoryRead]], max_span: int = 120,
) -> list[_ReadBlock]:
    """Coalesce nearby telemetry/working-RAM values like the proven Flasher reader."""
    grouped: list[tuple[int, int, str, list[_BlockItem]]] = []
    for channel, read in sorted(selected, key=lambda pair: pair[1].address):
        cls = address_class(read.address)
        can_merge = cls in ("DA-BUFFER", "WORKING-RAM")
        if grouped:
            start, end, prior_class, items = grouped[-1]
            merged_end = max(end, read.address + read.length)
            if can_merge and prior_class == cls and merged_end - start <= max_span:
                items.append(_BlockItem(channel, read.address - start, read.length))
                grouped[-1] = (start, merged_end, prior_class, items)
                continue
        grouped.append((
            read.address,
            read.address + read.length,
            cls,
            [_BlockItem(channel, 0, read.length)],
        ))
    return [
        _ReadBlock(MemoryRead(start, end - start), tuple(items))
        for start, end, _cls, items in grouped
    ]

def _default_clock() -> float:
    return time.monotonic() * 1000.0

class LoggerEngine:
    clock: Callable[[], float]                     # injectable ms clock (deterministic in tests)

    def __init__(self, connection: ConnectionManager, definition: LoggerDefinition,
                 *, clock: Callable[[], float] | None = None) -> None:
        self._conn = connection
        self._def = definition
        self._selected: tuple[tuple[LoggerChannel, MemoryRead], ...] = ()
        self._subs: list[Callable[[Sample], None]] = []
        self._state_lock = threading.Lock()
        self._poll_mode: PollMode = "auto"
        self._batch_ready = False
        self._batch_failure = ""
        self._selection_report = SelectionReport((), (), {})
        self.clock = clock or _default_clock

    def select(self, channel_ids: Sequence[str]) -> None:
        ecu_id = self._conn.ecu_id
        if ecu_id is None:
            raise CommsError("connection not initialised; call ConnectionManager.init() first")
        selected: list[tuple[LoggerChannel, MemoryRead]] = []
        unavailable: dict[str, str] = {}
        requested = tuple(dict.fromkeys(channel_ids))
        with self._state_lock:
            poll_mode = self._poll_mode
        for cid in requested:
            try:
                ch = self._def.by_id(cid)
            except KeyError:
                unavailable[cid] = "unknown channel"
                continue                              # unknown id -> skip
            addrs = ch.resolve(ecu_id)
            if not addrs or addrs[0].address is None:
                unavailable[cid] = f"not available for ECU {ecu_id}"
                continue                              # dropped for this ECU-ID
            if address_class(addrs[0].address) == "ADC-CHANNEL" and not (
                is_address_list_channel(ch)
                and poll_mode != "memory"
                and self._conn.supports_telegram
            ):
                unavailable[cid] = "ADC channel requires a verified protocol-specific query"
                continue
            if ch.groupsize and not is_address_list_channel(ch):
                # MS41 group/block read: one telegram fetches the whole `groupsize`-byte block at
                # the group base; poll_once's decode slices the datum's bytes at its index within
                # the block (index 0 for a single-datum group).
                length = ch.groupsize
            else:
                width = storage_width(ch.conversion.storage_type if ch.conversion else "uint8")
                length = max(len(addrs), width)
            selected.append((ch, MemoryRead(addrs[0].address, length)))
        report = SelectionReport(requested, tuple(ch.id for ch, _ in selected), unavailable)
        with self._state_lock:
            self._selected = tuple(selected)
            self._selection_report = report
            self._batch_failure = ""       # an explicit re-selection retries fast mode
            self._batch_ready = False       # selected addresses/order are the ECU response contract

    def selected_channels(self) -> list[LoggerChannel]:
        with self._state_lock:
            return [ch for ch, _ in self._selected]

    def set_poll_mode(self, mode: str) -> None:
        if mode not in ("auto", "batch", "memory"):
            raise ValueError(f"unknown logger poll mode {mode!r}")
        with self._state_lock:
            self._poll_mode = mode  # type: ignore[assignment]
            self._batch_failure = ""
            self._batch_ready = False

    def poll_once(self) -> Sample:
        with self._state_lock:
            selected = self._selected
            requested_mode = self._poll_mode
            batch_ready = self._batch_ready
            batch_failure = self._batch_failure

        values: dict[str, float] = {}
        telegram_selected = tuple(pair for pair in selected if is_address_list_channel(pair[0]))
        use_batch = bool(telegram_selected) and requested_mode != "memory" \
            and self._conn.supports_telegram and not batch_failure
        memory_selected = selected
        if use_batch:
            try:
                slots = telegram_slots(
                    tuple(channel for channel, _read in telegram_selected), self.ecu_id or "",
                )
                if not batch_ready:
                    self._conn.telegram_setup(telegram_entries(slots))
                    with self._state_lock:
                        self._batch_ready = True
                expected_length = sum(slot.width for slot in slots)
                payload = self._conn.telegram_poll(expected_length)
                values.update(decode_telegram_payload(payload, slots))
                memory_selected = tuple(
                    pair for pair in selected if not is_address_list_channel(pair[0])
                )
            except (CommsError, ValueError) as exc:
                # Batch is an accelerator, never a reason to lose logging.  Keep the user's full
                # selection and fall back to the known-compatible 0x06 reads for this session.
                with self._state_lock:
                    self._batch_failure = str(exc)
                    self._batch_ready = False
                # ADC values are mux indices, not RAM addresses.  In particular P17 normally
                # resolves to ADC index 0x07; only the batch's verified V_IGK mirror may supply it.
                memory_selected = tuple(
                    pair for pair in selected
                    if address_class(pair[1].address) != "ADC-CHANNEL"
                )

        blocks = _memory_blocks(memory_selected)
        payloads = self._conn.read_memory([block.read for block in blocks]) if blocks else []
        for block, payload in zip(blocks, payloads):
            for item in block.items:
                chunk = payload[item.offset:item.offset + item.length]
                values[item.channel.id] = item.channel.decode(chunk)
        return Sample(timestamp_ms=self.clock(), values=values)

    def subscribe(self, callback: Callable[[Sample], None]) -> Callable[[], None]:
        self._subs.append(callback)
        def unsubscribe() -> None:
            if callback in self._subs:
                self._subs.remove(callback)
        return unsubscribe

    def run(self, stop: threading.Event) -> None:
        while not stop.is_set():
            sample = self.poll_once()
            for cb in list(self._subs):
                cb(sample)

    @property
    def ecu_id(self) -> str | None:
        return self._conn.ecu_id

    @property
    def cal_id(self) -> str:
        # (Phase 8c, spec §9.2) mirror of ConnectionManager.cal_id, like ecu_id; "" when unknown.
        return getattr(self._conn, "cal_id", "")

    @property
    def selection_report(self) -> SelectionReport:
        with self._state_lock:
            return self._selection_report

    @property
    def poll_status(self) -> str:
        with self._state_lock:
            can_batch = any(is_address_list_channel(ch) for ch, _ in self._selected) \
                and self._poll_mode != "memory" \
                and self._conn.supports_telegram
            failure = self._batch_failure
        if failure:
            return "Compatible (batch fallback)"
        return "Fast batch" if can_batch else "Compatible"
