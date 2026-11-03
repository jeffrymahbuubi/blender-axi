"""`render --sheet`: compose the rendered stills into one contact-sheet image (0.7.0 plan K4, requirements F5).

The stills are loaded as Blender images, their pixels copied tile by tile into one float buffer (Blender rows run
bottom-up, so tile 1 lands top-left and the grid reads left to right, top to bottom), written to a new image and saved
in the format of the output extension. Every image created here is removed again in `finally`. Empty tiles of the last
row stay zero (transparent). Only the standard library is used: `array.array` buffers go straight into
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


def grid(n: int, cols: int | None = None) -> tuple[int, int]:
    """`(cols, rows)` for `n` tiles; `cols` defaults to ceil(sqrt(n))."""
    n = max(1, int(n))
    cols = int(cols) if cols else math.ceil(math.sqrt(n))
    cols = max(1, min(cols, n))
    return cols, math.ceil(n / cols)


def _remove(bpy, images: list) -> None:
    for img in images:
        try:
            bpy.data.images.remove(img)
        except Exception:  # an image that is already gone is not an error here
            pass


def compose(bpy, stills: list[str], out: str, cols: int) -> dict:
    """Write the `stills` (in order) as one grid image at `out`. Returns path, cols, rows, width, height, size_bytes, tiles."""
    if not stills:
        raise OpError("OP_FAILED", "contact sheet failed: no frames to compose")
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
            "size_bytes": os.path.getsize(out), "tiles": len(stills)}
