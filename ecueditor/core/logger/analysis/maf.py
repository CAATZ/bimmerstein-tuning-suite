from __future__ import annotations
from dataclasses import dataclass, field
import numpy as np
from ecueditor.core.logger.analysis.base import AnalysisResult
from ecueditor.core.logger.analysis.channels import ChannelMap
from ecueditor.core.plugins.registry import register

_MAF_ROLES = ("maf_voltage", "afr", "rpm", "maf", "iat", "ect",
              "closed_loop", "throttle", "learning", "correction")

@dataclass
class MafFilters:                       # fact base 3.1 defaults
    afr_min: float = 13.0
    afr_max: float = 16.0
    rpm_min: float = 0.0
    rpm_max: float = 4500.0
    maf_min: float = 0.0
    maf_max: float = 100.0
    mafv_min: float = 1.20
    mafv_max: float = 2.60
    ect_min: float = 70.0
    iat_max: float = 100.0
    dmafv_dt_max: float = 0.1           # V per second
    tip_in_max: float = 50.0            # throttle %/s; inferred default, configurable (fact base 7.4)
    poly_order: int = 6                 # 3..20 (fact base 3.1)

@register("analyses", "maf")
@dataclass
class MafAnalysis:
    channel_map: ChannelMap
    filters: MafFilters = field(default_factory=MafFilters)
    id: str = "maf"
    title: str = "MAF"
    points: list[tuple[float, float]] = field(default_factory=list)
    table_candidates: tuple[str, ...] = ("MAF Sensor Scaling", "MAF Sensor Scaling (16x16)", "MAF")
    _prev_v: float | None = None
    _prev_thr: float | None = None
    _prev_t: float | None = None
    _order: int = 0
    _coeffs: "np.ndarray | None" = None

    @property
    def required_channels(self) -> tuple[str, ...]:
        return self.channel_map.resolve(_MAF_ROLES)

    @property
    def required_roles(self) -> tuple[str, ...]:
        """(Phase 6b) Logical roles accept() reads. The UI resolves them through
        ChannelMap.missing() for the configure-channels affordance (concrete-engine API,
        deliberately not on the AnalysisTab Protocol -- same policy as reset())."""
        return _MAF_ROLES

    def _rate(self, cur: float, prev: float | None, cur_t: float) -> float:
        if prev is None or self._prev_t is None:
            return 0.0
        dt = (cur_t - self._prev_t) / 1000.0     # ms -> s
        return abs(cur - prev) / dt if dt > 0 else 0.0

    def accept(self, sample) -> None:
        r = {role: self.channel_map.roles[role] for role in _MAF_ROLES}
        v = sample.values
        if any(cid not in v for cid in r.values()):
            # (Phase 6b, H2) Sample lacks a required channel (not selected in the logger, or a
            # role rebound mid-session): skip WITHOUT advancing rate baselines -- membership
            # guard precedent set by plugins/afr_target_tab.py. Live wiring feeds EVERY logger
            # sample here via LoggerWindow._dispatch_sample.
            return
        mafv = v[r["maf_voltage"]]; thr = v[r["throttle"]]; t = sample.timestamp_ms
        f = self.filters
        dv = self._rate(mafv, self._prev_v, t)
        dthr = self._rate(thr, self._prev_thr, t)
        ok = (
            bool(v[r["closed_loop"]])
            and f.afr_min <= v[r["afr"]] <= f.afr_max
            and f.rpm_min <= v[r["rpm"]] <= f.rpm_max
            and f.maf_min <= v[r["maf"]] <= f.maf_max
            and f.mafv_min <= mafv <= f.mafv_max
            and v[r["ect"]] >= f.ect_min
            and v[r["iat"]] <= f.iat_max
            and dv <= f.dmafv_dt_max
            and dthr <= f.tip_in_max
        )
        # advance rate baselines regardless of gate outcome
        self._prev_v, self._prev_thr, self._prev_t = mafv, thr, t
        if ok:
            self.points.append((mafv, v[r["learning"]] + v[r["correction"]]))

    def interpolate(self, order: int) -> None:
        if not (3 <= order <= 20):
            raise ValueError(f"polynomial order must be 3..20, got {order}")
        if len(self.points) <= order:
            raise ValueError(f"need > {order} points to fit order {order}; have {len(self.points)}")
        xs = np.array([p[0] for p in self.points], dtype=float)
        ys = np.array([p[1] for p in self.points], dtype=float)
        self._coeffs = np.polyfit(xs, ys, order)     # highest power first
        self._order = order

    def correction_at(self, mafv: float) -> float:
        if self._coeffs is None:
            raise ValueError("call interpolate() first")
        return float(np.polyval(self._coeffs, mafv))

    def reset(self) -> None:
        """Clear accumulated points, the fitted polynomial, and rate-gate baselines -- equivalent
        to a fresh engine. Closes the stale-fit-to-ROM hole: without this, a Reset that only cleared
        points would leave apply_to_rom() free to silently re-apply the OLD fit to a new ROM."""
        self.points.clear()
        self._coeffs = None
        self._order = 0
        self._prev_v = self._prev_thr = self._prev_t = None

    def result(self) -> AnalysisResult:
        corrections: dict[str, float] = {}
        fit_x: tuple[float, ...] = ()
        fit_y: tuple[float, ...] = ()
        if self._coeffs is not None and self.points:
            lo = min(p[0] for p in self.points); hi = max(p[0] for p in self.points)
            grid = np.linspace(lo, hi, 32)
            fit_x = tuple(float(x) for x in grid)
            fit_y = tuple(float(np.polyval(self._coeffs, x)) for x in grid)
            corrections["poly_order"] = float(self._order)
        return AnalysisResult(
            kind="maf", x_label="MAF Sensor Voltage (V)", y_label="Total Fuel Trim (%)",
            points=tuple(self.points), fit_x=fit_x, fit_y=fit_y,
            corrections=corrections, sample_count=len(self.points),
        )

    def _find_table(self, rom):
        for name in self.table_candidates:
            if name in rom.tables:
                return rom.tables[name], name
        return None, None

    @staticmethod
    def _looks_volts(axis) -> bool:
        """True if this breakpoint axis reads as MAF volts (by name or units)."""
        if axis is None:
            return False
        name = (axis.name or "").lower()
        scale = getattr(axis.definition, "scale", None) if axis.definition else None
        units = (scale.units if scale else "").strip().lower()
        return "volt" in name or units in ("v", "volts")

    @staticmethod
    def _axis_span(axis) -> float:
        """Breakpoint span (max real - min real) of an axis sub-table; 0.0 when empty."""
        vals = [c.real() for c in axis.cells]
        return (max(vals) - min(vals)) if vals else 0.0

    def _volt_axis(self, table):
        """Pick the breakpoint axis carrying MAF voltage and its orientation ('x' or 'y').
        Prefers an axis whose name/units read as volts; falls back to X (the 2D static-X curve shape).
        When BOTH axes read as volts, the larger breakpoint span wins: on the real MS41 16x16 "MAF"
        map both axes are volts-named -- X is the fine ~0.3 V intra-row offset ("0 to 5 Volts - Each
        row is ~0.3 Volts wide"), Y the coarse 0-4.7 V sweep ("0 to 5 Volts") -- and the correction
        rides the coarse sweep (an X-first pick would put every breakpoint below mafv_min and
        silently write nothing)."""
        x_volts = self._looks_volts(table.x_axis)
        y_volts = self._looks_volts(table.y_axis)
        if x_volts and y_volts:
            if self._axis_span(table.y_axis) > self._axis_span(table.x_axis):
                return table.y_axis, "y"
            return table.x_axis, "x"
        if x_volts:
            return table.x_axis, "x"
        if y_volts:
            return table.y_axis, "y"
        if table.x_axis is not None:
            return table.x_axis, "x"
        return table.y_axis, "y"

    def apply_to_rom(self, rom) -> list[str]:
        """Mutate the ROM's MAF scaling table in place with the fitted percent correction; apply-once
        semantics -- a repeated call multiplies the correction onto itself again. Returns advisory notes."""
        if self._coeffs is None:
            raise ValueError("interpolate() before apply_to_rom()")
        table, name = self._find_table(rom)
        if table is None:
            return [f"MAF scaling table not found (tried {', '.join(self.table_candidates)})"]
        axis, orient = self._volt_axis(table)
        if axis is None:
            return [f"MAF: {name!r} has no voltage breakpoint axis; nothing written"]
        f = self.filters
        sx, sy = table.shape()                              # (size_x, size_y); cells are X-fastest row-major
        changed = 0
        for i, cell in enumerate(table.cells):
            bp = (i % sx) if orient == "x" else (i // sx)   # flat index -> volt-axis breakpoint index
            volt = axis.cell_at(bp, 0).real()
            if not (f.mafv_min <= volt <= f.mafv_max):
                continue
            pct = self.correction_at(volt)
            cell.set_real(cell.real() * (1.0 + pct / 100.0))
            changed += 1
        return [f"MAF: applied percent corrections to {changed} cell(s) of {name!r}"]
