"""0.5.0 U2: the read-only `names` op (fake bpy)."""

from types import SimpleNamespace as NS

from blender_axi import live
from blender_axi.blender_side import op_names, ops


def scene(camera=None, name="Scene Collection"):
    return NS(collection=NS(name=name), camera=camera)


def test_shape_and_scene_collection_first(fake_bpy):
    fake_bpy.context.scene = scene(NS(name="Cam"))
    fake_bpy.data.objects = [NS(name="Ring", type="MESH"), NS(name="Cam", type="CAMERA")]
    fake_bpy.data.collections = [NS(name="Parts"), NS(name="Scene Collection")]
    fake_bpy.data.materials = [NS(name="Red")]
    assert op_names.run({}) == {
        "objects": [{"name": "Ring", "type": "MESH"}, {"name": "Cam", "type": "CAMERA"}],
        "collections": ["Scene Collection", "Parts"], "materials": ["Red"], "scene_camera": "Cam"}


def test_empty_file_and_no_camera(fake_bpy):
    fake_bpy.context.scene = scene(None, name="Master")
    fake_bpy.data.objects, fake_bpy.data.collections, fake_bpy.data.materials = [], [], []
    assert op_names.run({}) == {"objects": [], "collections": ["Master"], "materials": [], "scene_camera": None}


def test_dispatch_and_read_only(fake_bpy):
    fake_bpy.context.scene = scene()
    fake_bpy.data.objects, fake_bpy.data.collections, fake_bpy.data.materials = [], [], []
    assert ops.run("names", {})["scene_camera"] is None
    assert "names" not in live.MUTATING_OPS  # live mode must not track or undo it
