"""`names`: every object, collection and material name of the loaded file (read-only; the Blender side of `apply --strict`).

params: none. Result: {objects: [{name, type}], collections: [names, the scene collection first], materials: [names],
scene_camera: name | None}. Data API only, nothing is changed, so it is not in `live.MUTATING_OPS`.
"""


def run(params: dict) -> dict:
    import bpy

    scene = bpy.context.scene
    collections = [scene.collection.name]
    collections += [c.name for c in bpy.data.collections if c.name not in collections]
    camera = getattr(scene, "camera", None)
    return {
        "objects": [{"name": o.name, "type": o.type} for o in bpy.data.objects],
        "collections": collections,
        "materials": [m.name for m in bpy.data.materials],
        "scene_camera": camera.name if camera is not None else None,
    }
