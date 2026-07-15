from __future__ import annotations
import json
import math
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path

_GAUGE_STYLES = frozenset({"digital", "needle", "bar-h", "bar-v", "sparkline"})

@dataclass
class EditorSettings:
    settings_version: int = 3
    # table display
    font_size: int = 11
    color_cells: bool = True
    colormap: str = "rainbow"               # rainbow preferred | viridis optional
    table_density: str = "normal"            # normal | compact
    # appearance
    theme: str = "dark"                     # dark | light | system (dark first-class, spec §3)
    user_level: int = 5                     # tree/palette filter ceiling
    ui_state: dict[str, str] = field(default_factory=dict)   # geometry/window_state base64
    # library
    definition_paths: list[str] = field(default_factory=list)
    # logger composition
    logger_definition_path: str = ""    # RomRaider logger-definition XML; "" = prompt on first launch
    cars_def_path: str = ""             # cars_def.xml for dyno CarProfiles; "" = search next to the logger def
    # checksum (spec 4.1: per-ROM manager override, keyed by xmlid; empty = automatic binding)
    checksum_override: dict[str, str] = field(default_factory=dict)
    # logger last-session state: keys "poll"|"livedata"|"graph"|"dash" -> channel ids
    logger_selections: dict[str, list[str]] = field(default_factory=dict)
    # logger CSV output directory; "" = Path.home()
    logger_csv_dir: str = ""
    # per-gauge warning thresholds: channel_id -> [mode, value]
    warn_thresholds: dict[str, list[str | float]] = field(default_factory=dict)
    # per-gauge style: channel_id -> style
    gauge_styles: dict[str, str] = field(default_factory=dict)

def settings_path() -> Path:
    base = os.environ.get("ECUEDITOR_CONFIG_DIR")
    if base:
        return Path(base) / "settings.json"
    home = Path(os.environ.get("APPDATA", Path.home()))
    return home / "ecueditor" / "settings.json"

_DELETED_V1_KEYS = {
    "max_color", "min_color", "highlight_color", "select_color", "warning_color",
    "increase_color", "decrease_color", "axis_color", "value_limit_warning",
    "default_scale", "clipboard_format", "font_family", "font_bold",
}

_DELETED_V3_KEYS = {"cell_width", "cell_height", "editor_window_size"}


def _int_setting(value: object, default: int, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return max(minimum, min(maximum, value))


def _string_setting(value: object, default: str) -> str:
    return value if isinstance(value, str) else default


def _string_map(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {
        key: item for key, item in value.items()
        if isinstance(key, str) and isinstance(item, str)
    }


def _gauge_style_map(value: object) -> dict[str, str]:
    return {
        key: style for key, style in _string_map(value).items()
        if style in _GAUGE_STYLES
    }


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _string_list_map(value: object) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        return {}
    return {
        key: _string_list(item) for key, item in value.items()
        if isinstance(key, str) and isinstance(item, list)
    }


def _threshold_map(value: object) -> dict[str, list[str | float]]:
    if not isinstance(value, dict):
        return {}
    thresholds: dict[str, list[str | float]] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, list) or len(item) != 2:
            continue
        mode, limit = item
        if mode not in {"above", "below"}:
            continue
        if isinstance(limit, bool) or not isinstance(limit, (int, float)):
            continue
        numeric_limit = float(limit)
        if not math.isfinite(numeric_limit):
            continue
        thresholds[key] = [mode, numeric_limit]
    return thresholds

def load_settings(path: str | Path | None = None) -> EditorSettings:
    p = Path(path) if path is not None else settings_path()
    if not p.is_file():
        return EditorSettings()
    try:
        raw = json.loads(p.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError, UnicodeError):
        return EditorSettings()
    if not isinstance(raw, dict):
        return EditorSettings()             # valid JSON, but not an object (null / scalar / array)
    try:
        version = int(raw.get("settings_version", 1))
    except (TypeError, ValueError):
        version = 1
    if version < 2:                                  # v1 migration (spec D12)
        raw = {k: v for k, v in raw.items() if k not in _DELETED_V1_KEYS}
        if raw.get("theme", "system") == "system":
            raw["theme"] = "dark"
        raw["settings_version"] = 2
        version = 2
    if version < 3:
        raw = {k: v for k, v in raw.items() if k not in _DELETED_V3_KEYS}
        raw["settings_version"] = 3
    defaults = EditorSettings()
    theme = _string_setting(raw.get("theme"), defaults.theme)
    if theme not in {"dark", "light", "system"}:
        theme = defaults.theme
    colormap = _string_setting(raw.get("colormap"), defaults.colormap)
    if colormap not in {"rainbow", "viridis"}:
        colormap = defaults.colormap
    density = _string_setting(raw.get("table_density"), defaults.table_density)
    if density not in {"normal", "compact"}:
        density = defaults.table_density

    return EditorSettings(
        settings_version=defaults.settings_version,
        font_size=_int_setting(raw.get("font_size"), defaults.font_size, 7, 24),
        color_cells=(
            raw["color_cells"]
            if isinstance(raw.get("color_cells"), bool)
            else defaults.color_cells
        ),
        colormap=colormap,
        table_density=density,
        theme=theme,
        user_level=_int_setting(raw.get("user_level"), defaults.user_level, 1, 5),
        ui_state=_string_map(raw.get("ui_state")),
        definition_paths=_string_list(raw.get("definition_paths")),
        logger_definition_path=_string_setting(
            raw.get("logger_definition_path"), defaults.logger_definition_path
        ),
        cars_def_path=_string_setting(raw.get("cars_def_path"), defaults.cars_def_path),
        checksum_override=_string_map(raw.get("checksum_override")),
        logger_selections=_string_list_map(raw.get("logger_selections")),
        logger_csv_dir=_string_setting(raw.get("logger_csv_dir"), defaults.logger_csv_dir),
        warn_thresholds=_threshold_map(raw.get("warn_thresholds")),
        gauge_styles=_gauge_style_map(raw.get("gauge_styles")),
    )

def save_settings(settings: EditorSettings, path: str | Path | None = None) -> None:
    p = Path(path) if path is not None else settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(asdict(settings), indent=2), encoding="utf-8")
