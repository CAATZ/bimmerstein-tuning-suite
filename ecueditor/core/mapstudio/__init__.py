"""Qt-free calibration table resampling, repair, and review primitives.

The UI deliberately consumes these finished numerical models.  Nothing in this
package imports PySide6 or :mod:`ecueditor.ui`.
"""

from .adapter import (
    QuantizedTableProposal,
    TableSnapshot,
    fingerprint_table,
    quantize_table_proposal,
    snapshot_table,
)
from .features import SafetyReport, build_safety_report
from .history import UndoHistory
from .interpolation import (
    CurveResampleResult,
    ResampleResult,
    even_axis,
    resample_curve,
    resample_map,
)
from .model import (
    CollapsedCurve,
    CollapsedMap,
    CurveData,
    MapData,
    MapValidationError,
    collapse_duplicate_curve,
    collapse_duplicate_map,
    validate_axis,
    validate_map_axis,
)
from .smoothing import (
    AnomalyResult,
    CurveAnomalyResult,
    detect_anomalies,
    detect_curve_anomalies,
    repair_curve_selection,
    repair_selected_region,
    smooth_entire_curve,
    smooth_entire_table,
)

__all__ = [
    "AnomalyResult",
    "CollapsedCurve",
    "CollapsedMap",
    "CurveAnomalyResult",
    "CurveData",
    "CurveResampleResult",
    "MapData",
    "MapValidationError",
    "QuantizedTableProposal",
    "ResampleResult",
    "SafetyReport",
    "TableSnapshot",
    "UndoHistory",
    "build_safety_report",
    "collapse_duplicate_curve",
    "collapse_duplicate_map",
    "detect_anomalies",
    "detect_curve_anomalies",
    "even_axis",
    "fingerprint_table",
    "quantize_table_proposal",
    "repair_curve_selection",
    "repair_selected_region",
    "resample_curve",
    "resample_map",
    "smooth_entire_curve",
    "smooth_entire_table",
    "snapshot_table",
    "validate_axis",
    "validate_map_axis",
]
