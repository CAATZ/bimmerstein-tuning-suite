# SPDX-License-Identifier: GPL-2.0-or-later
# Includes code adapted from MS41 Projects/Flasher under the MIT License.
# Copyright (c) 2026 CAATZ
# Modified for BimmerStein Tuning Suite on 2026-07-13; see THIRD_PARTY_NOTICES.md.

from __future__ import annotations
import time
from typing import Callable
from ecueditor.core.comms.transport.base import SerialParams
from ecueditor.core.errors import CommsError, CommsTimeout

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
        self._pending_rx = bytearray()

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
        self._pending_rx.clear()
        try:
            self._open_device(port, params)
        except CommsError:
            self._close_partial_open()
            raise
        except Exception as exc:  # noqa: BLE001 - normalize optional driver errors
            self._close_partial_open()
            raise CommsError(f"failed to open {self.name} transport on {port!r}: {exc}") from exc
        self._is_open = True

    def write(self, data: bytes) -> None:
        data = bytes(data)
        try:
            self._raw_write(data)
            self._raw_flush()                    # drain TX to the wire before reading echo
            if self._strip_echo:
                # Sleep the exact TX time so a present echo is fully on the wire.  Discard only
                # an exact, complete match; a no-echo adapter may already expose the ECU reply
                # during this probe, and those bytes must be preserved for read().
                self._sleep(len(data) * self._bits_per_char / self._baud)
                probe = self._read_raw_upto(len(data), ECHO_READ_MS)
                if probe != data:
                    self._pending_rx.extend(probe)
        except CommsError:
            raise
        except Exception as exc:  # noqa: BLE001 - normalize optional driver errors
            raise CommsError(f"{self.name} write failed: {exc}") from exc

    def read(self, n: int, timeout_ms: int) -> bytes:
        got = self._read_upto(n, timeout_ms)
        if len(got) < n:
            raise CommsTimeout(f"half-duplex read {len(got)}/{n} bytes")
        return got

    def _read_upto(self, n: int, timeout_ms: int) -> bytes:
        buf = bytearray(self._pending_rx[:n])
        del self._pending_rx[:len(buf)]
        if len(buf) >= n:
            return bytes(buf)
        try:
            buf.extend(self._read_raw_upto(n - len(buf), timeout_ms))
        except CommsError:
            raise
        except Exception as exc:  # noqa: BLE001 - normalize optional driver errors
            raise CommsError(f"{self.name} read failed: {exc}") from exc
        return bytes(buf)

    def _read_raw_upto(self, n: int, timeout_ms: int) -> bytes:
        self._set_read_timeout(timeout_ms)
        buf = bytearray()
        while len(buf) < n:
            chunk = self._raw_read(n - len(buf))
            if not chunk:
                break
            buf.extend(chunk)
        return bytes(buf)

    def flush_input(self) -> None:
        self._pending_rx.clear()
        try:
            self._raw_reset_input()
        except Exception as exc:  # noqa: BLE001 - normalize optional driver errors
            raise CommsError(f"{self.name} input flush failed: {exc}") from exc

    def close(self) -> None:
        if self._is_open:
            try:
                self._close_device()
            finally:
                self._is_open = False
                self._pending_rx.clear()

    def _close_partial_open(self) -> None:
        try:
            self._close_device()
        except Exception:  # noqa: BLE001 - preserve the original open failure
            pass
        self._is_open = False
        self._pending_rx.clear()

    @property
    def is_open(self) -> bool:
        return self._is_open
