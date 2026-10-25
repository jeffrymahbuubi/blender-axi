"""`set`: change one object's name, transform, parent, collection or visibility with the data API (no operators).

params: name, new_name, location[3], rotation_deg[3], scale[3], parent (object name | "none"), collection, hide
(True = hide, False = show; each may be None = leave alone).
Result: {name (final), changed: [{field, from, to}]}; `changed` lists only fields whose value really differed, so a
repeat call changes nothing (and the caller's fingerprint then reports modified: false). Fields: name, location,
rotation (degrees), scale, parent (name | None), collection ("A|B" of the old ones), hidden (bool).
Everything referenced is looked up before anything is changed, so a bad name leaves the object untouched.
"""

import difflib
import math

from .common import count, find_collection
from .ops import OpError

TOLERANCE = 1e-6
DEGREE_TOLERANCE = 1e-4


def _find(bpy, name, label: str = "object"):
    for candidate in bpy.data.objects:
        if candidate.name == name:
            return candidate
    names = [o.name for o in bpy.data.objects]
    close = difflib.get_close_matches(str(name), names, n=3, cutoff=0.5)
    hint = f" (did you mean: {', '.join(close)}?)" if close else f" (the file has {count(len(names), 'object')})"
    raise OpError("NOT_FOUND", f"{label} '{name}' not found{hint}")


def _vec(v) -> list:
    return [round(float(x), 3) + 0.0 for x in v]


def _differs(a, b, tolerance: float) -> bool:
    return any(abs(float(x) - float(y)) > tolerance for x, y in zip(a, b))


def _rotation_deg(ob) -> list:
    return [math.degrees(a) for a in ob.rotation_euler]


def _refresh(bpy) -> None:
    view_layer = getattr(bpy.context, "view_layer", None)
    if view_layer is not None:
        view_layer.update()  # matrix_world is stale until the depsgraph runs


def _is_below(candidate, ancestor) -> bool:
    node = candidate
    while node is not None:
        if node.name == ancestor.name:
            return True
        node = node.parent
    return False


def _hidden(ob, hide: bool) -> bool:
    """Current state as seen from the request: hide asks for 'fully hidden', show for 'nothing hidden anywhere'."""
    if hide:
        return bool(ob.hide_viewport and ob.hide_render)
    state = bool(ob.hide_viewport or ob.hide_render)
    try:
        state = state or bool(ob.hide_get())
    except Exception:
        pass
    return state


def _set_parent(bpy, ob, parent) -> None:
    _refresh(bpy)
    world = ob.matrix_world.copy()
    had_parent = ob.parent is not None
    ob.parent = parent
    if parent is not None:
        ob.matrix_parent_inverse = parent.matrix_world.inverted()
    if parent is None or had_parent:
        ob.matrix_world = world  # keep the object where it is in the world


def run(params: dict) -> dict:
    import bpy

    ob = _find(bpy, params.get("name"))
    changed = []

    # look everything up first: a missing parent/collection must leave the object untouched
    parent_req = params.get("parent")
    parent = None
    if parent_req is not None and parent_req != "none":
        parent = _find(bpy, parent_req, "parent object")
        if _is_below(parent, ob):
            raise OpError("OP_FAILED", f"cannot parent '{ob.name}' to '{parent.name}': "
                          + ("it is the same object" if parent is ob else "that would make a loop"))
    coll = None
    if params.get("collection"):
        coll = find_collection(bpy, bpy.context.scene, params["collection"])

    if parent_req is not None:
        before = ob.parent.name if ob.parent is not None else None
        after = parent.name if parent is not None else None
        if before != after:
            _set_parent(bpy, ob, parent)
            changed.append({"field": "parent", "from": before, "to": after})

    if coll is not None:
        before = [c.name for c in ob.users_collection]
        if before != [coll.name]:
            for old in list(ob.users_collection):
                old.objects.unlink(ob)
            coll.objects.link(ob)
            changed.append({"field": "collection", "from": "|".join(before) or "none", "to": coll.name})

    if params.get("location") is not None:
        want = [float(x) for x in params["location"]]
        if _differs(ob.location, want, TOLERANCE):
            changed.append({"field": "location", "from": _vec(ob.location), "to": _vec(want)})
            ob.location = tuple(want)
    if params.get("rotation_deg") is not None:
        want = [float(x) for x in params["rotation_deg"]]
        have = _rotation_deg(ob)
        if _differs(have, want, DEGREE_TOLERANCE):
            changed.append({"field": "rotation", "from": _vec(have), "to": _vec(want)})
            ob.rotation_euler = tuple(math.radians(x) for x in want)
    if params.get("scale") is not None:
        want = [float(x) for x in params["scale"]]
        if _differs(ob.scale, want, TOLERANCE):
            changed.append({"field": "scale", "from": _vec(ob.scale), "to": _vec(want)})
            ob.scale = tuple(want)

    hide = params.get("hide")
    if hide is not None:
        hide = bool(hide)
        if _hidden(ob, hide) != hide:
            changed.append({"field": "hidden", "from": not hide, "to": hide})
            ob.hide_viewport = ob.hide_render = hide
            if not hide:
                try:
                    ob.hide_set(False)  # view-layer state; may raise without a view-layer context (A3)
                except Exception:
                    pass

    new_name = params.get("new_name")
    if new_name and new_name != ob.name:
        old = ob.name
        ob.name = new_name
        changed.append({"field": "name", "from": old, "to": ob.name})  # Blender may add a .001 suffix

    _refresh(bpy)
    return {"name": ob.name, "changed": changed}
