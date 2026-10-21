"""Generate static/favicon.ico without third-party dependencies."""

from __future__ import annotations

import struct
from pathlib import Path

GLYPH = [
    "#........#",
    "##......##",
    "#.#....#.#",
    "#..#..#..#",
    "#...##...#",
    "#...##...#",
    "#........#",
    "#........#",
    "#........#",
    "#........#",
]

BG = (0x15, 0x1A, 0x21, 0xFF)      # BGRA
BORDER = (0xFF, 0x9C, 0x7F, 0xFF)  # accent blue, BGRA
INK = (0xF2, 0xEC, 0xE8, 0xFF)     # near-white, BGRA
CLEAR = (0, 0, 0, 0)


def build_pixels(size: int) -> list[list[tuple[int, int, int, int]]]:
    scale = 1 if size <= 16 else 3
    glyph_w = len(GLYPH[0]) * scale
    glyph_h = len(GLYPH) * scale
    ox = (size - glyph_w) // 2
    oy = (size - glyph_h) // 2

    rows: list[list[tuple[int, int, int, int]]] = []
    for y in range(size):
        row: list[tuple[int, int, int, int]] = []
        for x in range(size):
            edge = x == 0 or y == 0 or x == size - 1 or y == size - 1
            corner = (x < 2 and y < 2) or (x >= size - 2 and y < 2)
            corner = corner or (x < 2 and y >= size - 2) or (
                x >= size - 2 and y >= size - 2
            )
            if corner:
                row.append(CLEAR)
                continue
            gx = (x - ox) // scale
            gy = (y - oy) // scale
            if 0 <= gy < len(GLYPH) and 0 <= gx < len(GLYPH[0]):
                if GLYPH[gy][gx] == "#":
                    row.append(INK)
                    continue
            row.append(BORDER if edge else BG)
        rows.append(row)
    return rows


def ico_image(size: int) -> bytes:
    rows = build_pixels(size)
    # BITMAPINFOHEADER: height is doubled (XOR bitmap + AND mask).
    header = struct.pack(
        "<IiiHHIIiiII",
        40,
        size,
        size * 2,
        1,
        32,
        0,
        len(rows) * size * 4,
        0,
        0,
        0,
        0,
    )
    xor = bytearray()
    for row in reversed(rows):  # BMP rows are bottom-up
        for b, g, r, a in row:
            xor += bytes((b, g, r, a))
    mask_row_bytes = ((size + 31) // 32) * 4
    and_mask = bytearray()
    for row in reversed(rows):
        bits = bytearray(mask_row_bytes)
        for x, (_b, _g, _r, a) in enumerate(row):
            if a == 0:
                bits[x // 8] |= 0x80 >> (x % 8)
        and_mask += bits
    return header + bytes(xor) + bytes(and_mask)


def build() -> bytes:
    sizes = [16, 32]
    images = [ico_image(size) for size in sizes]
    out = bytearray(struct.pack("<HHH", 0, 1, len(sizes)))
    offset = 6 + 16 * len(sizes)
    for size, image in zip(sizes, images):
        out += struct.pack(
            "<BBBBHHII",
            size if size < 256 else 0,
            size if size < 256 else 0,
            0,
            0,
            1,
            32,
            len(image),
            offset,
        )
        offset += len(image)
    for image in images:
        out += image
    return bytes(out)


target = Path(__file__).parent.parent / "src" / "monoke" / "web" / "static" / "favicon.ico"
target.write_bytes(build())
print(f"wrote {target} ({target.stat().st_size} bytes)")
