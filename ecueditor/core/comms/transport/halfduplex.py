# SPDX-License-Identifier: GPL-2.0-or-later
# Includes code adapted from MS41 Projects/Flasher under the MIT License.
# Copyright (c) 2026 CAATZ
# Modified for BimmerStein Tuning Suite on 2026-07-13; see THIRD_PARTY_NOTICES.md.

from __future__ import annotations
import time
from typing import Callable
from ecueditor.core.comms.transport.base import SerialParams
from ecueditor.core.errors import CommsTimeout

ECHO_READ_MS = 5   # per-read budget while consuming half-duplex TX echo (FT232 ~1-2 ms latency timer)

class HalfDuplexTransport:
    """Base for K-line transports. Strips half-duplex TX echo BELOW ConnectionManager, so the
    manager stays echo-agnostic (backlog 'Phase 3 exit'). All byte I/O goes through overridable
    device primitives, so the echo/timeout logic is testable with a fake wire (no hardware).
    Ported from MS41 Projects/Flasher/ds2.py::_discard_echo (byte-verified on FT232)."""

    name = "halfduplex"

    def __init__(self, *, strip_echo: bool = True,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self._strip_echo = strip_echo
        self._sleep = sleep
        self._baud = 9600
        self._bits_per_char = 12                 # 8E2 default; recomputed in open()
        self._is_open = False

    # -- subclass primitives -------------------------------------------------
    def _open_device(self, port: str, params: SerialParams) -> None: raise NotImplementedError
    def _raw_write(self, data: bytes) -> None: raise NotImplementedError
    def _raw_read(self, n: int) -> bytes: raise NotImplementedError   # up to n within current timeout
    def _set_read_timeout(self, timeout_ms: int) -> None: raise NotImplementedError
    def _raw_flush(self) -> None: pass
    def _raw_reset_input(self) -> None: raise NotImplementedError
    def _close_device(self) -> None: raise NotImplementedError

    # -- Transport surface ---------------------------------------------------
    def open(self, port: str, params: SerialParams) -> None:
        self._baud = params.baud
        # bits/char = start + data + (parity?) + stop  (8E2 => 12)
        self._bits_per_char = 1 + params.databits + (0 if params.parity == "none" else 1) + params.stopbits
        self._open_device(port, params)
        self._is_open = True

    def write(self, data: bytes) -> None:
        data = bytes(data)
        self._raw_write(data)
        self._raw_flush()                        # drain TX to the wire before reading echo
        if self._strip_echo:
            # Sleep the exact TX time so a present echo is fully on the wire, then discard it.
            self._sleep(len(data) * self._bits_per_char / self._baud)
            self._read_upto(len(data), ECHO_READ_MS)   # tolerant: short is fine (echo-suppressing adapter)

    def read(self, n: int, timeout_ms: int) -> bytes:
        got = self._read_upto(n, timeout_ms)
        if len(got) < n:
            raise CommsTimeout(f"half-duplex read {len(got)}/{n} bytes")
        return got

    def _read_upto(self, n: int, timeout_ms: int) -> bytes:
        self._set_read_timeout(timeout_ms)
        buf = bytearray()
        while len(buf) < n:
            chunk = self._raw_read(n - len(buf))
            if not chunk:
                break
            buf.extend(chunk)
        return bytes(buf)

    def flush_input(self) -> None:
        self._raw_reset_input()

    def close(self) -> None:
        if self._is_open:
            self._close_device()
            self._is_open = False

    @property
    def is_open(self) -> bool:
        return self._is_open
