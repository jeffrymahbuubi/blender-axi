"""0.10.0 U5: the `tris` column of `objects` / line of `object` (fake transport, fake bpy, stubbed `tris.triangles`)."""

from __future__ import annotations

import types

import pytest

from blender_axi import flags
from blender_axi.blender_side import op_object_detail, op_objects_list
from blender_axi.commands import objects as cmd
from blender_axi.runner import RunOutcome


class FakeTransport:
    name = "fake"

    def __init__(self, result):
        self.result, self.calls = result, []

    def run(self, op, params, *, blend=None, save=None, timeout=None):
        self.calls.append((op, params))
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


def test_fields_tris_is_accepted_and_last():
    assert cmd.FIELDS[-1] == "tris" and op_objects_list.FIELDS == cmd.FIELDS
    listing = {"filepath": "x", "total": 2, "matched": 2, "rows": [
        {"name": "Cam", "type": "CAMERA", "collections": ["C"], "visible": True, "faces": None, "tris": None},
        {"name": "Cube", "type": "MESH", "collections": ["C"], "visible": True, "faces": 6, "tris": 12}]}
    t = FakeTransport(listing)
    res = invoke(cmd.OBJECTS_SPEC, ["--fields", "faces,TRIS"], t)
    assert t.calls[0][1]["fields"] == ["faces", "tris"]
    assert list(res.data["objects"][1]) == ["name", "type", "collection", "visible", "faces", "tris"]
    assert res.data["objects"][1]["tris"] == 12 and res.data["objects"][0]["tris"] is None


def test_object_prints_tris_after_faces_and_omits_it_for_a_camera():
    mesh = {"name": "Ball", "type": "MESH", "collections": ["C"], "parent": None, "children": [], "used_by": [],
            "location": [0, 0, 0], "rotation_deg": [0, 0, 0], "scale": [1, 1, 1], "dimensions": [2, 2, 2],
            "visible": True, "data": "Sphere", "verts": 482, "faces": 512, "tris": 960, "materials": [],
            "modifiers": [], "custom_props": {}}
    keys = list(invoke(cmd.OBJECT_SPEC, ["Ball"], FakeTransport(mesh)).data)
    assert keys[keys.index("faces") + 1] == "tris" and keys[keys.index("faces") - 1] == "verts"
    camera = dict(mesh, type="CAMERA", verts=None, faces=None, tris=None)
    assert "tris" not in invoke(cmd.OBJECT_SPEC, ["Cam"], FakeTransport(camera)).data
    no_key = {k: v for k, v in mesh.items() if k != "tris"}
    assert "tris" not in invoke(cmd.OBJECT_SPEC, ["Ball"], FakeTransport(no_key)).data


def _obj(name, kind="MESH"):
    return types.SimpleNamespace(
        name=name, type=kind, users_collection=[types.SimpleNamespace(name="C")], visible_get=lambda: True,
        location=(0, 0, 0), dimensions=(1, 1, 1), scale=(1, 1, 1), parent=None, children=[], rotation_mode="XYZ",
        rotation_euler=(0, 0, 0), hide_viewport=False, material_slots=[], modifiers=[], keys=lambda: [],
        data=types.SimpleNamespace(name=name, vertices=[], polygons=[]) if kind == "MESH" else None)


@pytest.fixture
def scene(fake_bpy, monkeypatch):
    objs = [_obj("A"), _obj("B"), _obj("Cam", "CAMERA")]
    asked = []
    fake_bpy.context.scene = types.SimpleNamespace(objects=objs, collection=types.SimpleNamespace(name="S"))
    fake_bpy.context.evaluated_depsgraph_get = lambda: asked.append(1) or "dg"
    fake_bpy.data.objects, fake_bpy.data.collections, fake_bpy.data.filepath = objs, [], "f.blend"
    calls = []

    def triangles(obj, depsgraph=None):
        calls.append((obj.name, depsgraph))
        return None if obj.type == "CAMERA" else 12

    monkeypatch.setattr(op_objects_list.tris, "triangles", triangles)
    return types.SimpleNamespace(asked=asked, calls=calls)


def test_op_list_asks_for_the_depsgraph_once_and_only_with_tris(scene):
    r = op_objects_list.run({"fields": ["tris"]})
    assert [x["tris"] for x in r["rows"]] == [12, 12, None]
    assert len(scene.asked) == 1 and scene.calls == [("A", "dg"), ("B", "dg"), ("Cam", "dg")]
    scene.asked.clear()
    scene.calls.clear()
    r = op_objects_list.run({"fields": ["faces"]})
    assert "tris" not in r["rows"][0] and scene.asked == [] and scene.calls == []


def test_op_detail_passes_the_evaluated_depsgraph(scene):
    assert op_object_detail.run({"name": "A"})["tris"] == 12
    assert op_object_detail.run({"name": "Cam"})["tris"] is None
    assert len(scene.asked) == 2 and scene.calls == [("A", "dg"), ("Cam", "dg")]
