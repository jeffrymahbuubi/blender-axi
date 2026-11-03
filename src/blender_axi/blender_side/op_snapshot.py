"""`snapshot`: a comparable, JSON-safe picture of the loaded file (the Blender side of `diff`). params: none.

Result: {filepath, blender_version, scene{name, engine, resolution[w,h,pct], frames[start,end], fps, camera, world},
objects[{name, type, collections, parent, location, rotation_deg, scale, dimensions, visible, hide_render, data,
verts, faces, materials, modifiers[{name, type, settings}], custom_props}]} with objects sorted by name.
Read-only: nothing is changed, so it is not in `live.MUTATING_OPS`.
"""

import math

PRIMITIVES = ("BOOLEAN", "INT", "FLOAT", "STRING", "ENUM")
SKIP_SETTINGS = frozenset({"rna_type", "name", "type", "is_override_data", "is_active", "persistent_uid"})


def _bevel_skip(mod) -> frozenset:
    """A bevel's `width`/`width_pct` are one value; only the one `offset_type` uses is recorded."""
    return frozenset({"width"}) if getattr(mod, "offset_type", None) == "PERCENT" else frozenset({"width_pct"})


# modifier type -> fn(mod) -> property ids to leave out of `settings`
CONDITIONAL = {"BEVEL": _bevel_skip}


def _vec(v) -> list:
    return [round(float(x), 3) + 0.0 for x in v]


def _plain(value):
    """Make an ID-property value JSON-safe (same rule as `object_detail`)."""
    if hasattr(value, "to_dict"):
        return {str(k): _plain(v) for k, v in value.to_dict().items()}
    if hasattr(value, "to_list"):
        return [_plain(v) for v in value.to_list()]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


def _prim(value):
    """A modifier property value as str/int/bool/float(3 dp)/list of those."""
    if isinstance(value, bool) or isinstance(value, (int, str)) or value is None:
        return value
    if isinstance(value, float):
        return round(value, 3) + 0.0
    if isinstance(value, (set, frozenset)):
        return sorted(str(v) for v in value)
    try:
        return [_prim(v) for v in value]
    except TypeError:
        return repr(value)


def _is_id_pointer(prop) -> bool:
    struct = getattr(prop, "fixed_type", None)
    while struct is not None:
        if getattr(struct, "identifier", None) == "ID":
            return True
        struct = getattr(struct, "base", None)
    return False


def _settings(mod) -> dict:
    out: dict = {}
    skip = SKIP_SETTINGS | CONDITIONAL.get(mod.type, lambda m: frozenset())(mod)
    for prop in mod.bl_rna.properties:
        pid = prop.identifier
        if pid in skip or pid.startswith("show_"):
            continue
        kind = prop.type
        try:
            if kind in PRIMITIVES:
                if getattr(prop, "is_readonly", False):
                    continue
                out[pid] = _prim(getattr(mod, pid))
            elif kind == "POINTER" and _is_id_pointer(prop):
                target = getattr(mod, pid)
                out[pid] = target.name if target is not None else None
        except Exception:  # a property that cannot be read in this context is simply not compared
            continue
    return out


def _rotation_deg(obj) -> list:
    euler = obj.matrix_basis.to_euler() if obj.rotation_mode in ("QUATERNION", "AXIS_ANGLE") else obj.rotation_euler
    return [round(math.degrees(a), 3) + 0.0 for a in euler]


def _visible(obj) -> bool:
    try:
        return bool(obj.visible_get())
    except Exception:
        return not obj.hide_viewport


def _object(obj) -> dict:
    is_mesh = obj.type == "MESH"
    return {
        "name": obj.name,
        "type": obj.type,
        "collections": sorted(c.name for c in obj.users_collection),
        "parent": obj.parent.name if obj.parent else None,
        "location": _vec(obj.location),
        "rotation_deg": _rotation_deg(obj),
        "scale": _vec(obj.scale),
        "dimensions": _vec(obj.dimensions),
        "visible": _visible(obj),
        "hide_render": bool(obj.hide_render),
        "data": obj.data.name if obj.data is not None else None,
        "verts": len(obj.data.vertices) if is_mesh else None,
        "faces": len(obj.data.polygons) if is_mesh else None,
        "materials": [s.material.name for s in obj.material_slots if s.material is not None],
        "modifiers": [{"name": m.name, "type": m.type, "settings": _settings(m)} for m in obj.modifiers],
        "custom_props": {k: _plain(obj[k]) for k in obj.keys() if not str(k).startswith("_")},
    }


def run(params: dict) -> dict:
    import bpy

    scene = bpy.context.scene
    render = scene.render
    return {
        "filepath": bpy.data.filepath,
        "blender_version": bpy.app.version_string,
        "scene": {
            "name": scene.name,
            "engine": render.engine,
            "resolution": [render.resolution_x, render.resolution_y, render.resolution_percentage],
            "frames": [scene.frame_start, scene.frame_end],
            "fps": round(render.fps / (render.fps_base or 1), 3),
            "camera": scene.camera.name if scene.camera else None,
            "world": scene.world.name if scene.world else None,
        },
        "objects": [_object(o) for o in sorted(bpy.data.objects, key=lambda o: o.name)],
    }
