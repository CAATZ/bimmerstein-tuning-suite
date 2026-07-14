"""Design tokens (spec §3, decisions D3-D5). Qt-free: hex strings + ints only.

Every color is "#rrggbb". Consumers: qss.py (chrome), table_grid/table_model (cells),
status_chips, gauges (8c), 3D (8b). DARK is the first-class theme; LIGHT derives from the
same token names.
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class Theme:
    name: str
    # surfaces (window -> docks/panels -> bars/tabs -> popups/cards)
    bg: str
    surface1: str
    surface2: str
    surface3: str
    border: str
    border_strong: str
    # text
    text: str
    text_dim: str
    text_disabled: str
    # accent — M Red (D3); interactive states only, never alarm semantics (D4)
    accent: str
    accent_hover: str
    accent_pressed: str
    # semantic (D4: danger/failure = danger_fill background + icon, never color alone)
    ok: str
    warn: str
    danger: str
    danger_fill: str
    # focus & selection (D5: selection = accent ring + near-white inner hairline)
    focus_ring: str
    sel_ring: str
    sel_ring_inner: str
    # data / grid
    grid_line: str
    compare_neutral: str      # unchanged cells in compare mode
    increase_border: str      # edited-cell border, value went up
    decrease_border: str      # edited-cell border, value went down
    live_ring: str            # logger live-overlay cell ring
    # charts (8c strip charts / dyno / analysis): six pens, NO red (D4)
    chart_pens: tuple[str, ...]
    # geometry
    space: tuple[int, ...]    # 4-step spacing scale, px
    radius: tuple[int, ...]   # 4-step corner radii, px


DARK = Theme(
    name="dark",
    bg="#101215", surface1="#15171b", surface2="#1d2026", surface3="#23272e",
    border="#26292f", border_strong="#3d4148",
    text="#e8eaed", text_dim="#9aa0a6", text_disabled="#5f6368",
    accent="#e5484d", accent_hover="#f16a6e", accent_pressed="#c73a3f",
    ok="#4caf7d", warn="#f5a623", danger="#e5484d", danger_fill="#8f2327",
    focus_ring="#f16a6e", sel_ring="#e5484d", sel_ring_inner="#f2f3f5",
    grid_line="#15171b", compare_neutral="#3a3f46",
    increase_border="#ff6b6b", decrease_border="#5c9ded", live_ring="#3da8dc",
    chart_pens=("#3da8dc", "#f5a623", "#4caf7d", "#b18ae8", "#4dd0e1", "#e8eaed"),
    space=(4, 8, 12, 16), radius=(3, 4, 6, 8),
)

LIGHT = Theme(
    name="light",
    bg="#f5f6f8", surface1="#ffffff", surface2="#eceef1", surface3="#ffffff",
    border="#d8dbe0", border_strong="#b7bcc4",
    text="#1a1d22", text_dim="#6a7076", text_disabled="#a6acb5",
    accent="#c73a3f", accent_hover="#e5484d", accent_pressed="#a83236",
    ok="#2e7d54", warn="#b26a00", danger="#c62f34", danger_fill="#8f2327",
    focus_ring="#e5484d", sel_ring="#c73a3f", sel_ring_inner="#1a1d22",
    grid_line="#e2e4e8", compare_neutral="#d2d5da",
    increase_border="#d84343", decrease_border="#2f6fce", live_ring="#1f8bbf",
    chart_pens=("#1f8bbf", "#b26a00", "#2e7d54", "#7c53c3", "#00838f", "#1a1d22"),
    space=(4, 8, 12, 16), radius=(3, 4, 6, 8),
)

_BY_NAME = {"dark": DARK, "light": LIGHT}


def theme_by_name(name: str) -> Theme | None:
    """Resolve a settings theme value; "system" and unknown names return None (no styling)."""
    return _BY_NAME.get((name or "").strip().lower())


def rgba(hex_color: str, alpha: float) -> str:
    """QSS rgba() string from a #rrggbb token — for tint fills in stylesheets."""
    r, g, b = int(hex_color[1:3], 16), int(hex_color[3:5], 16), int(hex_color[5:7], 16)
    return f"rgba({r},{g},{b},{int(alpha * 255)})"
