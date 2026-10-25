"""Unit tests for `objects` and `object` (fake transport) and the three ops (fake bpy)."""

from __future__ import annotations

import types

import pytest

from blender_axi import flags, toon
from blender_axi.blender_side import op_object_detail, op_objects_list
from blender_axi.blender_side.ops import OpError
from blender_axi.commands import objects as cmd
from blender_axi.errors import AxiError
from blender_axi.runner import RunOutcome


class FakeTransport:
    name = "fake"

    def __init__(self, result, error=None):
        self.result, self.error, self.calls = result, error, []

    def run(self, op, params, *, blend=None, save=None, timeout=None):
        self.calls.append((op, params, blend, timeout))
        if self.error:
            raise self.error
        return RunOutcome(result=self.result, modified=False, saved=None, blender_version="5.2.0 LTS",
                          duration_ms=1, stdout="", warnings=[])


def invoke(spec, argv, transport):
    inv, wants_help = flags.parse(spec, argv)
    assert not wants_help
    inv.transport = transport
    return spec.run(inv)


@pytest.fixture(autouse=True)
def blend_dir(tmp_path, monkeypatch):
    (tmp_path / "basic.blend").write_bytes(b"x")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def row(name, type_="MESH", colls=("Collection",), visible=True, **extra):
    return {"name": name, "type": type_, "collections": list(colls), "visible": visible, **extra}


LISTING = {"filepath": "x", "total": 3, "matched": 3,
           "rows": [row("Camera", "CAMERA"), row("Cube"), row("Light", "LIGHT")]}


# -- objects ---------------------------------------------------------------------------------------

def test_objects_default_shape():
    t = FakeTransport(LISTING)
    res = invoke(cmd.OBJECTS_SPEC, [], t)
    assert list(res.data) == ["file", "count", "objects"]
    assert res.data["file"] == "basic.blend" and res.data["count"] == "3 of 3 total"
    assert res.data["objects"][1] == {"name": "Cube", "type": "MESH", "collection": "Collection", "visible": True}
    assert t.calls[0][1] == {"type": None, "collection": None, "name_contains": None, "limit": 100, "fields": []}
    text = toon.encode({**res.data, "help": res.help})
    assert "objects[3]{name,type,collection,visible}:" in text


def test_objects_help_hints_limit_only_when_truncated():
    res = invoke(cmd.OBJECTS_SPEC, [], FakeTransport(LISTING))
    joined = "\n".join(res.help)
    assert "object <name> <file>" in joined and "--fields location,dimensions" in joined
    assert "--limit" not in joined
    cut = dict(LISTING, total=250, matched=250)
    res = invoke(cmd.OBJECTS_SPEC, ["--limit", "3"], FakeTransport(cut))
    assert res.data["count"] == "3 of 250 total"
    assert any("--limit <n>" in h and "250" in h for h in res.help)


def test_objects_fields_hint_hidden_when_fields_requested():
    res = invoke(cmd.OBJECTS_SPEC, ["--fields", "verts"], FakeTransport(LISTING))
    assert not any("--fields" in h for h in res.help)


def test_objects_fields_formatting_and_case():
    rows = [row("Ball", location=[3.0, 0.0, 0.1234], dimensions=[2.0, 2.0, 2.0], parent=None, verts=482,
                faces=512, materials=1, modifiers=0, data="Sphere")]
    t = FakeTransport({"filepath": "x", "total": 1, "matched": 1, "rows": rows})
    res = invoke(cmd.OBJECTS_SPEC, ["--fields", "Location,dimensions,parent,verts,faces,materials,modifiers,data",
                                    "--type", "mesh"], t)
    item = res.data["objects"][0]
    assert item["location"] == "3,0,0.123" and item["dimensions"] == "2,2,2"
    assert item["parent"] is None and item["verts"] == 482 and item["data"] == "Sphere"
    assert list(item)[:4] == ["name", "type", "collection", "visible"]
    assert t.calls[0][1]["type"] == "MESH" and t.calls[0][1]["fields"][0] == "location"
    assert res.data["count"] == "1 of 1 matching (1 total)"
    toon.encode(res.data)


def test_objects_multi_collection_label():
    r = {"filepath": "x", "total": 1, "matched": 1, "rows": [row("A", colls=("X", "Y"))]}
    assert invoke(cmd.OBJECTS_SPEC, [], FakeTransport(r)).data["objects"][0]["collection"] == "X|Y"


def test_objects_empty_with_filters():
    r = {"filepath": "x", "total": 5, "matched": 0, "rows": []}
    res = invoke(cmd.OBJECTS_SPEC, ["--type", "CAMERA", "--name-contains", "zzz"], FakeTransport(r))
    assert res.data["objects"] == "0 objects matching type=CAMERA, name_contains=zzz in basic.blend"
    assert res.data["count"] == "0 of 0 matching (5 total)"
    assert res.help == ["Run `blender-axi objects <file>` to list all 5 objects"]


def test_objects_empty_with_filters_in_a_one_object_file_is_singular():
    r = {"filepath": "x", "total": 1, "matched": 0, "rows": []}
    res = invoke(cmd.OBJECTS_SPEC, ["--name-contains", "zzz"], FakeTransport(r))
    assert res.help == ["Run `blender-axi objects <file>` to list all 1 object"]


def test_objects_empty_scene():
    r = {"filepath": "x", "total": 0, "matched": 0, "rows": []}
    res = invoke(cmd.OBJECTS_SPEC, [], FakeTransport(r))
    assert res.data["objects"] == "0 objects in basic.blend" and res.data["count"] == "0 of 0 total"
    assert res.help == ["Run `blender-axi scene <file>` for the scene overview"]


def test_objects_params_passthrough():
    t = FakeTransport(LISTING)
    invoke(cmd.OBJECTS_SPEC, ["--collection", "Props", "--name-contains", "ba", "--limit", "7"], t)
    assert t.calls[0][1] == {"type": None, "collection": "Props", "name_contains": "ba", "limit": 7, "fields": []}


@pytest.mark.parametrize("argv", [["--fields", "bogus"], ["--type", "WIDGET"], ["--limit", "abc"]])
def test_objects_bad_flags_exit_2(argv):
    with pytest.raises(AxiError) as e:
        flags.parse(cmd.OBJECTS_SPEC, argv)
    assert e.value.exit_code == 2


def test_objects_direct_validation_exit_2():
    from blender_axi.spec import Invocation

    inv = Invocation(command="objects", flags=flags.FlagValues(fields=["nope"], limit=5))
    inv.transport = FakeTransport(LISTING)
    with pytest.raises(AxiError) as e:
        cmd.OBJECTS_SPEC.run(inv)
    assert e.value.exit_code == 2 and "Valid fields" in e.value.help[0]
    inv = Invocation(command="objects", flags=flags.FlagValues(limit=0))
    with pytest.raises(AxiError) as e:
        cmd.OBJECTS_SPEC.run(inv)
    assert e.value.exit_code == 2


def test_objects_collection_not_found_gets_actionable_help():
    err = AxiError("NOT_FOUND", "collection 'X' not found", help=["Rerun with `--debug`"])
    with pytest.raises(AxiError) as e:
        invoke(cmd.OBJECTS_SPEC, ["--collection", "X"], FakeTransport(None, error=err))
    assert e.value.code == "NOT_FOUND" and e.value.help == ["Run `blender-axi scene <file>` to see the collections"]


def test_objects_other_errors_untouched():
    err = AxiError("TIMEOUT", "slow", help=["Raise the limit"])
    with pytest.raises(AxiError) as e:
        invoke(cmd.OBJECTS_SPEC, [], FakeTransport(None, error=err))
    assert e.value.help == ["Raise the limit"]


def test_objects_live_hints():
    r = dict(LISTING, filepath="C:/live.blend")
    t = FakeTransport(r)
    res = invoke(cmd.OBJECTS_SPEC, ["--live", "--host", "h", "--port", "1"], t)
    assert t.calls[0][2] is None and res.data["file"] == "C:/live.blend"
    assert res.help[0] == "Run `blender-axi object <name> --live --host h --port 1` for one object's details"


def test_objects_live_file_uses_forward_slashes():
    r = dict(LISTING, filepath="D:\\proj\\live.blend")
    res = invoke(cmd.OBJECTS_SPEC, ["--live"], FakeTransport(r))
    assert res.data["file"] == "D:/proj/live.blend"
    empty = dict(r, total=0, matched=0, rows=[])
    assert invoke(cmd.OBJECTS_SPEC, ["--live"], FakeTransport(empty)).data["objects"] == "0 objects in D:/proj/live.blend"
    assert invoke(cmd.OBJECTS_SPEC, ["--live"], FakeTransport(dict(r, filepath=""))).data["file"] == "(unsaved)"
    assert "\\" not in toon.encode(res.data)


# -- object ----------------------------------------------------------------------------------------

DETAIL = {
    "name": "Ball", "type": "MESH", "collections": ["Props"], "parent": None, "children": [],
    "location": [3.0, 0.0, 0.0], "rotation_deg": [0.0, 0.0, 90.0], "scale": [1.0, 1.0, 1.0],
    "dimensions": [2.0, 2.0, 2.0], "visible": True, "data": "Sphere", "verts": 482, "faces": 512,
    "materials": [], "modifiers": [], "custom_props": {},
}


def test_object_detail_shape():
    t = FakeTransport(DETAIL)
    res = invoke(cmd.OBJECT_SPEC, ["Ball"], t)
    assert list(res.data) == ["name", "type", "collection", "parent", "children", "location", "rotation_deg",
                              "scale", "dimensions", "visible", "verts", "faces", "materials", "modifiers",
                              "custom_props"]
    d = res.data
    assert d["location"] == "3,0,0" and d["rotation_deg"] == "0,0,90" and d["parent"] == "none"
    assert d["children"] == "none" and d["materials"] == "none" and d["modifiers"] == "none"
    assert d["custom_props"] == "none" and res.help == []
    assert t.calls[0][:2] == ("object_detail", {"name": "Ball"})


def test_object_detail_lists_and_non_mesh():
    r = dict(DETAIL, type="EMPTY", verts=None, faces=None, parent="Root", children=["A", "B"], materials=["M1"],
             modifiers=[{"name": "Bevel", "type": "BEVEL"}, {"name": "Sub", "type": "SUBSURF"}])
    d = invoke(cmd.OBJECT_SPEC, ["Ball"], FakeTransport(r)).data
    assert "verts" not in d and "faces" not in d
    assert d["parent"] == "Root" and d["children"] == ["A", "B"] and d["materials"] == ["M1"]
    text = toon.encode(d)
    assert "modifiers[2]{name,type}:" in text and "children[2]: A,B" in text


def test_object_custom_props_truncation_and_full():
    r = dict(DETAIL, custom_props={"note": "x" * 3000, "n": 1})
    d = invoke(cmd.OBJECT_SPEC, ["Ball"], FakeTransport(r)).data
    assert "(truncated," in d["custom_props"] and "--full" in d["custom_props"]
    inv, _ = flags.parse(cmd.OBJECT_SPEC, ["Ball", "--full"])
    inv.transport = FakeTransport(r)
    full = cmd.OBJECT_SPEC.run(inv).data["custom_props"]
    assert "truncated" not in full and full.endswith('n=1') and len(full) > 3000
    small = invoke(cmd.OBJECT_SPEC, ["Ball"], FakeTransport(dict(DETAIL, custom_props={"k": "v", "n": 2}))).data
    assert small["custom_props"] == 'k="v", n=2'


def test_object_requires_name():
    with pytest.raises(AxiError) as e:
        flags.parse(cmd.OBJECT_SPEC, [])
    assert e.value.exit_code == 2


def test_object_not_found_help():
    err = AxiError("NOT_FOUND", "object 'Nope' not found", help=["Rerun with `--debug`"])
    with pytest.raises(AxiError) as e:
        invoke(cmd.OBJECT_SPEC, ["Nope"], FakeTransport(None, error=err))
    assert e.value.help == ["Run `blender-axi objects <file>` to list object names"]


def test_object_explicit_file(blend_dir):
    (blend_dir / "b.blend").write_bytes(b"x")
    t = FakeTransport(DETAIL)
    invoke(cmd.OBJECT_SPEC, ["Ball", "b.blend"], t)
    assert t.calls[0][2].name == "b.blend"


# -- ops with fake bpy -----------------------------------------------------------------------------

class Obj:
    def __init__(self, name, type_="MESH", colls=("Collection",), loc=(0, 0, 0), dims=(1, 1, 1), parent=None,
                 verts=8, faces=6, materials=(), modifiers=(), props=None, hidden=False):
        self.name, self.type = name, type_
        self.users_collection = [types.SimpleNamespace(name=c) for c in colls]
        self.location, self.dimensions, self.scale = loc, dims, (1.0, 1.0, 1.0)
        self.parent, self.children = parent, []
        self.data = (types.SimpleNamespace(name=name + "Data", vertices=range(verts), polygons=range(faces))
                     if type_ == "MESH" else None)
        self.material_slots = [types.SimpleNamespace(material=m) for m in materials]
        self.modifiers = [types.SimpleNamespace(name=n, type=t) for n, t in modifiers]
        self.rotation_mode, self.rotation_euler = "XYZ", (0.0, 0.0, 1.5707963267948966)
        self.hide_viewport = False
        self._hidden, self._props = hidden, props or {}

    def visible_get(self):
        return not self._hidden

    def keys(self):
        return list(self._props)

    def __getitem__(self, k):
        return self._props[k]


class IdArr:
    def __init__(self, v):
        self.v = v

    def to_list(self):
        return self.v


@pytest.fixture
def scene_bpy(fake_bpy):
    cam = Obj("Camera", "CAMERA", loc=(7.36, -6.93, 4.96))
    cube = Obj("Cube", materials=[types.SimpleNamespace(name="Material")], modifiers=[("Bevel", "BEVEL")],
               props={"tag": "hero", "arr": IdArr([1, 2]), "_hidden": 1})
    ball = Obj("Ball", colls=("Props",), loc=(3, 0, 0), verts=482, faces=512)
    anchor = Obj("Anchor", "EMPTY", colls=("Props",), loc=(0, 3, 0), parent=ball)
    ball.children = [anchor]
    props = types.SimpleNamespace(name="Props", all_objects=[ball, anchor])
    sc = types.SimpleNamespace(objects=[cube, cam, ball, anchor], collection=types.SimpleNamespace(name="Scene Collection",
                                                                                          all_objects=[cube, cam, ball, anchor]))
    fake_bpy.context.scene = sc
    fake_bpy.data.objects = [cube, cam, ball, anchor]
    fake_bpy.data.collections = [props]
    fake_bpy.data.filepath = "f.blend"
    return fake_bpy


def test_op_list_sorted_all_and_hidden(scene_bpy):
    scene_bpy.data.objects[0]._hidden = True
    r = op_objects_list.run({})
    assert [x["name"] for x in r["rows"]] == ["Anchor", "Ball", "Camera", "Cube"]
    assert r["total"] == 4 and r["matched"] == 4 and r["filepath"] == "f.blend"
    assert [x["visible"] for x in r["rows"]] == [True, True, True, False]
    assert set(r["rows"][0]) == {"name", "type", "collections", "visible"}


def test_op_list_filters_and_limit(scene_bpy):
    assert [x["name"] for x in op_objects_list.run({"type": "MESH"})["rows"]] == ["Ball", "Cube"]
    assert [x["name"] for x in op_objects_list.run({"collection": "Props"})["rows"]] == ["Anchor", "Ball"]
    assert [x["name"] for x in op_objects_list.run({"collection": "Scene Collection"})["rows"]][0] == "Anchor"
    assert [x["name"] for x in op_objects_list.run({"name_contains": "CA"})["rows"]] == ["Camera"]
    assert op_objects_list.run({"type": "CAMERA", "name_contains": "zzz"}) == {
        "filepath": "f.blend", "total": 4, "matched": 0, "rows": []}
    r = op_objects_list.run({"limit": 2})
    assert len(r["rows"]) == 2 and r["matched"] == 4 and r["total"] == 4


def test_op_list_fields(scene_bpy):
    r = op_objects_list.run({"fields": ["location", "dimensions", "parent", "verts", "faces", "materials",
                                        "modifiers", "data", "bogus"], "name_contains": "ball"})
    row_ = r["rows"][0]
    assert row_["location"] == [3.0, 0.0, 0.0] and row_["verts"] == 482 and row_["faces"] == 512
    assert row_["parent"] is None and row_["data"] == "BallData" and "bogus" not in row_
    empty = op_objects_list.run({"fields": ["verts", "parent", "data"], "name_contains": "anchor"})["rows"][0]
    assert empty["verts"] is None and empty["parent"] == "Ball" and empty["data"] is None
    cube = op_objects_list.run({"fields": ["materials", "modifiers"], "name_contains": "cube"})["rows"][0]
    assert cube["materials"] == 1 and cube["modifiers"] == 1


def test_op_list_unknown_collection(scene_bpy):
    with pytest.raises(OpError) as e:
        op_objects_list.run({"collection": "Nope"})
    assert e.value.code == "NOT_FOUND" and "available: Props" in e.value.message
    scene_bpy.data.collections = []
    with pytest.raises(OpError) as e:
        op_objects_list.run({"collection": "Nope"})
    assert "no collections" in e.value.message


def test_op_detail(scene_bpy):
    r = op_object_detail.run({"name": "Cube"})
    assert r["name"] == "Cube" and r["verts"] == 8 and r["faces"] == 6 and r["parent"] is None
    assert r["materials"] == ["Material"] and r["modifiers"] == [{"name": "Bevel", "type": "BEVEL"}]
    assert r["custom_props"] == {"tag": "hero", "arr": [1, 2]}
    assert r["rotation_deg"] == [0.0, 0.0, 90.0] and r["collections"] == ["Collection"]
    ball = op_object_detail.run({"name": "Ball"})
    assert ball["children"] == ["Anchor"] and ball["collections"] == ["Props"]
    anchor = op_object_detail.run({"name": "Anchor"})
    assert anchor["parent"] == "Ball" and anchor["verts"] is None and anchor["data"] is None


def test_op_detail_quaternion_rotation(scene_bpy):
    obj = scene_bpy.data.objects[0]
    obj.rotation_mode = "QUATERNION"
    obj.matrix_basis = types.SimpleNamespace(to_euler=lambda: (0.0, 0.0, 3.141592653589793))
    assert op_object_detail.run({"name": "Cube"})["rotation_deg"] == [0.0, 0.0, 180.0]


def test_op_detail_not_found_suggests(scene_bpy):
    with pytest.raises(OpError) as e:
        op_object_detail.run({"name": "Bal"})
    assert e.value.code == "NOT_FOUND" and "did you mean: Ball" in e.value.message
    with pytest.raises(OpError) as e:
        op_object_detail.run({"name": "Qqqqqq"})
    assert "the file has 4 objects" in e.value.message
    scene_bpy.data.objects[:] = scene_bpy.data.objects[:1]
    with pytest.raises(OpError) as e:
        op_object_detail.run({"name": "Qqqqqq"})
    assert "(the file has 1 object)" in e.value.message
