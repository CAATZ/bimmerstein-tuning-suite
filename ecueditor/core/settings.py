from __future__ import annotations
import json
import os
from dataclasses import dataclass, field, asdict, fields
from pathlib import Path

@dataclass
class EditorSettings:
    settings_version: int = 2
    # table display
    cell_width: int = 42
    cell_height: int = 18
    font_size: int = 11
    color_cells: bool = True
    colormap: str = "rainbow"               # rainbow preferred | viridis optional
    table_density: str = "normal"            # normal | compact
    editor_window_size: str = "medium"       # small | medium | large (MDI editors only)
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
    warn_thresholds: dict[str, list] = field(default_factory=dict)
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

def load_settings(path: str | Path | None = None) -> EditorSettings:
    p = Path(path) if path is not None else settings_path()
    if not p.is_file():
        return EditorSettings()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return EditorSettings()
    if not isinstance(raw, dict):
        return EditorSettings()             # valid JSON, but not an object (null / scalar / array)
    if int(raw.get("settings_version", 1)) < 2:      # v1 migration (spec D12)
        raw = {k: v for k, v in raw.items() if k not in _DELETED_V1_KEYS}
        if raw.get("theme", "system") == "system":
            raw["theme"] = "dark"
        raw["settings_version"] = 2
    known = {f.name for f in fields(EditorSettings)}
    return EditorSettings(**{k: v for k, v in raw.items() if k in known})

def save_settings(settings: EditorSettings, path: str | Path | None = None) -> None:
    p = Path(path) if path is not None else settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(asdict(settings), indent=2), encoding="utf-8")
