from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .model import MapData, MapValidationError


@dataclass(frozen=True)
class SafetyReport:
    changed_cells: int
    extrapolated_cells: int
    maximum_absolute_change: float
    mean_absolute_change: float
    rms_change: float
    source_minimum: float
    source_maximum: float
    result_minimum: float
    result_maximum: float
    below_source_range: int
    above_source_range: int
    sharp_edges: int
    reference_label: str

    def to_text(self) -> str:
        return "\n".join(
            [
                "Map Studio safety report",
                f"Comparison reference: {self.reference_label}",
                f"Changed cells: {self.changed_cells}",
                f"Extrapolated cells: {self.extrapolated_cells}",
                f"Maximum absolute change: {self.maximum_absolute_change:.8g}",
                f"Mean absolute change: {self.mean_absolute_change:.8g}",
                f"RMS change: {self.rms_change:.8g}",
                f"Source range: {self.source_minimum:.8g} to {self.source_maximum:.8g}",
                f"Result range: {self.result_minimum:.8g} to {self.result_maximum:.8g}",
                f"Below source range: {self.below_source_range}",
                f"Above source range: {self.above_source_range}",
                f"Unusually sharp adjacent edges: {self.sharp_edges}",
                "This is a numerical review aid, not an engine-safety determination.",
            ]
        )


def _sharp_edges(values: np.ndarray) -> int:
    differences = np.concatenate(
        (np.abs(np.diff(values, axis=0)).ravel(), np.abs(np.diff(values, axis=1)).ravel())
    )
    positive = differences[differences > np.finfo(float).eps]
    if positive.size < 4:
        return 0
    median = float(np.median(positive))
    mad = float(np.median(np.abs(positive - median)))
    return int(np.count_nonzero(differences > median + 6.0 * max(mad, np.finfo(float).eps)))


def build_safety_report(
    source: MapData,
    result: MapData,
    reference: MapData,
    extrapolated_mask: np.ndarray | None = None,
    reference_label: str = "bilinear result on the target grid",
) -> SafetyReport:
    if result.z.shape != reference.z.shape:
        raise MapValidationError("Safety-report result and reference dimensions do not match.")
    if not (
        np.allclose(result.x, reference.x, rtol=1e-10, atol=1e-12)
        and np.allclose(result.y, reference.y, rtol=1e-10, atol=1e-12)
    ):
        raise MapValidationError("Safety-report result and reference target axes do not match.")
    delta = result.z - reference.z
    changed = ~np.isclose(delta, 0.0, atol=1e-12, rtol=1e-10)
    source_min, source_max = source.value_range
    result_min, result_max = result.value_range
    tolerance = max(1.0, abs(source_min), abs(source_max)) * 1e-12
    outside = (
        np.zeros_like(result.z, dtype=bool)
        if extrapolated_mask is None
        else np.asarray(extrapolated_mask, dtype=bool)
    )
    if outside.shape != result.z.shape:
        raise MapValidationError("Safety-report extrapolation mask dimensions do not match.")
    return SafetyReport(
        int(np.count_nonzero(changed)),
        int(np.count_nonzero(outside)),
        float(np.max(np.abs(delta))),
        float(np.mean(np.abs(delta))),
        float(np.sqrt(np.mean(np.square(delta)))),
        source_min,
        source_max,
        result_min,
        result_max,
        int(np.count_nonzero(result.z < source_min - tolerance)),
        int(np.count_nonzero(result.z > source_max + tolerance)),
        _sharp_edges(result.z),
        reference_label,
    )
