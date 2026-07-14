from __future__ import annotations
from dataclasses import dataclass, field
import numpy as np
from ecueditor.core.logger.analysis.base import AnalysisResult
from ecueditor.core.logger.analysis.channels import ChannelMap
from ecueditor.core.logger.analysis.maf import MafFilters
from ecueditor.core.plugins.registry import register

_INJ_ROLES = ("pulse_width", "load", "afr", "rpm", "maf", "iat", "ect", "closed_loop", "throttle")

@dataclass
class InjectorParams:
    stoich_afr: float = 14.7          # fact base 3.1 default
    fuel_density: float = 732.0       # fact base 3.1 default

class InjectorFilters(MafFilters):    # same gating thresholds as MAF (fact base 3.1)
    pass

@register("analyses", "injector")
@dataclass
class InjectorAnalysis:
    channel_map: ChannelMap
    params: InjectorParams = field(default_factory=InjectorParams)
    filters: InjectorFilters = field(default_factory=InjectorFilters)
    latency_table: str = "Fuel Injector - Dead Time / Latency"
    flow_table_candidates: tuple[str, ...] = ("Injector Flow Scaling", "Injector Scaling")
    id: str = "injector"
    title: str = "Injector"
    points: list[tuple[float, float]] = field(default_factory=list)
    _prev_v: float | None = None
    _prev_thr: float | None = None
    _prev_t: float | None = None

    @property
    def required_channels(self) -> tuple[str, ...]:
        return self.channel_map.resolve(_INJ_ROLES)

    @property
    def required_roles(self) -> tuple[str, ...]:
        """(Phase 6b) Logical roles accept() requires; maf_voltage stays optional here."""
        return _INJ_ROLES

    def fuelcc(self, load: float) -> float:
        p = self.params
        return load / 2.0 / p.stoich_afr * 1000.0 / p.fuel_density

    def _rate(self, cur, prev, cur_t):
        if prev is None or self._prev_t is None:
            return 0.0
        dt = (cur_t - self._prev_t) / 1000.0
        return abs(cur - prev) / dt if dt > 0 else 0.0

    def accept(self, sample) -> None:
        r = self.channel_map.roles
        v = sample.values
        if any(r[role] not in v for role in _INJ_ROLES):
            return   # (Phase 6b, H2) missing a required channel: skip, baselines untouched
        mafv = v[r["maf_voltage"]] if r["maf_voltage"] in v else None
        thr = v[r["throttle"]]; t = sample.timestamp_ms
        f = self.filters
        dthr = self._rate(thr, self._prev_thr, t)
        dv = self._rate(mafv, self._prev_v, t) if mafv is not None else 0.0
        ok = (
            bool(v[r["closed_loop"]])
            and f.afr_min <= v[r["afr"]] <= f.afr_max
            and f.rpm_min <= v[r["rpm"]] <= f.rpm_max
            and f.maf_min <= v[r["maf"]] <= f.maf_max
            and v[r["ect"]] >= f.ect_min
            and v[r["iat"]] <= f.iat_max
            and dv <= f.dmafv_dt_max
            and dthr <= f.tip_in_max
        )
        self._prev_v, self._prev_thr, self._prev_t = mafv, thr, t
        if ok:
            self.points.append((v[r["pulse_width"]], self.fuelcc(v[r["load"]])))

    def reset(self) -> None:
        """Clear accumulated points and rate-gate baselines -- equivalent to a fresh engine."""
        self.points.clear()
        self._prev_v = self._prev_thr = self._prev_t = None

    def _regress(self):
        xs = np.array([p[0] for p in self.points], dtype=float)
        ys = np.array([p[1] for p in self.points], dtype=float)
        slope, intercept = np.polyfit(xs, ys, 1)      # coeff[0]=slope, coeff[1]=intercept
        return float(slope), float(intercept)

    def result(self) -> AnalysisResult:
        corrections: dict[str, float] = {}
        fit_x: tuple[float, ...] = ()
        fit_y: tuple[float, ...] = ()
        if len(self.points) >= 2:
            slope, intercept = self._regress()
            flow = slope * 1000.0 * 60.0
            latency = -intercept / slope if slope != 0 else 0.0
            corrections = {"slope_cc_per_ms": slope, "intercept_cc": intercept,
                           "flow_ccmin": flow, "latency_ms": latency}
            xs = sorted(p[0] for p in self.points)
            fit_x = (xs[0], xs[-1])
            fit_y = (slope * xs[0] + intercept, slope * xs[-1] + intercept)
        return AnalysisResult(kind="injector", x_label="Injector Pulse Width (ms)",
                              y_label="Fuel (cc)", points=tuple(self.points),
                              fit_x=fit_x, fit_y=fit_y, corrections=corrections,
                              sample_count=len(self.points))

    def apply_to_rom(self, rom) -> list[str]:
        """Mutate the ROM's flow-scaling/latency tables in place; apply-once semantics -- a repeated
        call stacks the latency offset again on top of the last write. Returns advisory notes."""
        if len(self.points) < 2:
            raise ValueError("need >= 2 points before apply_to_rom()")
        slope, intercept = self._regress()
        flow = slope * 1000.0 * 60.0
        latency = -intercept / slope if slope != 0 else 0.0
        notes: list[str] = []

        # flow scaling: SET (configurable target; MS41 has no such table by RomRaider's name -> fact base 7.4)
        flow_tbl = next((rom.tables[n] for n in self.flow_table_candidates if n in rom.tables), None)
        if flow_tbl is None:
            notes.append(f"Injector: flow-scaling table not found "
                         f"(tried {', '.join(self.flow_table_candidates)}); flow={flow:.1f} cc/min not written")
        else:
            for cell in flow_tbl.cells:
                cell.set_real(flow)
            notes.append(f"Injector: set flow scaling {flow:.1f} cc/min in {flow_tbl.name!r}")

        # latency: ADD the offset to each dead-time cell (shifts the curve, RomRaider semantics)
        if self.latency_table in rom.tables:
            lat = rom.tables[self.latency_table]
            for cell in lat.cells:
                cell.set_real(cell.real() + latency)
            notes.append(f"Injector: added latency offset {latency:.3f} ms to {self.latency_table!r}")
        else:
            notes.append(f"Injector: latency table {self.latency_table!r} not found")
        return notes
