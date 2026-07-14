from __future__ import annotations
import csv
import re
import threading
import time
from pathlib import Path
from typing import Callable, Iterator
from ecueditor.core.logger.engine import Sample

_ID_SUFFIX = re.compile(r"\s*\[([^\[\]]+)\]\s*$")   # trailing "[id]" that CsvRecorder appends

def _channel_id(header_label: str) -> str:
    """Recover the channel id from a "name (units) [id]" column ("Load (mg/stroke) [E2]" -> "E2")."""
    m = _ID_SUFFIX.search(header_label)
    return m.group(1) if m else header_label

class PlaybackSource:
    """Replays a recorded RomRaider CSV as Samples (the feature RomRaider left stubbed)."""

    def __init__(self, csv_path: Path) -> None:
        self._path = Path(csv_path)

    def samples(self) -> Iterator[Sample]:
        with self._path.open(newline="", encoding="utf-8") as fh:
            reader = csv.reader(fh)
            header = next(reader, None)
            if not header:
                return
            keys = [_channel_id(h) for h in header[1:]]   # "name (units) [id]" -> channel id
            for row in reader:
                if not row:
                    continue
                ts = float(row[0])
                values: dict[str, float] = {}
                for key, cell in zip(keys, row[1:]):
                    if cell != "":
                        values[key] = float(cell)
                yield Sample(timestamp_ms=ts, values=values)

    def play(self, callback: Callable[[Sample], None], *, speed: float = 1.0,
             stop: threading.Event | None = None) -> None:
        prev_ms: float | None = None
        for sample in self.samples():
            if stop is not None and stop.is_set():
                return
            if prev_ms is not None and speed > 0:
                dt = (sample.timestamp_ms - prev_ms) / 1000.0 / speed
                if dt > 0:
                    time.sleep(dt)
            callback(sample)
            prev_ms = sample.timestamp_ms
