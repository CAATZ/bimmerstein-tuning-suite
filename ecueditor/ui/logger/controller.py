from __future__ import annotations
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Sequence

from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot

_log = logging.getLogger(__name__)


class _EngineWorker(QObject):
    """Runs the blocking engine.run(stop) loop on a QThread."""
    finished = Signal()
    failed = Signal(str)

    def __init__(self, engine, stop: threading.Event) -> None:
        super().__init__()
        self._engine = engine
        self._stop = stop

    @Slot()
    def run(self) -> None:
        try:
            self._engine.run(self._stop)
        except Exception as exc:          # noqa: BLE001 — surfaced to the UI, never crashes the thread
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()


@dataclass(frozen=True)
class LoggerStats:
    """Periodic query stats the controller assembles from poll outcomes (INTERFACES.md ui/logger)."""
    polls: int
    errors: int
    rate_hz: float


class LoggerController(QObject):
    sampleReady = Signal(object)          # Sample; queued cross-thread to the UI
    started = Signal(str)                  # ECU-ID
    stopped = Signal()
    statsUpdated = Signal(object)          # LoggerStats: polls, errors, rate_hz (assembled on a timer)
    modeUpdated = Signal(str)              # Fast batch / Compatible / fallback state
    errorOccurred = Signal(str)

    _RATE_WINDOW_S = 3.0

    def __init__(self, engine, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._engine = engine
        self._thread: QThread | None = None
        self._worker: _EngineWorker | None = None
        self._stop = threading.Event()
        self._unsubscribe = None
        self._poll_count = 0
        self._error_count = 0
        self._poll_times: deque[float] = deque(maxlen=4096)
        self._last_mode_status = ""
        self._stats_timer = QTimer(self)       # lives on the main thread with the controller
        self._stats_timer.setInterval(500)
        self._stats_timer.timeout.connect(self._emit_stats)

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.isRunning()

    @property
    def cal_id(self) -> str:
        # Pass through LoggerEngine.cal_id for the window's CAL chip. Engines without a
        # verified CAL ID return an empty string, which the window renders as unknown.
        return getattr(self._engine, "cal_id", "")

    def start(self, channel_ids: Sequence[str], *, poll_mode: str = "auto") -> None:
        if self.is_running:
            return
        set_mode = getattr(self._engine, "set_poll_mode", None)
        if set_mode is not None:
            set_mode(poll_mode)
        self._engine.select(list(channel_ids))
        self._unsubscribe = self._engine.subscribe(self._on_sample)
        self._stop = threading.Event()
        self._poll_count = 0
        self._error_count = 0
        self._poll_times = deque(maxlen=4096)

        self._thread = QThread()
        self._worker = _EngineWorker(self._engine, self._stop)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._thread.quit)
        self._worker.failed.connect(self._on_worker_failed)
        self._thread.finished.connect(self._cleanup)
        self._thread.start()
        self._stats_timer.start()
        # ECU-ID comes off the engine once the connection has init'd (LoggerEngine.ecu_id, a
        # contract property mirroring ConnectionManager.ecu_id). Emit after the thread is live.
        self.started.emit(self._engine.ecu_id or "")
        self._emit_mode_status()

    def update_selection(self, channel_ids: Sequence[str], *, poll_mode: str = "auto") -> None:
        """Atomically replace the engine poll set; LoggerEngine applies it on the next cycle."""
        set_mode = getattr(self._engine, "set_poll_mode", None)
        if set_mode is not None:
            set_mode(poll_mode)
        self._engine.select(list(channel_ids))
        self._emit_mode_status()

    def _on_sample(self, sample) -> None:
        # Runs on the WORKER thread. Emitting a Qt signal is thread-safe and is delivered
        # to the receiver's thread via a queued connection — the only legal UI crossing.
        self._poll_count += 1        # monotonic display counter; read by the stats timer
        self._poll_times.append(time.monotonic())    # GIL-tolerant append; read by the stats timer
        self.sampleReady.emit(sample)

    @Slot(str)
    def _on_worker_failed(self, msg: str) -> None:
        self._error_count += 1
        self.errorOccurred.emit(msg)

    def _emit_stats(self) -> None:
        now = time.monotonic()
        cutoff = now - self._RATE_WINDOW_S
        # Snapshot via list() FIRST: iterating self._poll_times directly races the worker
        # thread's append() ("deque mutated during iteration"); list(deque) is atomic under
        # the GIL (no Python bytecode runs mid-copy), the comprehension over the snapshot is safe.
        recent = [t for t in list(self._poll_times) if t >= cutoff]
        if len(recent) >= 2:
            span = max(now - min(recent), 1e-9)
            rate = len(recent) / span
        else:
            rate = 0.0
        self.statsUpdated.emit(LoggerStats(polls=self._poll_count,
                                            errors=self._error_count, rate_hz=rate))
        self._emit_mode_status()

    def _emit_mode_status(self) -> None:
        status = getattr(self._engine, "poll_status", "Compatible")
        if status != self._last_mode_status:
            self._last_mode_status = status
            self.modeUpdated.emit(status)

    @property
    def selection_report(self):
        return getattr(self._engine, "selection_report", None)

    def stop(self) -> None:
        self._stats_timer.stop()
        if self._unsubscribe is not None:
            try:
                self._unsubscribe()
            finally:
                self._unsubscribe = None
        self._stop.set()
        if self._thread is not None:
            self._thread.quit()
            if not self._thread.wait(5000):
                _log.warning("logger thread still running after 5s; waiting unbounded")
                self._thread.wait()

    @Slot()
    def _cleanup(self) -> None:
        self._thread = None
        self._worker = None
        self.stopped.emit()
