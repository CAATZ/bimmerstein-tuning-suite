from __future__ import annotations

from dataclasses import dataclass
import warnings

import numpy as np
from scipy.sparse import lil_matrix
from scipy.sparse.linalg import MatrixRankWarning, spsolve

from .model import (
    CurveData,
    MapData,
    MapValidationError,
    collapse_duplicate_curve,
    collapse_duplicate_map,
)


@dataclass(frozen=True)
class AnomalyResult:
    mask: np.ndarray
    predicted: np.ndarray
    residual: np.ndarray
    threshold: float

    @property
    def count(self) -> int:
        return int(np.count_nonzero(self.mask))


@dataclass(frozen=True)
class CurveAnomalyResult:
    mask: np.ndarray
    predicted: np.ndarray
    residual: np.ndarray
    threshold: float

    @property
    def count(self) -> int:
        return int(np.count_nonzero(self.mask))


def _axis_neighbor_weights(axis: np.ndarray, index: int) -> list[tuple[int, float]]:
    if index == 0:
        distance = abs(float(axis[1] - axis[0]))
        return [(1, 1.0 / distance**2)]
    if index == axis.size - 1:
        distance = abs(float(axis[-1] - axis[-2]))
        return [(axis.size - 2, 1.0 / distance**2)]
    left = abs(float(axis[index] - axis[index - 1]))
    right = abs(float(axis[index + 1] - axis[index]))
    total = left + right
    return [(index - 1, 2.0 / (left * total)), (index + 1, 2.0 / (right * total))]


def repair_selected_region(map_data: MapData, selected_mask: np.ndarray) -> MapData:
    mask = np.asarray(selected_mask, dtype=bool)
    if mask.shape != map_data.z.shape:
        raise MapValidationError("The smoothing selection does not match the source table.")
    collapsed = collapse_duplicate_map(map_data)
    if collapsed.has_duplicates:
        repaired = repair_selected_region(collapsed.map_data, collapsed.collapse_mask(mask))
        return MapData(
            map_data.x,
            map_data.y,
            collapsed.expand_values(repaired.z),
            f"{map_data.name} — repaired",
        )
    selected_count = int(np.count_nonzero(mask))
    if not selected_count:
        raise MapValidationError("Select at least one source cell to repair.")
    if selected_count == mask.size:
        raise MapValidationError(
            "Selection repair needs unchanged surrounding cells."
        )
    coordinates = [(int(value[0]), int(value[1])) for value in np.argwhere(mask)]
    lookup = {coordinate: index for index, coordinate in enumerate(coordinates)}
    matrix = lil_matrix((selected_count, selected_count), dtype=float)
    rhs = np.zeros(selected_count, dtype=float)
    for equation, (row, column) in enumerate(coordinates):
        neighbors = [
            (row, neighbor, weight)
            for neighbor, weight in _axis_neighbor_weights(map_data.x, column)
        ]
        neighbors += [
            (neighbor, column, weight)
            for neighbor, weight in _axis_neighbor_weights(map_data.y, row)
        ]
        matrix[equation, equation] = sum(weight for _, _, weight in neighbors)
        for neighbor_row, neighbor_column, weight in neighbors:
            coordinate = (neighbor_row, neighbor_column)
            if mask[coordinate]:
                matrix[equation, lookup[coordinate]] -= weight
            else:
                rhs[equation] += weight * map_data.z[coordinate]
    with warnings.catch_warnings():
        warnings.simplefilter("error", MatrixRankWarning)
        try:
            values = spsolve(matrix.tocsr(), rhs)
        except (MatrixRankWarning, RuntimeError) as exc:
            raise MapValidationError(
                "The selected region could not be reconstructed from its surroundings."
            ) from exc
    if not np.all(np.isfinite(values)):
        raise MapValidationError(
            "The selected region needs more unchanged surrounding reference cells."
        )
    repaired = map_data.z.copy()
    for coordinate, value in zip(coordinates, values):
        repaired[coordinate] = value
    return MapData(map_data.x, map_data.y, repaired, f"{map_data.name} — repaired")


def _local_plane_value(
    map_data: MapData, row: int, column: int, *, include_center: bool
) -> float:
    points: list[tuple[float, float, float]] = []
    for neighbor_row in range(max(0, row - 1), min(map_data.rows, row + 2)):
        for neighbor_column in range(max(0, column - 1), min(map_data.columns, column + 2)):
            if not include_center and (neighbor_row, neighbor_column) == (row, column):
                continue
            points.append(
                (
                    float(map_data.x[neighbor_column] - map_data.x[column]),
                    float(map_data.y[neighbor_row] - map_data.y[row]),
                    float(map_data.z[neighbor_row, neighbor_column]),
                )
            )
    if len(points) < 3:
        return float(map_data.z[row, column])
    coordinates = np.asarray([(x, y) for x, y, _ in points], dtype=float)
    values = np.asarray([value for _, _, value in points], dtype=float)
    x_scale = max(float(np.max(np.abs(coordinates[:, 0]))), np.finfo(float).eps)
    y_scale = max(float(np.max(np.abs(coordinates[:, 1]))), np.finfo(float).eps)
    nx, ny = coordinates[:, 0] / x_scale, coordinates[:, 1] / y_scale
    design = np.column_stack((np.ones(len(points)), nx, ny))
    roots = np.sqrt(1.0 / (1.0 + nx**2 + ny**2))
    coefficients, *_ = np.linalg.lstsq(
        design * roots[:, None], values * roots, rcond=None
    )
    return float(coefficients[0])


def _anomaly_threshold(samples: np.ndarray, value_range: float) -> float:
    range_floor = 0.05 * max(float(value_range), np.finfo(float).eps)
    # A local spike affects neighboring residuals too. Median/MAD needs at least
    # seven samples before those three contaminated values remain a minority.
    if samples.size < 7:
        return range_floor
    median = float(np.median(samples))
    mad = float(np.median(np.abs(samples - median)))
    return max(median + 6.0 * 1.4826 * mad, range_floor)


def smooth_entire_table(map_data: MapData) -> MapData:
    collapsed = collapse_duplicate_map(map_data)
    if collapsed.has_duplicates:
        smoothed = smooth_entire_table(collapsed.map_data)
        return MapData(
            map_data.x,
            map_data.y,
            collapsed.expand_values(smoothed.z),
            f"{map_data.name} — smoothed",
        )
    values = np.empty_like(map_data.z, dtype=float)
    for row in range(map_data.rows):
        for column in range(map_data.columns):
            values[row, column] = _local_plane_value(
                map_data, row, column, include_center=True
            )
    return MapData(map_data.x, map_data.y, values, f"{map_data.name} — smoothed")


def detect_anomalies(map_data: MapData) -> AnomalyResult:
    collapsed = collapse_duplicate_map(map_data)
    if collapsed.has_duplicates:
        result = detect_anomalies(collapsed.map_data)
        return AnomalyResult(
            collapsed.expand_values(result.mask).astype(bool),
            collapsed.expand_values(result.predicted),
            collapsed.expand_values(result.residual),
            result.threshold,
        )
    predicted = map_data.z.copy()
    residual = np.zeros_like(map_data.z, dtype=float)
    if map_data.rows < 3 or map_data.columns < 3:
        return AnomalyResult(np.zeros_like(map_data.z, dtype=bool), predicted, residual, 0.0)
    samples: list[float] = []
    for row in range(1, map_data.rows - 1):
        for column in range(1, map_data.columns - 1):
            predicted[row, column] = _local_plane_value(
                map_data, row, column, include_center=False
            )
            residual[row, column] = abs(map_data.z[row, column] - predicted[row, column])
            samples.append(float(residual[row, column]))
    sample_array = np.asarray(samples, dtype=float)
    threshold = _anomaly_threshold(sample_array, float(np.ptp(map_data.z)))
    mask = residual > threshold
    mask[[0, -1], :] = False
    mask[:, [0, -1]] = False
    return AnomalyResult(mask, predicted, residual, threshold)


def _curve_segments(mask: np.ndarray) -> list[tuple[int, int]]:
    segments: list[tuple[int, int]] = []
    index = 0
    while index < mask.size:
        if not mask[index]:
            index += 1
            continue
        start = index
        while index + 1 < mask.size and mask[index + 1]:
            index += 1
        segments.append((start, index))
        index += 1
    return segments


def repair_curve_selection(curve: CurveData, selected_mask: np.ndarray) -> CurveData:
    mask = np.asarray(selected_mask, dtype=bool).reshape(-1)
    if mask.size != curve.size:
        raise MapValidationError("The smoothing selection does not match the curve.")
    collapsed = collapse_duplicate_curve(curve)
    if collapsed.has_duplicates:
        repaired = repair_curve_selection(collapsed.curve_data, collapsed.collapse_mask(mask))
        return CurveData(
            curve.x,
            collapsed.expand_values(repaired.values),
            f"{curve.name} — repaired",
        )
    if not np.any(mask):
        raise MapValidationError("Select at least one curve value to repair.")
    if np.all(mask):
        raise MapValidationError("Selection repair needs unchanged reference points.")
    values = curve.values.copy()
    fixed = np.flatnonzero(~mask)
    for start, stop in _curve_segments(mask):
        left = start - 1 if start else None
        right = stop + 1 if stop + 1 < curve.size else None
        if left is not None and right is not None:
            first, second = left, right
        elif left is None and fixed.size >= 2:
            first, second = int(fixed[0]), int(fixed[1])
        elif right is None and fixed.size >= 2:
            first, second = int(fixed[-2]), int(fixed[-1])
        else:
            raise MapValidationError("The edge selection needs two unchanged reference points.")
        weights = (curve.x[start : stop + 1] - curve.x[first]) / (
            curve.x[second] - curve.x[first]
        )
        values[start : stop + 1] = curve.values[first] + weights * (
            curve.values[second] - curve.values[first]
        )
    return CurveData(curve.x, values, f"{curve.name} — repaired")


def _local_line(curve: CurveData, index: int, include_center: bool) -> float:
    indexes = np.arange(max(0, index - 1), min(curve.size, index + 2))
    if not include_center:
        indexes = indexes[indexes != index]
    if indexes.size < 2:
        return float(curve.values[index])
    x = curve.x[indexes] - curve.x[index]
    scale = max(float(np.max(np.abs(x))), np.finfo(float).eps)
    design = np.column_stack((np.ones(indexes.size), x / scale))
    coefficients, *_ = np.linalg.lstsq(design, curve.values[indexes], rcond=None)
    return float(coefficients[0])


def smooth_entire_curve(curve: CurveData) -> CurveData:
    collapsed = collapse_duplicate_curve(curve)
    if collapsed.has_duplicates:
        smoothed = smooth_entire_curve(collapsed.curve_data)
        return CurveData(
            curve.x,
            collapsed.expand_values(smoothed.values),
            f"{curve.name} — smoothed",
        )
    values = np.asarray([_local_line(curve, index, True) for index in range(curve.size)])
    return CurveData(curve.x, values, f"{curve.name} — smoothed")


def detect_curve_anomalies(curve: CurveData) -> CurveAnomalyResult:
    collapsed = collapse_duplicate_curve(curve)
    if collapsed.has_duplicates:
        result = detect_curve_anomalies(collapsed.curve_data)
        return CurveAnomalyResult(
            collapsed.expand_values(result.mask).astype(bool),
            collapsed.expand_values(result.predicted),
            collapsed.expand_values(result.residual),
            result.threshold,
        )
    predicted = curve.values.copy()
    residual = np.zeros(curve.size, dtype=float)
    if curve.size < 3:
        return CurveAnomalyResult(np.zeros(curve.size, dtype=bool), predicted, residual, 0.0)
    for index in range(1, curve.size - 1):
        predicted[index] = _local_line(curve, index, False)
        residual[index] = abs(curve.values[index] - predicted[index])
    interior = residual[1:-1]
    threshold = _anomaly_threshold(interior, float(np.ptp(curve.values)))
    mask = residual > threshold
    mask[[0, -1]] = False
    return CurveAnomalyResult(mask, predicted, residual, threshold)
