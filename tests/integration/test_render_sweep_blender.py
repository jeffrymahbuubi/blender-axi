"""Real-Blender checks for the 0.10.0 render changes (plan U2): the sheet grid without empty tiles, `--columns`, the
turntable `--fit` that frames every step (swept corners) and `--transparent` (film transparent + RGBA).

The scene is a thin slab with a cube at its end (`Head`, x = +4): the orbit centre of the auto subject is off the cube, so
the cube swings through the frame. Tiles are rendered at 160x100 with WORKBENCH; a tile whose 1-pixel border is fully
transparent has the whole subject inside the frame. Everything lands under .tmp/tests/render_sweep/.
"""

from __future__ import annotations

import json
import math
import shutil
import struct
import zlib
from pathlib import Path

import pytest

from blender_axi import cli, paths

pytestmark = pytest.mark.blender

TILE_W, TILE_H = 160, 100
RES = f"{TILE_W}x{TILE_H}"


def axi(capsys, *argv):
    code = cli.main(["--json", *map(str, argv)])
    out = capsys.readouterr()
    assert out.err == ""
    return code, json.loads(out.out)


def read_png(path: Path) -> tuple[int, list[list[tuple]]]:
    """Minimal 8-bit PNG decoder (colour types 2 and 6, all five filters) -> (colour type, rows of pixel tuples)."""
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
    return ctype, rows


def clipped_tiles(rows: list[list[tuple]], cols: int, n: int) -> list[int]:
    """1-based numbers of the tiles whose 1-pixel border has a pixel with alpha > 0 (geometry touching the frame edge)."""
    bad = []
    for i in range(n):
        top, left = divmod(i, cols)
        y0, x0 = top * TILE_H, left * TILE_W
        edge = ([rows[y0][x] for x in range(x0, x0 + TILE_W)] + [rows[y0 + TILE_H - 1][x] for x in range(x0, x0 + TILE_W)]
                + [rows[y][x0] for y in range(y0, y0 + TILE_H)] + [rows[y][x0 + TILE_W - 1] for y in range(y0, y0 + TILE_H)])
        if any(px[3] > 0 for px in edge):
            bad.append(i + 1)
    return bad


def fit_distance(text: str, centre) -> float:
    """Distance from `centre` to the camera position in `Camera moved a,b,c -> x,y,z`."""
    return math.dist([float(v) for v in text.split("-> ")[1].split(",")[:3]], centre)


@pytest.fixture(scope="module")
def work():
    d = paths.tmp_root() / "render_sweep"
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    return d


@pytest.fixture(scope="module")
def sweep(work):
    path = work / "sweep.blend"
    for argv in (["new", path], ["add", "cube", path, "--name", "Slab", "--scale", "6,1,0.2", "--in-place"],
                 ["add", "cube", path, "--name", "Head", "--location", "4,0,1", "--in-place"],
                 ["add", "camera", path, "--location", "0,-12,6", "--rotation", "60,0,0", "--in-place"],
                 ["add", "light", path, "--type", "SUN", "--in-place"]):
        assert cli.main(["--json", *map(str, argv)]) == 0
    return path


def sheet_args(blend, out, *extra):
    return ["render", blend, "--engine", "WORKBENCH", "--resolution", RES, "--sheet", "--no-labels",
            "--out", out, "--force", *extra]


def test_eight_tiles_make_a_4x2_sheet_with_transparent_borders(capsys, work, sweep):
    out = work / "sweep.png"
    code, data = axi(capsys, *sheet_args(sweep, out, "--turntable", "8", "--fit", "--subject", "Head", "--transparent"))
    assert code == 0, data
    assert data["sheet"].startswith("4x2 tiles, 640x200, ") and data["background"] == "transparent"
    ctype, rows = read_png(out)
    assert ctype == 6 and (len(rows[0]), len(rows)) == (640, 200)  # RGBA canvas
    # the slab (not the subject) runs out of every tile, so the border check lives in the auto-subject test below
    assert any(px[3] > 0 for row in rows for px in row) and rows[0][0][3] == 0  # geometry exists, background is clear
    plain_code, plain = axi(capsys, "render", sweep, "--engine", "WORKBENCH", "--resolution", RES, "--fit",
                            "--subject", "Head", "--out", work / "plain.png", "--force")
    assert plain_code == 0, plain
    head = (4.0, 0.0, 1.0)
    assert fit_distance(data["fit"], head) > fit_distance(plain["fit"], head)  # the swept fit stands back further


def test_off_centre_subject_stays_in_frame_over_the_whole_orbit(capsys, work, sweep):
    """Auto subject = slab + head: the orbit centre is between them, the head swings out to the far side."""
    out = work / "auto.png"
    code, data = axi(capsys, *sheet_args(sweep, out, "--turntable", "8", "--fit", "--transparent"))
    assert code == 0, data
    assert data["sheet"].startswith("4x2 tiles, 640x200, ")
    _, rows = read_png(out)
    # 0.9.0 (first-view fit): tiles 3 and 7 clipped, `fit: Camera moved 0,-12,6 -> 0,-17.57,8.248`; now 0,-18.015,7.212
    assert clipped_tiles(rows, 4, 8) == []


def test_columns_overrides_the_grid(capsys, work, sweep):
    out = work / "cols3.png"
    code, data = axi(capsys, *sheet_args(sweep, out, "--turntable", "8", "--fit", "--columns", "3"))
    assert code == 0, data
    assert data["sheet"].startswith("3x3 tiles, 480x300, ")
    ctype, rows = read_png(out)
    assert (len(rows[0]), len(rows)) == (480, 300)


def test_default_sheet_of_eight_has_no_empty_tile(capsys, work, sweep):
    out = work / "eight.png"
    code, data = axi(capsys, *sheet_args(sweep, out, "--turntable", "8", "--fit"))
    assert code == 0, data
    assert data["sheet"].startswith("4x2 tiles, 640x200, ")
    _, rows = read_png(out)
    for i in (0, 7):  # first and last tile both carry the (opaque) background
        top, left = divmod(i, 4)
        assert rows[top * TILE_H + 50][left * TILE_W + 80][3] == 255


def test_transparent_png_has_alpha_zero_background_and_opaque_subject(capsys, work, sweep):
    out = work / "t.png"
    code, data = axi(capsys, "render", sweep, "--engine", "WORKBENCH", "--resolution", "64x64", "--fit", "--transparent",
                     "--out", out, "--force")
    assert code == 0, data
    assert data["background"] == "transparent" and list(data).index("background") == list(data).index("engine") + 1
    ctype, rows = read_png(out)
    assert ctype == 6 and rows[0][0] == (0, 0, 0, 0)
    assert sum(1 for row in rows for px in row if px[3] == 255) > 100  # the subject is opaque


def test_a_world_background_is_opaque_without_transparent_and_clear_with_it(capsys, work, sweep):
    """The file has a world (the `sweep` scene has none, and then the RGBA render is clear anyway)."""
    blend = work / "sweepw.blend"
    shutil.copy(sweep, blend)
    code, data = axi(capsys, "exec", blend, "--allow-exec", "--in-place", "--code",
                     "w = bpy.data.worlds.new('W'); bpy.context.scene.world = w; "
                     "w.use_nodes = False; w.color = (0.5, 0.6, 0.75); result = {'world': w.name}")
    assert code == 0, data
    solid, clear = work / "w_solid.png", work / "w_clear.png"
    common = ["render", blend, "--engine", "WORKBENCH", "--resolution", "64x64", "--fit", "--force"]
    code, data = axi(capsys, *common, "--out", solid)
    assert code == 0 and "background" not in data, data
    code, data = axi(capsys, *common, "--transparent", "--out", clear)
    assert code == 0 and data["background"] == "transparent", data
    assert read_png(solid)[1][0][0][3] == 255 and read_png(clear)[1][0][0] == (0, 0, 0, 0)
    code, state = axi(capsys, "exec", blend, "--allow-exec", "--code", "result = {'ft': bpy.context.scene.render.film_transparent}")
    assert code == 0 and state["result"]["ft"] is False  # the file is never changed by a render


def test_transparent_webp_keeps_its_alpha(capsys, work, sweep):
    """ASSUMED A6: WEBP with RGBA keeps the alpha channel (read back through Blender: 4 channels, corner alpha 0)."""
    out = work / "t.webp"
    code, data = axi(capsys, "render", sweep, "--engine", "WORKBENCH", "--resolution", "64x64", "--fit", "--transparent",
                     "--out", out, "--force")
    assert code == 0, data
    code, res = axi(capsys, "exec", sweep, "--allow-exec", "--code",
                    f"img = bpy.data.images.load({str(out)!r}); result = {{'channels': img.channels, 'alpha': img.pixels[3]}}")
    assert code == 0, res
    assert res["result"]["channels"] == 4 and res["result"]["alpha"] == 0.0


def test_transparent_is_refused_for_jpg_before_blender_starts(capsys, work, sweep):
    code, data = axi(capsys, "render", sweep, "--transparent", "--out", work / "x.jpg")
    assert code == 2 and data["code"] == "VALIDATION_ERROR"
    assert not (work / "x.jpg").exists()


STATE = ("r = bpy.context.scene.render; result = {'film': r.film_transparent, 'mode': r.image_settings.color_mode, "
         "'fmt': r.image_settings.file_format, 'engine': r.engine}")


def test_live_transparent_render_restores_film_and_colour_mode(capsys, blender_info, work, sweep):
    """The live session (a REAL headless MCP server on a free port, never 9876) keeps its film and colour mode: they are in
    the render snapshot. This is the headless twin of the user's GUI check G1."""
    from test_live_optional import real_server

    copy = work / "live_sweep.blend"
    shutil.copy(sweep, copy)
    with real_server(blender_info, copy) as (port, _log):
        assert port != 9876
        live = ["--live", "--host", "127.0.0.1", "--port", port]
        code, _ = axi(capsys, "exec", *live, "--allow-exec", "--code",  # a world, so only the film switch makes the corner clear
                      "w = bpy.data.worlds.new('W'); bpy.context.scene.world = w; w.use_nodes = False; "
                      "w.color = (0.5, 0.6, 0.75); result = {'world': w.name}")
        assert code == 0
        code, before = axi(capsys, "exec", *live, "--allow-exec", "--code", STATE)
        assert code == 0 and before["result"]["film"] is False
        out = work / "live_t.png"
        code, data = axi(capsys, "render", *live, "--engine", "WORKBENCH", "--resolution", "64x64", "--fit", "--transparent",
                         "--out", out, "--force")
        assert code == 0 and data["background"] == "transparent", data
        _, rows = read_png(out)
        assert rows[0][0] == (0, 0, 0, 0)
        code, after = axi(capsys, "exec", *live, "--allow-exec", "--code", STATE)
        assert code == 0 and after["result"] == before["result"]  # film_transparent False, colour mode and engine back
