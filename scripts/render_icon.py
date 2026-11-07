"""Rasterize everoute/assets/route-desk.svg to PNG sizes and a multi-resolution ICO.

The SVG is the master. Runtime code loads the generated PNG and ICO files.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

ROOT = Path(__file__).resolve().parents[1]
SVG = ROOT / "everoute" / "assets" / "route-desk.svg"
OUT = ROOT / "everoute" / "assets"
SIZES = (16, 24, 32, 48, 64, 128, 256)


def render(size: int) -> QImage:
    renderer = QSvgRenderer(QByteArray(SVG.read_bytes()))
    if not renderer.isValid():
        raise SystemExit(f"SVG did not render: {SVG}")
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(0)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter)
    painter.end()
    return image


def dib_from_image(image: QImage) -> bytes:
    image = image.convertToFormat(QImage.Format.Format_ARGB32)
    width = image.width()
    height = image.height()
    header = struct.pack(
        "<IiiHHIIiiII",
        40,
        width,
        height * 2,
        1,
        32,
        0,
        width * height * 4,
        0,
        0,
        0,
        0,
    )
    pixels = bytearray()
    for y in range(height - 1, -1, -1):
        for x in range(width):
            argb = image.pixel(x, y)
            pixels += bytes(
                (
                    argb & 0xFF,
                    (argb >> 8) & 0xFF,
                    (argb >> 16) & 0xFF,
                    (argb >> 24) & 0xFF,
                )
            )
    mask_stride = ((width + 31) // 32) * 4
    return header + bytes(pixels) + (b"\x00" * (mask_stride * height))


def write_ico(images: list[QImage], dest: Path) -> None:
    count = len(images)
    blobs = [dib_from_image(image) for image in images]
    header = struct.pack("<HHH", 0, 1, count)
    directory = bytearray()
    offset = 6 + 16 * count
    for image, blob in zip(images, blobs, strict=True):
        width = 0 if image.width() >= 256 else image.width()
        height = 0 if image.height() >= 256 else image.height()
        directory += struct.pack("<BBBBHHII", width, height, 0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
    dest.write_bytes(header + bytes(directory) + b"".join(blobs))


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    images = []
    for size in SIZES:
        image = render(size)
        png = OUT / f"route-desk-{size}.png"
        if not image.save(str(png), "PNG"):
            raise SystemExit(f"Could not write {png}")
        images.append(image)
        print(f"wrote {png.name}")
    ico = OUT / "route-desk.ico"
    write_ico(images, ico)
    print(f"wrote {ico.name} ({len(SIZES)} sizes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
