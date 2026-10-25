"""`objects_list`: filtered, limited object rows for the active scene.

params: type (str|None), collection (str|None), name_contains (str|None), limit (int), fields (list[str]).
Result: {filepath, total, matched, rows[{name,type,collections,visible,<fields>}]}; field values are raw
(numbers/lists/None); the CLI formats them.
"""

from .ops import OpError

FIELDS = ("location", "dimensions", "parent", "verts", "faces", "materials", "modifiers", "data")


def _vec(v) -> list:
    return [round(float(x), 3) + 0.0 for x in v]


def _visible(obj) -> bool:
    try:
        return bool(obj.visible_get())
    except Exception:
        return not getattr(obj, "hide_viewport", False)


def _field(obj, name: str):
    is_mesh = obj.type == "MESH"
    if name == "location":
        return _vec(obj.location)
    if name == "dimensions":
        return _vec(obj.dimensions)
    if name == "parent":
        return obj.parent.name if obj.parent else None
    if name == "verts":
        return len(obj.data.vertices) if is_mesh else None
    if name == "faces":
        return len(obj.data.polygons) if is_mesh else None
    if name == "materials":
        return sum(1 for s in obj.material_slots if s.material is not None)
    if name == "modifiers":
        return len(obj.modifiers)
    if name == "data":
        return obj.data.name if obj.data is not None else None
    raise OpError("OP_FAILED", f"unknown field {name!r}")


def _find(seq, name: str):
    for item in seq:
        if item.name == name:
            return item
    return None


def run(params: dict) -> dict:
    import bpy

    scene = bpy.context.scene
    objects = list(scene.objects)
    total = len(objects)

    wanted_collection = params.get("collection")
    if wanted_collection:
        coll = scene.collection if scene.collection.name == wanted_collection else _find(
            bpy.data.collections, wanted_collection)
        if coll is None:
            names = [c.name for c in bpy.data.collections]
            raise OpError("NOT_FOUND", f"collection '{wanted_collection}' not found"
                          + (f" (available: {', '.join(names)})" if names else " (the file has no collections)"))
        members = {o.name for o in coll.all_objects}
        objects = [o for o in objects if o.name in members]
    if params.get("type"):
        objects = [o for o in objects if o.type == params["type"]]
    needle = (params.get("name_contains") or "").lower()
    if needle:
        objects = [o for o in objects if needle in o.name.lower()]

    objects.sort(key=lambda o: o.name.lower())
    matched = len(objects)
    limit = params.get("limit")
    if limit is not None:
        objects = objects[: max(int(limit), 0)]

    fields = [f for f in (params.get("fields") or []) if f in FIELDS]
    rows = []
    for obj in objects:
        row = {"name": obj.name, "type": obj.type, "collections": [c.name for c in obj.users_collection],
               "visible": _visible(obj)}
        for f in fields:
            row[f] = _field(obj, f)
        rows.append(row)
    return {"filepath": bpy.data.filepath, "total": total, "matched": matched, "rows": rows}
