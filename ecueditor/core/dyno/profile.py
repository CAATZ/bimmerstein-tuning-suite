from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from xml.etree import ElementTree as ET
from ecueditor.core.errors import ECUEditorError

@dataclass(frozen=True)
class CarProfile:
    name: str
    car_mass_lb: float
    final_ratio: float
    roll_coeff: float
    drag_coeff: float
    frontal_area_ft2: float
    transmission: Literal["manual", "automatic"]
    gear_ratios: tuple[float, ...]
    tire_width_mm: float
    tire_aspect_pct: float
    wheel_size_in: float

def _norm_transmission(text: str | None) -> Literal["manual", "automatic"]:
    t = (text or "").strip().lower()
    if t in ("automatic", "at", "auto", "a"):
        return "automatic"
    return "manual"          # "manual"/"mt"/"m"/anything else -> manual (fact base §4.4)

def _f(node: ET.Element, tag: str) -> float:
    child = node.find(tag)
    if child is None or child.text is None or not child.text.strip():
        raise ECUEditorError(f"cars_def <car> missing <{tag}>")
    return float(child.text.strip())

def load_car_profiles(path: str | Path) -> list[CarProfile]:
    try:
        root = ET.parse(Path(path)).getroot()          # cars_def.dtd is SYSTEM-external; ET ignores it
    except ET.ParseError as exc:
        raise ECUEditorError(f"cannot parse cars_def {path}: {exc}") from exc
    cars: list[CarProfile] = []
    for car in root.iter("car"):
        gears: list[float] = []
        for i in range(1, 9):                            # gearratio1..gearratio8 (only present ones)
            g = car.find(f"gearratio{i}")
            if g is not None and g.text and g.text.strip():
                gears.append(float(g.text.strip()))
        cars.append(CarProfile(
            name=(car.findtext("type") or "").strip(),
            car_mass_lb=_f(car, "carmass"),
            final_ratio=_f(car, "finalratio"),
            roll_coeff=_f(car, "rollcoeff"),
            drag_coeff=_f(car, "dragcoeff"),
            frontal_area_ft2=_f(car, "frontalarea"),
            transmission=_norm_transmission(car.findtext("transmission")),
            gear_ratios=tuple(gears),
            tire_width_mm=_f(car, "tirewidth"),
            tire_aspect_pct=_f(car, "tireaspect"),
            wheel_size_in=_f(car, "wheelsize"),
        ))
    return cars
