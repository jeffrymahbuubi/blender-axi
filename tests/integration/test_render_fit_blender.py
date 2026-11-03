"""Real-Blender checks for `render --fit` (plan U4): tiny WORKBENCH renders, pixels read back from the PNG.

Headless tests run through `cli.main`; the live tests start a REAL headless MCP server on a free port (never 9876) on a
COPY of the scene. Everything lands under .tmp/tests/render_fit/.

ASSUMED A3 outcome: cubes of the default size at x = -6 / +6 are entirely outside the unfitted 8-unit camera view, so the
scene is built with 8-unit cubes (cropped when unfitted). In the fitted render the gap between the cubes is background,
so the centre pixel equals the corners; the proof of framing is "corners are background, both cubes show".
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

STEPS_WIDE = ["add cube --name L --size 8 --location -6,0,0", "add cube --name R --size 8 --location 6,0,0",
              "add camera --name Cam --location 0,-8,4 --rotation 65,0,0"]
SIDE = 64


def axi(capsys, *argv):
    code = cli.main(["--json", *map(str, argv)])
    out = capsys.readouterr()
    assert out.err == ""
    return code, json.loads(out.out)


def read_png(path: Path) -> tuple[int, int, list[list[tuple]]]:
    """Minimal 8-bit PNG decoder (colour types 2 and 6, all five filters) -> (width, height, rows of pixel tuples)."""
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
    return width, height, rows


def corners(path: Path) -> list[tuple]:
    w, h, rows = read_png(path)
    return [rows[0][0], rows[0][w - 1], rows[h - 1][0], rows[h - 1][w - 1]]


def pixel(path: Path, fx: float, fy: float) -> tuple:
    w, h, rows = read_png(path)
    return rows[int(h * fy)][int(w * fx)]


@pytest.fixture(scope="module")
def work():
    d = paths.tmp_root() / "render_fit"
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    return d


@pytest.fixture(scope="module")
def wide(work):
    path = work / "wide.blend"
    steps = work / "wide.json"
    steps.write_text(json.dumps(STEPS_WIDE), encoding="utf-8")
    assert cli.main(["--json", "new", str(path)]) == 0
    assert cli.main(["--json", "apply", str(steps), str(path), "--in-place"]) == 0
    return path


@pytest.fixture(scope="module")
def cube_only(work):
    """A scene with one cube and no camera at all (the `new` empty template)."""
    path = work / "cube_only.blend"
    steps = work / "cube.json"
    steps.write_text(json.dumps(["add cube --name Box"]), encoding="utf-8")
    assert cli.main(["--json", "new", str(path)]) == 0
    assert cli.main(["--json", "apply", str(steps), str(path), "--in-place"]) == 0
    return path


def render_args(blend, out, *extra):
    return ["render", *([blend] if blend is not None else []), "--engine", "WORKBENCH", "--resolution", f"{SIDE}x{SIDE}",
            "--out", out, *extra]


def cam_location(capsys, blend, *extra):
    code, data = axi(capsys, "object", "Cam", *([blend] if blend else []), *extra)
    assert code == 0, data
    return data["location"]


def test_fit_frames_both_cubes_and_leaves_the_file_alone(capsys, work, wide):
    before = (wide.stat().st_mtime_ns, wide.stat().st_size)
    plain, fitted = work / "plain.png", work / "fitted.png"

    code, data = axi(capsys, *render_args(wide, plain))
    assert code == 0, data
    assert "fit" not in data
    plain_corners = corners(plain)
    assert len(set(plain_corners)) > 1  # A3: unfitted, the 8-unit cubes crop the view (a corner shows a cube)

    code, data = axi(capsys, *render_args(wide, fitted, "--fit"))
    assert code == 0, data
    assert data["fit"].startswith("Cam moved 0,-8,4 -> ")
    assert any("--in-place" in h and "set Cam" in h and "--location" in h for h in data["help"])
    fitted_corners = corners(fitted)
    assert len(set(fitted_corners)) == 1  # background in all four corners
    bg = fitted_corners[0]
    assert pixel(fitted, 0.25, 0.5) != bg and pixel(fitted, 0.75, 0.5) != bg  # both cubes are in frame

    assert (wide.stat().st_mtime_ns, wide.stat().st_size) == before
    assert cam_location(capsys, wide) == "0,-8,4"  # render stays read-only


def test_fit_margin_changes_the_distance(capsys, work, wide):
    _, tight = axi(capsys, *render_args(wide, work / "m1.png", "--fit", "--fit-margin", "1", "--force"))
    _, loose = axi(capsys, *render_args(wide, work / "m2.png", "--fit", "--fit-margin", "2", "--force"))
    assert tight["fit"] != loose["fit"]
    code, data = axi(capsys, *render_args(wide, work / "m3.png", "--fit-margin", "2"))
    assert code == 2 and data["code"] == "VALIDATION_ERROR"
    code, data = axi(capsys, *render_args(wide, work / "m3.png", "--fit", "--fit-margin", "0.5"))
    assert code == 2 and data["code"] == "VALIDATION_ERROR"


def test_fit_without_a_camera_uses_a_temporary_one(capsys, work, cube_only):
    out = work / "temp.png"
    before = (cube_only.stat().st_mtime_ns, cube_only.stat().st_size)
    code, data = axi(capsys, *render_args(cube_only, out, "--fit"))
    assert code == 0, data
    assert data["fit"].startswith("temporary camera at ") and "(rotation 63.6,0,46.7)" in data["fit"]
    assert any("add camera" in h and "--in-place" in h for h in data["help"])
    assert len(set(corners(out))) == 1 and pixel(out, 0.5, 0.5) != corners(out)[0]  # the cube is centred in frame
    code, listing = axi(capsys, "objects", cube_only, "--type", "CAMERA")
    assert code == 0 and "AxiFit" not in json.dumps(listing)
    assert (cube_only.stat().st_mtime_ns, cube_only.stat().st_size) == before


def test_no_camera_without_fit_still_fails(capsys, work, cube_only):
    code, data = axi(capsys, *render_args(cube_only, work / "nocam.png"))
    assert code == 1 and data["code"] == "NOT_FOUND" and "no active camera" in data["error"]


def test_fit_on_an_empty_scene_is_an_op_failure(capsys, work, fixtures_dir):
    code, data = axi(capsys, *render_args(fixtures_dir / "empty.blend", work / "empty.png", "--fit"))
    assert code == 1 and data["code"] == "OP_FAILED" and "nothing to fit" in data["error"]
    assert not (work / "empty.png").exists()


@pytest.fixture
def live_wide(blender_info, work, wide):
    copy = work / "live_wide.blend"
    shutil.copy(wide, copy)
    with real_server(blender_info, copy) as (port, _log):
        assert port != 9876
        yield ["--live", "--host", "127.0.0.1", "--port", port]


def test_live_fit_restores_the_camera(capsys, work, live_wide):
    before = cam_location(capsys, None, *live_wide)
    assert before == "0,-8,4"
    out = work / "live_fit.png"
    code, data = axi(capsys, *render_args(None, out, "--fit", *live_wide))
    assert code == 0, data
    assert data["fit"].startswith("Cam moved 0,-8,4 -> ")
    assert any("set Cam --location" in h and "--live" in h and "<file>" not in h for h in data["help"])
    assert len(set(corners(out))) == 1
    assert cam_location(capsys, None, *live_wide) == before  # the open session's camera is back where it was


def test_live_without_a_camera_is_not_found_with_a_hint(capsys, blender_info, work, cube_only):
    copy = work / "live_cube_only.blend"
    shutil.copy(cube_only, copy)
    with real_server(blender_info, copy) as (port, _log):
        live_args = ["--live", "--host", "127.0.0.1", "--port", port]
        code, data = axi(capsys, *render_args(None, work / "live_nocam.png", "--fit", *live_args))
    assert code == 1 and data["code"] == "NOT_FOUND"
    assert any("add camera" in h and "--fit" in h for h in data["help"])
