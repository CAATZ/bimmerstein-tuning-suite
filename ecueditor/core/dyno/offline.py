from __future__ import annotations
import csv
from pathlib import Path
from ecueditor.core.errors import ECUEditorError
from ecueditor.core.logger.engine import Sample
from ecueditor.core.dyno.run import ENGINE_SPEED, THROTTLE_ANGLE, VEHICLE_SPEED

# header base (paren unit-suffix stripped, lowercased) -> time-scale (multiply to reach ms).
# Keys are matched AFTER _base() strips "(...)", so a raw "Time (msec)" column bases to "time" —
# there is deliberately no "time (msec)" key (it could never match once the suffix is stripped).
_TIME_HEADERS = {"time": 1.0, "time/s": 1000.0, "seconds": 1000.0}
_RPM_HEADERS = ("engine speed", "rpm")
_THROTTLE_HEADERS = ("throttle",)
_SPEED_HEADERS = ("vehicle speed",)

def _base(header: str) -> str:
    return header.split("(")[0].strip().lower()

def load_dyno_samples(path: str | Path) -> list[Sample]:
    """Parse a logger CSV into dyno Samples (channel ids P8/P13/P9). fact base §4.6.

    Raises ECUEditorError if no time column is recognized, if two different columns both look
    like the time axis (ambiguous), or if any row is malformed -- a numeric cell fails to parse
    or a row has too few columns for a header it must read (hostile input, spec §6).
    """
    rows = list(csv.reader(Path(path).read_text(encoding="utf-8").splitlines()))
    if not rows:
        return []
    headers = rows[0]
    time_col: int | None = None
    time_scale: float | None = None
    rpm_col: int | None = None
    thr_col: int | None = None
    vs_col: int | None = None
    for i, h in enumerate(headers):
        b = _base(h)
        if b in _TIME_HEADERS:
            if time_col is not None:
                raise ECUEditorError(
                    f"ambiguous time column in {path}: matched {headers[time_col]!r} and {h!r}"
                )
            time_col, time_scale = i, _TIME_HEADERS[b]
        elif any(b == k or b.startswith(k) for k in _RPM_HEADERS):
            rpm_col = i
        elif any(b.startswith(k) for k in _THROTTLE_HEADERS):
            thr_col = i
        elif any(b.startswith(k) for k in _SPEED_HEADERS):
            vs_col = i
    if time_col is None or time_scale is None:
        raise ECUEditorError(
            f"no recognized time column in {path}: headers={headers!r} "
            "(expected one of Time/Time (msec)/Seconds/Time/s)"
        )
    samples: list[Sample] = []
    for row in rows[1:]:
        if not row:
            continue
        try:
            values: dict[str, float] = {}
            if rpm_col is not None and row[rpm_col].strip():
                values[ENGINE_SPEED] = float(row[rpm_col])
            if thr_col is not None and row[thr_col].strip():
                values[THROTTLE_ANGLE] = float(row[thr_col])
            if vs_col is not None and row[vs_col].strip():
                values[VEHICLE_SPEED] = float(row[vs_col])
            ts = float(row[time_col]) * time_scale
        except (ValueError, IndexError) as exc:
            raise ECUEditorError(
                f"malformed CSV row in {path} (bad cell or too few columns): {row!r}: {exc}"
            ) from exc
        samples.append(Sample(timestamp_ms=ts, values=values))
    return samples
