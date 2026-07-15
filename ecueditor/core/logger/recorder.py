from __future__ import annotations
import csv
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence, TextIO
from ecueditor.core.logger.engine import Sample
from ecueditor.core.loggerdef.channel import LoggerChannel

class CsvRecorder:
    def __init__(self, out_dir: Path, *, absolute_time: bool, name_infix: str = "",
                 timestamp: str | None = None) -> None:
        self._dir = Path(out_dir)
        self._absolute = absolute_time
        self._infix = name_infix
        self._timestamp = timestamp                 # inject for determinism; else now() at start()
        self._fh: TextIO | None = None
        self._writer: Any = None                    # csv.writer has no public type; Any keeps mypy clean
        self._channels: list[LoggerChannel] = []
        self._start_ms: float | None = None
        self.path: Path | None = None

    def start(self, channels: Sequence[LoggerChannel]) -> Path:
        self.stop()
        self._dir.mkdir(parents=True, exist_ok=True)
        stamp = self._timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
        infix = f"_{self._infix}" if self._infix else ""
        self._channels = list(channels)
        self._start_ms = None
        stem = f"ecueditorlog{infix}_{stamp}"
        suffix = 1
        while True:
            name = f"{stem}.csv" if suffix == 1 else f"{stem}_{suffix}.csv"
            candidate = self._dir / name
            try:
                self._fh = candidate.open("x", newline="", encoding="utf-8")
            except FileExistsError:
                suffix += 1
                continue
            self.path = candidate
            break
        self._writer = csv.writer(self._fh)
        time_header = "Time" if self._absolute else "Time (msec)"
        # header column = "name (units) [id]"; the "[id]" suffix lets PlaybackSource key replayed
        # Samples by channel id (matching live Samples), per INTERFACES.md.
        header = [time_header] + [
            f"{c.name} ({c.conversion.units if c.conversion else ''}) [{c.id}]"
            for c in self._channels
        ]
        self._writer.writerow(header)
        return candidate

    def write(self, sample: Sample) -> None:
        if self._writer is None:
            raise RuntimeError("CsvRecorder.write called before start()")
        if self._absolute:
            tcol: float = sample.timestamp_ms
        else:
            if self._start_ms is None:
                self._start_ms = sample.timestamp_ms
            tcol = sample.timestamp_ms - self._start_ms
        row = [tcol] + [sample.values.get(c.id, "") for c in self._channels]
        self._writer.writerow(row)

    def stop(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None
            self._writer = None
