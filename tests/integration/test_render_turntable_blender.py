"""Real-Blender checks for `render --turntable` (0.6.0 plan U5): tiny WORKBENCH/EEVEE renders, pixels read back from the
PNGs, an mp4 encoded through the temporary VSE scene. Headless tests run through `cli.main`; the live test starts a REAL
headless MCP server on a free port (never 9876) on a COPY of the scene. Everything lands under .tmp/tests/turntable/.

ASSUMED A2 outcome: an orbit keeps the camera-to-centre distance, so the `--fit` framing survives every step (asserted:
every one of the 4 frames shows geometry that differs from the background). ASSUMED A9 outcome: the live Blender writes
the stills into the CLI-made run dir (the live test writes the mp4 from them).
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
STEPS_UNLIT = ["add cube --name L --size 3 --location -4,0,0", "add cube --name R --size 3 --location 4,0,0",
               "add camera --name Cam --location 0,-10,5 --rotation 65,0,0"]
EXEC_STATE = ("result = {'scenes': len(bpy.data.scenes), 'worlds': len(bpy.data.worlds), "
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


def geometry_pixels(path: Path) -> int:
    """Pixels that differ from the background (the top-left corner)."""
    rows = read_png(path)
    bg = rows[0][0]
    return sum(1 for row in rows for px in row if px != bg)


@pytest.fixture(scope="module")
def work():
    d = paths.tmp_root() / "turntable"
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    return d


def build(work: Path, name: str, steps: list[str]) -> Path:
    path = work / f"{name}.blend"
    steps_file = work / f"{name}.json"
    steps_file.write_text(json.dumps(steps), encoding="utf-8")
    assert cli.main(["--json", "new", str(path)]) == 0
    assert cli.main(["--json", "apply", str(steps_file), str(path), "--in-place"]) == 0
    return path


@pytest.fixture(scope="module")
def wide(work):
    return build(work, "wide", STEPS_WIDE)


@pytest.fixture(scope="module")
def unlit(work):
    return build(work, "unlit", STEPS_UNLIT)


def render_args(blend, out, *extra, engine="WORKBENCH"):
    return ["render", *([blend] if blend is not None else []), "--engine", engine, "--resolution", f"{SIDE}x{SIDE}",
            "--out", out, "--force", *extra]


def cam(capsys, blend, *extra):
    code, data = axi(capsys, "object", "Cam", *([blend] if blend else []), *extra)
    assert code == 0, data
    return data["location"], data["rotation_deg"], data["scale"]


def leftover_run_dirs():
    runs = paths.tmp_root() / "runs"
    return sorted(p.name for p in runs.glob("turntable-*")) if runs.is_dir() else []


def test_png_turntable_orbits_and_restores_everything(capsys, work, wide):
    before_file = (wide.stat().st_mtime_ns, wide.stat().st_size)
    before_cam = cam(capsys, wide)
    code, data = axi(capsys, *render_args(wide, work / "t_####.png", "--turntable", "4", "--fit"))
    assert code == 0, data
    assert data["rendered"] == "4 frames" and data["output"].endswith("t_####.png")
    assert data["turntable"].startswith("4 steps around ") and data["turntable"].endswith("(Cam)")
    assert data["fit"].startswith("Cam moved 0,-10,5 -> ") and "video" not in data
    assert any("--turntable 12 --fit --out <dir/turn_####.png>" in h for h in data["help"])
    frames = [work / f"t_{i:04d}.png" for i in (1, 2, 3, 4)]
    assert all(f.is_file() for f in frames)
    assert all(geometry_pixels(f) > 20 for f in frames)  # A2: every step still shows the cubes
    middle = [tuple(map(tuple, read_png(f)[SIDE // 2])) for f in frames]
    assert sum(1 for i in range(4) for j in range(i + 1, 4) if middle[i] != middle[j]) >= 3  # the views really differ
    assert cam(capsys, wide) == before_cam
    assert (wide.stat().st_mtime_ns, wide.stat().st_size) == before_file


def test_mp4_turntable_writes_one_video_and_cleans_up(capsys, work, wide):
    out = work / "t.mp4"
    runs_before = leftover_run_dirs()
    code, data = axi(capsys, *render_args(wide, out, "--turntable", "4", "--fit", "--fps", "24"))
    assert code == 0, data
    assert data["rendered"] == "4 frames -> 1 video" and data["output"].endswith("t.mp4")
    assert data["video"].startswith("H264 24 fps, ")
    assert any("--out <turn.mp4>" in h for h in data["help"])
    blob = out.read_bytes()
    assert len(blob) > 500 and blob[4:8] == b"ftyp"
    assert not list(work.glob("turn_*.png")) and not list(out.parent.glob("turn_*"))
    assert leftover_run_dirs() == runs_before
    # a second run without --force is refused before Blender starts
    code, data = axi(capsys, "render", wide, "--turntable", "4", "--out", out)
    assert code == 1 and data["code"] == "WRITE_REFUSED"


def test_turntable_with_auto_light_leaves_nothing_behind(capsys, work, unlit):
    code, data = axi(capsys, *render_args(unlit, work / "a_####.png", "--turntable", "3", "--fit", "--auto-light",
                                          engine="EEVEE"))
    assert code == 0, data
    assert data["auto_light"] == "temporary Sun + world fill (removed after)" and "warning" not in data
    assert data["rendered"] == "3 frames"
    assert all(geometry_pixels(work / f"a_{i:04d}.png") > 20 for i in (1, 2, 3))
    code, listing = axi(capsys, "objects", unlit)
    assert code == 0 and "AxiSun" not in json.dumps(listing) and "AxiFit" not in json.dumps(listing)


def test_turntable_without_a_camera_uses_the_temporary_one(capsys, work, fixtures_dir):
    steps = work / "nocam.json"
    steps.write_text(json.dumps(["add cube --name Box"]), encoding="utf-8")
    blend = work / "nocam.blend"
    assert axi(capsys, "new", blend)[0] == 0
    assert axi(capsys, "apply", steps, blend, "--in-place")[0] == 0
    code, data = axi(capsys, *render_args(blend, work / "n_####.png", "--turntable", "2", "--fit"))
    assert code == 0 and data["fit"].startswith("temporary camera at "), data
    assert data["turntable"].endswith("(AxiFit)")
    code, listing = axi(capsys, "objects", blend, "--type", "CAMERA")
    assert code == 0 and "AxiFit" not in json.dumps(listing)


def test_validation_errors_exit_2(capsys, work, wide):
    for argv, text in ((["--turntable", "1"], "at least 2 steps"), (["--turntable", "3", "--frame", "2"], "drop --frame"),
                       (["--out", work / "v.mp4"], "needs --turntable")):
        code, data = axi(capsys, "render", wide, *argv)
        assert code == 2 and data["code"] == "VALIDATION_ERROR" and text in data["error"], data


@pytest.fixture
def live_wide(blender_info, work, wide):
    copy = work / "live_wide.blend"
    shutil.copy(wide, copy)
    with real_server(blender_info, copy) as (port, _log):
        assert port != 9876
        yield ["--live", "--host", "127.0.0.1", "--port", port]


def test_live_mp4_turntable_restores_the_session(capsys, work, live_wide):
    before = cam(capsys, None, *live_wide)
    code, state_before = axi(capsys, "exec", *live_wide, "--allow-exec", "--code", EXEC_STATE)
    assert code == 0 and state_before["result"]["scenes"] == 1
    out = work / "live.mp4"
    runs_before = leftover_run_dirs()
    code, data = axi(capsys, *render_args(None, out, "--turntable", "3", "--fit", *live_wide))
    assert code == 0, data
    assert data["rendered"] == "3 frames -> 1 video" and data["video"].startswith("H264 12 fps, ")
    assert out.read_bytes()[4:8] == b"ftyp"  # A9: the live Blender wrote the stills into the CLI-made run dir
    assert any("--live" in h and "finer turn" in h for h in data["help"])
    assert cam(capsys, None, *live_wide) == before
    code, state_after = axi(capsys, "exec", *live_wide, "--allow-exec", "--code", EXEC_STATE)
    assert code == 0 and state_after["result"] == state_before["result"]
    assert leftover_run_dirs() == runs_before
