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
        self._batch_signature: tuple[tuple[int, int], ...] | None = None
        self._batch_failure = ""
        self._selection_generation = 0
        self._selection_report = SelectionReport((), (), {})
        self.clock = clock or _default_clock

    def select(self, channel_ids: Sequence[str]) -> None:
        self._select(channel_ids, {})

    def select_with_units(
        self, channel_ids: Sequence[str], units: Mapping[str, str | None],
    ) -> None:
        self._select(channel_ids, units)

    def _select(
        self, channel_ids: Sequence[str], units: Mapping[str, str | None],
    ) -> None:
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
            ch = ch.with_units(units.get(cid))
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
            self._batch_signature = None    # selected addresses/order are the ECU response contract
            self._selection_generation += 1

    def selected_channels(self) -> list[LoggerChannel]:
        with self._state_lock:
            return [ch for ch, _ in self._selected]

    def set_poll_mode(self, mode: str) -> None:
        if mode not in ("auto", "batch", "memory"):
            raise ValueError(f"unknown logger poll mode {mode!r}")
        with self._state_lock:
            self._poll_mode = mode  # type: ignore[assignment]
            self._batch_failure = ""
            self._batch_signature = None
            self._selection_generation += 1

    def poll_once(self) -> Sample:
        while True:
            with self._state_lock:
                selected = self._selected
                requested_mode = self._poll_mode
                batch_signature = self._batch_signature
                batch_failure = self._batch_failure
                generation = self._selection_generation

            values: dict[str, float] = {}
            telegram_selected = tuple(
                pair for pair in selected if is_address_list_channel(pair[0])
            )
            use_batch = bool(telegram_selected) and requested_mode != "memory" \
                and self._conn.supports_telegram and not batch_failure
            memory_selected = selected
            if use_batch:
                try:
                    slots = telegram_slots(
                        tuple(channel for channel, _read in telegram_selected), self.ecu_id or "",
                    )
                    max_entries = max(1, int(getattr(
                        self._conn, "telegram_max_entries", len(slots) or 1,
                    )))
                    for offset in range(0, len(slots), max_entries):
                        chunk_slots = slots[offset:offset + max_entries]
                        entries = telegram_entries(chunk_slots)
                        if batch_signature != entries:
                            self._conn.telegram_setup(entries)
                            if not self._is_current(generation):
                                break
                            with self._state_lock:
                                self._batch_signature = entries
                            batch_signature = entries
                        expected_length = sum(slot.width for slot in chunk_slots)
                        payload = self._conn.telegram_poll(expected_length)
                        if not self._is_current(generation):
                            break
                        values.update(decode_telegram_payload(payload, chunk_slots))
                    if not self._is_current(generation):
                        continue
                    memory_selected = tuple(
                        pair for pair in selected if not is_address_list_channel(pair[0])
                    )
                except (CommsError, ValueError) as exc:
                    if not self._is_current(generation):
                        continue
                    values.clear()  # discard any earlier chunk; fallback must be one coherent plan
                    # Batch is an accelerator, never a reason to lose memory-readable channels.
                    # ADC mux selectors cannot be read with 0x06, so surface those explicitly in
                    # SelectionReport rather than silently producing blank values.
                    adc_ids = {
                        channel.id for channel, read in selected
                        if address_class(read.address) == "ADC-CHANNEL"
                    }
                    with self._state_lock:
                        self._batch_failure = str(exc)
                        self._batch_signature = None
                        prior = self._selection_report
                        unavailable = dict(prior.unavailable)
                        unavailable.update({
                            channel_id: "batch polling failed; ADC unavailable in compatible mode"
                            for channel_id in adc_ids
                        })
                        self._selection_report = SelectionReport(
                            prior.requested,
                            tuple(cid for cid in prior.selected if cid not in adc_ids),
                            unavailable,
                        )
                    memory_selected = tuple(
                        pair for pair in selected
                        if address_class(pair[1].address) != "ADC-CHANNEL"
                    )

            blocks = _memory_blocks(memory_selected)
            payloads = self._conn.read_memory([block.read for block in blocks]) if blocks else []
            if not self._is_current(generation):
                continue
            for block, payload in zip(blocks, payloads):
                for item in block.items:
                    chunk = payload[item.offset:item.offset + item.length]
                    values[item.channel.id] = item.channel.decode(chunk)
            return Sample(timestamp_ms=self.clock(), values=values)

    def _is_current(self, generation: int) -> bool:
        with self._state_lock:
            return generation == self._selection_generation

    def subscribe(self, callback: Callable[[Sample], None]) -> Callable[[], None]:
        self._subs.append(callback)
        def unsubscribe() -> None:
            if callback in self._subs:
                self._subs.remove(callback)
        return unsubscribe

    def run(self, stop: threading.Event) -> None:
        while not stop.is_set():
            with self._state_lock:
                has_selection = bool(self._selected)
            if not has_selection:
                stop.wait(0.05)
                continue
            sample = self.poll_once()
            for cb in list(self._subs):
                cb(sample)

    def close(self) -> None:
        self._conn.close()

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
            unavailable = len(self.selection_report.unavailable)
            suffix = f"; {unavailable} unavailable" if unavailable else ""
            return f"Compatible (batch fallback{suffix})"
        return "Fast batch" if can_batch else "Compatible"
