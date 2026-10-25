"""Real-Blender checks for `add` (v1.2 U1): headless --in-place / --out, every kind, A1/A2, and --live on a private server.

Headless tests run the command through `cli.main` (the registry entry is patched in, so they pass before the lead's
splice). Live tests start a REAL headless MCP server on a free port (never 9876) on a copy of basic.blend.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from blender_axi import cli, paths, registry
from test_live_optional import real_server

pytestmark = pytest.mark.blender

ADD = "blender_axi.commands.add:SPEC"


@pytest.fixture(autouse=True)
def _register_add(monkeypatch):
    monkeypatch.setitem(registry.COMMANDS, "add", ADD)


@pytest.fixture
def work():
    d = paths.tmp_root() / "add"
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    return d


@pytest.fixture
def empty(work, fixtures_dir):
    path = work / "empty.blend"
    shutil.copy2(fixtures_dir / "empty.blend", path)
    return path


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


def vec(text: str) -> list[float]:
    return [float(x) for x in text.split(",")]


# kind, extra flags, type, verts, faces, dimensions
KINDS = [
    ("cube", [], "MESH", 8, 6, [2, 2, 2]),
    ("plane", [], "MESH", 4, 1, [2, 2, 0]),
    ("sphere", [], "MESH", 482, 512, [2, 2, 2]),
    ("icosphere", [], "MESH", 42, 80, [1.902, 2, 2]),
    ("cylinder", ["--radius", "0.5", "--depth", "1"], "MESH", 64, 34, [1, 1, 1]),
    ("cone", [], "MESH", 33, 33, [2, 2, 2]),
    ("monkey", [], "MESH", 507, 500, None),
]


@pytest.mark.parametrize("kind,extra,typ,verts,faces,dims", KINDS, ids=[k[0] for k in KINDS])
def test_each_mesh_kind(capsys, empty, kind, extra, typ, verts, faces, dims):
    code, data = axi(capsys, "add", kind, empty, *extra, "--in-place")
    assert code == 0, data
    assert data["type"] == typ and data["verts"] == verts and data["faces"] == faces, data
    if dims is not None:
        assert vec(data["dimensions"]) == pytest.approx(dims, abs=1e-3)
    code, obj = axi(capsys, "object", data["added"], empty)
    assert code == 0 and obj["type"] == "MESH" and obj["verts"] == verts and obj["dimensions"] == data["dimensions"]


def test_default_names_are_blenders_and_clash_suffixes(capsys, empty):
    assert axi(capsys, "add", "cube", empty, "--in-place")[1]["added"] == "Cube"
    assert axi(capsys, "add", "cube", empty, "--in-place")[1]["added"] == "Cube.001"
    assert axi(capsys, "add", "monkey", empty, "--in-place")[1]["added"] == "Suzanne"


def test_a2_cylinder_depth_runs_along_z(capsys, empty):  # ASSUMED A2
    code, data = axi(capsys, "add", "cylinder", empty, "--radius", "0.5", "--depth", "3", "--in-place")
    assert code == 0 and data["dimensions"] == "1,1,3"
    _, obj = axi(capsys, "object", "Cylinder", empty)
    assert obj["dimensions"] == "1,1,3"


def test_camera_light_empty_and_light_options(capsys, empty, work):
    code, cam = axi(capsys, "add", "camera", empty, "--name", "Cam", "--location", "0,-5,2", "--in-place")
    assert code == 0 and cam["type"] == "CAMERA" and cam["location"] == "0,-5,2" and "verts" not in cam
    assert cam["scene_camera"] == "set" and axi(capsys, "scene", empty)[1]["camera"] == "Cam"
    assert any("to render through it" in h for h in cam["help"])
    code, sun = axi(capsys, "add", "light", empty, "--type", "SUN", "--energy", "3", "--in-place")
    assert code == 0 and sun["type"] == "LIGHT" and sun["added"] == "Sun" and "scene_camera" not in sun
    code, obj = axi(capsys, "add", "empty", empty, "--name", "Anchor", "--in-place")
    assert code == 0 and obj["type"] == "EMPTY" and "scene_camera" not in obj
    png = work / "cam.png"  # 0.3.0: NOT_FOUND "scene has no active camera"
    code, _ = axi(capsys, "render", empty, "--engine", "WORKBENCH", "--resolution", "32x32", "--out", png)
    assert code == 0 and png.exists()
    assert axi(capsys, "object", "Sun", empty)[1]["type"] == "LIGHT"
    code, ex = axi(capsys, "exec", empty, "--allow-exec", "--code",
                   "l = bpy.data.lights['Sun']\nresult = {'type': l.type, 'energy': l.energy}")
    assert code == 0 and ex["result"] == {"type": "SUN", "energy": 3.0}


def test_second_camera_keeps_the_scene_camera(capsys, basic):
    code, cam = axi(capsys, "add", "camera", basic, "--name", "Cam2", "--in-place")
    assert code == 0 and cam["scene_camera"] == "unchanged (Camera)"
    assert not any("to render through it" in h for h in cam["help"])
    assert axi(capsys, "scene", basic)[1]["camera"] == "Camera"


def test_location_rotation_scale_round_trip(capsys, empty):
    code, data = axi(capsys, "add", "cube", empty, "--name", "Box", "--location", "1,2,3", "--rotation", "0,0,90",
                     "--scale", "1,2,3", "--in-place")
    assert code == 0 and data["location"] == "1,2,3" and data["dimensions"] == "2,4,6"
    _, obj = axi(capsys, "object", "Box", empty)
    assert obj["location"] == "1,2,3" and obj["rotation_deg"] == "0,0,90" and obj["scale"] == "1,2,3"


def test_in_place_writes_the_file_with_a_backup(capsys, empty):
    assert not empty.with_name("empty.blend1").exists()
    code, data = axi(capsys, "add", "cube", empty, "--in-place")
    assert code == 0 and data["modified"] is True and data["saved"].endswith("empty.blend")
    assert data["backup"].endswith("empty.blend1") and empty.with_name("empty.blend1").is_file()
    assert "undo" not in data and data["file"] == str(empty)


def test_out_leaves_the_original_untouched(capsys, empty, work):
    before = (empty.stat().st_mtime_ns, empty.read_bytes())
    copy = work / "copy.blend"
    code, data = axi(capsys, "add", "cube", empty, "--out", copy)
    assert code == 0 and data["modified"] is True and data["saved"].endswith("copy.blend") and "backup" not in data
    assert (empty.stat().st_mtime_ns, empty.read_bytes()) == before and not empty.with_name("empty.blend1").exists()
    assert axi(capsys, "objects", copy)[1]["count"] == "1 of 1 total"
    assert axi(capsys, "objects", empty)[1]["count"] == "0 of 0 total"
    assert any("objects" in h and "copy.blend" in h for h in data["help"])

    code, data = axi(capsys, "add", "cube", empty, "--out", copy)
    assert code == 1 and data["code"] == "WRITE_REFUSED"
    assert any(h.startswith("Run `blender-axi add cube ") and "--force" in h for h in data["help"])
    code, data = axi(capsys, "add", "cube", empty, "--out", copy, "--force")
    assert code == 0 and axi(capsys, "objects", copy)[1]["count"] == "1 of 1 total"


def test_neither_flag_is_exit_2_and_writes_nothing(capsys, empty):
    before = (empty.stat().st_mtime_ns, empty.read_bytes())
    code, data = axi(capsys, "add", "cube", empty)
    assert code == 2 and data["code"] == "VALIDATION_ERROR" and "say where to write it" in data["error"]
    assert any("--in-place" in h for h in data["help"]) and any("--out <copy.blend>" in h for h in data["help"])
    assert (empty.stat().st_mtime_ns, empty.read_bytes()) == before and not empty.with_name("empty.blend1").exists()


def test_bad_arguments_exit_2_before_blender(capsys, empty):
    for argv in (["torus", empty, "--in-place"], ["cube", empty, "--depth", "2", "--in-place"],
                 ["cube", empty, "--location", "1,2", "--in-place"], ["cube", empty, "--in-place", "--out", "x.blend"]):
        code, data = axi(capsys, "add", *argv)
        assert code == 2 and data["code"] == "VALIDATION_ERROR", (argv, data)


def test_missing_collection_is_not_found_and_changes_nothing(capsys, basic):
    before = basic.read_bytes()
    code, data = axi(capsys, "add", "cube", basic, "--collection", "Nope", "--in-place")
    assert code == 1 and data["code"] == "NOT_FOUND" and "Props" in data["error"]
    assert basic.read_bytes() == before


def test_collection_flag_and_scene_collection(capsys, basic):
    code, data = axi(capsys, "add", "cube", basic, "--name", "InProps", "--collection", "Props", "--in-place")
    assert code == 0 and data["collection"] == "Props"
    code, listing = axi(capsys, "objects", basic, "--collection", "Props")
    assert "InProps" in {o["name"] for o in listing["objects"]}
    code, data = axi(capsys, "add", "cube", basic, "--name", "InRoot", "--collection", "Scene Collection", "--in-place")
    assert code == 0 and data["collection"] == "Scene Collection"


def test_a1_default_collection_is_the_context_collection(capsys, basic, blender_info):  # ASSUMED A1
    proc = subprocess.run(
        [str(blender_info.path), "--background", "--factory-startup", str(basic), "--python-expr",
         "import bpy; print('CTXCOLL=' + bpy.context.collection.name)"],
        env=paths.blender_env(), cwd=str(basic.parent), capture_output=True, text=True, timeout=120)
    line = next(ln for ln in proc.stdout.splitlines() if ln.startswith("CTXCOLL="))
    code, data = axi(capsys, "add", "cube", basic, "--in-place")
    assert code == 0 and data["collection"] == line.removeprefix("CTXCOLL=")


# -- live: a private headless MCP server on a copy of basic.blend ------------------------------------------------------

@pytest.fixture(scope="module")
def session(blender_info, fixtures_dir):
    work = paths.tmp_root() / "add_live"
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

    code, data = axi(capsys, "add", "cube", *live, "--name", "LiveBox", "--location", "0,0,5")
    assert code == 0, data
    assert data["added"] == "LiveBox" and data["modified"] is True and data["location"] == "0,0,5"
    assert data["undo"] == "unavailable (headless server)" and data["saved"] == "none (live session, unsaved)"
    assert data["file"] == blend.as_posix()
    assert any("Changes are only in the open session" in h for h in data["help"])
    assert any(h.startswith("Run `blender-axi set LiveBox --location x,y,z --live --host 127.0.0.1") for h in data["help"])
    assert axi(capsys, "live", "--host", "127.0.0.1", "--port", port)[1]["unsaved_changes"] is True
    _, listing = axi(capsys, "objects", *live)
    assert "LiveBox" in {o["name"] for o in listing["objects"]}

    # --out has no meaning in live mode, a file positional neither; both fail before any change
    code, err = axi(capsys, "add", "cube", *live, "--out", "x.blend")
    assert code == 2 and "no copy" in err["error"]
    code, err = axi(capsys, "add", "cube", blend, *live)
    assert code == 2 and err["error"] == "--live uses the file open in Blender; drop <file>"
    _, listing = axi(capsys, "objects", *live)
    assert [o["name"] for o in listing["objects"]].count("Cube") == 1

    # --in-place saves the open file
    before = blend.stat().st_mtime_ns
    code, data = axi(capsys, "add", "sphere", *live, "--name", "Ball2", "--in-place")
    assert code == 0 and data["modified"] is True and data["saved"].endswith("session.blend")
    assert blend.stat().st_mtime_ns > before
    assert axi(capsys, "live", "--host", "127.0.0.1", "--port", port)[1]["unsaved_changes"] is False

    # the session already has a scene camera: keep it; clear it and the next camera takes over
    code, data = axi(capsys, "add", "camera", *live, "--name", "LiveCam")
    assert code == 0 and data["scene_camera"] == "unchanged (Camera)", data
    code, _ = axi(capsys, "exec", *live, "--allow-exec", "--code", "bpy.context.scene.camera = None")
    assert code == 0
    code, data = axi(capsys, "add", "camera", *live, "--name", "LiveCam2")
    assert code == 0 and data["scene_camera"] == "set", data
    assert axi(capsys, "scene", *live)[1]["camera"] == "LiveCam2"
