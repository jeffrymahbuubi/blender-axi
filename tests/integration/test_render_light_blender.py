"""Real-Blender checks for the `render` unlit warning and `--auto-light` (0.6.0 plan U2): tiny EEVEE renders, pixels
read back from the PNG. Headless tests run through `cli.main`; the live test starts a REAL headless MCP server on a free
port (never 9876) on a COPY of the scene. Everything lands under .tmp/tests/light/.

ASSUMED A8 outcome: EEVEE renders a cube lit by the temporary Sun + grey fill at 16x16 in a headless run (see the
pixel assertions); the unlit render of the same scene is black at the centre.
ASSUMED A7 outcome: in the headless MCP session `unsaved_changes` stays False across `render --auto-light --live`
(asserted below); a GUI session may differ (Q13: documented, the datablocks are always gone).
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

SIDE = 16
UNLIT_WARNING = "unlit scene: no light, no world background and no emissive material; EEVEE renders black"
EXEC_STATE = ("result = {'worlds': len(bpy.data.worlds), 'world': bpy.context.scene.world is None, "
              "'lights': [l.name for l in bpy.data.lights], 'objects': sorted(o.name for o in bpy.data.objects)}")


def axi(capsys, *argv):
    code = cli.main(["--json", *map(str, argv)])
    out = capsys.readouterr()
    assert out.err == ""
    return code, json.loads(out.out)


def centre_pixel(path: Path) -> tuple:
    """Centre pixel of an 8-bit PNG (colour types 2 and 6, all five filters)."""
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos, idat = 8, b""
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
    raw, stride, prev, line = zlib.decompress(idat), width * bpp, bytearray(width * bpp), bytearray()
    for y in range(height // 2 + 1):
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
        prev = line
    x = width // 2
    return tuple(line[x * bpp:x * bpp + bpp])


def is_black(px: tuple) -> bool:
    return max(px[:3]) < 8


@pytest.fixture(scope="module")
def work():
    d = paths.tmp_root() / "light"
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
def unlit(work, fixtures_dir):
    """The empty fixture plus one cube: no camera, no light, no world."""
    path = work / "unlit.blend"
    shutil.copy(fixtures_dir / "empty.blend", path)
    steps = work / "unlit.json"
    steps.write_text(json.dumps(["add cube --name Box"]), encoding="utf-8")
    assert cli.main(["--json", "apply", str(steps), str(path), "--in-place"]) == 0
    return path


@pytest.fixture(scope="module")
def unlit_cam(work):
    """A `new` file with a cube and a camera (live renders cannot use a temporary camera): still no light, no world."""
    return build(work, "unlit_cam", ["add cube --name Box", "add camera --name Cam --location 7,-7,5 --rotation 63.6,0,46.7"])


def render_args(blend, out, *extra):
    return ["render", *([blend] if blend is not None else []), "--engine", "EEVEE", "--resolution", f"{SIDE}x{SIDE}",
            "--out", out, "--force", *extra]


def state(capsys, blend, *extra):
    code, data = axi(capsys, "exec", *([blend] if blend else []), *extra, "--allow-exec", "--code", EXEC_STATE)
    assert code == 0, data
    return data["result"]


def test_unlit_scene_warns_and_renders_black(capsys, work, unlit):
    before = (unlit.stat().st_mtime_ns, unlit.stat().st_size)
    out = work / "unlit.png"
    code, data = axi(capsys, *render_args(unlit, out, "--fit"))
    assert code == 0, data
    assert data["warning"] == UNLIT_WARNING
    assert "auto_light" not in data
    assert any("--auto-light" in h and "add light" in h and "--in-place" in h for h in data["help"])
    assert is_black(centre_pixel(out)), centre_pixel(out)
    assert (unlit.stat().st_mtime_ns, unlit.stat().st_size) == before


def test_auto_light_lights_the_render_and_leaves_nothing_behind(capsys, work, unlit):
    before = (unlit.stat().st_mtime_ns, unlit.stat().st_size)
    out = work / "auto.png"
    code, data = axi(capsys, *render_args(unlit, out, "--fit", "--auto-light"))
    assert code == 0, data
    assert "warning" not in data and data["auto_light"] == "temporary Sun + world fill (removed after)"
    assert not is_black(centre_pixel(out)), centre_pixel(out)  # A8
    assert not any("--auto-light" in h for h in data["help"])
    code, listing = axi(capsys, "objects", unlit)
    assert code == 0 and "AxiSun" not in json.dumps(listing) and "AxiFit" not in json.dumps(listing)
    assert (unlit.stat().st_mtime_ns, unlit.stat().st_size) == before
    # a render never saves: the file still has no world and no light
    assert state(capsys, unlit) == {"worlds": 0, "world": True, "lights": [], "objects": ["Box"]}


def test_workbench_needs_no_light(capsys, work, unlit):
    out = work / "wb.png"
    code, data = axi(capsys, "render", unlit, "--engine", "WORKBENCH", "--resolution", f"{SIDE}x{SIDE}", "--fit",
                     "--out", out, "--force")
    assert code == 0 and "warning" not in data and "auto_light" not in data, data
    code, data = axi(capsys, "render", unlit, "--engine", "WORKBENCH", "--resolution", f"{SIDE}x{SIDE}", "--fit",
                     "--auto-light", "--out", out, "--force")
    assert code == 0 and "warning" not in data and data["auto_light"] == "not needed (scene is lit)", data


def test_a_lit_scene_makes_auto_light_a_noop(capsys, work, unlit):
    lit = work / "lit.blend"
    shutil.copy(unlit, lit)
    assert axi(capsys, "add", "light", lit, "--type", "SUN", "--in-place")[0] == 0
    code, data = axi(capsys, *render_args(lit, work / "lit.png", "--fit", "--auto-light"))
    assert code == 0 and data["auto_light"] == "not needed (scene is lit)" and "warning" not in data, data
    code, data = axi(capsys, *render_args(lit, work / "lit.png", "--fit"))
    assert code == 0 and "warning" not in data and not any("--auto-light" in h for h in data["help"])


def test_auto_light_with_fit_removes_the_temporary_camera_and_sun(capsys, work, unlit):
    code, data = axi(capsys, *render_args(unlit, work / "both.png", "--fit", "--auto-light"))
    assert code == 0 and data["fit"].startswith("temporary camera at "), data
    code, listing = axi(capsys, "objects", unlit)
    assert code == 0
    text = json.dumps(listing)
    assert "AxiSun" not in text and "AxiFit" not in text and "Box" in text


@pytest.fixture
def live_unlit(blender_info, work, unlit_cam):
    copy = work / "live_unlit.blend"
    shutil.copy(unlit_cam, copy)
    with real_server(blender_info, copy) as (port, _log):
        assert port != 9876
        yield ["--live", "--host", "127.0.0.1", "--port", port]


def test_live_auto_light_leaves_the_session_as_found(capsys, work, live_unlit):
    code, before = axi(capsys, "scene", *live_unlit)
    assert code == 0 and before["camera"] == "Cam"
    assert state(capsys, None, *live_unlit) == {"worlds": 0, "world": True, "lights": [], "objects": ["Box", "Cam"]}

    out = work / "live_auto.png"
    code, data = axi(capsys, *render_args(None, out, "--auto-light", *live_unlit))
    assert code == 0, data
    assert data["auto_light"] == "temporary Sun + world fill (removed after)" and "warning" not in data
    assert not is_black(centre_pixel(out)), centre_pixel(out)

    code, listing = axi(capsys, "objects", *live_unlit)
    assert code == 0 and "AxiSun" not in json.dumps(listing)
    assert state(capsys, None, *live_unlit) == {"worlds": 0, "world": True, "lights": [], "objects": ["Box", "Cam"]}
    code, after = axi(capsys, "scene", *live_unlit)
    assert code == 0
    for key in set(before) - {"duration_ms", "help"}:  # includes unsaved_changes (A7: False -> False in the headless session)
        assert after[key] == before[key], key

    code, plain = axi(capsys, *render_args(None, work / "live_plain.png", *live_unlit))
    assert code == 0 and "unlit scene" in plain["warning"]
    assert any("add light --type SUN --live" in h and "<file>" not in h for h in plain["help"])
