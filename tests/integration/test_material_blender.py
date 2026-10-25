"""Real-Blender checks for `material` (v1.2 U3): headless --in-place / --out, create vs reuse, change detection,
the A6 WORKBENCH pixel check, and --live on a private server (never port 9876).

Headless tests run the command through `cli.main` (the registry entry is patched in, so they pass before the lead's
splice). Live tests start a REAL headless MCP server on a free port on a copy of basic.blend.
"""

from __future__ import annotations

import json
import shutil
import struct
import zlib
from pathlib import Path

import pytest

from blender_axi import cli, paths, registry
from test_live_optional import real_server

pytestmark = pytest.mark.blender

MATERIAL = "blender_axi.commands.material:SPEC"


@pytest.fixture(autouse=True)
def _register_material(monkeypatch):
    monkeypatch.setitem(registry.COMMANDS, "material", MATERIAL)


@pytest.fixture
def work():
    d = paths.tmp_root() / "material"
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    return d


@pytest.fixture
def basic(work, fixtures_dir):
    path = work / "basic.blend"
    shutil.copy2(fixtures_dir / "basic.blend", path)
    return path


def axi(capsys, *argv):
    code = cli.main(["--json", *map(str, argv)])
    out = capsys.readouterr()
    assert out.err == ""
    return code, json.loads(out.out)


def state(capsys, blend, obj="Cube", *live):
    """What is really in the file: the material slots of `obj` and the Principled values of each (via exec)."""
    code = (
        "o = bpy.data.objects[%r]\n"
        "rows = []\n"
        "for m in o.data.materials:\n"
        "    n = next(n for n in m.node_tree.nodes if n.type == 'BSDF_PRINCIPLED')\n"
        "    rows.append({'name': m.name, 'base': [round(x, 3) for x in n.inputs['Base Color'].default_value],\n"
        "                 'metallic': round(n.inputs['Metallic'].default_value, 3),\n"
        "                 'roughness': round(n.inputs['Roughness'].default_value, 3),\n"
        "                 'emission': [round(x, 3) for x in n.inputs['Emission Color'].default_value],\n"
        "                 'strength': round(n.inputs['Emission Strength'].default_value, 3),\n"
        "                 'diffuse': [round(x, 3) for x in m.diffuse_color]})\n"
        "result = {'rows': rows}\n" % obj)
    argv = ["exec", *live] if live else ["exec", blend]
    c, data = axi(capsys, *argv, "--allow-exec", "--code", code)
    assert c == 0, data
    return data["result"]["rows"]


# -- headless ---------------------------------------------------------------------------------------------------------

def test_create_and_replace_slot_zero(capsys, basic):
    assert [r["name"] for r in state(capsys, basic)] == ["Material"]  # the factory cube's own material
    code, data = axi(capsys, "material", "Cube", basic, "--name", "Red", "--color", "1,0,0", "--in-place")
    assert code == 0, data
    assert data["created"] is True and data["slots"] == "1 (Red)" and data["material"] == "Red"
    assert data["color"] == "1,0,0,1" and data["modified"] is True and data["saved"].endswith("basic.blend")
    assert data["backup"].endswith("basic.blend1") and "undo" not in data
    assert any("render" in h and "--engine WORKBENCH" in h for h in data["help"])
    rows = state(capsys, basic)
    assert [r["name"] for r in rows] == ["Red"] and rows[0]["base"] == [1, 0, 0, 1] and rows[0]["diffuse"] == [1, 0, 0, 1]


def test_reuse_and_change_detection(capsys, basic):
    axi(capsys, "material", "Cube", basic, "--name", "Red", "--color", "1,0,0", "--in-place")
    code, data = axi(capsys, "material", "Cube", basic, "--name", "Red", "--roughness", "0.2", "--in-place")
    assert code == 0 and data["created"] is False and data["modified"] is True and data["roughness"] == "0.2"
    assert data["color"] == "1,0,0,1" and data["slots"] == "1 (Red)"

    before = (basic.stat().st_mtime_ns, basic.read_bytes())
    code, data = axi(capsys, "material", "Cube", basic, "--name", "Red", "--roughness", "0.2", "--color", "1,0,0",
                     "--in-place")
    assert code == 0 and data["modified"] is False and data["saved"] == "none (no changes)" and "backup" not in data
    assert (basic.stat().st_mtime_ns, basic.read_bytes()) == before


def test_colour_only_and_emission_only_edits_are_saved(capsys, basic):
    axi(capsys, "material", "Cube", basic, "--name", "Red", "--color", "1,0,0", "--in-place")
    code, data = axi(capsys, "material", "Cube", basic, "--name", "Red", "--color", "0,0,1", "--in-place")
    assert code == 0 and data["modified"] is True and data["saved"].endswith("basic.blend")
    assert state(capsys, basic)[0]["base"] == [0, 0, 1, 1]
    code, data = axi(capsys, "material", "Cube", basic, "--name", "Red", "--emission", "1,0.5,0",
                     "--emission-strength", "3", "--in-place")
    assert code == 0 and data["modified"] is True
    row = state(capsys, basic)[0]
    assert row["emission"] == [1, 0.5, 0, 1] and row["strength"] == 3


def test_slot_add_appends_and_replace_keeps_the_others(capsys, basic):
    axi(capsys, "material", "Cube", basic, "--name", "Red", "--color", "1,0,0", "--in-place")
    code, data = axi(capsys, "material", "Cube", basic, "--name", "Blue", "--color", "0,0,1", "--slot", "add", "--in-place")
    assert code == 0 and data["slots"] == "2 (Red, Blue)"
    code, data = axi(capsys, "material", "Cube", basic, "--name", "Green", "--color", "0,1,0", "--in-place")
    assert code == 0 and data["slots"] == "2 (Green, Blue)"
    assert [r["name"] for r in state(capsys, basic)] == ["Green", "Blue"]


def test_metallic_roughness_and_alpha(capsys, basic):
    code, data = axi(capsys, "material", "Cube", basic, "--name", "Steel", "--color", "0.6,0.6,0.65,0.5",
                     "--metallic", "1", "--roughness", "0.3", "--in-place")
    assert code == 0 and data["color"] == "0.6,0.6,0.65,0.5" and data["metallic"] == "1" and data["roughness"] == "0.3"
    row = state(capsys, basic)[0]
    assert row["metallic"] == 1 and row["roughness"] == 0.3 and row["diffuse"] == [0.6, 0.6, 0.65, 0.5]


def test_ball_in_a_collection_and_a_plain_material_name(capsys, basic):
    code, data = axi(capsys, "material", "Ball", basic, "--name", "Rubber", "--in-place")  # no property flags at all
    assert code == 0 and data["created"] is True and data["slots"] == "1 (Rubber)" and data["color"] == "0.8,0.8,0.8,1"


def test_light_camera_and_empty_cannot_take_a_material(capsys, basic):
    before = basic.read_bytes()
    for name, kind in (("Light", "LIGHT"), ("Camera", "CAMERA"), ("Anchor", "EMPTY")):
        code, data = axi(capsys, "material", name, basic, "--name", "X", "--in-place")
        assert code == 1 and data["code"] == "OP_FAILED", data
        assert data["error"] == f"'{name}' ({kind}) cannot take a material"
    assert basic.read_bytes() == before and not basic.with_name("basic.blend1").exists()


def test_unknown_object_is_not_found(capsys, basic):
    before = basic.read_bytes()
    code, data = axi(capsys, "material", "Cub", basic, "--name", "X", "--in-place")
    assert code == 1 and data["code"] == "NOT_FOUND" and "Cube" in data["error"]
    assert any("objects" in h and "basic.blend" in h for h in data["help"])
    assert basic.read_bytes() == before


def test_out_leaves_the_original_untouched(capsys, basic, work):
    before = (basic.stat().st_mtime_ns, basic.read_bytes())
    copy = work / "copy.blend"
    code, data = axi(capsys, "material", "Cube", basic, "--name", "Red", "--color", "1,0,0", "--out", copy)
    assert code == 0 and data["modified"] is True and data["saved"].endswith("copy.blend") and "backup" not in data
    assert (basic.stat().st_mtime_ns, basic.read_bytes()) == before
    assert [r["name"] for r in state(capsys, copy)] == ["Red"] and [r["name"] for r in state(capsys, basic)] == ["Material"]
    assert any("render" in h and "copy.blend" in h for h in data["help"])


def test_neither_flag_is_exit_2_and_writes_nothing(capsys, basic):
    before = (basic.stat().st_mtime_ns, basic.read_bytes())
    code, data = axi(capsys, "material", "Cube", basic, "--name", "Red")
    assert code == 2 and data["code"] == "VALIDATION_ERROR" and "say where to write it" in data["error"]
    assert any("--in-place" in h for h in data["help"]) and any("--out <copy.blend>" in h for h in data["help"])
    assert (basic.stat().st_mtime_ns, basic.read_bytes()) == before and not basic.with_name("basic.blend1").exists()


def test_bad_arguments_exit_2_before_blender(capsys, basic):
    for argv in (["Cube", basic, "--in-place"], ["Cube", basic, "--name", "R", "--color", "2,0,0", "--in-place"],
                 ["Cube", basic, "--name", "R", "--metallic", "1.5", "--in-place"],
                 ["Cube", basic, "--name", "R", "--slot", "all", "--in-place"]):
        code, data = axi(capsys, "material", *argv)
        assert code == 2 and data["code"] == "VALIDATION_ERROR", (argv, data)


# -- A6: WORKBENCH shows the colour headless -------------------------------------------------------------------------

def centre_pixel(png: Path) -> tuple[int, ...]:
    """(r, g, b[, a]) of the middle pixel of an 8-bit non-interlaced PNG, decoded with zlib/struct only."""
    raw = png.read_bytes()
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"
    pos, idat, header = 8, b"", None
    while pos < len(raw):
        length, kind = struct.unpack(">I4s", raw[pos:pos + 8])
        body = raw[pos + 8:pos + 8 + length]
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat += body
        pos += 12 + length
    width, height, depth, ctype, _, _, interlace = header
    assert depth == 8 and interlace == 0 and ctype in (2, 6), header
    bpp = 3 if ctype == 2 else 4
    data, stride, prev = zlib.decompress(idat), width * bpp, bytearray(width * bpp)
    for y in range(height // 2 + 1):
        ft, line = data[y * (stride + 1)], bytearray(data[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
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
    x = (width // 2) * bpp
    return tuple(prev[x:x + bpp])


@pytest.mark.parametrize("color,dominant", [("1,0,0", 0), ("0,0,1", 2)])
def test_a6_workbench_render_shows_the_material_colour(capsys, basic, work, color, dominant):  # ASSUMED A6
    code, data = axi(capsys, "material", "Cube", basic, "--name", "Paint", "--color", color, "--in-place")
    assert code == 0, data
    png = work / "paint.png"
    code, data = axi(capsys, "render", basic, "--engine", "WORKBENCH", "--resolution", "64x64", "--out", png)
    assert code == 0, data
    pixel = centre_pixel(png)
    others = [pixel[i] for i in range(3) if i != dominant]
    assert pixel[dominant] > max(others) + 40, pixel


# -- live: a private headless MCP server on a copy of basic.blend ------------------------------------------------------

@pytest.fixture(scope="module")
def session(blender_info, fixtures_dir):
    work = paths.tmp_root() / "material_live"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    blend = work / "session.blend"
    shutil.copy2(fixtures_dir / "basic.blend", blend)
    with real_server(blender_info, blend) as (port, _log):
        assert port != 9876
        yield port, blend


def test_live_story(capsys, session):
    port, blend = session
    live = ["--live", "--host", "127.0.0.1", "--port", port]
    assert axi(capsys, "live", "--host", "127.0.0.1", "--port", port)[1]["unsaved_changes"] is False

    code, data = axi(capsys, "material", "Cube", *live, "--name", "Blue", "--color", "0,0,1")
    assert code == 0, data
    assert data["created"] is True and data["modified"] is True and data["slots"] == "1 (Blue)"
    assert data["undo"] == "unavailable (headless server)" and data["saved"] == "none (live session, unsaved)"
    assert data["file"] == blend.as_posix()
    assert any("Changes are only in the open session" in h for h in data["help"])
    assert any(h.startswith("Run `blender-axi render --engine WORKBENCH") and "--live" in h for h in data["help"])
    assert axi(capsys, "live", "--host", "127.0.0.1", "--port", port)[1]["unsaved_changes"] is True
    assert state(capsys, blend, "Cube", *live)[0]["base"] == [0, 0, 1, 1]
    assert state(capsys, blend)[0]["name"] == "Material"  # the file on disk is untouched

    # a file positional and --out have no meaning live; both fail before any change
    code, err = axi(capsys, "material", "Cube", blend, *live, "--name", "X")
    assert code == 2 and err["error"] == "--live uses the file open in Blender; drop <file>"
    code, err = axi(capsys, "material", "Cube", *live, "--name", "X", "--out", "x.blend")
    assert code == 2 and "no copy" in err["error"]

    # --in-place saves the open file
    before = blend.stat().st_mtime_ns
    code, data = axi(capsys, "material", "Cube", *live, "--name", "Blue", "--roughness", "0.1", "--in-place")
    assert code == 0 and data["created"] is False and data["modified"] is True and data["saved"].endswith("session.blend")
    assert blend.stat().st_mtime_ns > before
    assert state(capsys, blend)[0]["name"] == "Blue" and state(capsys, blend)[0]["roughness"] == 0.1
    assert axi(capsys, "live", "--host", "127.0.0.1", "--port", port)[1]["unsaved_changes"] is False
