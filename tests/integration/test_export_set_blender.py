"""0.5.0 U1 against real Blender: export set (modifiers, hidden, exclude, --object, --dry-run), glTF axis facts, live restore.

`ring.blend` is built with `apply` (Ring minus a hidden Hole cutter, a Floor, a cube named `A,B`, a camera). glb/obj output
is parsed to prove what was exported. Outputs go under .tmp/tests/export_set/. Live tests start a REAL headless MCP server
on a free port (never 9876).
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import struct

import pytest

from blender_axi import cli, live, paths
from test_live_optional import real_server

pytestmark = pytest.mark.blender

RECIPE = [
    "add cylinder --name Ring --radius 2 --depth 0.5",
    "add cylinder --name Hole --radius 0.5 --depth 1",
    "modifier Ring boolean --object Hole --operation DIFFERENCE",
    "set Hole --hide",
    "add plane --name Floor --size 12 --location 0,0,-1",
    {"cmd": "add", "kind": "cube", "name": "A,B", "location": [5, 0, 0]},
    "add camera --name Cam --location 0,-8,4 --rotation 65,0,0",
]
GROUP_RECIPE = [
    "add empty --name Grp",
    "add cube --name A",
    "add cube --name B --location 3,0,0",
    "set A --parent Grp",
    "set B --parent Grp",
]
AXIS_RECIPE = [
    "add cube --name Moved --location 1,2,3 --scale 1,2,3",
    "add cube --name Turned --location 0,0,0 --rotation 90,0,0",
]


@pytest.fixture(scope="module")
def work():
    d = paths.tmp_root() / "export_set"
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    return d


@pytest.fixture(scope="module")
def build(work):
    def make(name, recipe):
        blend, recipe_file = work / f"{name}.blend", work / f"{name}.json"
        recipe_file.write_text(json.dumps(recipe), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):  # keep the CLI output out of the test's capsys
            assert cli.main(["--json", "new", str(blend)]) == 0
            assert cli.main(["--json", "apply", str(recipe_file), str(blend), "--in-place"]) == 0
        return blend
    return make


@pytest.fixture(scope="module")
def ring(build):
    return build("ring", RECIPE)


@pytest.fixture(scope="module")
def group(build):
    return build("group", GROUP_RECIPE)


def axi(capsys, *argv):
    code = cli.main(["--json", *map(str, argv)])
    out = capsys.readouterr()
    assert out.err == ""
    return code, json.loads(out.out)


def glb_json(path) -> dict:
    raw = path.read_bytes()
    assert raw[:4] == b"glTF"
    length, kind = struct.unpack_from("<I4s", raw, 12)
    assert kind == b"JSON"
    return json.loads(raw[20:20 + length])


def node_names(doc: dict) -> list[str]:
    return [n.get("name") for n in doc["nodes"]]


def vertex_count(doc: dict, node_name: str) -> int:
    node = next(n for n in doc["nodes"] if n.get("name") == node_name)
    prim = doc["meshes"][node["mesh"]]["primitives"][0]
    return doc["accessors"][prim["attributes"]["POSITION"]]["count"]


def test_default_applies_modifiers_and_skips_hidden(capsys, ring, work):
    out = work / "ring.glb"
    code, data = axi(capsys, "export", ring, "--out", out)
    assert code == 0, data
    assert data["modifiers"] == "applied" and data["skipped"] == "1 hidden (Hole)" and data["objects"] == 3
    assert any("--include-hidden" in h for h in data["help"])
    names = node_names(glb_json(out))
    assert "Hole" not in names and {"Ring", "Floor", "A,B"} <= set(names)

    raw = work / "ring_raw.glb"
    code, data = axi(capsys, "export", ring, "--out", raw, "--no-apply-modifiers")
    assert code == 0 and data["modifiers"] == "raw", data
    assert vertex_count(glb_json(out), "Ring") > vertex_count(glb_json(raw), "Ring")  # the boolean was applied


def test_obj_hidden_skipped_and_include_hidden_whole_scene(capsys, ring, work):
    out = work / "ring.obj"
    code, data = axi(capsys, "export", ring, "--out", out)
    assert code == 0, data
    text = out.read_text(encoding="utf-8")
    assert "o Ring" in text and "o Hole" not in text
    code, data = axi(capsys, "export", ring, "--out", work / "ring_all.obj", "--include-hidden")
    assert code == 0 and data["skipped"] == "none" and data["objects"] == 4, data
    assert "o Hole" in (work / "ring_all.obj").read_text(encoding="utf-8")


def test_include_hidden_selective_and_glb(capsys, ring, work):
    out = work / "hole.glb"
    code, data = axi(capsys, "export", ring, "--out", out, "--objects", "Hole", "--include-hidden")
    assert code == 0, data
    assert node_names(glb_json(out)) == ["Hole"] and data["objects"] == 1
    code, data = axi(capsys, "export", ring, "--out", work / "hole_default.glb", "--objects", "Hole")
    assert code == 1 and data["code"] == "NOT_FOUND" and "--include-hidden" in data["error"]
    assert not (work / "hole_default.glb").exists()
    out = work / "all.glb"
    code, data = axi(capsys, "export", ring, "--out", out, "--include-hidden")
    assert code == 0 and "Hole" in node_names(glb_json(out)), data


def test_exclude(capsys, ring, work):
    out = work / "nofloor.glb"
    code, data = axi(capsys, "export", ring, "--out", out, "--exclude", "Floor")
    assert code == 0, data
    assert data["skipped"] == "1 hidden (Hole), 1 excluded (Floor)" and data["objects"] == 2
    names = node_names(glb_json(out))
    assert "Floor" not in names and "Ring" in names
    code, data = axi(capsys, "export", ring, "--out", work / "bad.glb", "--exclude", "Nope")
    assert code == 1 and data["code"] == "NOT_FOUND" and "Nope" in data["error"]
    assert not (work / "bad.glb").exists()


def test_object_flag_takes_a_comma_name_verbatim(capsys, ring, work):
    out = work / "ab.obj"
    code, data = axi(capsys, "export", ring, "--out", out, "--object", "A,B")
    assert code == 0, data
    assert data["objects"] == 1 and "o A,B" in out.read_text(encoding="utf-8")
    code, data = axi(capsys, "export", ring, "--out", work / "ab2.obj", "--object", "A,B", "--object", "Floor")
    assert code == 0 and data["objects"] == 2, data


def test_dry_run_writes_nothing(capsys, ring, work):
    out = work / "dry.glb"
    code, data = axi(capsys, "export", ring, "--out", out, "--dry-run", "--exclude", "Floor")
    assert code == 0, data
    assert not out.exists() and data["dry_run"] is True and data["would_export"] == "2 (Ring, A,B)"
    assert [r["name"] for r in data["export_set"]] == ["Ring", "A,B"] and data["skipped"] == "1 hidden (Hole), 1 excluded (Floor)"
    assert data["help"][0].startswith("Run `blender-axi export") and "--dry-run" not in data["help"][0]
    out.write_bytes(b"old")  # a dry run does not mind an existing output
    code, data = axi(capsys, "export", ring, "--out", out, "--dry-run")
    assert code == 0 and out.read_bytes() == b"old"


def test_usd_ignores_no_apply_modifiers_with_a_warning(capsys, ring, work):
    out = work / "raw.usda"
    code, data = axi(capsys, "export", ring, "--out", out, "--no-apply-modifiers")
    assert code == 0, data
    assert data["modifiers"].startswith("evaluated") and "ignored" in data["warning"] and out.stat().st_size > 0


def test_whole_scene_and_selection_paths_agree(capsys, fixtures_dir, work):
    """A4: an exclude of something that is not geometry forces the selection path; the glb must be about as big."""
    basic = fixtures_dir / "basic.blend"
    code, whole = axi(capsys, "export", basic, "--out", work / "whole.glb")
    code2, sel = axi(capsys, "export", basic, "--out", work / "sel.glb", "--exclude", "Camera")
    assert (code, code2) == (0, 0), (whole, sel)
    assert whole["objects"] == sel["objects"] == 2 and whole["skipped"] == "none"
    assert abs(whole["size_kb"] - sel["size_kb"]) <= 0.05 * whole["size_kb"], (whole, sel)


def test_gltf_axis_conversion_facts(capsys, build, work):
    """A1: Blender (x, y, z) -> glTF (x, z, -y); scale swaps y/z; rotation is a quaternion. README table source."""
    blend = build("axis", AXIS_RECIPE)
    out = work / "axis.glb"
    code, data = axi(capsys, "export", blend, "--out", out)
    assert code == 0, data
    nodes = {n["name"]: n for n in glb_json(out)["nodes"]}
    moved, turned = nodes["Moved"], nodes["Turned"]
    print("OBSERVED Moved", {k: moved.get(k) for k in ("translation", "scale", "rotation")})
    print("OBSERVED Turned", {k: turned.get(k) for k in ("translation", "scale", "rotation")})
    assert [round(v, 4) for v in moved["translation"]] == [1, 3, -2]
    assert [round(v, 4) for v in moved["scale"]] == [1, 3, 2]
    assert len(turned["rotation"]) == 4 and abs(turned["rotation"][0]) > 0.5  # 90 degrees about X, quaternion x != 0


def index_triangles(doc: dict) -> int:
    return sum(doc["accessors"][p["indices"]]["count"] // 3 for m in doc["meshes"] for p in m["primitives"])


def test_triangles_equal_the_glb_index_count_in_dry_run_and_export(capsys, ring, work):
    """0.10.0: `triangles` counts the evaluated meshes (boolean applied, hidden cutter evaluated), as the glb has them."""
    code, dry = axi(capsys, "export", ring, "--out", work / "tri.glb", "--dry-run")
    assert code == 0, dry
    assert "also" not in dry and not (work / "tri.glb").exists()  # the camera is not geometry; no Empty in the file
    out = work / "tri.glb"
    code, real = axi(capsys, "export", ring, "--out", out)
    assert code == 0, real
    expected = index_triangles(glb_json(out))
    assert dry["triangles"] == real["triangles"] == expected
    raw = work / "tri_raw.glb"
    code, raw_data = axi(capsys, "export", ring, "--out", raw, "--no-apply-modifiers")
    assert raw_data["triangles"] == index_triangles(glb_json(raw)) < expected  # the cut adds triangles
    print("OBSERVED ring triangles", dry["triangles"], real["triangles"], expected, "raw", raw_data["triangles"])


def test_dry_run_lists_the_empty_and_the_glb_writes_it(capsys, group, work):
    """Y3-6: a parent Empty of the set is a node of a whole-scene glb; the dry run now says so."""
    blend = group
    out = work / "group.glb"
    code, dry = axi(capsys, "export", blend, "--out", out, "--dry-run")
    assert code == 0, dry
    assert dry["also"] == "1 empty (Grp)" and dry["would_export"] == "2 (A, B)"
    assert [r["name"] for r in dry["export_set"]] == ["A", "B"]
    code, real = axi(capsys, "export", blend, "--out", out)
    assert code == 0, real
    doc = glb_json(out)
    grp = next(n for n in doc["nodes"] if n.get("name") == "Grp")
    assert set(node_names(doc)) == {"Grp", "A", "B"} and len(grp["children"]) == 2
    assert real["triangles"] == index_triangles(doc) == 24
    selected = work / "group_ab.glb"  # F6: the unselected parent is not added by a selective export
    code, data = axi(capsys, "export", blend, "--out", selected, "--objects", "A,B")
    assert code == 0 and sorted(node_names(glb_json(selected))) == ["A", "B"], data
    code, dry = axi(capsys, "export", blend, "--out", work / "g.obj", "--dry-run")
    assert code == 0 and "also" not in dry  # obj writes no Empty
    print("OBSERVED Grp node", grp, "nodes", node_names(doc), "triangles", real["triangles"])


def test_fbx_whole_scene_writes_the_empty(capsys, group, work):
    """A1: the fbx exporter writes the Empty of a whole-scene export, so `also` is right for fbx too."""
    blend = group
    out = work / "group.fbx"
    code, data = axi(capsys, "export", blend, "--out", out)
    assert code == 0, data
    raw = out.read_bytes()
    assert b"Grp" in raw
    code, dry = axi(capsys, "export", blend, "--out", work / "g2.fbx", "--dry-run")
    assert dry["also"] == "1 empty (Grp)"
    print("OBSERVED fbx has Grp:", b"Grp" in raw)


def test_cameras_flag_writes_the_camera_node(capsys, ring, work):
    plain = work / "nocam.glb"
    code, data = axi(capsys, "export", ring, "--out", plain)
    assert code == 0 and "cameras" not in data and "cameras" not in glb_json(plain), data
    out = work / "cam.glb"
    code, data = axi(capsys, "export", ring, "--out", out, "--cameras")
    assert code == 0 and data["cameras"] == "1 (Cam)", data
    doc = glb_json(out)
    cam = next(n for n in doc["nodes"] if n.get("name") == "Cam")
    assert len(doc["cameras"]) == 1 and "camera" in cam
    assert [round(v, 4) for v in cam["translation"]] == [0, 4, 8]  # Blender (0, -8, 4) -> (x, z, -y)
    code, dry = axi(capsys, "export", ring, "--out", work / "cam_dry.glb", "--dry-run", "--cameras")
    assert dry["also"] == "1 camera (Cam)" and dry["help"][0].endswith("--cameras` to write it")
    code, data = axi(capsys, "export", ring, "--out", work / "cam.fbx", "--cameras")
    assert code == 2 and data["code"] == "VALIDATION_ERROR" and "glb and gltf" in data["error"]
    code, data = axi(capsys, "export", ring, "--out", work / "camsel.glb", "--objects", "Ring,Cam", "--cameras")
    assert code == 0 and data["cameras"] == "1 (Cam)" and sorted(node_names(glb_json(work / "camsel.glb"))) == ["Cam", "Ring"], data
    print("OBSERVED Cam node", cam, "camera", doc["cameras"])


def test_json_dry_run_lists_every_object(capsys, build, work):
    recipe = [f"add cube --name C{i:02d} --location {i},0,0" for i in range(23)]
    blend = build("many", recipe)
    code, dry = axi(capsys, "export", blend, "--out", work / "many.glb", "--dry-run")
    assert code == 0 and len(dry["export_set"]) == 23 and dry["would_export"].startswith("23 (")
    assert not any("--full" in h for h in dry["help"])


# --- live: the open session keeps its hide state and selection ----------------------------------------------------

READ_STATE = """
import bpy
s = bpy.context.scene
vl = bpy.context.view_layer
result = {
    "selected": sorted(o.name for o in s.objects if o.select_get()),
    "active": vl.objects.active.name if vl.objects.active else None,
    "hidden": sorted(o.name for o in s.objects if not o.visible_get()),
    "hide_viewport": sorted(o.name for o in s.objects if o.hide_viewport),
    "hide_get": sorted(o.name for o in s.objects if o.hide_get()),
}
"""
SET_SELECTION = """
import bpy
for o in bpy.context.scene.objects:
    o.select_set(False)
bpy.data.objects['Floor'].select_set(True)
bpy.context.view_layer.objects.active = bpy.data.objects['Floor']
result = {"ok": True}
"""


def _exec(port, code):
    response = live.send("127.0.0.1", port, code, strict_json=True, timeout=120)
    assert response.get("status") == "ok", response
    return response["result"]


def test_live_export_skips_hidden_and_restores_the_session(blender_info, ring, work, capsys):
    session_blend = work / "live_ring.blend"
    shutil.copy(ring, session_blend)
    with real_server(blender_info, session_blend) as (port, _log):
        assert port != 9876
        live_args = ["--live", "--host", "127.0.0.1", "--port", port]
        _exec(port, SET_SELECTION)
        before = _exec(port, READ_STATE)
        assert before["selected"] == ["Floor"] and "Hole" in before["hidden"]

        out = work / "live.glb"
        code, data = axi(capsys, "export", *live_args, "--out", out, "--force")
        assert code == 0, data
        assert data["skipped"] == "1 hidden (Hole)" and data["modifiers"] == "applied" and data["objects"] == 3
        assert "Hole" not in node_names(glb_json(out))
        assert _exec(port, READ_STATE) == before

        for flags in (["--include-hidden"], ["--objects", "Hole", "--include-hidden"]):  # A2: un-hide, export, re-hide
            out = work / f"live_{len(flags)}.glb"
            code, data = axi(capsys, "export", *live_args, "--out", out, "--force", *flags)
            assert code == 0, (flags, data)
            assert "Hole" in node_names(glb_json(out)), flags
            assert _exec(port, READ_STATE) == before, flags

        code, data = axi(capsys, "export", *live_args, "--out", work / "never.glb", "--dry-run")
        assert code == 0 and data["dry_run"] is True and not (work / "never.glb").exists()
        assert _exec(port, READ_STATE) == before
