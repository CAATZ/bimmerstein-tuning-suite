from __future__ import annotations
from ecueditor.core.errors import TableError

_WIDTH = {"uint8": 1, "int8": 1, "uint16": 2, "int16": 2, "uint32": 4, "int32": 4,
          "float": 4, "movi20": 4, "movi20s": 4}

def storage_width(storage_type: str) -> int:
    try:
        return _WIDTH[storage_type]
    except KeyError as exc:
        raise TableError(f"unknown storage type {storage_type!r}") from exc

def is_signed(storage_type: str) -> bool:
    return storage_type.startswith("int") or storage_type.startswith("movi20")

def storage_bounds(storage_type: str) -> tuple[int, int]:
    w = storage_width(storage_type)
    bits = w * 8
    if is_signed(storage_type):
        return (-(1 << (bits - 1)), (1 << (bits - 1)) - 1)
    return (0, (1 << bits) - 1)

def read_int(data, offset: int, storage_type: str, little_endian: bool) -> int:
    w = storage_width(storage_type)
    chunk = bytes(data[offset:offset + w])
    v = int.from_bytes(chunk, "little" if little_endian else "big", signed=False)
    if is_signed(storage_type) and v >= (1 << (w * 8 - 1)):
        v -= (1 << (w * 8))
    return v

def write_int(data: bytearray, offset: int, value: int, storage_type: str, little_endian: bool) -> None:
    w = storage_width(storage_type)
    lo, hi = storage_bounds(storage_type)
    if not (lo <= value <= hi):
        raise TableError(f"value {value} out of range for {storage_type}")
    data[offset:offset + w] = int(value).to_bytes(w, "little" if little_endian else "big",
                                                   signed=is_signed(storage_type))
