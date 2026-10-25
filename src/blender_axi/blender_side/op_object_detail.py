"""`object_detail`: everything worth knowing about one object. params: name (str)."""

import difflib
import math

from .common import count
from .ops import OpError


def _vec(v) -> list:
    return [round(float(x), 3) + 0.0 for x in v]


def _plain(value):
    """Make an ID-property value JSON-safe."""
    if hasattr(value, "to_dict"):
        return {str(k): _plain(v) for k, v in value.to_dict().items()}
    if hasattr(value, "to_list"):
        return [_plain(v) for v in value.to_list()]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


def run(params: dict) -> dict:
    import bpy

    name = params.get("name")
    obj = None
    for candidate in bpy.data.objects:
        if candidate.name == name:
            obj = candidate
            break
    if obj is None:
        names = [o.name for o in bpy.data.objects]
        close = difflib.get_close_matches(str(name), names, n=3, cutoff=0.5)
        hint = f" (did you mean: {', '.join(close)}?)" if close else f" (the file has {count(len(names), 'object')})"
        raise OpError("NOT_FOUND", f"object '{name}' not found{hint}")

    if obj.rotation_mode in ("QUATERNION", "AXIS_ANGLE"):
        euler = obj.matrix_basis.to_euler()
    else:
        euler = obj.rotation_euler
    try:
        visible = bool(obj.visible_get())
    except Exception:
        visible = not obj.hide_viewport
    is_mesh = obj.type == "MESH"
    props = {k: _plain(obj[k]) for k in obj.keys() if not str(k).startswith("_")}
    return {
        "name": obj.name,
        "type": obj.type,
        "collections": [c.name for c in obj.users_collection],
        "parent": obj.parent.name if obj.parent else None,
        "children": sorted(c.name for c in obj.children),
        "location": _vec(obj.location),
        "rotation_deg": [round(math.degrees(a), 3) + 0.0 for a in euler],
        "scale": _vec(obj.scale),
        "dimensions": _vec(obj.dimensions),
        "visible": visible,
        "data": obj.data.name if obj.data is not None else None,
        "verts": len(obj.data.vertices) if is_mesh else None,
        "faces": len(obj.data.polygons) if is_mesh else None,
        "materials": [s.material.name for s in obj.material_slots if s.material is not None],
        "modifiers": [{"name": m.name, "type": m.type} for m in obj.modifiers],
        "custom_props": props,
    }
