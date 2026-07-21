from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.interpolate import PchipInterpolator, RegularGridInterpolator

from .model import (
    CurveData,
    MapData,
    MapValidationError,
    collapse_duplicate_curve,
    collapse_duplicate_map,
    validate_axis,
    validate_map_axis,
)

_TREND_BOUNDARIES = {"trend", "global_trend"}


def even_axis(start: float, stop: float, count: int) -> np.ndarray:
    start, stop, count = float(start), float(stop), int(count)
    if not np.isfinite(start) or not np.isfinite(stop):
        raise MapValidationError("Axis limits must be finite.")
    if count < 2:
        raise MapValidationError("An axis needs at least two values.")
    if start == stop:
        raise MapValidationError("Axis limits must be different.")
    result = np.linspace(start, stop, count, dtype=float)
    result[0], result[-1] = start, stop
    return result


@dataclass(frozen=True)
class ResampleResult:
    map_data: MapData
    extrapolated_mask: np.ndarray
    bilinear_reference: MapData
    delta_vs_bilinear: MapData
    method: str
    boundary: str

    @property
    def extrapolated_cells(self) -> int:
        return int(np.count_nonzero(self.extrapolated_mask))


@dataclass(frozen=True)
class CurveResampleResult:
    curve_data: CurveData
    extrapolated_mask: np.ndarray
    linear_reference: CurveData
    delta_vs_linear: CurveData
    method: str
    boundary: str

    @property
    def extrapolated_points(self) -> int:
        return int(np.count_nonzero(self.extrapolated_mask))


def _map_outside(source: MapData, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    xs = max(1.0, float(np.max(np.abs(source.x))))
    ys = max(1.0, float(np.max(np.abs(source.y))))
    xt = np.finfo(float).eps * xs * 16.0
    yt = np.finfo(float).eps * ys * 16.0
    ox = (x < source.x[0] - xt) | (x > source.x[-1] + xt)
    oy = (y < source.y[0] - yt) | (y > source.y[-1] + yt)
    return oy[:, None] | ox[None, :]


def _curve_outside(source: CurveData, x: np.ndarray) -> np.ndarray:
    scale = max(1.0, float(np.max(np.abs(source.x))))
    tolerance = np.finfo(float).eps * scale * 16.0
    return (x < source.x[0] - tolerance) | (x > source.x[-1] + tolerance)


def _bilinear(source: MapData, target_x: np.ndarray, target_y: np.ndarray) -> np.ndarray:
    xi = np.clip(np.searchsorted(source.x, target_x, side="right") - 1, 0, source.x.size - 2)
    yi = np.clip(np.searchsorted(source.y, target_y, side="right") - 1, 0, source.y.size - 2)
    wx = (target_x - source.x[xi]) / (source.x[xi + 1] - source.x[xi])
    wy = (target_y - source.y[yi]) / (source.y[yi + 1] - source.y[yi])
    z00 = source.z[np.ix_(yi, xi)]
    z10 = source.z[np.ix_(yi, xi + 1)]
    z01 = source.z[np.ix_(yi + 1, xi)]
    z11 = source.z[np.ix_(yi + 1, xi + 1)]
    wx2, wy2 = wx[None, :], wy[:, None]
    return (
        z00 * (1.0 - wx2) * (1.0 - wy2)
        + z10 * wx2 * (1.0 - wy2)
        + z01 * (1.0 - wx2) * wy2
        + z11 * wx2 * wy2
    )


def _nearest_axis_indices(axis: np.ndarray, value: float, count: int = 4) -> np.ndarray:
    return np.sort(np.argsort(np.abs(axis - value), kind="stable")[: min(count, axis.size)])


def _least_squares_trend(
    source: MapData,
    target_x: np.ndarray,
    target_y: np.ndarray,
    outside: np.ndarray,
    *,
    whole_table: bool,
) -> np.ndarray:
    boundary_x = np.clip(target_x, source.x[0], source.x[-1])
    boundary_y = np.clip(target_y, source.y[0], source.y[-1])
    values = _bilinear(source, boundary_x, boundary_y)
    for row, column in np.argwhere(outside):
        anchor_x, anchor_y = float(boundary_x[column]), float(boundary_y[row])
        x_indexes = (
            np.arange(source.x.size)
            if whole_table
            else _nearest_axis_indices(source.x, anchor_x)
        )
        y_indexes = (
            np.arange(source.y.size)
            if whole_table
            else _nearest_axis_indices(source.y, anchor_y)
        )
        local_x, local_y = source.x[x_indexes], source.y[y_indexes]
        x_scale = max(float(np.max(np.abs(local_x - anchor_x))), np.finfo(float).eps)
        y_scale = max(float(np.max(np.abs(local_y - anchor_y))), np.finfo(float).eps)
        normalized_x = (local_x - anchor_x) / x_scale
        normalized_y = (local_y - anchor_y) / y_scale
        yy, xx = np.meshgrid(normalized_y, normalized_x, indexing="ij")
        design = np.column_stack((np.ones(xx.size), xx.ravel(), yy.ravel(), (xx * yy).ravel()))
        weights = (
            np.ones(xx.size)
            if whole_table
            else 1.0 / (1.0 + xx.ravel() ** 2 + yy.ravel() ** 2)
        )
        root_weights = np.sqrt(weights)
        coefficients, *_ = np.linalg.lstsq(
            design * root_weights[:, None],
            source.z[np.ix_(y_indexes, x_indexes)].ravel() * root_weights,
            rcond=None,
        )
        dx = (float(target_x[column]) - anchor_x) / x_scale
        dy = (float(target_y[row]) - anchor_y) / y_scale
        values[row, column] += (
            coefficients[1] * dx + coefficients[2] * dy + coefficients[3] * dx * dy
        )
    return values


def _pchip_map(source: MapData, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    if source.x.size < 4 or source.y.size < 4:
        raise MapValidationError(
            "PCHIP needs at least four source values on both X and Y axes."
        )
    interpolator = RegularGridInterpolator(
        (source.y, source.x), source.z, method="pchip", bounds_error=False, fill_value=None
    )
    yy, xx = np.meshgrid(y, x, indexing="ij")
    return interpolator(np.column_stack((yy.ravel(), xx.ravel()))).reshape(yy.shape)


def resample_map(
    source: MapData,
    target_x,
    target_y,
    method: str = "bilinear",
    boundary: str = "hold",
    **legacy,
) -> ResampleResult:
    boundary = legacy.pop("extrapolation", boundary)
    if legacy:
        raise TypeError(f"Unexpected arguments: {', '.join(legacy)}")
    if method not in {"bilinear", "pchip"}:
        raise MapValidationError(f"Unknown interpolation method: {method}.")
    if boundary not in {"hold", "linear", *_TREND_BOUNDARIES, "disallow"}:
        raise MapValidationError(f"Unknown boundary policy: {boundary}.")
    requested_x = validate_map_axis(target_x, "Target X")
    requested_y = validate_map_axis(target_y, "Target Y")
    source_ascending = collapse_duplicate_map(source).map_data.ascending()
    target_x_ascending = requested_x if requested_x[0] < requested_x[-1] else requested_x[::-1]
    target_y_ascending = requested_y if requested_y[0] < requested_y[-1] else requested_y[::-1]
    outside = _map_outside(source_ascending, target_x_ascending, target_y_ascending)
    if boundary == "disallow" and np.any(outside):
        raise MapValidationError(
            f"Target axes create {int(np.count_nonzero(outside))} cells outside the source range."
        )
    if boundary == "hold":
        eval_x = np.clip(target_x_ascending, source_ascending.x[0], source_ascending.x[-1])
        eval_y = np.clip(target_y_ascending, source_ascending.y[0], source_ascending.y[-1])
    else:
        eval_x, eval_y = target_x_ascending, target_y_ascending
    bilinear = _bilinear(source_ascending, eval_x, eval_y)
    if method == "bilinear":
        values = bilinear.copy()
    else:
        pchip_x = np.clip(target_x_ascending, source_ascending.x[0], source_ascending.x[-1])
        pchip_y = np.clip(target_y_ascending, source_ascending.y[0], source_ascending.y[-1])
        values = _pchip_map(source_ascending, pchip_x, pchip_y)
        if boundary == "linear":
            values[outside] = bilinear[outside]
    if boundary in _TREND_BOUNDARIES and np.any(outside):
        trend = _least_squares_trend(
            source_ascending,
            eval_x,
            eval_y,
            outside,
            whole_table=boundary == "global_trend",
        )
        values[outside] = trend[outside]
    if requested_y[0] > requested_y[-1]:
        values, bilinear, outside = values[::-1, :], bilinear[::-1, :], outside[::-1, :]
    if requested_x[0] > requested_x[-1]:
        values, bilinear, outside = values[:, ::-1], bilinear[:, ::-1], outside[:, ::-1]
    result = MapData(requested_x, requested_y, values, f"{source.name} — resampled")
    reference = MapData(requested_x, requested_y, bilinear, "Bilinear reference")
    return ResampleResult(
        result,
        outside,
        reference,
        MapData(requested_x, requested_y, values - bilinear, "Difference vs bilinear"),
        method,
        boundary,
    )


def _linear_curve(source: CurveData, target: np.ndarray) -> np.ndarray:
    indexes = np.clip(
        np.searchsorted(source.x, target, side="right") - 1, 0, source.size - 2
    )
    weights = (target - source.x[indexes]) / (source.x[indexes + 1] - source.x[indexes])
    return source.values[indexes] * (1.0 - weights) + source.values[indexes + 1] * weights


def resample_curve(
    source: CurveData,
    target_x,
    method: str = "linear",
    boundary: str = "hold",
    **legacy,
) -> CurveResampleResult:
    boundary = legacy.pop("extrapolation", boundary)
    if legacy:
        raise TypeError(f"Unexpected arguments: {', '.join(legacy)}")
    if method not in {"linear", "pchip"}:
        raise MapValidationError(f"Unknown curve interpolation method: {method}.")
    if boundary not in {"hold", "linear", "disallow"}:
        raise MapValidationError(f"Unknown boundary policy: {boundary}.")
    requested = validate_axis(target_x, "Target X")
    source_ascending = collapse_duplicate_curve(source).curve_data.ascending()
    ascending_target = requested if requested[0] < requested[-1] else requested[::-1]
    outside = _curve_outside(source_ascending, ascending_target)
    if boundary == "disallow" and np.any(outside):
        raise MapValidationError(
            f"Target axis creates {int(np.count_nonzero(outside))} points outside the source range."
        )
    if boundary == "hold":
        evaluation = np.clip(ascending_target, source_ascending.x[0], source_ascending.x[-1])
    else:
        evaluation = ascending_target
    linear = _linear_curve(source_ascending, evaluation)
    if method == "linear":
        values = linear.copy()
    else:
        if source_ascending.size < 4:
            raise MapValidationError("PCHIP needs at least four source values.")
        inside_x = np.clip(
            ascending_target, source_ascending.x[0], source_ascending.x[-1]
        )
        values = np.asarray(
            PchipInterpolator(source_ascending.x, source_ascending.values, extrapolate=False)(
                inside_x
            ),
            dtype=float,
        )
        if boundary == "linear":
            values[outside] = linear[outside]
    if requested[0] > requested[-1]:
        values, linear, outside = values[::-1], linear[::-1], outside[::-1]
    result = CurveData(requested, values, f"{source.name} — resampled")
    reference = CurveData(requested, linear, "Linear reference")
    return CurveResampleResult(
        result,
        outside,
        reference,
        CurveData(requested, values - linear, "Difference vs linear"),
        method,
        boundary,
    )
