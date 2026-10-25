"""`scene_summary`: one-call overview of the active scene (settings, camera, object/collection counts)."""


def run(params: dict) -> dict:
    import bpy

    from blender_axi.blender_side import session

    scene = bpy.context.scene
    render = scene.render
    types: dict[str, int] = {}
    objects = list(scene.objects)
    for obj in objects:
        types[obj.type] = types.get(obj.type, 0) + 1
    try:
        collections = [c.name for c in scene.collection.children_recursive]
    except AttributeError:
        collections = [c.name for c in bpy.data.collections]
    return {
        "filepath": bpy.data.filepath,
        "blender_version": bpy.app.version_string,
        "scene": scene.name,
        "engine": render.engine,
        "res_x": render.resolution_x,
        "res_y": render.resolution_y,
        "percent": render.resolution_percentage,
        "frame_start": scene.frame_start,
        "frame_end": scene.frame_end,
        "frame_current": scene.frame_current,
        "fps": render.fps,
        "fps_base": render.fps_base,
        "camera": scene.camera.name if scene.camera else None,
        "object_count": len(objects),
        "types": types,
        "collections": collections,
        "materials": [m.name for m in bpy.data.materials],
        "is_dirty": session.unsaved_changes(bpy),  # dirty flag or a valid blender-axi marker (live)
    }
