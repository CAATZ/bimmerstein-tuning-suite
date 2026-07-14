from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from ecueditor.core.dyno import units
from ecueditor.core.errors import ECUEditorError
from ecueditor.core.logger.engine import Sample
from ecueditor.core.dyno.profile import CarProfile
from ecueditor.core.dyno import physics

@dataclass(frozen=True)
class DynoEnv:
    """Ambient environment for a pull. Defaults are RomRaider's (fact base §4.6)."""
    air_temp_c: float = 20.0            # 68 F
    humidity_pct: float = 60.0
    elevation_ft: float = 200.0
    delta_mass_lb: float = 225.0        # occupants + accessories
    pressure_pa_override: float | None = None   # set from ECU ATM sample; else derived from elevation

    def pressure_pa(self) -> float:
        if self.pressure_pa_override is not None:
            return self.pressure_pa_override
        # calculateEnv barometric formula (fact base §4.3)
        alt_m = units.ft_to_m(self.elevation_ft)
        return 101325.0 * (1.0 - 22.5577e-6 * alt_m) ** 5.25578

    def mass_kg(self, car_mass_lb: float) -> float:
        return units.lb_to_kg(car_mass_lb + self.delta_mass_lb)

# Standard logger channel ids the dyno binds (fact base §4.1).
ENGINE_SPEED = "P8"
VEHICLE_SPEED = "P9"
IAT = "P11"
THROTTLE_ANGLE = "P13"
THROTTLE_VOLTS = "P19"
ATM = "P24"

@dataclass
class DynoRun:
    rpm: list[float]
    power_hp: list[float]
    torque_lbft: list[float]
    max_power: tuple[float, float]      # (hp, rpm) or (kW, rpm) if Metric
    max_torque: tuple[float, float]     # (lbf-ft, rpm) or (N-m, rpm) if Metric
    units: str = "Imperial"             # (Phase 6b) which unit family the fields hold;
                                        # stamped by finish(), read back by load_run()

    def save(self, path: str | Path, *, units: str, smoothing_order: int,
             extra_stats: tuple[float, ...] = ()) -> None:
        """Serialize to RomRaider's tab-header + `rpm,power,torque` CSV form (fact base §4.6).

        Tab-separated header: units, smoothing_order, maxPower, maxPower_rpm, maxTorque,
        maxTorque_rpm, *extra_stats. `units` is "Imperial" (hp/lbf-ft) or "Metric" (kW/N-m).
        `extra_stats` is the reserved fToE/sToE/tToS/AUC slot (deferred this phase, so empty).
        """
        p = Path(path)
        power, power_rpm = self.max_power
        torque, torque_rpm = self.max_torque
        header: list[str] = [
            units, str(smoothing_order),
            f"{power}", f"{power_rpm}", f"{torque}", f"{torque_rpm}",
            *[f"{s}" for s in extra_stats],
        ]
        lines: list[str] = ["\t".join(header)]
        lines += [f"{r},{pw},{tq}" for r, pw, tq in zip(self.rpm, self.power_hp, self.torque_lbft)]
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")

class DynoCapture:
    """WOT-pull state machine. Manual mode: reads engine speed (P8) + throttle (P13, %)."""
    def __init__(self, profile: CarProfile, gear_ratio: float, env: DynoEnv, *, tps_min: float) -> None:
        self.profile = profile
        self.gear_ratio = gear_ratio
        self.env = env
        self.tps_min = tps_min
        self.rpm2mph = physics.rpm_to_mph_factor(profile, gear_ratio)
        self._t: list[float] = []
        self._mph: list[float] = []
        self._wot = False
        self._stopped = False

    def accept(self, sample: Sample) -> None:
        if self._stopped:
            return
        throttle = sample.values.get(THROTTLE_ANGLE)
        rpm = sample.values.get(ENGINE_SPEED)
        if throttle is None or rpm is None:
            return
        if throttle > self.tps_min:
            self._wot = True
            self._t.append(sample.timestamp_ms)
            self._mph.append(physics.mph_from_rpm(rpm, self.rpm2mph))
        elif self._wot:
            self._stopped = True                         # throttle lift auto-stops the run

    @property
    def is_stopped(self) -> bool:
        """(Phase 6b) True once the pull auto-stopped on throttle lift -- the run boundary the
        main window polls after each accept() to uncheck Record and finish() the run."""
        return self._stopped

    def finish(self, *, smoothing_order: int, rpm_range: tuple[float, float],
               metric: bool = False) -> DynoRun:
        if len(self._t) < smoothing_order + 1:
            raise ECUEditorError("not enough WOT samples to fit a dyno curve")
        poly = physics.smooth_speed(self._t, self._mph, smoothing_order)
        dpoly = poly.deriv()
        mass_kg = self.env.mass_kg(self.profile.car_mass_lb)
        air_den = physics.air_density(self.env.air_temp_c, self.env.pressure_pa(), self.env.humidity_pct)
        lo, hi = rpm_range
        rpm_out: list[float] = []
        power_out: list[float] = []       # hp (Imperial) or kW (Metric)
        torque_out: list[float] = []      # lbf-ft (Imperial) or N-m (Metric)
        t = self._t[0]
        end = self._t[-1]
        while t <= end:                                  # evaluate every 10 ms (fact base §4.3)
            mph = float(poly(t))
            rpm = mph * self.rpm2mph
            if lo <= rpm <= hi:
                g = physics.accel_g(float(dpoly(t)))     # accel is mph per ms
                watts = physics.power_watts(g, mph, mass_kg, self.profile, air_den)
                if metric:
                    power = watts / 1000.0                               # kW
                    torque = physics.torque_from_power_metric(power, rpm)  # N-m
                else:
                    power = watts / 745.7                                # hp
                    torque = physics.torque_from_power(power, rpm)         # lbf-ft
                rpm_out.append(rpm)
                power_out.append(power)
                torque_out.append(torque)
            t += 10.0
        if not power_out:
            raise ECUEditorError("no evaluated points fell inside the RPM range")
        pk_i = max(range(len(power_out)), key=lambda i: power_out[i])
        tq_i = max(range(len(torque_out)), key=lambda i: torque_out[i])
        return DynoRun(rpm=rpm_out, power_hp=power_out, torque_lbft=torque_out,
                       max_power=(power_out[pk_i], rpm_out[pk_i]),
                       max_torque=(torque_out[tq_i], rpm_out[tq_i]),
                       units="Metric" if metric else "Imperial")

def load_run(path: str | Path) -> DynoRun:
    """Read back a run written by `DynoRun.save` (fact base §4.6)."""
    text = Path(path).read_text(encoding="utf-8").splitlines()
    if not text:
        raise ECUEditorError(f"empty dyno run file: {path}")
    head = text[0].split("\t")
    if len(head) < 6:
        raise ECUEditorError(f"malformed dyno run header in {path}: {text[0]!r}")
    units = head[0].strip() or "Imperial"    # (Phase 6b) head[0] was parsed but dropped -- H6
    max_power = (float(head[2]), float(head[3]))
    max_torque = (float(head[4]), float(head[5]))
    rpm: list[float] = []
    power: list[float] = []
    torque: list[float] = []
    for line in text[1:]:
        if not line.strip():
            continue
        r, pw, tq = line.split(",")
        rpm.append(float(r))
        power.append(float(pw))
        torque.append(float(tq))
    return DynoRun(rpm=rpm, power_hp=power, torque_lbft=torque,
                   max_power=max_power, max_torque=max_torque, units=units)
