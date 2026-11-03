"""`sheet.compose` / `sheet.grid` (0.7.0 plan U2): tile placement, cleanup and errors with a plain-stub `bpy.data.images`."""

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


@pytest.mark.parametrize("n, cols, rows", [(1, 1, 1), (2, 2, 1), (3, 2, 2), (4, 2, 2), (6, 3, 2), (12, 4, 3), (13, 4, 4)])
def test_grid_is_ceil_sqrt_columns(n, cols, rows):
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
    assert res == {"path": target, "cols": 2, "rows": 1, "width": 4, "height": 2, "size_bytes": 3, "tiles": 2}
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
