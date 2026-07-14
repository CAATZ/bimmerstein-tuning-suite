from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence
from ecueditor.core.comms.connection import ConnectionManager
from ecueditor.core.comms.protocol.ds2 import DS2Protocol
from ecueditor.core.comms.transport.replay import ReplayTransport
from ecueditor.core.loggerdef.parser import parse_logger_definition
from ecueditor.core.logger.engine import LoggerEngine, Sample
from ecueditor.core.logger.recorder import CsvRecorder


@dataclass(frozen=True)
class ReplaySessionResult:
    ecu_id: str
    samples: list[Sample]


def run_replay_session(logger_def: str | Path, script_file: str | Path,
                       channels: Sequence[str], *, polls: int = 1,
                       record_dir: str | Path | None = None,
                       timestamp: str | None = None) -> ReplaySessionResult:
    definition = parse_logger_definition(logger_def)
    transport = ReplayTransport.from_file(script_file)
    conn = ConnectionManager(transport, DS2Protocol())
    conn.open("REPLAY")
    conn.init()
    ecu_id = conn.ecu_id
    assert ecu_id is not None                          # init() sets it; narrow for mypy before use
    tick = {"n": 0}
    engine = LoggerEngine(conn, definition,
                          clock=lambda: float(tick["n"] * 100))   # deterministic 100 ms cadence
    engine.select(channels)
    recorder: CsvRecorder | None = None
    if record_dir is not None:
        recorder = CsvRecorder(Path(record_dir), absolute_time=False, timestamp=timestamp)
        recorder.start(engine.selected_channels())     # resolvable channels, in selection order
    out: list[Sample] = []
    try:
        for _ in range(polls):
            sample = engine.poll_once()
            out.append(sample)
            if recorder is not None:
                recorder.write(sample)
            tick["n"] += 1
    finally:
        if recorder is not None:
            recorder.stop()
        conn.close()
    return ReplaySessionResult(ecu_id=ecu_id, samples=out)
