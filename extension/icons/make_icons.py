"""Generate the extension icons. Pure stdlib, no image library.

    python extension/icons/make_icons.py

Three bars on a dark tile: met, partial, gap — the shape of a match report.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

BG = (26, 30, 42)
BARS = [((124, 214, 163), 0.50), ((233, 196, 106), 0.72), ((240, 138, 138), 0.34)]
HERE = Path(__file__).resolve().parent


def _chunk(tag: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data)) + tag + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def render(size: int) -> bytes:
    pad = max(1, round(size * 0.16))
    gap = max(1, round(size * 0.07))
    usable = size - 2 * pad
    bar_w = max(1, (usable - 2 * gap) // 3)
    radius = size * 0.22

    rows = []
    for y in range(size):
        row = bytearray([0])  # filter byte 0 (None)
        for x in range(size):
            # rounded-corner mask
            cx = min(x, size - 1 - x)
            cy = min(y, size - 1 - y)
            if cx < radius and cy < radius:
                dx, dy = radius - cx, radius - cy
                if dx * dx + dy * dy > radius * radius:
                    row += bytes((0, 0, 0, 0))
                    continue

            pixel = BG
            for i, (colour, height) in enumerate(BARS):
                left = pad + i * (bar_w + gap)
                if left <= x < left + bar_w:
                    top = size - pad - round(usable * height)
                    if top <= y < size - pad:
                        pixel = colour
                    break
            row += bytes((*pixel, 255))
        rows.append(bytes(row))

    raw = b"".join(rows)
    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(raw, 9))
        + _chunk(b"IEND", b"")
    )


if __name__ == "__main__":
    for size in (16, 48, 128):
        out = HERE / f"icon-{size}.png"
        out.write_bytes(render(size))
        print(f"wrote {out.name} ({out.stat().st_size} bytes)")
