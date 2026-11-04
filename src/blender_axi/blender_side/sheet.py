"""`render --sheet`: compose the rendered stills into one contact-sheet image (0.7.0 plan K4, requirements F5).

The stills are loaded as Blender images, their pixels copied tile by tile into one float buffer (Blender rows run
bottom-up, so tile 1 lands top-left and the grid reads left to right, top to bottom), written to a new image and saved
in the format of the output extension. Every image created here is removed again in `finally`. Empty tiles of the last
row stay zero (transparent). Each tile gets its number (0.8.0 K5): white digits from a 3x5 pixel font on a black box at the
tile's top-left, written straight into the float canvas (1.0 / 0.0 survive any colour management). Only the standard library is used: `array.array` buffers go straight into
`Image.pixels.foreach_get/foreach_set`, so the unit tests need no numpy.

Data API only; `bpy` is passed in so the unit tests can drive it with plain stubs. `grid` is pure.
"""

import math
import os
from array import array

from .ops import OpError

SHEET_IMAGE = "AxiSheet"
CHANNELS = 4
_FORMATS = {
    ".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WEBP", ".exr": "OPEN_EXR",
    ".tif": "TIFF", ".tiff": "TIFF", ".bmp": "BMP", ".tga": "TARGA",
}

# 3x5 digit font: "#" = lit. A glyph unit is `label_scale(tile_h)` pixels square; glyphs sit 1 unit apart, PAD units from the box edge.
_DIGITS = {
    "0": ("###", "# #", "# #", "# #", "###"),
    "1": (" # ", "## ", " # ", " # ", "###"),
    "2": ("###", "  #", "###", "#  ", "###"),
    "3": ("###", "  #", "###", "  #", "###"),
    "4": ("# #", "# #", "###", "  #", "  #"),
    "5": ("###", "#  ", "###", "  #", "###"),
    "6": ("###", "#  ", "###", "# #", "###"),
    "7": ("###", "  #", " # ", " # ", " # "),
    "8": ("###", "# #", "###", "# #", "###"),
    "9": ("###", "# #", "###", "  #", "###"),
}
GLYPH_W, GLYPH_H, PAD = 3, 5, 1
_WHITE, _BLACK = (1.0, 1.0, 1.0, 1.0), (0.0, 0.0, 0.0, 1.0)


def grid(n: int, cols: int | None = None) -> tuple[int, int]:
    """`(cols, rows)` for `n` tiles. An explicit `cols` is clamped to 1..n. Otherwise the width starts at ceil(sqrt(n))
    and grows (at most twice the rows) while that leaves fewer empty tiles; the smallest width wins a tie (8 -> 4x2)."""
    n = max(1, int(n))
    if cols:
        cols = max(1, min(int(cols), n))
        return cols, math.ceil(n / cols)
    best = None
    for c in range(math.ceil(math.sqrt(n)), n + 1):
        r = math.ceil(n / c)
        if c > 2 * r:
            break
        empty = c * r - n
        if best is None or empty < best[0]:
            best = (empty, c)
    return best[1], math.ceil(n / best[1])


def _remove(bpy, images: list) -> None:
    for img in images:
        try:
            bpy.data.images.remove(img)
        except Exception:  # an image that is already gone is not an error here
            pass


def label_scale(tile_h: int) -> int:
    """Pixels per glyph unit: 1 up to a 79 px tile, then one more per 40 px (48 -> 1, 180 -> 4, 540 -> 13)."""
    return max(1, int(tile_h) // 40)


def stamp(canvas: array, sheet_w: int, x0: int, y_top: int, tile_w: int, tile_h: int, text: str) -> bool:
    """Draw `text` (digits) as white glyphs on a black box whose top-left is pixel (x0, y_top), rows counted from the top.

    `canvas` is the bottom-up RGBA float buffer of a sheet `sheet_w` wide. Returns False, drawing nothing, when the box
    does not fit the tile (`tile_w` x `tile_h`) or `text` is empty. Characters other than digits are blank.
    """
    if not text:
        return False
    scale = label_scale(tile_h)
    units_w, units_h = len(text) * (GLYPH_W + 1) - 1 + 2 * PAD, GLYPH_H + 2 * PAD
    if units_w * scale > tile_w or units_h * scale > tile_h:
        return False
    sheet_h = len(canvas) // (sheet_w * CHANNELS)
    for uy in range(units_h):
        units = []
        for ux in range(units_w):
            gx, gy = ux - PAD, uy - PAD
            lit = False
            if 0 <= gy < GLYPH_H and gx >= 0 and gx % (GLYPH_W + 1) < GLYPH_W:
                glyph = _DIGITS.get(text[gx // (GLYPH_W + 1)]) if gx // (GLYPH_W + 1) < len(text) else None
                lit = glyph is not None and glyph[gy][gx % (GLYPH_W + 1)] == "#"
            units.append(_WHITE if lit else _BLACK)
        row = array("f", [v for px in units for _ in range(scale) for v in px])
        for sy in range(scale):
            y_img = sheet_h - 1 - (y_top + uy * scale + sy)  # image rows are bottom-up
            dst = (y_img * sheet_w + x0) * CHANNELS
            canvas[dst:dst + len(row)] = row
    return True


def compose(bpy, stills: list[str], out: str, cols: int, labels: list[str] | None = None) -> dict:
    """Write the `stills` (in order) as one grid image at `out`, tile i numbered `labels[i]` when given.

    Returns path, cols, rows, width, height, size_bytes, tiles and labels (how many tiles got their number; a tile too
    small for the box is left as it is).
    """
    if not stills:
        raise OpError("OP_FAILED", "contact sheet failed: no frames to compose")
    if labels is not None and len(labels) != len(stills):
        raise OpError("OP_FAILED", "contact sheet failed: labels do not match the frames")
    stamped = 0
    created: list = []
    try:
        cols, rows = grid(len(stills), cols)
        tiles = []
        for still in stills:
            img = bpy.data.images.load(still)
            created.append(img)
            w, h = (int(v) for v in img.size)
            buf = array("f", bytes(4 * w * h * CHANNELS))
            img.pixels.foreach_get(buf)
            tiles.append((w, h, buf))
        tile_w, tile_h = tiles[0][0], tiles[0][1]
        if any((w, h) != (tile_w, tile_h) for w, h, _ in tiles):
            raise OpError("OP_FAILED", "contact sheet failed: the stills differ in size")
        sheet_w, sheet_h = cols * tile_w, rows * tile_h
        row_len = tile_w * CHANNELS
        canvas = array("f", bytes(4 * sheet_w * sheet_h * CHANNELS))
        for i, (_, _, buf) in enumerate(tiles):
            top, left = divmod(i, cols)  # reading order, row 0 on top
            base_y = (rows - 1 - top) * tile_h  # image rows are bottom-up
            for y in range(tile_h):
                dst = ((base_y + y) * sheet_w + left * tile_w) * CHANNELS
                canvas[dst:dst + row_len] = buf[y * row_len:(y + 1) * row_len]
            if labels is not None:
                stamped += stamp(canvas, sheet_w, left * tile_w, top * tile_h, tile_w, tile_h, str(labels[i]))
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        sheet = bpy.data.images.new(SHEET_IMAGE, sheet_w, sheet_h, alpha=True)
        created.append(sheet)
        sheet.pixels.foreach_set(canvas)
        sheet.filepath_raw = out
        sheet.file_format = _FORMATS.get(os.path.splitext(out)[1].lower(), "PNG")
        sheet.save()
    except OpError:
        raise
    except Exception as exc:
        raise OpError("OP_FAILED", f"contact sheet failed: {exc}") from exc
    finally:
        _remove(bpy, created)
    if not os.path.isfile(out) or os.path.getsize(out) == 0:
        raise OpError("OP_FAILED", f"Blender composed the contact sheet but wrote no file at {out}")
    return {"path": out, "cols": cols, "rows": rows, "width": sheet_w, "height": sheet_h,
            "size_bytes": os.path.getsize(out), "tiles": len(stills), "labels": stamped}
