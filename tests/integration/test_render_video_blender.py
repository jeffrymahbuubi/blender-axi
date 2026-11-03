"""Real-Blender checks for `render --frames a..b --out x.mp4` and `render --sheet` (0.7.0 plan U2): tiny WORKBENCH renders,
the mp4 frame count read from its `stsz` box, the contact sheet compared pixel by pixel with the stills kept by
`--keep-tmp`. Headless tests run through `cli.main`; the live test starts a REAL headless MCP server on a free port (never
9876) on a COPY of the scene. Everything lands under .tmp/tests/video/.

ASSUMED A3 outcome: the sheet's tile 1 equals the first still within 1/255 per channel (byte PNG -> pixels -> new image ->
PNG round-trips without a colour-space shift): asserted for the first and the last tile, no colour-space fix was needed.
ASSUMED A5 outcome: `Image.save()` honours `file_format` for JPEG as for PNG (the file starts with the JPEG magic bytes).
0.8.0 plan U3 (K5, K6, A5): every tile carries its number in a black box with white digits (asserted on the PNG: the box
corner is pure 0,0,0 and the box holds pure 255,255,255 pixels), and `--out sheet.<ext>` is verified by magic bytes for
jpg, webp, exr, tif, bmp and tga (the other formats are not decoded here).
"""

from __future__ import annotations

import json
import shutil
import struct
import zlib
from pathlib import Path

import pytest

from blender_axi import cli, paths
from test_live_optional import real_server

pytestmark = pytest.mark.blender

SIDE = 48
STEPS_WIDE = ["add cube --name L --size 3 --location -4,0,0", "add cube --name R --size 3 --location 4,0,0",
              "material L --name Red --color 1,0,0",
              "add camera --name Cam --location 0,-10,5 --rotation 65,0,0", "add light --type SUN"]
EXEC_STATE = ("result = {'scenes': len(bpy.data.scenes), 'images': sorted(i.name for i in bpy.data.images if i.name != 'Render Result'), "
              "'objects': sorted(o.name for o in bpy.data.objects), 'frame': bpy.context.scene.frame_current}")


def axi(capsys, *argv):
    code = cli.main(["--json", *map(str, argv)])
    out = capsys.readouterr()
    assert out.err == ""
    return code, json.loads(out.out)


def read_png(path: Path) -> list[list[tuple]]:
    """Minimal 8-bit PNG decoder (colour types 2 and 6, all five filters) -> rows of pixel tuples."""
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos, idat, width = 8, b"", 0
    while pos < len(data):
        (length,), kind = struct.unpack(">I", data[pos:pos + 4]), data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        if kind == b"IHDR":
            width, height, depth, ctype = struct.unpack(">IIBB", body[:10])
            assert depth == 8 and ctype in (2, 6), (depth, ctype)
        elif kind == b"IDAT":
            idat += body
        pos += 12 + length
    bpp = 3 if ctype == 2 else 4
    raw, stride, rows, prev = zlib.decompress(idat), width * bpp, [], bytearray(width * bpp)
    for y in range(height):
        ft, line = raw[y * (stride + 1)], bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        for i in range(stride):
            a = line[i - bpp] if i >= bpp else 0
            b, c = prev[i], (prev[i - bpp] if i >= bpp else 0)
            if ft == 1:
                line[i] = (line[i] + a) & 255
            elif ft == 2:
                line[i] = (line[i] + b) & 255
            elif ft == 3:
                line[i] = (line[i] + (a + b) // 2) & 255
            elif ft == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        rows.append([tuple(line[x * bpp:x * bpp + bpp]) for x in range(width)])
        prev = line
    return rows


def mp4_frames(path: Path) -> int:
    """Sample count of the first track: the `stsz` box holds version/flags, sample size, sample count."""
    blob = path.read_bytes()
    i = blob.find(b"stsz")
    assert i > 0, "no stsz box"
    return struct.unpack(">I", blob[i + 12:i + 16])[0]


def tile(rows, col: int, row: int):
    """The SIDE x SIDE tile at (col, row) of a sheet, first three channels only."""
    return [[px[:3] for px in r[col * SIDE:(col + 1) * SIDE]] for r in rows[row * SIDE:(row + 1) * SIDE]]


def max_diff(a, b) -> int:
    return max(abs(x - y) for ra, rb in zip(a, b) for pa, pb in zip(ra, rb) for x, y in zip(pa, pb))


LABEL_ROWS, LABEL_COLS = 7, 5  # the number box of a 48 px (or 32 px) tile: (4 * digits + 1) x 7 pixels at scale 1


def assert_labelled(t) -> None:
    """A tile's top-left box is black (corner) and holds white pixels (the digit), within the first 7 rows x 8 columns."""
    assert t[0][0] == (0, 0, 0), t[0][0]
    box = [px for r in t[:LABEL_ROWS] for px in r[:8]]
    assert any(px == (255, 255, 255) for px in box), box


@pytest.fixture(scope="module")
def work():
    d = paths.tmp_root() / "video"
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    return d


@pytest.fixture(scope="module")
def wide(work):
    path = work / "wide.blend"
    steps_file = work / "wide.json"
    steps_file.write_text(json.dumps(STEPS_WIDE), encoding="utf-8")
    assert cli.main(["--json", "new", str(path)]) == 0
    assert cli.main(["--json", "apply", str(steps_file), str(path), "--in-place"]) == 0
    assert cli.main(["--json", "configure", str(path), "--fps", "24", "--frames", "1..6", "--in-place"]) == 0
    return path


def render_args(blend, out, *extra, engine="WORKBENCH"):
    return ["render", *([blend] if blend is not None else []), "--engine", engine, "--resolution", f"{SIDE}x{SIDE}",
            "--out", out, "--force", *extra]


def leftover_run_dirs():
    runs = paths.tmp_root() / "runs"
    return sorted(p.name for p in runs.glob("frames-*")) + sorted(p.name for p in runs.glob("turntable-*")) if runs.is_dir() else []


def test_range_mp4_uses_the_scene_fps_and_cleans_up(capsys, work, wide):
    out = work / "clip.mp4"
    before_file = (wide.stat().st_mtime_ns, wide.stat().st_size)
    runs_before = leftover_run_dirs()
    code, data = axi(capsys, *render_args(wide, out, "--frames", "1..6"))
    assert code == 0, data
    assert data["rendered"] == "6 frames -> 1 video" and data["output"].endswith("clip.mp4")
    assert data["video"].startswith("H264 24 fps, "), data  # the scene fps (configure --fps 24)
    assert any("--frames <a..b> --out <clip.mp4>" in h for h in data["help"])
    blob = out.read_bytes()
    assert len(blob) > 500 and blob[4:8] == b"ftyp" and mp4_frames(out) == 6
    assert not list(work.glob("frame_*")) and not list(work.glob("*.png"))  # the stills lived in the run dir
    assert leftover_run_dirs() == runs_before
    assert (wide.stat().st_mtime_ns, wide.stat().st_size) == before_file


def test_range_mp4_fps_flag_wins(capsys, work, wide):
    out = work / "clip12.mp4"
    code, data = axi(capsys, *render_args(wide, out, "--frames", "2..4", "--fps", "12"))
    assert code == 0 and data["video"].startswith("H264 12 fps, "), data
    assert mp4_frames(out) == 3
    code, data = axi(capsys, "render", wide, "--frames", "2..4", "--out", out)  # exists, no --force
    assert code == 1 and data["code"] == "WRITE_REFUSED"


def test_turntable_sheet_matches_the_stills(capsys, work, wide):
    out = work / "sheet.png"
    runs_before = set(leftover_run_dirs())
    code, data = axi(capsys, "--keep-tmp", *render_args(wide, out, "--turntable", "6", "--fit", "--sheet"))
    assert code == 0, data
    kept = [p for p in leftover_run_dirs() if p not in runs_before]
    try:
        assert data["rendered"] == "6 frames -> 1 sheet" and data["output"].endswith("sheet.png")
        assert data["sheet"].startswith("3x2 tiles, 144x96, "), data
        assert any("--turntable 12 --fit --sheet --out <sheet.png>" in h for h in data["help"])
        assert "video" not in data and not list(work.glob("turn_*"))
        sheet = read_png(out)
        assert len(sheet) == 2 * SIDE and len(sheet[0]) == 3 * SIDE
        assert len(kept) == 1
        stills = sorted((paths.tmp_root() / "runs" / kept[0]).glob("turn_*.png"))
        assert len(stills) == 6
        # A3: tile 1 (top-left) and tile 6 (bottom-right) equal their stills within 1/255 per channel, below the label box
        # (0.8.0: the number sits in the tile's top-left corner, 5 x 7 pixels at this size; the rows under it are the still)
        below = LABEL_ROWS
        assert max_diff(tile(sheet, 0, 0)[below:], [[px[:3] for px in r] for r in read_png(stills[0])][below:]) <= 1
        assert max_diff(tile(sheet, 2, 1)[below:], [[px[:3] for px in r] for r in read_png(stills[5])][below:]) <= 1
        centre = SIDE // 2  # the centre pixel keeps equalling the still's
        assert max_diff([[tile(sheet, 0, 0)[centre][centre]]], [[read_png(stills[0])[centre][centre][:3]]]) <= 1
        assert max_diff(tile(sheet, 0, 0)[below:], tile(sheet, 1, 0)[below:]) > 0  # the views differ: not one tile repeated
        # A5 (png): every tile's top-left is a pure black box with white digit pixels inside it
        for col, row in ((c, r) for r in range(2) for c in range(3)):
            assert_labelled(tile(sheet, col, row))
    finally:
        for name in kept:
            shutil.rmtree(paths.tmp_root() / "runs" / name, ignore_errors=True)


def test_range_sheet_has_a_two_by_two_grid_and_an_empty_fourth_tile(capsys, work, wide):
    out = work / "rsheet.png"
    code, data = axi(capsys, *render_args(wide, out, "--frames", "1..3", "--sheet"))
    assert code == 0, data
    assert data["rendered"] == "3 frames -> 1 sheet" and data["sheet"].startswith("2x2 tiles, 96x96, ")
    assert any("--frames <a..b> --sheet --out <sheet.png>" in h for h in data["help"])
    sheet = read_png(out)
    assert all(px[3] == 0 for r in sheet[SIDE:] for px in r[SIDE:])  # the unused tile stays transparent: no label either
    assert leftover_run_dirs() == []


def test_range_sheet_numbers_every_rendered_tile(capsys, work, wide):  # 0.8.0 U3: 3 labels for --frames 3..5
    out = work / "rsheet35.png"
    code, data = axi(capsys, *render_args(wide, out, "--frames", "3..5", "--sheet"))
    assert code == 0, data
    assert data["rendered"] == "3 frames -> 1 sheet" and data["sheet"].startswith("2x2 tiles, 96x96, ")
    sheet = read_png(out)
    labelled = [assert_labelled([[px[:3] for px in r] for r in tile_rgba(sheet, c, r)]) for c, r in ((0, 0), (1, 0), (0, 1))]
    assert len(labelled) == 3
    t4 = tile_rgba(sheet, 1, 1)
    assert all(px[3] == 0 for r in t4 for px in r)  # the empty fourth tile carries no label
    # frames 3, 4 and 5 are different digits: the three label boxes are not all identical
    boxes = [[[px[:3] for px in r[:LABEL_COLS]] for r in tile_rgba(sheet, c, r_)[:LABEL_ROWS]] for c, r_ in ((0, 0), (1, 0), (0, 1))]
    assert boxes[0] != boxes[1] and boxes[1] != boxes[2] and boxes[0] != boxes[2]


def tile_rgba(rows, col: int, row: int):
    return [list(r[col * SIDE:(col + 1) * SIDE]) for r in rows[row * SIDE:(row + 1) * SIDE]]


@pytest.mark.parametrize("ext, magic", [("jpg", b"\xff\xd8\xff"), ("webp", b"RIFF"), ("exr", b"v/1\x01"), ("tif", b"II*\x00"),
                                        ("bmp", b"BM"), ("tga", b"\x00\x00\x0a")])
def test_sheet_formats_write_the_right_container(capsys, work, wide, ext, magic):  # 0.8.0 K6 / F4
    out = work / f"fmt.{ext}"
    runs_before = leftover_run_dirs()
    code, data = axi(capsys, "render", wide, "--engine", "WORKBENCH", "--resolution", "32x32", "--out", out, "--force",
                     "--frames", "1..2", "--sheet")
    assert code == 0, data
    assert data["rendered"] == "2 frames -> 1 sheet" and data["output"].endswith(f"fmt.{ext}") and data["sheet"].startswith("2x1 tiles, 64x32, ")
    blob = out.read_bytes()
    assert blob.startswith(magic), blob[:8]
    assert leftover_run_dirs() == runs_before and not list(work.glob("frame_*"))


def test_sheet_in_jpeg_is_a_real_jpeg(capsys, work, wide):  # A5
    out = work / "sheet.jpg"
    code, data = axi(capsys, *render_args(wide, out, "--frames", "1..4", "--sheet"))
    assert code == 0, data
    assert out.read_bytes()[:3] == b"\xff\xd8\xff"


def test_validation_errors_exit_2(capsys, work, wide):
    for argv, text in ((["--frame", "3", "--out", work / "x.mp4"], "needs --frames <a..b> or --turntable N"),
                       (["--sheet", "--out", work / "x.mp4"], "--sheet needs --frames <a..b> or --turntable N"),
                       (["--frames", "1..3", "--sheet", "--out", work / "x.mp4"], "--sheet writes one image grid"),
                       (["--turntable", "3", "--sheet", "--out", work / "x.mp4"], "--sheet writes one image grid")):
        code, data = axi(capsys, "render", wide, *argv)
        assert code == 2 and data["code"] == "VALIDATION_ERROR" and text in data["error"], data
    assert not list(work.glob("x.*"))


@pytest.fixture
def live_wide(blender_info, work, wide):
    copy = work / "live_wide.blend"
    shutil.copy(wide, copy)
    with real_server(blender_info, copy) as (port, _log):
        assert port != 9876
        yield ["--live", "--host", "127.0.0.1", "--port", port]


def test_live_range_mp4_and_sheet_restore_the_session(capsys, work, live_wide):
    code, before = axi(capsys, "exec", *live_wide, "--allow-exec", "--code", EXEC_STATE)
    assert code == 0 and before["result"]["scenes"] == 1
    runs_before = leftover_run_dirs()
    out = work / "live.mp4"
    code, data = axi(capsys, *render_args(None, out, "--frames", "1..3", *live_wide))
    assert code == 0, data
    assert data["rendered"] == "3 frames -> 1 video" and data["video"].startswith("H264 24 fps, ")
    assert out.read_bytes()[4:8] == b"ftyp" and mp4_frames(out) == 3
    assert any("--live" in h and "to render a range" in h for h in data["help"])
    sheet = work / "live_sheet.png"
    code, data = axi(capsys, *render_args(None, sheet, "--turntable", "4", "--fit", "--sheet", *live_wide))
    assert code == 0, data
    assert data["sheet"].startswith("2x2 tiles, 96x96, ") and len(read_png(sheet)) == 2 * SIDE
    code, after = axi(capsys, "exec", *live_wide, "--allow-exec", "--code", EXEC_STATE)
    assert code == 0 and after["result"] == before["result"]  # frame, scenes, objects, and no AxiSheet / still images left
    assert leftover_run_dirs() == runs_before
