from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Mapping, Sequence

from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot

_log = logging.getLogger(__name__)


class _EngineWorker(QObject):
    """Run the blocking engine loop on a dedicated QThread."""

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
        except (Exception, SystemExit) as exc:  # surfaced to the UI; keep KeyboardInterrupt
            self.failed.emit(str(exc) or type(exc).__name__)
        finally:
            self.finished.emit()


@dataclass(frozen=True)
class LoggerStats:
    polls: int
    errors: int
    rate_hz: float


class LoggerController(QObject):
    """Own one initialized logger engine, its worker thread, and its connection lifetime."""

    sampleReady = Signal(object)
    started = Signal(str)
    stopped = Signal()
    statsUpdated = Signal(object)
    modeUpdated = Signal(str)
    errorOccurred = Signal(str)

    _RATE_WINDOW_S = 3.0

    def __init__(self, engine, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._engine = engine
        self._thread: QThread | None = None
        self._worker: _EngineWorker | None = None
        self._stop = threading.Event()
        self._unsubscribe: Callable[[], None] | None = None
        self._poll_count = 0
        self._error_count = 0
        self._poll_times: deque[float] = deque(maxlen=4096)
        self._last_mode_status = ""
        self._engine_closed = False
        self._cleanup_done = False
        self._stats_timer = QTimer(self)
        self._stats_timer.setInterval(500)
        self._stats_timer.timeout.connect(self._emit_stats)

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.isRunning()

    @property
    def cal_id(self) -> str:
        return getattr(self._engine, "cal_id", "")

    def start(
        self,
        channel_ids: Sequence[str],
        *,
        poll_mode: str = "auto",
        units: Mapping[str, str | None] | None = None,
    ) -> None:
        if self.is_running:
            return
        if self._engine_closed:
            raise RuntimeError("logger controller cannot restart a closed connection")
        try:
            set_mode = getattr(self._engine, "set_poll_mode", None)
            if set_mode is not None:
                set_mode(poll_mode)
            self._select(channel_ids, units)
            self._unsubscribe = self._engine.subscribe(self._on_sample)
        except Exception:
            self._close_engine()
            raise

        self._stop = threading.Event()
        self._poll_count = 0
        self._error_count = 0
        self._poll_times = deque(maxlen=4096)
        self._cleanup_done = False

        thread = QThread()
        worker = _EngineWorker(self._engine, self._stop)
        self._thread = thread
        self._worker = worker
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(thread.quit)
        worker.failed.connect(self._on_worker_failed)
        thread.finished.connect(lambda thread=thread: self._cleanup(thread))
        thread.start()
        self._stats_timer.start()
        self.started.emit(self._engine.ecu_id or "")
        self._emit_mode_status()

    def update_selection(
        self,
        channel_ids: Sequence[str],
        *,
        poll_mode: str = "auto",
        units: Mapping[str, str | None] | None = None,
    ) -> None:
        set_mode = getattr(self._engine, "set_poll_mode", None)
        if set_mode is not None:
            set_mode(poll_mode)
        self._select(channel_ids, units)
        self._emit_mode_status()

    def _select(
        self, channel_ids: Sequence[str], units: Mapping[str, str | None] | None,
    ) -> None:
        select_with_units = getattr(self._engine, "select_with_units", None)
        if units and callable(select_with_units):
            select_with_units(list(channel_ids), units)
        else:
            self._engine.select(list(channel_ids))

    def _on_sample(self, sample) -> None:
        self._poll_count += 1
        self._poll_times.append(time.monotonic())
        self.sampleReady.emit(sample)

    @Slot(str)
    def _on_worker_failed(self, message: str) -> None:
        self._error_count += 1
        self.errorOccurred.emit(message)

    def _emit_stats(self) -> None:
        now = time.monotonic()
        cutoff = now - self._RATE_WINDOW_S
        recent = [timestamp for timestamp in list(self._poll_times) if timestamp >= cutoff]
        if len(recent) >= 2:
            span = max(now - min(recent), 1e-9)
            rate = len(recent) / span
        else:
            rate = 0.0
        self.statsUpdated.emit(LoggerStats(
            polls=self._poll_count, errors=self._error_count, rate_hz=rate,
        ))
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
        self._unsubscribe_now()
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.quit()
            if not thread.wait(5000):
                _log.warning("logger thread still running after 5s; closing transport to interrupt I/O")
                self._close_engine()
                if not thread.wait(2000):
                    _log.warning("logger thread still running after transport close; waiting unbounded")
                    thread.wait()
        self._cleanup(thread)

    def _unsubscribe_now(self) -> None:
        if self._unsubscribe is not None:
            try:
                self._unsubscribe()
            finally:
                self._unsubscribe = None

    def _close_engine(self) -> None:
        if self._engine_closed:
            return
        self._engine_closed = True
        close = getattr(self._engine, "close", None)
        if callable(close):
            try:
                close()
            except (Exception, SystemExit) as exc:  # teardown must finish
                _log.warning("logger connection close failed: %s", exc, exc_info=exc)

    @Slot()
    def _cleanup(self, thread: QThread | None = None) -> None:
        if thread is not None and self._thread is not None and thread is not self._thread:
            return
        if self._cleanup_done:
            return
        self._cleanup_done = True
        self._stats_timer.stop()
        self._unsubscribe_now()
        self._stop.set()
        self._close_engine()
        if thread is None or thread is self._thread:
            self._thread = None
            self._worker = None
        self.stopped.emit()
