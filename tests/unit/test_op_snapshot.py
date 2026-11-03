"""Unit tests for the `snapshot` op (fake bpy)."""

from __future__ import annotations

import json
import types

from blender_axi.blender_side import op_snapshot, ops

NS = types.SimpleNamespace


def prop(identifier, kind, readonly=False, fixed=None):
    return NS(identifier=identifier, type=kind, is_readonly=readonly, fixed_type=fixed)


ID_STRUCT = NS(identifier="Object", base=NS(identifier="ID", base=None))
PLAIN_STRUCT = NS(identifier="Thing", base=None)


def modifier(name="Bevel", kind="BEVEL", offset_type="OFFSET"):
    props = [
        prop("rna_type", "POINTER", True, PLAIN_STRUCT), prop("name", "STRING"), prop("type", "ENUM", True),
        prop("show_viewport", "BOOLEAN"), prop("show_render", "BOOLEAN"), prop("is_override_data", "BOOLEAN"),
        prop("persistent_uid", "INT", True), prop("is_active", "BOOLEAN"), prop("execution_time", "FLOAT", True),
        prop("width", "FLOAT"), prop("width_pct", "FLOAT"), prop("offset_type", "ENUM"), prop("segments", "INT"), prop("use_clamp", "BOOLEAN"), prop("vertex_group", "STRING"),
        prop("affect", "ENUM"), prop("flags", "ENUM"), prop("offset", "FLOAT"), prop("mirror_object", "POINTER", False, ID_STRUCT),
        prop("origin", "POINTER", False, ID_STRUCT), prop("settings", "POINTER", False, PLAIN_STRUCT),
        prop("curve", "COLLECTION"),
    ]
    return NS(name=name, type=kind, bl_rna=NS(properties=props), width=0.12345, width_pct=0.54321, offset_type=offset_type, segments=3, use_clamp=True,
              vertex_group="", affect="EDGES", flags={"B", "A"}, offset=(0.0, 1.23456, -0.0), mirror_object=NS(name="Mirror"),
              origin=None, settings=object(), curve=[], show_viewport=True, show_render=True, is_override_data=False,
              persistent_uid=123456, is_active=True, execution_time=0.001, rna_type=object())


class FakeObj:
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def __getitem__(self, key):
        return {"a": 1.5, "_hidden": 9}[key]


def obj(name, kind="MESH", **kw):
    base = dict(
        name=name, type=kind, users_collection=[NS(name="Coll")], parent=None, location=(1, 2, 3), rotation_mode="XYZ",
        rotation_euler=(0.0, 0.0, 1.5707963), scale=(1, 1, 1), dimensions=(2, 2, 2), visible_get=lambda: True,
        hide_render=False, data=NS(name=name + "Mesh", vertices=range(8), polygons=range(6)),
        material_slots=[NS(material=NS(name="Mat")), NS(material=None)], modifiers=[], keys=lambda: ["a", "_hidden"],
        hide_viewport=False)
    base.update(kw)
    return FakeObj(**base)


def fake_scene(**kw):
    render = NS(engine="BLENDER_EEVEE", resolution_x=1920, resolution_y=1080, resolution_percentage=100, fps=24,
                fps_base=1.0)
    base = dict(name="Scene", render=render, frame_start=1, frame_end=250, camera=NS(name="Cam"), world=None)
    base.update(kw)
    return NS(**base)


def install(fake_bpy, objects, scene=None):
    fake_bpy.data = NS(objects=objects, filepath="C:/x/a.blend")
    fake_bpy.context = NS(scene=scene or fake_scene())
    fake_bpy.app = NS(version_string="5.2.0 LTS")


def test_shape_sorted_and_json_safe(fake_bpy):
    install(fake_bpy, [obj("Zed"), obj("Alpha", rotation_mode="QUATERNION", matrix_basis=NS(to_euler=lambda: (0, 0, 0)))])
    r = op_snapshot.run({})
    json.dumps(r)
    assert list(r) == ["filepath", "blender_version", "scene", "objects"]
    assert r["scene"] == {"name": "Scene", "engine": "BLENDER_EEVEE", "resolution": [1920, 1080, 100], "frames": [1, 250],
                          "fps": 24.0, "camera": "Cam", "world": None}
    assert [o["name"] for o in r["objects"]] == ["Alpha", "Zed"]
    zed = r["objects"][1]
    assert zed["rotation_deg"] == [0.0, 0.0, 90.0] and zed["location"] == [1.0, 2.0, 3.0]
    assert zed["collections"] == ["Coll"] and zed["materials"] == ["Mat"] and zed["verts"] == 8 and zed["faces"] == 6
    assert zed["data"] == "ZedMesh" and zed["visible"] is True and zed["hide_render"] is False
    assert zed["custom_props"] == {"a": 1.5}
    assert r["objects"][0]["rotation_deg"] == [0.0, 0.0, 0.0]  # quaternion objects go through matrix_basis


def test_non_mesh_object_has_no_counts_and_hidden_falls_back(fake_bpy):
    def broken():
        raise RuntimeError("not in view layer")

    install(fake_bpy, [obj("Cam", "CAMERA", data=None, visible_get=broken, hide_viewport=True)])
    o = op_snapshot.run({})["objects"][0]
    assert o["verts"] is None and o["faces"] is None and o["data"] is None and o["visible"] is False


def test_modifier_settings_pick_primitives_and_id_pointers(fake_bpy):
    install(fake_bpy, [obj("A", modifiers=[modifier()])])
    m = op_snapshot.run({})["objects"][0]["modifiers"][0]
    assert (m["name"], m["type"]) == ("Bevel", "BEVEL")
    assert m["settings"] == {"width": 0.123, "offset_type": "OFFSET", "segments": 3, "use_clamp": True, "vertex_group": "", "affect": "EDGES",
                             "flags": ["A", "B"], "offset": [0.0, 1.235, 0.0], "mirror_object": "Mirror", "origin": None}


def test_bevel_records_only_the_width_its_offset_type_uses(fake_bpy):
    install(fake_bpy, [obj("A", modifiers=[modifier()]), obj("B", modifiers=[modifier(offset_type="PERCENT")])])
    a, b = (o["modifiers"][0]["settings"] for o in op_snapshot.run({})["objects"])
    assert "width" in a and "width_pct" not in a and a["offset_type"] == "OFFSET"
    assert b["width_pct"] == 0.543 and "width" not in b and b["offset_type"] == "PERCENT"


def test_non_bevel_modifier_keeps_both_width_properties(fake_bpy):
    install(fake_bpy, [obj("A", modifiers=[modifier("Other", "ARRAY"), modifier("Other2", "ARRAY", "PERCENT")])])
    for m in op_snapshot.run({})["objects"][0]["modifiers"]:
        assert "width" in m["settings"] and "width_pct" in m["settings"]


def test_conditional_keys_are_modifier_types():
    assert set(op_snapshot.CONDITIONAL) == {"BEVEL"}


def test_op_is_reachable_through_the_dispatcher_and_is_read_only(fake_bpy):
    install(fake_bpy, [])
    assert ops.run("snapshot", {})["objects"] == []
    from blender_axi import live

    assert "snapshot" not in live.MUTATING_OPS
