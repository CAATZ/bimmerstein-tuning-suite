"""Non-mutating diagnostics for source curves."""

from __future__ import annotations

from collections.abc import Sequence


def source_curve_warnings(flow_values_kg_per_hr: Sequence[float]) -> tuple[str, ...]:
    warnings: list[str] = []
    if any(value < 0 for value in flow_values_kg_per_hr):
        warnings.append(
            "source curve contains negative low-voltage flow values; values were preserved"
        )
    if any(
        right < left
        for left, right in zip(flow_values_kg_per_hr, flow_values_kg_per_hr[1:], strict=False)
    ):
        warnings.append("source curve contains local decreases; values were preserved")
    return tuple(warnings)
