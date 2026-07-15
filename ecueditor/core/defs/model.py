from __future__ import annotations
from dataclasses import dataclass
from typing import Literal, Mapping

TableType = Literal["1D", "2D", "3D", "Switch", "BitwiseSwitch"]
AxisRole = Literal["X", "Y"]


@dataclass(frozen=True)
class RomId:
    xmlid: str
    internal_id_address: int | None      # RAW file offset (hex in XML); may be -1 for "force"
    internal_id_string: str | None       # "0x.."=hex bytes, "force"=wildcard, else ASCII
    ecuid: str | None = None
    filesize: int | None = None          # bytes (kb/mb suffixes resolved)
    memmodel: str | None = None
    memmodel_endian: str | None = None   # if set, overrides per-table endian
    make: str | None = None
    market: str | None = None
    model: str | None = None
    submodel: str | None = None
    transmission: str | None = None
    year: str | None = None
    no_ram_offset: bool = False

    # `probe` is where the id bytes live in THIS image (computed by find_matching via probe_offset()).
    # If None, fall back to the raw internal_id_address (used by direct unit tests).
    def matches(self, image: bytes, probe: int | None = None) -> bool:
        s = self.internal_id_string
        if not s:
            return False
        if self.internal_id_address == -1 and s.lower() == "force":
            return True
        addr = self.internal_id_address if probe is None else probe
        addr = addr or 0
        if s.startswith("0x"):
            want = bytes.fromhex(s[2:])
            return image[addr:addr + len(want)] == want
        want = s.encode("latin-1")
        return image[addr:addr + len(want)].lower() == want.lower()


@dataclass(frozen=True)
class ScaleDef:      # scaling as declared on a table/axis (pre-resolution)
    units: str
    expression: str
    to_byte: str
    format: str
    fine_increment: float
    coarse_increment: float


@dataclass(frozen=True)
class AxisDef:
    role: AxisRole
    storage_address: int | None          # hex; None for static or inherited
    storage_type: str | None             # integer types; parsed "float" is rejected by integer-backed ROM cells
    endian: str | None
    size: int | None                     # length (from parent dimension if None)
    scale: ScaleDef | None
    static_values: tuple[float | str, ...] | None = None   # for "Static X/Y Axis"; str entries are prose labels
    name: str | None = None
    logparam: str | None = None          # logger-channel id bound to this axis (real def puts logparam on axes)


@dataclass(frozen=True)
class TableDef:
    name: str
    type: TableType
    category: str | None
    storage_address: int | None
    storage_type: str | None
    endian: str | None
    size_x: int | None                   # DECIMAL in XML
    size_y: int | None
    scale: ScaleDef | None
    x_axis: AxisDef | None
    y_axis: AxisDef | None
    description: str | None = None
    states: tuple[tuple[str, str], ...] = ()   # Switch: (name, hex-data)
    bits: tuple[tuple[str, int], ...] = ()      # BitwiseSwitch: (name, position)
    logparam: str | None = None
    user_level: int = 1
    locked: bool = False
    swap_xy: bool = False
    flip_x: bool = False
    flip_y: bool = False


@dataclass(frozen=True)
class RomDefinition:
    romid: RomId
    tables: Mapping[str, TableDef]        # keyed by table name (already inheritance-merged)
    checksum_type: str | None = None      # from an optional <checksum type="..."> element (MS41 defs: None)
