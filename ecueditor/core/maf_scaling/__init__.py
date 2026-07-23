"""Built-in deterministic MAF scaling API."""

from .catalog import (
    get_maf,
    list_mafs,
    load_mafs,
    new_maf_record,
    save_mafs,
    user_maf_catalog_path,
    validate_catalog,
)
from .destination import (
    KNOWN_MAF_TABLE_NAMES,
    MafPreview,
    build_maf_preview,
    is_known_maf_destination,
    is_manual_maf_candidate,
    maf_voltage_axes,
    shape_maf_values,
    table_maf_record,
)
from .grids import CANONICAL_VOLTAGES_V, canonical_voltage_grid
from .models import DiameterMetadata, MafRecord, ScalingRequest, ScalingResult
from .resampling import from_16x16, resample_curve, to_16x16
from .scaling import ELECTRICAL_PRESETS_OHMS, scale_maf

__all__ = [
    "CANONICAL_VOLTAGES_V",
    "ELECTRICAL_PRESETS_OHMS",
    "KNOWN_MAF_TABLE_NAMES",
    "DiameterMetadata",
    "MafPreview",
    "MafRecord",
    "ScalingRequest",
    "ScalingResult",
    "build_maf_preview",
    "canonical_voltage_grid",
    "from_16x16",
    "get_maf",
    "is_known_maf_destination",
    "is_manual_maf_candidate",
    "list_mafs",
    "load_mafs",
    "maf_voltage_axes",
    "new_maf_record",
    "resample_curve",
    "scale_maf",
    "save_mafs",
    "shape_maf_values",
    "table_maf_record",
    "to_16x16",
    "user_maf_catalog_path",
    "validate_catalog",
]
