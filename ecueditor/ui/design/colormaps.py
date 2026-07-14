"""Full-spectrum heatmap LUTs (spec §3 D6): viridis default + classic-rainbow toggle.

Qt-free. LUTs are built once at import by piecewise-linear interpolation of anchor stops.
"""
from __future__ import annotations

RGB = tuple[int, int, int]

_VIRIDIS_ANCHORS = ("#440154", "#482878", "#3e4a89", "#31688e", "#26828e",
                    "#1f9e89", "#35b779", "#6ece58", "#b5de2b", "#fde725")
_RAINBOW_ANCHORS = ("#30123b", "#4145ab", "#4675ed", "#39a2fc", "#1bcfd4",
                    "#24eca6", "#61fc6c", "#a4fc3b", "#d1e834", "#f3c63a",
                    "#fe9b2d", "#f36315", "#d93806", "#b11901", "#7a0402")


def _hex(c: str) -> RGB:
    return int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16)


def _build_lut(anchors: tuple[str, ...]) -> tuple[RGB, ...]:
    stops = [_hex(a) for a in anchors]
    lut: list[RGB] = []
    n = len(stops) - 1
    for i in range(256):
        pos = i / 255 * n
        k = min(int(pos), n - 1)
        t = pos - k
        a, b = stops[k], stops[k + 1]
        lut.append(tuple(round(a[j] + (b[j] - a[j]) * t) for j in range(3)))  # type: ignore[arg-type]
    return tuple(lut)


COLORMAPS: dict[str, tuple[RGB, ...]] = {
    "viridis": _build_lut(_VIRIDIS_ANCHORS),
    "rainbow": _build_lut(_RAINBOW_ANCHORS),
}


def heat_color(ratio: float, colormap: str = "viridis") -> RGB:
    lut = COLORMAPS.get(colormap, COLORMAPS["viridis"])
    r = 0.0 if ratio < 0 else 1.0 if ratio > 1 else ratio
    return lut[int(r * 255)]


def text_color_for(rgb: RGB) -> RGB:
    """Near-white on dark cells, near-black on bright cells (WCAG-ish luminance split)."""
    lum = 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]
    return (12, 13, 16) if lum > 140 else (232, 234, 237)
