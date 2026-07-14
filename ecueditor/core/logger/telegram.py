"""RomRaider-compatible DS2 address-list polling for logger definitions.

For logger channels declared as ``group=0x0B`` / ``subgroup=0x01``, the ECU is
loaded with the currently selected addresses once and subsequently polled with
``0x0B 0x00``.  Address, response width, ordering, ADC/procedure classification,
and conversion all come from the resolved logger definition.
"""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Sequence

from ecueditor.core.loggerdef.channel import LoggerChannel
from ecueditor.core.rom.storage import storage_width


def _protocol_byte(value: str | None) -> int | None:
    if value is None:
        return None
    text = value.strip().lower()
    try:
        return int(text, 16)
    except ValueError:
        return None


def is_address_list_channel(channel: LoggerChannel) -> bool:
    """Whether RomRaider routes this logger channel through DS2 SET_ADDRESS."""
    return _protocol_byte(channel.group) == 0x0B and _protocol_byte(channel.subgroup) == 0x01


@dataclass(frozen=True)
class TelegramSlot:
    """One selected logger query in ECU response order."""

    channel: LoggerChannel
    address: int
    width: int

    @property
    def entry(self) -> tuple[int, int]:
        # RomRaider DS2LoggerProtocol: addresses below 0x1C are ADC/procedure
        # selectors (type 2); normal addresses use width-1 (0 byte, 1 word).
        type_flag = 0x02 if self.address < 0x1C else self.width - 1
        return type_flag, self.address


def telegram_slots(
    channels: Sequence[LoggerChannel], ecu_id: str,
) -> tuple[TelegramSlot, ...]:
    """Resolve selected definition channels into the exact SET_ADDRESS order."""
    slots: list[TelegramSlot] = []
    for channel in channels:
        if not is_address_list_channel(channel):
            continue
        addresses = channel.resolve(ecu_id)
        if not addresses or addresses[0].address is None:
            raise ValueError(f"logger channel {channel.id} has no address for ECU {ecu_id}")
        conversion_type = channel.conversion.storage_type if channel.conversion else "uint8"
        # This matches RomRaider EcuQueryData: use the larger of the number of
        # address bytes represented by the definition and the conversion width.
        width = max(len(addresses), storage_width(conversion_type))
        slots.append(TelegramSlot(channel, addresses[0].address, width))
    return tuple(slots)


def telegram_entries(slots: Sequence[TelegramSlot]) -> tuple[tuple[int, int], ...]:
    return tuple(slot.entry for slot in slots)


def decode_telegram_payload(
    payload: bytes, slots: Sequence[TelegramSlot],
) -> dict[str, float]:
    """Decode a variable-length 0x0B/0x00 response using the definition plan."""
    expected = sum(slot.width for slot in slots)
    if len(payload) < expected:
        raise ValueError(f"telegram payload {len(payload)} < planned {expected}")
    values: dict[str, float] = {}
    offset = 0
    for slot in slots:
        chunk = payload[offset:offset + slot.width]
        offset += slot.width
        values[slot.channel.id] = slot.channel.decode(chunk)
    return values
