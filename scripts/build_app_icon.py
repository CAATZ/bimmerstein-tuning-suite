"""Render the BimmerStein Tuning Suite vector mark into a Windows icon."""
from __future__ import annotations

import struct
from pathlib import Path

from PySide6.QtCore import QByteArray, QBuffer, QIODevice
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "resources" / "icons" / "app.svg"
TARGET = ROOT / "resources" / "icons" / "app.ico"
SIZES = (16, 24, 32, 48, 64, 128, 256)


def _render_png(renderer: QSvgRenderer, size: int) -> bytes:
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(0)
    painter = QPainter(image)
    renderer.render(painter)
    painter.end()

    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, "PNG"):
        raise RuntimeError(f"Could not encode {size}x{size} icon frame")
    return bytes(data)


def build_icon() -> None:
    app = QGuiApplication.instance() or QGuiApplication([])
    renderer = QSvgRenderer(str(SOURCE))
    if not renderer.isValid():
        raise RuntimeError(f"Could not load icon source: {SOURCE}")

    frames = [(size, _render_png(renderer, size)) for size in SIZES]
    directory_size = 6 + (16 * len(frames))
    offset = directory_size
    entries: list[bytes] = []
    payloads: list[bytes] = []
    for size, payload in frames:
        dimension = 0 if size == 256 else size
        entries.append(
            struct.pack(
                "<BBBBHHII",
                dimension,
                dimension,
                0,
                0,
                1,
                32,
                len(payload),
                offset,
            )
        )
        payloads.append(payload)
        offset += len(payload)

    TARGET.write_bytes(
        struct.pack("<HHH", 0, 1, len(frames)) + b"".join(entries) + b"".join(payloads)
    )
    print(f"Wrote {TARGET} ({', '.join(f'{size}px' for size in SIZES)})")
    del app


if __name__ == "__main__":
    build_icon()
