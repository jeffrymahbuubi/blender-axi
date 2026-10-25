"""Generate test .blend files. Runs inside Blender:

    blender --background --factory-startup --python make_fixtures.py -- <out_dir>

Writes `basic.blend` (Cube, Camera, Light, collection Props with UV sphere Ball and Empty Anchor,
frames 1-24, 64x64) and `empty.blend` (no objects).
"""

import sys
from pathlib import Path

import bpy


def _setup_scene(scene) -> None:
    scene.frame_start = 1
    scene.frame_end = 24
    scene.render.resolution_x = 64
    scene.render.resolution_y = 64
    scene.render.resolution_percentage = 100


def _build_basic(out: Path) -> None:
    bpy.ops.wm.read_factory_settings(use_empty=False)
    scene = bpy.context.scene
    _setup_scene(scene)

    props = bpy.data.collections.new("Props")
    scene.collection.children.link(props)

    bpy.ops.mesh.primitive_uv_sphere_add(location=(3, 0, 0))
    ball = bpy.context.active_object
    ball.name = "Ball"
    for coll in list(ball.users_collection):
        coll.objects.unlink(ball)
    props.objects.link(ball)

    anchor = bpy.data.objects.new("Anchor", None)
    anchor.location = (0, 3, 0)
    props.objects.link(anchor)

    scene.camera = bpy.data.objects["Camera"]
    bpy.ops.wm.save_as_mainfile(filepath=str(out / "basic.blend"))


def _build_empty(out: Path) -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    _setup_scene(bpy.context.scene)
    bpy.ops.wm.save_as_mainfile(filepath=str(out / "empty.blend"))


def main(argv: list[str]) -> None:
    if not argv:
        raise SystemExit("usage: make_fixtures.py -- <out_dir>")
    out = Path(argv[0]).resolve()
    out.mkdir(parents=True, exist_ok=True)
    _build_basic(out)
    _build_empty(out)


if __name__ == "__main__":
    main(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else [])
