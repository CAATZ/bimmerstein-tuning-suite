from __future__ import annotations
from dataclasses import dataclass
from ecueditor.core.logger.engine import Sample
from ecueditor.core.dyno.run import VEHICLE_SPEED

_SPLIT_FEET = (60, 330, 660, 1000, 1320)   # 60ft, 330ft, 1/8mi, 1000ft, 1/4mi (fact base §4.5)

@dataclass(frozen=True)
class ETResult:
    splits: dict[int, tuple[float, float]]   # ft -> (elapsed_s, trap_speed)
    zero_to_sixty_s: float | None
    quarter_mile_s: float | None

class ETCapture:
    """Quarter-mile timer. Integrates distance from vehicle speed; auto-stops past 1330 ft (fact base §4.5)."""
    def __init__(self, *, kmh: bool = False) -> None:
        self.kmh = kmh
        self._distance_ft = 0.0
        self._prev_t: float | None = None
        self._t0_ms: float | None = None
        self._splits: dict[int, tuple[float, float]] = {}
        self._zero_to_sixty: float | None = None
        self._stopped = False

    def accept(self, sample: Sample) -> None:
        if self._stopped:
            return
        vs = sample.values.get(VEHICLE_SPEED)
        if vs is None:
            return
        vs_mph = vs / 1.609344 if self.kmh else vs
        if self._t0_ms is None:
            self._t0_ms = sample.timestamp_ms
        t_s = sample.timestamp_ms / 1000.0
        elapsed_s = (sample.timestamp_ms - self._t0_ms) / 1000.0
        if self._prev_t is not None:
            dt_ms = sample.timestamp_ms - self._prev_t * 1000.0
            # distance += vs_mph*5280/3600 * dt_s   (fact base §4.5)
            self._distance_ft += vs_mph * 5280.0 / 3600.0 * dt_ms / 1000.0
        self._prev_t = t_s
        if self._zero_to_sixty is None and vs_mph >= 60.0:
            self._zero_to_sixty = elapsed_s
        for ft in _SPLIT_FEET:
            if ft not in self._splits and self._distance_ft >= ft:
                self._splits[ft] = (elapsed_s, vs_mph)
        if self._distance_ft > 1330.0:
            self._stopped = True

    @property
    def is_stopped(self) -> bool:
        """(Phase 6b) True once integrated distance passed 1330 ft (fact base §4.5)."""
        return self._stopped

    def finish(self) -> ETResult:
        quarter = self._splits.get(1320, (None, None))[0]
        return ETResult(splits=dict(self._splits), zero_to_sixty_s=self._zero_to_sixty,
                        quarter_mile_s=quarter)
