# SPDX-License-Identifier: GPL-2.0-or-later
# Portions adapted from RomRaider DynoControlPanel.java.
# Copyright (C) 2006-2022 RomRaider.com
# Translated and modified for BimmerStein Tuning Suite on 2026-07-13; see THIRD_PARTY_NOTICES.md.

from __future__ import annotations
from collections.abc import Sequence
import numpy as np
from ecueditor.core.dyno.profile import CarProfile

def air_density(temp_c: float, pressure_pa: float, humidity_pct: float) -> float:
    """Humid-air density (kg/m^3). Ported verbatim from RomRaider calculateEnv (fact base §4.3)."""
    t = temp_c + 273.15                                          # Kelvin
    p_sat = 610.78 * 10.0 ** ((7.5 * t - 2048.625) / (t - 35.85))
    p_v = (humidity_pct / 100.0) * p_sat
    p_d = pressure_pa - p_v
    return p_d / (287.05 * t) + p_v / (461.495 * t)

def tire_diameter_in(p: CarProfile) -> float:
    """Loaded tire diameter (inches). fact base §4.3."""
    return p.wheel_size_in + p.tire_width_mm / 25.4 * p.tire_aspect_pct / 100.0 * 2.0

def rpm_to_mph_factor(p: CarProfile, gear_ratio: float) -> float:
    """rpm-per-mph in the given gear. fact base §4.3."""
    return gear_ratio * p.final_ratio / (tire_diameter_in(p) * 0.002975)

def mph_from_rpm(rpm: float, rpm2mph: float) -> float:
    return rpm / rpm2mph

def rpm_from_speed(vs: float, rpm2mph: float, *, kmh: bool) -> float:
    """rpm from vehicle speed. mph logs: vs*rpm2mph. km/h logs: vs*rpm2mph/1.609344 (fact base §4.3)."""
    return vs * rpm2mph / 1.609344 if kmh else vs * rpm2mph

MPH_PER_MS_TO_G = 45.5486542443     # RomRaider calcHp constant, verbatim (fact base §7.5 quirk)

def accel_g(accel_mph_per_ms: float) -> float:
    return accel_mph_per_ms * MPH_PER_MS_TO_G

def power_watts(accel_g: float, mph: float, mass_kg: float, p: CarProfile, air_den: float) -> float:
    """Wheel power in watts = drive + rolling + aero (fact base §4.3)."""
    ms = 0.44704 * mph                                  # mph -> m/s
    drive = 9.8067 * accel_g * mass_kg * ms
    rolling = ms * p.roll_coeff * mass_kg * 9.8067
    aero = 0.5 * p.drag_coeff * air_den * 0.0929 * p.frontal_area_ft2 * ms ** 3
    return drive + rolling + aero

def torque_from_power(power_hp: float, rpm: float) -> float:
    """lbf-ft (Imperial). fact base §4.3."""
    return power_hp / rpm * 5252.113122

def torque_from_power_metric(power_kw: float, rpm: float) -> float:
    """N-m (Metric). fact base §4.3."""
    return power_kw / rpm * 9549.296748

def smooth_speed(times_ms: Sequence[float], speeds: Sequence[float], order: int) -> np.poly1d:
    """Least-squares polynomial fit of speed vs time (fact base §4.3, jamlab Polyfit equivalent).
    Returns a numpy poly1d in natural time coordinates so p(t) is speed and p.deriv()(t) is acceleration
    (mph per ms). RomRaider fits raw time with order 5-19 (default 9); high orders over wide time ranges are
    numerically stiff — that matches RomRaider and is acceptable for dyno-length pulls."""
    coeffs = np.polyfit(np.asarray(times_ms, dtype=float), np.asarray(speeds, dtype=float), order)
    return np.poly1d(coeffs)
