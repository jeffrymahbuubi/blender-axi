"""`sheet.compose` / `sheet.grid` (0.7.0 plan U2): tile placement, cleanup and errors with a plain-stub `bpy.data.images`."""

from array import array
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from blender_axi.blender_side import sheet
from blender_axi.blender_side.ops import OpError

WORK = Path(__file__).resolve().parents[2] / ".tmp" / "tests" / "unit_sheet"
W, H = 2, 2  # tile size of the stubs


def tile_pixels(n: int, w: int = W, h: int = H) -> list[float]:
    """Still number n: every pixel's channels are n*100 + pixel index (so each value says where it came from)."""
    return [float(n * 100 + p) for p in range(w * h) for _ in range(4)]


class FakePixels:
    def __init__(self, data=None):
        self.data = list(data) if data is not None else []

    def foreach_get(self, buf):
        buf[:] = type(buf)("f", self.data)

    def foreach_set(self, buf):
        self.data = list(buf)


class FakeImages:
    def __init__(self, sizes=None, fail_save=False, skip_write=False):
        self.loaded, self.new_calls, self.removed, self.sheet = [], [], [], None
        self.sizes, self.fail_save, self.skip_write = sizes or {}, fail_save, skip_write

    def load(self, path):
        n = len(self.loaded) + 1
        w, h = self.sizes.get(n, (W, H))
        img = NS(name=Path(path).name, size=(w, h), pixels=FakePixels(tile_pixels(n, w, h)))
        self.loaded.append(img)
        return img

    def new(self, name, w, h, alpha=False):
        images = self

        def save():
            if images.fail_save:
                raise RuntimeError("disk full")
            if not images.skip_write:
                Path(img.filepath_raw).write_bytes(b"img")

        img = NS(name=name, size=(w, h), alpha=alpha, pixels=FakePixels(), filepath_raw="", file_format="", save=save)
        self.new_calls.append((name, w, h, alpha))
        self.sheet = img
        return img

    def remove(self, img):
        self.removed.append(img)


@pytest.fixture
def bpy(fake_bpy):
    WORK.mkdir(parents=True, exist_ok=True)
    fake_bpy.data.images = FakeImages()
    return fake_bpy


def out(name="s.png") -> str:
    p = WORK / name
    p.unlink(missing_ok=True)
    return str(p)


def rows_of(img, width, height):
    """The sheet as rows of pixel first-channel values, top row first (Blender stores rows bottom-up)."""
    flat = img.pixels.data
    rows = [[flat[(y * width + x) * 4] for x in range(width)] for y in range(height)]
    return rows[::-1]


@pytest.mark.parametrize("n, cols, rows", [(1, 1, 1), (2, 2, 1), (3, 2, 2), (4, 2, 2), (5, 3, 2), (6, 3, 2), (7, 4, 2),
                                           (8, 4, 2), (9, 3, 3), (10, 4, 3), (12, 4, 3), (13, 5, 3), (16, 4, 4), (24, 6, 4)])
def test_grid_has_the_fewest_empty_tiles_at_most_twice_as_wide_as_high(n, cols, rows):
    # n=10: the frozen rule `cols <= 2*rows` rules out 5x2 (5 > 4), so it stays 4x3 with 2 empty tiles (plan §3.3 lists 5x2)
    assert sheet.grid(n) == (cols, rows)


def test_grid_explicit_columns_are_clamped():
    assert sheet.grid(5, 2) == (2, 3) and sheet.grid(3, 9) == (3, 1) and sheet.grid(0) == (1, 1)


def test_tiles_are_placed_in_reading_order_top_left_first(bpy):
    res = sheet.compose(bpy, [f"s{i}.png" for i in (1, 2, 3)], out(), 2)  # 2x2 grid, the 4th tile stays empty
    assert res["cols"] == 2 and res["rows"] == 2 and res["width"] == 4 and res["height"] == 4 and res["tiles"] == 3
    rows = rows_of(bpy.data.images.sheet, 4, 4)
    # top-left tile = still 1, top-right = still 2, bottom-left = still 3, bottom-right = empty (zero);
    # inside a tile the first stored pixels (index 0, 1) are its BOTTOM row, so the visible top row holds 2, 3
    assert rows[0] == [102.0, 103.0, 202.0, 203.0] and rows[1] == [100.0, 101.0, 200.0, 201.0]
    assert rows[2] == [302.0, 303.0, 0.0, 0.0] and rows[3] == [300.0, 301.0, 0.0, 0.0]
    assert bpy.data.images.new_calls == [("AxiSheet", 4, 4, True)]


def test_result_reports_the_written_file(bpy):
    target = out()
    res = sheet.compose(bpy, ["a.png", "b.png"], target, 2)
    assert res == {"path": target, "cols": 2, "rows": 1, "width": 4, "height": 2, "size_bytes": 3, "tiles": 2, "labels": 0}
    assert Path(target).read_bytes() == b"img"


@pytest.mark.parametrize("name, fmt", [("s.png", "PNG"), ("s.jpg", "JPEG"), ("s.JPEG", "JPEG"), ("s.webp", "WEBP"), ("s.tga", "TARGA"),
                                      ("s.unknown", "PNG")])
def test_file_format_follows_the_extension(bpy, name, fmt):
    target = out(name)
    sheet.compose(bpy, ["a.png"], target, 1)
    assert bpy.data.images.sheet.file_format == fmt and bpy.data.images.sheet.filepath_raw == target


def test_every_image_is_removed_afterwards(bpy):
    sheet.compose(bpy, ["a.png", "b.png", "c.png"], out(), 2)
    images = bpy.data.images
    assert len(images.removed) == 4 and set(map(id, images.removed)) == set(map(id, [*images.loaded, images.sheet]))


def test_images_are_removed_when_saving_fails(bpy):
    bpy.data.images = FakeImages(fail_save=True)
    with pytest.raises(OpError) as err:
        sheet.compose(bpy, ["a.png", "b.png"], out(), 2)
    assert err.value.code == "OP_FAILED" and err.value.message == "contact sheet failed: disk full"
    assert len(bpy.data.images.removed) == 3


def test_stills_of_different_sizes_fail_and_are_cleaned_up(bpy):
    bpy.data.images = FakeImages(sizes={2: (3, 2)})
    with pytest.raises(OpError, match="contact sheet failed: the stills differ in size"):
        sheet.compose(bpy, ["a.png", "b.png"], out(), 2)
    assert len(bpy.data.images.removed) == 2 and bpy.data.images.new_calls == []


def test_a_missing_still_is_wrapped(bpy):
    def load(path):
        raise RuntimeError(f"Cannot read '{path}'")

    bpy.data.images.load = load
    with pytest.raises(OpError, match="contact sheet failed: Cannot read 'gone.png'"):
        sheet.compose(bpy, ["gone.png"], out(), 1)


def test_no_file_written_is_an_error(bpy):
    bpy.data.images = FakeImages(skip_write=True)
    with pytest.raises(OpError, match="wrote no file at"):
        sheet.compose(bpy, ["a.png"], out(), 1)
    assert len(bpy.data.images.removed) == 2


def test_no_stills_is_an_error(bpy):
    with pytest.raises(OpError, match="no frames to compose"):
        sheet.compose(bpy, [], out(), 1)


# --- 0.8.0 U3: numbered tiles -------------------------------------------------------------------------------------------

WHITE, BLACK, GREY = (1.0, 1.0, 1.0, 1.0), (0.0, 0.0, 0.0, 1.0), (0.5,) * 4


def blank_canvas(w: int, h: int, value: float = 0.5):
    return array("f", [value] * (w * h * 4))


def px(canvas, sheet_w: int, sheet_h: int, x: int, y_top: int) -> tuple:
    """Pixel at column x, row y_top counted from the TOP of the sheet (the canvas is stored bottom-up)."""
    i = ((sheet_h - 1 - y_top) * sheet_w + x) * 4
    return tuple(canvas[i:i + 4])


@pytest.mark.parametrize("tile_h, scale", [(48, 1), (180, 4), (540, 13), (10, 1), (39, 1), (40, 1), (80, 2)])
def test_label_scale_grows_with_the_tile(tile_h, scale):
    assert sheet.label_scale(tile_h) == scale


def test_digit_font_is_complete_and_five_by_three():
    assert sorted(sheet._DIGITS) == list("0123456789")
    assert all(len(rows) == sheet.GLYPH_H and all(len(r) == sheet.GLYPH_W for r in rows) for rows in sheet._DIGITS.values())
    assert sheet._DIGITS["7"] == ("###", "  #", " # ", " # ", " # ")


def test_stamp_draws_box_and_glyph_at_the_tile_top_left():
    sw, sh = 24, 8  # two 12x8 tiles side by side
    canvas = blank_canvas(sw, sh)
    assert sheet.stamp(canvas, sw, 12, 0, 12, 8, "1") is True  # tile 2's top-left
    # the box is (1*4-1+2) x 7 = 5 x 7 pixels at scale 1: black padding on the border, the glyph one unit in
    assert px(canvas, sw, sh, 12, 0) == BLACK and px(canvas, sw, sh, 16, 6) == BLACK  # box corners
    assert px(canvas, sw, sh, 17, 0) == GREY and px(canvas, sw, sh, 12, 7) == GREY  # outside the box
    # glyph "1": row 0 " # " -> lit at glyph column 1 = x 12 + PAD + 1; row 1 "## "; row 4 "###"
    assert px(canvas, sw, sh, 14, 1) == WHITE and px(canvas, sw, sh, 13, 1) == BLACK and px(canvas, sw, sh, 15, 1) == BLACK
    assert px(canvas, sw, sh, 13, 2) == WHITE and px(canvas, sw, sh, 14, 2) == WHITE and px(canvas, sw, sh, 15, 2) == BLACK
    assert [px(canvas, sw, sh, x, 5) for x in (13, 14, 15)] == [WHITE] * 3
    assert px(canvas, sw, sh, 0, 0) == GREY  # tile 1 untouched


def test_stamp_scales_the_box_with_the_tile():
    canvas = blank_canvas(60, 80)
    assert sheet.stamp(canvas, 60, 0, 0, 60, 80, "7") is True  # tile_h 80 -> scale 2: box 10 x 14
    assert px(canvas, 60, 80, 9, 13) == BLACK and px(canvas, 60, 80, 10, 0) == GREY and px(canvas, 60, 80, 0, 14) == GREY
    assert px(canvas, 60, 80, 2, 2) == WHITE and px(canvas, 60, 80, 5, 3) == WHITE  # the top bar of the 7 is 2x2 blocks
    assert px(canvas, 60, 80, 2, 4) == BLACK  # row 1 of the 7 is "  #": its left unit is dark


def test_stamp_handles_two_digits():
    sw, sh = 40, 10
    canvas = blank_canvas(sw, sh)
    assert sheet.stamp(canvas, sw, 0, 0, 40, 10, "12") is True  # box (2*4-1+2) = 9 wide
    assert px(canvas, sw, sh, 8, 0) == BLACK and px(canvas, sw, sh, 9, 0) == GREY
    assert px(canvas, sw, sh, 6, 1) == WHITE  # the "2" starts at x = PAD + 4 and its top bar is "###"


def test_stamp_skips_a_tile_too_small_and_leaves_the_canvas_alone():
    canvas = blank_canvas(8, 4)
    before = list(canvas)
    assert sheet.stamp(canvas, 8, 0, 0, 4, 4, "1") is False and list(canvas) == before
    assert sheet.stamp(canvas, 8, 0, 0, 8, 4, "") is False and list(canvas) == before


def test_a_non_digit_label_draws_only_the_box():
    canvas = blank_canvas(12, 8)
    assert sheet.stamp(canvas, 12, 0, 0, 12, 8, "x") is True
    assert all(px(canvas, 12, 8, x, y) == BLACK for x in range(5) for y in range(7))


def test_compose_stamps_one_label_per_tile(bpy):
    bpy.data.images = FakeImages(sizes={1: (12, 8), 2: (12, 8)})
    res = sheet.compose(bpy, ["a.png", "b.png"], out(), 2, labels=["1", "2"])
    assert res["labels"] == 2
    canvas = array("f", bpy.data.images.sheet.pixels.data)
    assert px(canvas, 24, 8, 0, 0) == BLACK and px(canvas, 24, 8, 2, 1) == WHITE  # tile 1: the "1" glyph column 1
    assert px(canvas, 24, 8, 12, 0) == BLACK and px(canvas, 24, 8, 13, 1) == WHITE  # tile 2: the "2" top bar starts at its glyph column 0
    assert px(canvas, 24, 8, 11, 7) not in (BLACK, WHITE)  # outside the boxes the stills' own pixels remain


def test_compose_without_labels_changes_no_pixel(bpy):
    sizes = {1: (12, 8), 2: (12, 8)}
    bpy.data.images = FakeImages(sizes=sizes)
    assert sheet.compose(bpy, ["a.png", "b.png"], out(), 2)["labels"] == 0
    plain = list(bpy.data.images.sheet.pixels.data)
    bpy.data.images = FakeImages(sizes=sizes)
    sheet.compose(bpy, ["a.png", "b.png"], out(), 2, labels=["1", "2"])
    labelled = list(bpy.data.images.sheet.pixels.data)
    assert plain != labelled
    bottom = 24 * 4  # the bottom-up row 0 (visual row 7) lies below both 7-row boxes
    assert plain[:bottom] == labelled[:bottom]


def test_compose_counts_only_the_labels_that_fit(bpy):
    res = sheet.compose(bpy, ["a.png", "b.png"], out(), 2, labels=["1", "2"])  # 2x2 tiles are far too small
    assert res["labels"] == 0 and res["tiles"] == 2
    assert bpy.data.images.sheet.pixels.data[0] == 100.0  # untouched


def test_compose_rejects_labels_that_do_not_match_the_frames(bpy):
    with pytest.raises(OpError, match="contact sheet failed: labels do not match the frames"):
        sheet.compose(bpy, ["a.png", "b.png"], out(), 2, labels=["1"])
    assert bpy.data.images.loaded == [] and bpy.data.images.new_calls == []
