# SPDX-License-Identifier: GPL-2.0-or-later
# Includes code adapted from MS41 Projects/Flasher under the MIT License.
# Copyright (c) 2026 CAATZ
# Modified for BimmerStein Tuning Suite on 2026-07-13; see THIRD_PARTY_NOTICES.md.

from __future__ import annotations
from ecueditor.core.plugins.registry import register
from ecueditor.core.checksum.base import ChecksumReport, RegionStatus
from ecueditor.core.errors import ChecksumError

# ─────────────────────────────────────────────────────────────────────────────
# Ported verbatim from the MS41 Flasher checksum reference — DO NOT
# modify the algorithm. Original module docstring, preserved here for context:
#
# checksum.py — BMW MS41.0/.1/.2 ROM checksum verification and correction.
#
# The full algorithm was reverse-engineered with the help of the Siemens_MS41_Checksum
# project and the pyms41 project, and VERIFIED byte-for-byte against real dumps:
# a stock MS41.1 323i plus three independent ID-60 (MS41.1) DME reads, two of them
# tuned.  All checksums match on every ID-60 image.
#
# There are THREE checksum systems in a 256 KB MS41 image, all CRC-16 (poly 0xA001,
# reflected, table-driven), all computed directly on FILE offsets (no whole-image
# swap), and all stored little-endian:
#
#   1. Boot-sector checksum
#        region  : file[0x4000 : 0x5C14]
#        initial : 0x4711 (constant)
#        stored  : file[0x5C80] (uint16 LE)
#
#   2. Program checksum  (3 regions, CRC chained through them)
#        initial : big-endian uint16 at file[0x6066]
#        region a: file[0x6100 : trim_ff(0x7FFF)]
#        region b: file[0x0000 : trim_ff(0x3FFF)]
#        region c: upper 128 KB rearranged into a linear buffer (adjacent 0x4000
#                  block pairs swapped), trimmed of trailing 0xFF
#        stored  : file[0x6050] (uint16 LE)
#
#   3. Calibration checksum table  (the "4E 00 FF FF" block at the cal section)
#        A linked list: at the block start a table of uint16 offsets (SS) each
#        points to where a 2-byte checksum is stored; the checksummed region runs
#        from the previous slot to that slot.  initial = big-endian uint16 at
#        (block_start + 0x0E).  Each checksum stored uint16 LE.  ~16 entries.
#
# The three systems cover disjoint regions and none include their own stored bytes,
# so they can be corrected independently and in any order.
#
# Separately, a checksum-ENABLE switch byte exists at file 0x605C
# (0x30 = ECU verifies at boot / stock, 0xFF = verification disabled).
#
# MS41.3 uses a different program/cal layout — this module verifies/corrects it on
# a best-effort basis and flags when results look inconsistent.
# ─────────────────────────────────────────────────────────────────────────────

import struct
from typing import List, Tuple

FULL_ROM_SIZE = 256 * 1024     # 262144
TUNE_SIZE     = 24  * 1024     # 24576

CHECKSUM_SWITCH_ADDR = 0x605C
CK_ENABLED  = 0x30
CK_DISABLED = 0xFF

_BOOT_REGION = (0x4000, 0x5C14)
_BOOT_INIT   = 0x4711
_BOOT_STORE  = 0x5C80

_PROG_INIT_AT = 0x6066         # big-endian uint16
_PROG_STORE   = 0x6050
_CAL_MAGIC    = b"\x4E\x00\xFF\xFF"


# ─────────────────────────── CRC-16 (poly 0xA001) ─────────────────────────────
_POLY = 0xA001
_TABLE = []
for _i in range(256):
    _n = 0; _n2 = _i
    for _j in range(8):
        _n = ((_n >> 1) ^ _POLY) if (_n2 ^ _n) & 1 else (_n >> 1)
        _n2 >>= 1
    _TABLE.append(_n)


def _crc(buf, init: int) -> int:
    s = init
    for b in buf:
        s = (s >> 8) ^ _TABLE[(s ^ b) & 0xFF]
    return s

def _u16le(d, a): return d[a] | (d[a + 1] << 8)
def _be16(d, a):  return (d[a] << 8) | d[a + 1]

def _find_end(buf, start: int) -> int:
    """Scan downward from `start` while bytes are 0xFF; return first-kept index."""
    while start > 0 and buf[start] == 0xFF:
        start -= 1
    return start + 1


# ─────────────────────────── Individual checksums ─────────────────────────────

def _boot_calc(d) -> int:
    return _crc(d[_BOOT_REGION[0]:_BOOT_REGION[1]], _BOOT_INIT)

def _prog_calc(d) -> int:
    init = _be16(d, _PROG_INIT_AT)
    s = _crc(d[0x6100:_find_end(d, 0x7FFF)], init)
    s = _crc(d[0x0000:_find_end(d, 0x3FFF)], s)
    buf = bytearray(0x20000)
    for src, dst in ((0x24000, 0x00000), (0x20000, 0x04000), (0x2C000, 0x08000),
                     (0x28000, 0x0C000), (0x34000, 0x10000), (0x30000, 0x14000),
                     (0x3C000, 0x18000), (0x38000, 0x1C000)):
        buf[dst:dst + 0x4000] = d[src:src + 0x4000]
    s = _crc(buf[0x00000:_find_end(buf, 0x1FFFF)], s)
    return s

def _cal_entries(d):
    """Yield (store_addr, calc_value) for each calibration-table checksum."""
    start = d.find(_CAL_MAGIC)
    if start < 0:
        return
    init = _be16(d, start + 0x0E)
    pos = start
    for _ in range(20):
        ss = _u16le(d, pos)
        if ss == 0xFFFF:
            break
        store = start + ss
        if store + 2 > len(d) or store < pos:
            break
        yield store, _crc(d[pos:store], init)
        pos = store + 2


# ─────────────────────────── Public verify / correct ─────────────────────────

def _cal_verify(d) -> Tuple[bool, int, int]:
    """Return (all_ok, n_ok, n_total) for the calibration checksum table."""
    cal = list(_cal_entries(d))
    n_ok = sum(1 for a, c in cal if _u16le(d, a) == c)
    return (n_ok == len(cal) and len(cal) > 0), n_ok, len(cal)


def verify_checksum(data: bytearray) -> Tuple[bool, List[str]]:
    """
    Verify MS41 checksums. Returns (all_ok, details).
      * 256 KB full ROM : boot-sector + program + calibration table + switch state.
      * 24 KB partial   : calibration checksum table (the only checksums present in
        the partial; boot/program live outside it and are unaffected by cal edits).
    """
    size = len(data)
    d = bytearray(data)

    if size == FULL_ROM_SIZE:
        details = []
        ok = True
        bc, bs = _boot_calc(d), _u16le(d, _BOOT_STORE)
        details.append(f"Boot-sector  : stored 0x{bs:04X} / calc 0x{bc:04X}  "
                       f"{'OK' if bc == bs else 'MISMATCH'}")
        ok &= (bc == bs)
        pc, ps = _prog_calc(d), _u16le(d, _PROG_STORE)
        details.append(f"Program      : stored 0x{ps:04X} / calc 0x{pc:04X}  "
                       f"{'OK' if pc == ps else 'MISMATCH'}")
        ok &= (pc == ps)
        cal_ok, n_ok, n_tot = _cal_verify(d)
        details.append(f"Calibration  : {n_ok}/{n_tot} checksums OK")
        ok &= cal_ok
        details.append(f"Verify switch: {checksum_state(d).upper()} "
                       f"[0x{CHECKSUM_SWITCH_ADDR:05X}=0x{d[CHECKSUM_SWITCH_ADDR]:02X}]")
        return ok, details

    if size == TUNE_SIZE:
        cal_ok, n_ok, n_tot = _cal_verify(d)
        if n_tot == 0:
            return False, ["24 KB partial: no calibration checksum table found "
                           "(missing '4E 00 FF FF' block)."]
        return cal_ok, [
            f"24 KB partial — calibration checksum table: {n_ok}/{n_tot} OK.",
            "Boot/program checksums are outside the partial and unaffected by "
            "calibration edits, so the cal table is what a partial write must fix.",
        ]

    return False, [f"File size {size:,} bytes is not a recognised MS41 image "
                   f"(expected 262,144 or 24,576 bytes)."]


def correct_checksums(data: bytearray, correct_program: bool = True
                      ) -> Tuple[bytearray, List[str]]:
    """
    Recompute and write MS41 checksums.  256 KB ROM: boot + calibration + program.
    24 KB partial: calibration table only (the only checksums in the partial).

    The CALIBRATION-table algorithm is verified for MS41.0/.1/.2 AND MS41.3 (a
    checksum-corrected MS41.3 partial reads 16/16 with it).  The BOOT-sector CRC is
    also verified across variants.  The PROGRAM checksum is only verified for
    MS41.0/.1/.2; pass correct_program=False (e.g. for an MS41.3 full ROM, whose
    program-checksum layout is not yet confirmed) to leave it untouched.

    Returns (corrected_copy, details).  Only checksum storage bytes are written.
    """
    out = bytearray(data)
    size = len(out)
    if size not in (FULL_ROM_SIZE, TUNE_SIZE):
        return out, [f"Checksum correction needs a 256 KB ROM or 24 KB partial "
                     f"(got {size:,} bytes)."]
    details = []
    fixed = 0

    if size == FULL_ROM_SIZE:
        bc = _boot_calc(out); bs = _u16le(out, _BOOT_STORE)
        if bc != bs:
            struct.pack_into("<H", out, _BOOT_STORE, bc); fixed += 1
            details.append(f"Boot-sector corrected: 0x{bs:04X} → 0x{bc:04X}")

    # Calibration table — present in both full ROM and 24 KB partial (magic auto-located).
    for store, calc in _cal_entries(out):
        if _u16le(out, store) != calc:
            struct.pack_into("<H", out, store, calc); fixed += 1
            details.append(f"Cal checksum @0x{store:05X} corrected → 0x{calc:04X}")

    if size == FULL_ROM_SIZE and correct_program:
        pc = _prog_calc(out); ps = _u16le(out, _PROG_STORE)
        if pc != ps:
            struct.pack_into("<H", out, _PROG_STORE, pc); fixed += 1
            details.append(f"Program corrected: 0x{ps:04X} → 0x{pc:04X}")

    details.append(f"{fixed} checksum(s) corrected." if fixed
                   else "All checksums already valid — no changes.")
    return out, details


# ─────────────────────────── Enable switch ───────────────────────────────────

def checksum_state(data: bytearray) -> str:
    if len(data) != FULL_ROM_SIZE:
        return "n/a"
    b = data[CHECKSUM_SWITCH_ADDR]
    if b == CK_ENABLED:  return "enabled"
    if b == CK_DISABLED: return "disabled"
    return "unknown"


# ─────────────────────────────────────────────────────────────────────────────
# End of verbatim port from Flasher/checksum.py.
# ─────────────────────────────────────────────────────────────────────────────


@register("checksums", "ms41")
class MS41Checksum:
    name = "ms41"
    def __init__(self, *, correct_program: bool = True) -> None:
        # correct_program=False for MS41.3 full reads (program-checksum layout unverified — see the
        # Flasher/checksum.py docstring). Boot + calibration checksums are corrected regardless.
        self._correct_program = correct_program
    def validate(self, data: bytes) -> tuple[bool, list[str]]:
        return verify_checksum(bytearray(data))
    def update(self, data: bytearray) -> list[str]:
        if len(data) not in (FULL_ROM_SIZE, TUNE_SIZE):
            raise ChecksumError(
                "MS41 checksum correction requires a 24 KB partial or 256 KB full read "
                f"(got {len(data):,} bytes)"
            )
        corrected, details = correct_checksums(data, correct_program=self._correct_program)
        data[:] = corrected          # write in place
        return details

    def report(self, data: bytes) -> ChecksumReport:
        # bytearray, not bytes: matches verify_checksum's own local `d` and satisfies
        # checksum_state(data: bytearray)'s existing (ported-verbatim) type — no behavior
        # difference, since every helper below only slices/indexes/`.find()`s `d`.
        d = bytearray(data)
        if len(d) != FULL_ROM_SIZE:
            # 24 KB cal-only framing: boot/program/verify-switch regions are absent.
            cal_ok, n_ok, n_tot = _cal_verify(d)  # 3-tuple (all_ok, n_ok, n_total) — matches verify_checksum
            return ChecksumReport(regions=(
                RegionStatus("Boot", "n/a", "not present in 24 KB read"),
                RegionStatus("Program", "n/a", "not present in 24 KB read"),
                RegionStatus("Calibration", "ok" if cal_ok else "mismatch", f"{n_ok}/{n_tot}"),
                RegionStatus("Verify switch", "n/a", "not present in 24 KB read"),
            ))
        bc, bs = _boot_calc(d), _u16le(d, _BOOT_STORE)
        boot = RegionStatus("Boot", "ok" if bc == bs else "mismatch",
                            f"stored 0x{bs:04X} / calc 0x{bc:04X}")
        if not self._correct_program:
            prog = RegionStatus("Program", "n/a", "not corrected on MS41.3")
        else:
            pc, ps = _prog_calc(d), _u16le(d, _PROG_STORE)
            prog = RegionStatus("Program", "ok" if pc == ps else "mismatch",
                                f"stored 0x{ps:04X} / calc 0x{pc:04X}")
        cal_ok, n_ok, n_tot = _cal_verify(d)
        cal = RegionStatus("Calibration", "ok" if cal_ok else "mismatch", f"{n_ok}/{n_tot}")
        state = checksum_state(d)                  # "enabled" | "disabled" | "unknown" | "n/a"
        verify = RegionStatus("Verify switch",
                              {"enabled": "on", "disabled": "off"}.get(state, state), "")
        return ChecksumReport(regions=(boot, prog, cal, verify))
