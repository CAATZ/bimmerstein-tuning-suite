from __future__ import annotations

from dataclasses import dataclass

import numpy as np


class MapValidationError(ValueError):
    """Raised when a calibration grid cannot be transformed safely."""


def validate_axis(values, name: str, minimum_points: int = 2) -> np.ndarray:
    axis = np.asarray(values, dtype=float).reshape(-1)
    if axis.size < minimum_points:
        raise MapValidationError(
            f"{name} axis needs at least {minimum_points} values; got {axis.size}."
        )
    if not np.all(np.isfinite(axis)):
        raise MapValidationError(f"{name} axis contains a blank or non-finite value.")
    steps = np.diff(axis)
    if np.any(steps == 0):
        raise MapValidationError(f"{name} axis contains duplicate values.")
    if not (np.all(steps > 0) or np.all(steps < 0)):
        raise MapValidationError(
            f"{name} axis must be strictly ascending or strictly descending."
        )
    return axis


def validate_map_axis(values, name: str, minimum_points: int = 2) -> np.ndarray:
    """Validate an ordered map axis while allowing repeated padding bins."""
    axis = np.asarray(values, dtype=float).reshape(-1)
    if axis.size < minimum_points:
        raise MapValidationError(
            f"{name} axis needs at least {minimum_points} values; got {axis.size}."
        )
    if not np.all(np.isfinite(axis)):
        raise MapValidationError(f"{name} axis contains a blank or non-finite value.")
    steps = np.diff(axis)
    nonzero = steps[steps != 0]
    if nonzero.size == 0 or np.unique(axis).size < minimum_points:
        raise MapValidationError(f"{name} axis needs at least {minimum_points} distinct values.")
    if not (np.all(nonzero > 0) or np.all(nonzero < 0)):
        raise MapValidationError(
            f"{name} axis must be ascending or descending; repeated values may only pad "
            "an otherwise ordered axis."
        )
    return axis


@dataclass(frozen=True)
class MapData:
    x: np.ndarray
    y: np.ndarray
    z: np.ndarray
    name: str = "Untitled map"

    def __post_init__(self) -> None:
        x = validate_map_axis(self.x, "X")
        y = validate_map_axis(self.y, "Y")
        z = np.asarray(self.z, dtype=float)
        expected = (y.size, x.size)
        if z.ndim != 2 or z.shape != expected:
            raise MapValidationError(
                f"Z grid shape must be {expected[0]} rows by {expected[1]} columns; "
                f"got {tuple(z.shape)}."
            )
        if not np.all(np.isfinite(z)):
            raise MapValidationError("Z grid contains a blank or non-finite value.")
        object.__setattr__(self, "x", x.copy())
        object.__setattr__(self, "y", y.copy())
        object.__setattr__(self, "z", z.copy())

    @property
    def rows(self) -> int:
        return int(self.y.size)

    @property
    def columns(self) -> int:
        return int(self.x.size)

    @property
    def value_range(self) -> tuple[float, float]:
        return float(np.min(self.z)), float(np.max(self.z))

    def ascending(self) -> MapData:
        x, y, z = self.x.copy(), self.y.copy(), self.z.copy()
        if x[0] > x[-1]:
            x, z = x[::-1], z[:, ::-1]
        if y[0] > y[-1]:
            y, z = y[::-1], z[::-1, :]
        return MapData(x, y, z, self.name)


@dataclass(frozen=True)
class CurveData:
    x: np.ndarray
    values: np.ndarray
    name: str = "Untitled curve"

    def __post_init__(self) -> None:
        x = validate_map_axis(self.x, "X")
        values = np.asarray(self.values, dtype=float).reshape(-1)
        if values.size != x.size:
            raise MapValidationError(
                f"Curve needs one value for each X breakpoint; got {values.size} values "
                f"for {x.size} breakpoints."
            )
        if not np.all(np.isfinite(values)):
            raise MapValidationError("Curve contains a blank or non-finite value.")
        object.__setattr__(self, "x", x.copy())
        object.__setattr__(self, "values", values.copy())

    @property
    def size(self) -> int:
        return int(self.x.size)

    @property
    def value_range(self) -> tuple[float, float]:
        return float(np.min(self.values)), float(np.max(self.values))

    def ascending(self) -> CurveData:
        if self.x[0] < self.x[-1]:
            return CurveData(self.x, self.values, self.name)
        return CurveData(self.x[::-1], self.values[::-1], self.name)


@dataclass(frozen=True)
class CollapsedCurve:
    curve_data: CurveData
    x_inverse: np.ndarray
    removed_x: int

    @property
    def has_duplicates(self) -> bool:
        return bool(self.removed_x)

    def collapse_mask(self, mask: np.ndarray) -> np.ndarray:
        source = np.asarray(mask, dtype=bool).reshape(-1)
        if source.size != self.x_inverse.size:
            raise MapValidationError("Mask size must match the padded curve.")
        collapsed = np.zeros(self.curve_data.size, dtype=bool)
        for index in np.flatnonzero(source):
            collapsed[self.x_inverse[index]] = True
        return collapsed

    def expand_values(self, values: np.ndarray) -> np.ndarray:
        source = np.asarray(values).reshape(-1)
        if source.size != self.curve_data.size:
            raise MapValidationError("Collapsed values do not match the unique-coordinate curve.")
        return source[self.x_inverse].copy()

    def synchronize_values(self, proposed: np.ndarray) -> np.ndarray:
        """Mirror one physical edit across its logical duplicate-coordinate group."""
        source = np.asarray(proposed, dtype=float)
        expected = (self.x_inverse.size,)
        if source.shape != expected:
            raise MapValidationError(f"Proposed curve size must be {expected[0]} values.")
        if not np.all(np.isfinite(source)):
            raise MapValidationError("Proposed curve contains a non-finite value.")
        original = self.expand_values(self.curve_data.values)
        output = source.copy()
        changed = source != original
        for unique_index in range(self.curve_data.size):
            indexes = np.flatnonzero(self.x_inverse == unique_index)
            candidates = source[indexes][changed[indexes]]
            if not candidates.size:
                continue
            if not np.all(candidates == candidates[0]):
                raise MapValidationError(
                    "Repeated-axis padding points represent one logical value; "
                    "the proposed values conflict."
                )
            output[indexes] = candidates[0]
        return output


@dataclass(frozen=True)
class CollapsedMap:
    map_data: MapData
    x_inverse: np.ndarray
    y_inverse: np.ndarray
    removed_x: int
    removed_y: int

    @property
    def has_duplicates(self) -> bool:
        return bool(self.removed_x or self.removed_y)

    def collapse_mask(self, mask: np.ndarray) -> np.ndarray:
        source = np.asarray(mask, dtype=bool)
        expected = (self.y_inverse.size, self.x_inverse.size)
        if source.shape != expected:
            raise MapValidationError(
                f"Mask shape must be {expected[0]} rows by {expected[1]} columns."
            )
        collapsed = np.zeros(self.map_data.z.shape, dtype=bool)
        for row, column in np.argwhere(source):
            collapsed[self.y_inverse[row], self.x_inverse[column]] = True
        return collapsed

    def expand_values(self, values: np.ndarray) -> np.ndarray:
        source = np.asarray(values)
        if source.shape != self.map_data.z.shape:
            raise MapValidationError("Collapsed values do not match the unique-coordinate map.")
        return source[np.ix_(self.y_inverse, self.x_inverse)].copy()

    def synchronize_values(self, proposed: np.ndarray) -> np.ndarray:
        """Mirror one physical edit across its logical duplicate-coordinate group."""
        source = np.asarray(proposed, dtype=float)
        expected = (self.y_inverse.size, self.x_inverse.size)
        if source.shape != expected:
            raise MapValidationError(
                f"Proposed map shape must be {expected[0]} rows by {expected[1]} columns."
            )
        if not np.all(np.isfinite(source)):
            raise MapValidationError("Proposed map contains a non-finite value.")
        original = self.expand_values(self.map_data.z)
        output = source.copy()
        changed = source != original
        for unique_row in range(self.map_data.rows):
            rows = np.flatnonzero(self.y_inverse == unique_row)
            for unique_column in range(self.map_data.columns):
                columns = np.flatnonzero(self.x_inverse == unique_column)
                group = np.ix_(rows, columns)
                candidates = source[group][changed[group]]
                if not candidates.size:
                    continue
                if not np.all(candidates == candidates[0]):
                    raise MapValidationError(
                        "Repeated-axis padding cells represent one logical value; "
                        "the proposed values conflict."
                    )
                output[group] = candidates[0]
        return output


def _axis_inverse(axis: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    keep: list[int] = []
    inverse = np.empty(axis.size, dtype=int)
    unique = -1
    previous: float | None = None
    for index, value in enumerate(axis):
        if index == 0 or value != previous:
            keep.append(index)
            unique += 1
        inverse[index] = unique
        previous = float(value)
    return np.asarray(keep, dtype=int), inverse


def _matching(left: np.ndarray, right: np.ndarray) -> bool:
    scale = max(1.0, float(np.max(np.abs(left))), float(np.max(np.abs(right))))
    return bool(np.allclose(left, right, rtol=1e-12, atol=scale * 1e-12))


def collapse_duplicate_curve(curve_data: CurveData) -> CollapsedCurve:
    x_keep, x_inverse = _axis_inverse(curve_data.x)
    for index, unique_index in enumerate(x_inverse):
        reference = x_keep[unique_index]
        if index != reference and not _matching(
            curve_data.values[index : index + 1],
            curve_data.values[reference : reference + 1],
        ):
            raise MapValidationError(
                f"X axis value {curve_data.x[index]:.12g} is repeated, but its duplicate "
                "points have different values; interpolation would be ambiguous."
            )
    unique_curve = CurveData(
        curve_data.x[x_keep],
        curve_data.values[x_keep],
        curve_data.name,
    )
    return CollapsedCurve(
        unique_curve,
        x_inverse,
        curve_data.size - unique_curve.size,
    )


def collapse_duplicate_map(map_data: MapData) -> CollapsedMap:
    x_keep, x_inverse = _axis_inverse(map_data.x)
    y_keep, y_inverse = _axis_inverse(map_data.y)
    for column, unique_column in enumerate(x_inverse):
        reference = x_keep[unique_column]
        if column != reference and not _matching(map_data.z[:, column], map_data.z[:, reference]):
            raise MapValidationError(
                f"X axis value {map_data.x[column]:.12g} is repeated, but its columns have "
                "different values; interpolation would be ambiguous."
            )
    for row, unique_row in enumerate(y_inverse):
        reference = y_keep[unique_row]
        if row != reference and not _matching(map_data.z[row, :], map_data.z[reference, :]):
            raise MapValidationError(
                f"Y axis value {map_data.y[row]:.12g} is repeated, but its rows have "
                "different values; interpolation would be ambiguous."
            )
    unique_map = MapData(
        map_data.x[x_keep],
        map_data.y[y_keep],
        map_data.z[np.ix_(y_keep, x_keep)],
        map_data.name,
    )
    return CollapsedMap(
        unique_map,
        x_inverse,
        y_inverse,
        map_data.columns - unique_map.columns,
        map_data.rows - unique_map.rows,
    )
