from __future__ import annotations
from dataclasses import dataclass
from typing import Literal
from ecueditor.core.scaling.expression import compile_expression

AddressClass = Literal["ADC-CHANNEL", "DA-BUFFER", "EXT-BUS", "SFR", "WORKING-RAM", "OTHER"]

_WIDTH = {"uint8": 1, "int8": 1, "uint16": 2, "int16": 2, "uint32": 4, "int32": 4, "float": 4}

def address_class(addr: int | None) -> AddressClass:
    # Class boundaries and ORDER copied from ms41log.addr_class (DA-BUFFER before EXT-BUS,
    # since 0xDA2A-0xDAA5 sits inside the 0xC000-0xDFFF external-bus window).
    if addr is None:
        return "OTHER"
    if addr < 0x20:
        return "ADC-CHANNEL"
    if 0xDA2A <= addr <= 0xDAA5:
        return "DA-BUFFER"
    if 0xC000 <= addr <= 0xDFFF:
        return "EXT-BUS"
    if 0xFE00 <= addr <= 0xFFFF:
        return "SFR"
    if 0xE000 <= addr <= 0xFDFF:
        return "WORKING-RAM"
    return "OTHER"

@dataclass(frozen=True)
class Conversion:
    units: str
    expr: str
    format: str
    storage_type: str = "uint8"
    endian: str | None = None                 # None => BIG default (RomRaider)
    gauge_min: float | None = None
    gauge_max: float | None = None
    gauge_step: float | None = None

    def decode(self, value: int) -> float:
        """value = assembled UNSIGNED register value. Two's-complement for intN, then expr."""
        st = self.storage_type or "uint8"
        x = value
        if st.startswith("int"):
            bits = _WIDTH.get(st, 1) * 8
            if x >= (1 << (bits - 1)):
                x -= (1 << bits)
        return compile_expression(self.expr or "x").evaluate(x)

@dataclass(frozen=True)
class ChannelAddress:
    address: int | None
    length: int = 1
    bit: int | None = None

@dataclass(frozen=True)
class LoggerChannel:
    id: str
    name: str
    desc: str | None
    group: str | None
    subgroup: str | None
    groupsize: int | None
    ecus: tuple[tuple[tuple[str, ...], tuple[ChannelAddress, ...]], ...]
    conversion: Conversion | None

    def resolve(self, ecu_id: str) -> tuple[ChannelAddress, ...] | None:
        for ids, addrs in self.ecus:
            if ecu_id in ids:
                return addrs
        return None

    @property
    def is_switch(self) -> bool:
        return self.id.startswith("S")             # MS41 switch-channel convention (S-prefixed ids)

    @property
    def bit(self) -> int | None:
        """Definition bit selector (the MS41 file uses one stable bit per channel)."""
        bits = {address.bit for _ids, addresses in self.ecus for address in addresses
                if address.bit is not None}
        return next(iter(bits)) if len(bits) == 1 else None

    def decode(self, raw_bytes: bytes) -> float:
        conv = self.conversion
        st = (conv.storage_type if conv else "uint8") or "uint8"
        width = _WIDTH.get(st, 1)
        chunk = bytes(raw_bytes[:width]) if len(raw_bytes) >= width else bytes(raw_bytes)
        endian = conv.endian if conv else None
        value = int.from_bytes(chunk, "little" if endian == "little" else "big")
        if self.bit is not None:
            # RomRaider's EcuParameterConvertor returns the selected bit directly and does not
            # evaluate the conversion expression for <address bit="N"> channels.
            return float(1 if value & (1 << self.bit) else 0)
        if conv is None:
            return float(value)
        return conv.decode(value)
