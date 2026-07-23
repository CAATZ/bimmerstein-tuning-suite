"""Versioned shipped and user-editable MAF catalog access."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import replace
from functools import cache, lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from ecueditor.core.settings import settings_path

from .grids import CANONICAL_GRID_ID, CANONICAL_VOLTAGES_V, FLOW_UNIT, VOLTAGE_UNIT
from .models import DiameterMetadata, MafRecord

_USER_CATALOG_VERSION = "v1"
_USER_CATALOG_FILENAME = "maf-transfer-functions.json"


def _canonical_bytes(document: dict[str, Any]) -> bytes:
    return (
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        + "\n"
    ).encode("utf-8")


def _record_data_hash(record: MafRecord) -> str:
    payload = {
        "flow_values_kg_per_hr": list(record.flow_values_kg_per_hr),
        "voltage_values_v": list(CANONICAL_VOLTAGES_V),
    }
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def _with_current_hash(record: MafRecord) -> MafRecord:
    return replace(record, data_sha256=_record_data_hash(record))


def _record_from_json(raw: dict[str, Any], default_diameter_in: float) -> MafRecord:
    diameter = raw["source_tube_diameter"]
    return MafRecord(
        id=str(raw["id"]),
        display_name=str(raw["display_name"]),
        manufacturer=raw["manufacturer"],
        part_number=raw["part_number"],
        variant=raw["variant"],
        source_header=str(raw["source_header"]),
        voltage_unit=str(raw["voltage_unit"]),
        flow_unit=str(raw["flow_unit"]),
        voltage_grid_id=str(raw["voltage_grid_id"]),
        flow_values_kg_per_hr=tuple(float(value) for value in raw["flow_values_kg_per_hr"]),
        source_tube_diameter=DiameterMetadata(
            value=diameter["value"],
            unit=diameter["unit"],
            diameter_type=diameter["diameter_type"],
            source_text=diameter["source_text"],
            uncertainty=diameter["uncertainty"],
        ),
        default_tube_diameter_in=float(default_diameter_in),
        source_workbook_filename=str(raw["source_workbook_filename"]),
        source_sheet=str(raw["source_sheet"]),
        source_cell_range=str(raw["source_cell_range"]),
        source_workbook_sha256=str(raw["source_workbook_sha256"]),
        data_sha256=str(raw["data_sha256"]),
        notes=tuple(str(item) for item in raw["notes"]),
        uncertainty=tuple(str(item) for item in raw["uncertainty"]),
    )


@lru_cache(maxsize=1)
def _catalog_document() -> dict[str, Any]:
    resource = files(__package__).joinpath("data/catalog-v1.json")
    return cast(dict[str, Any], json.loads(resource.read_text(encoding="utf-8")))


@lru_cache(maxsize=1)
def _shipped_records() -> tuple[MafRecord, ...]:
    document = _catalog_document()
    defaults = document["default_tube_diameters_in"]
    return tuple(
        _record_from_json(raw, defaults[raw["id"]]) for raw in document["records"]
    )


def new_maf_record(
    display_name: str,
    default_tube_diameter_in: float,
    flow_values_kg_per_hr,
    *,
    maf_id: str | None = None,
) -> MafRecord:
    """Create a user-managed 256-point transfer function."""

    record = MafRecord(
        id=maf_id or f"user-{uuid4().hex}",
        display_name=str(display_name).strip(),
        manufacturer=None,
        part_number=None,
        variant=None,
        source_header=str(display_name).strip(),
        voltage_unit=VOLTAGE_UNIT,
        flow_unit=FLOW_UNIT,
        voltage_grid_id=CANONICAL_GRID_ID,
        flow_values_kg_per_hr=tuple(float(value) for value in flow_values_kg_per_hr),
        source_tube_diameter=DiameterMetadata(None, None, "unknown", None),
        default_tube_diameter_in=float(default_tube_diameter_in),
        source_workbook_filename="",
        source_sheet="",
        source_cell_range="",
        source_workbook_sha256="",
        data_sha256="",
        notes=("User-managed MAF transfer function.",),
        uncertainty=(),
    )
    return _with_current_hash(record)


def user_maf_catalog_path() -> Path:
    return settings_path().with_name(_USER_CATALOG_FILENAME)


def _record_from_user_json(raw: dict[str, Any]) -> MafRecord:
    maf_id = str(raw["id"])
    name = str(raw["display_name"])
    diameter = float(raw["default_tube_diameter_in"])
    values = tuple(float(value) for value in raw["flow_values_kg_per_hr"])
    base = next((record for record in _shipped_records() if record.id == maf_id), None)
    if base is None:
        return new_maf_record(name, diameter, values, maf_id=maf_id)
    record = replace(
        base,
        display_name=name,
        default_tube_diameter_in=diameter,
        flow_values_kg_per_hr=values,
    )
    if values != base.flow_values_kg_per_hr:
        record = replace(
            record,
            source_workbook_filename="",
            source_sheet="",
            source_cell_range="",
            source_workbook_sha256="",
            notes=("Edited in MAF Transfer Function Manager.",),
            uncertainty=(),
        )
    return _with_current_hash(record)


def load_mafs(path: str | Path | None = None) -> tuple[MafRecord, ...]:
    target = Path(path) if path is not None else user_maf_catalog_path()
    if not target.is_file():
        return _shipped_records()
    document = json.loads(target.read_text(encoding="utf-8"))
    if document.get("catalog_version") != _USER_CATALOG_VERSION:
        raise ValueError("unsupported user MAF catalog version")
    records = tuple(_record_from_user_json(raw) for raw in document["records"])
    _validate_user_records(records)
    return records


@lru_cache(maxsize=1)
def list_mafs() -> tuple[MafRecord, ...]:
    try:
        return load_mafs()
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return _shipped_records()


@cache
def get_maf(maf_id: str) -> MafRecord:
    for record in list_mafs():
        if record.id == maf_id:
            return record
    raise KeyError(f"unknown MAF catalog ID: {maf_id}")


def _validate_user_records(records: tuple[MafRecord, ...]) -> None:
    if len({record.id for record in records}) != len(records):
        raise ValueError("MAF transfer-function IDs must be unique")
    for record in records:
        if not record.id or not record.display_name.strip():
            raise ValueError("Every MAF transfer function requires a name and ID")
        if not math.isfinite(record.default_tube_diameter_in) \
                or record.default_tube_diameter_in <= 0:
            raise ValueError(f"{record.display_name}: default tube diameter must be positive")
        values = record.flow_values_kg_per_hr
        if len(values) != 256 or not all(math.isfinite(value) for value in values):
            raise ValueError(f"{record.display_name}: transfer function must have 256 values")


def save_mafs(records, path: str | Path | None = None) -> Path:
    """Atomically save the complete user-managed transfer-function list."""

    normalized = tuple(records)
    _validate_user_records(normalized)
    target = Path(path) if path is not None else user_maf_catalog_path()
    document = {
        "catalog_version": _USER_CATALOG_VERSION,
        "records": [
            {
                "id": record.id,
                "display_name": record.display_name,
                "default_tube_diameter_in": record.default_tube_diameter_in,
                "flow_values_kg_per_hr": list(record.flow_values_kg_per_hr),
            }
            for record in normalized
        ],
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_bytes(_canonical_bytes(document))
    temporary.replace(target)
    if path is None:
        list_mafs.cache_clear()
        get_maf.cache_clear()
    return target


def validate_catalog() -> tuple[str, ...]:
    errors: list[str] = []
    document = _catalog_document()
    records = _shipped_records()
    if document.get("catalog_version") != "v1":
        errors.append("catalog_version must be v1")
    if document.get("curve_count") != 25 or len(records) != 25:
        errors.append("catalog must contain exactly 25 records")
    defaults = document.get("default_tube_diameters_in")
    if not isinstance(defaults, dict) or set(defaults) != {record.id for record in records}:
        errors.append("catalog must declare one default tube diameter per record")
    sources = document.get("sources")
    if not isinstance(sources, list) or len(sources) != 3:
        errors.append("catalog must declare all three approved source workbooks")
    if len({record.id for record in records}) != len(records):
        errors.append("catalog IDs must be unique")
    if (
        len(CANONICAL_VOLTAGES_V) != 256
        or CANONICAL_VOLTAGES_V[32] != 0.63
        or CANONICAL_VOLTAGES_V[160] != 3.13
    ):
        errors.append("canonical voltage grid is invalid")
    for record in records:
        if record.voltage_unit != VOLTAGE_UNIT or record.flow_unit != FLOW_UNIT:
            errors.append(f"{record.id}: unexpected units")
        if record.voltage_grid_id != CANONICAL_GRID_ID:
            errors.append(f"{record.id}: unexpected voltage grid")
        if len(record.flow_values_kg_per_hr) != 256:
            errors.append(f"{record.id}: expected 256 flow values")
        if record.data_sha256 != _record_data_hash(record):
            errors.append(f"{record.id}: data hash mismatch")
        if not math.isfinite(record.default_tube_diameter_in) \
                or record.default_tube_diameter_in <= 0:
            errors.append(f"{record.id}: invalid default tube diameter")
        diameter = record.source_tube_diameter
        if diameter.diameter_type == "inside" and (
            diameter.value is None or diameter.unit is None
        ):
            errors.append(f"{record.id}: inside diameter must include a value and unit")
        if diameter.diameter_type == "unknown" and diameter.value is not None:
            errors.append(f"{record.id}: unknown diameter cannot imply a numeric value")
    nissan = [record for record in records if record.part_number == "22680-7S000"]
    if len(nissan) != 2 or len({record.id for record in nissan}) != 2:
        errors.append("stock and edited Nissan records must remain distinct")
    nissan_350z = next(
        (record for record in records if record.id == "nissan-350z-3-5-inch-tube"),
        None,
    )
    if nissan_350z is None:
        errors.append("catalog must include the Nissan 350Z 3.5-inch transfer function")
    elif nissan_350z.source_cell_range != "B3:Q18":
        errors.append(
            "Nissan 350Z transfer function must preserve the B3:Q18 kg/hr source range"
        )
    return tuple(errors)
