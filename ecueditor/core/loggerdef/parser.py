from __future__ import annotations
import re
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET
from ecueditor.core.comms.transport.base import SerialParams
from ecueditor.core.loggerdef.channel import ChannelAddress, Conversion, LoggerChannel
from ecueditor.core.errors import DefinitionError

_PARITY = {"0": "none", "1": "odd", "2": "even"}     # KWP parity codes; "2" => EVEN (8E1)

def _int_or(v: str | None, default: int) -> int:
    try:
        return int(v) if v is not None else default
    except ValueError:
        return default

def _hex_or_none(txt: str | None) -> int | None:
    if txt is None:
        return None
    try:
        return int(txt.strip(), 16)
    except ValueError:
        return None

def _serial_params(proto: ET.Element) -> SerialParams:
    return SerialParams(
        baud=_int_or(proto.get("baud"), 9600),
        databits=_int_or(proto.get("databits"), 8),
        stopbits=_int_or(proto.get("stopbits"), 2),
        parity=_PARITY.get(proto.get("parity") or "2", "even"),
        connect_timeout_ms=_int_or(proto.get("connect_timeout"), 2000),
        response_timeout_ms=_int_or(proto.get("response_timeout"), 1500),
        inter_byte_timeout_ms=_int_or(proto.get("inter_byte_timeout"), 600),
        write_timeout_ms=_int_or(proto.get("write_timeout"), 3000),
    )

def _module_address(proto: ET.Element) -> int:
    fallback: int | None = None
    seen_first = False
    for mod in proto.iter("module"):
        addr = _hex_or_none(mod.get("address"))
        if (mod.get("id") or "").lower() == "ecu":
            return addr or 0x12
        if not seen_first:
            fallback = addr
            seen_first = True
    return fallback or 0x12

def _conversion(conv: ET.Element) -> Conversion:
    def _f(name: str) -> float | None:
        v = conv.get(name)
        return float(v) if v not in (None, "") else None
    return Conversion(
        units=conv.get("units") or "",
        expr=conv.get("expr") or "x",
        format=conv.get("format") or "0",
        storage_type=conv.get("storagetype") or "uint8",
        endian=conv.get("endian"),
        gauge_min=_f("gauge_min"), gauge_max=_f("gauge_max"), gauge_step=_f("gauge_step"),
    )


def _conversions(ep: ET.Element) -> tuple[Conversion, ...]:
    return tuple(_conversion(conv) for conv in ep.findall("conversions/conversion"))

def _channel_address(ad: ET.Element) -> ChannelAddress:
    bit = ad.get("bit")
    return ChannelAddress(
        address=_hex_or_none(ad.text),
        length=_int_or(ad.get("length"), 1),
        bit=int(bit) if bit is not None else None,
    )

def _channel(ep: ET.Element) -> LoggerChannel:
    ecus: list[tuple[tuple[str, ...], tuple[ChannelAddress, ...]]] = []
    for ecu in ep.findall("ecu"):
        ids = tuple(s.strip() for s in (ecu.get("id") or "").split(",") if s.strip())
        addrs = tuple(_channel_address(ad) for ad in ecu.findall("address"))
        ecus.append((ids, addrs))
    gs = ep.get("groupsize")
    conversions = _conversions(ep)
    return LoggerChannel(
        id=ep.get("id") or "", name=ep.get("name") or "", desc=ep.get("desc"),
        group=ep.get("group"), subgroup=ep.get("subgroup"),
        groupsize=int(gs) if gs else None,
        ecus=tuple(ecus), conversion=conversions[0] if conversions else None,
        conversions=conversions,
    )

@dataclass
class LoggerDefinition:
    protocol_id: str
    serial_params: SerialParams
    module_address: int
    channels: list[LoggerChannel]

    def for_ecu(self, ecu_id: str) -> list[LoggerChannel]:
        return [c for c in self.channels if c.resolve(ecu_id) is not None]

    def parameters(self) -> list[LoggerChannel]:
        return [c for c in self.channels if not c.is_switch]

    def switches(self) -> list[LoggerChannel]:
        return [c for c in self.channels if c.is_switch]

    def by_id(self, channel_id: str) -> LoggerChannel:
        for c in self.channels:
            if c.id == channel_id:
                return c
        raise KeyError(channel_id)

def parse_logger_definition(
    path: str | Path,
    *,
    supported_protocol_ids: Collection[str] | None = None,
) -> LoggerDefinition:
    raw = Path(path).read_text(encoding="utf-8", errors="replace")
    raw = re.sub(r"<!DOCTYPE.*?\]>", "", raw, flags=re.DOTALL)
    try:
        root = ET.fromstring(raw)              # comments (commented ecuparams) are dropped here
    except ET.ParseError as exc:
        raise DefinitionError(f"cannot parse logger def {path}: {exc}") from exc
    supported = set(supported_protocol_ids) if supported_protocol_ids is not None else {"DS2"}
    protocols = list(root.iter("protocol"))
    selected = next((p for p in protocols if (p.get("id") or "") in supported), None)
    if selected is None:
        available = sorted({p.get("id") or "<missing id>" for p in protocols})
        raise DefinitionError(
            "logger definition has no supported <protocol>; available: "
            + (", ".join(available) if available else "none")
        )
    protocol_id = selected.get("id") or ""
    channels = [_channel(ep) for ep in selected.iter("ecuparam")]
    return LoggerDefinition(
        protocol_id=protocol_id,
        serial_params=_serial_params(selected),
        module_address=_module_address(selected),
        channels=channels,
    )
