"""`export`: write the scene (or a collection / named objects) to a 3D interchange file (plan U5).

params: out (absolute path), format (glb|gltf|fbx|obj|stl|ply|usd|usdc|usda|abc), collection (name | None),
objects (list[str] | None), force.

`FORMAT_EXTS` / `GEOMETRY_TYPES` are pure data the CLI imports for validation.
"""

import os

from .ops import OpError

FORMAT_EXTS = {
    "glb": (".glb",), "gltf": (".gltf",), "fbx": (".fbx",), "obj": (".obj",), "stl": (".stl",),
    "ply": (".ply",), "usd": (".usd", ".usdc", ".usda"), "usdc": (".usdc",), "usda": (".usda",),
    "abc": (".abc",),
}
GEOMETRY_TYPES = {"MESH", "CURVE", "SURFACE", "META", "FONT", "CURVES", "ARMATURE"}


def _call(bpy, fmt: str, path: str, selected: bool) -> None:
    """Invoke the format's exporter. Verified operator names/params on Blender 5.2."""
    ops = bpy.ops
    if fmt == "obj":
        ops.wm.obj_export(filepath=path, export_selected_objects=selected)
    elif fmt in ("glb", "gltf"):
        ops.export_scene.gltf(
            filepath=path, export_format="GLB" if fmt == "glb" else "GLTF_SEPARATE", use_selection=selected)
    elif fmt == "fbx":
        ops.export_scene.fbx(filepath=path, use_selection=selected)
    elif fmt == "stl":
        ops.wm.stl_export(filepath=path, export_selected_objects=selected)
    elif fmt == "ply":
        ops.wm.ply_export(filepath=path, export_selected_objects=selected)
    elif fmt in ("usd", "usdc", "usda"):
        ops.wm.usd_export(filepath=path, selected_objects_only=selected)
    elif fmt == "abc":
        ops.wm.alembic_export(filepath=path, selected=selected)
    else:
        raise OpError("OP_FAILED", f"unsupported export format {fmt!r}")


def _collect(bpy, scene, collection: str | None, names: list[str] | None) -> list | None:
    """Objects to export, or None for the whole scene."""
    if collection:
        coll = bpy.data.collections.get(collection)
        if coll is None:
            raise OpError("NOT_FOUND", f"collection not found: {collection}")
        return list(coll.all_objects)
    if names:
        found, missing = [], []
        for name in names:
            obj = bpy.data.objects.get(name)
            (found if obj is not None else missing).append(obj if obj is not None else name)
        if missing:
            raise OpError("NOT_FOUND", "object not found: " + ", ".join(missing))
        return found
    return None


def _select(bpy, scene, objs: list) -> list:
    """Select exactly `objs` (those that can be selected); return the selected ones."""
    for o in list(scene.objects):
        try:
            o.select_set(False)
        except RuntimeError:
            pass
    chosen = []
    for o in objs:
        try:
            o.select_set(True)
            chosen.append(o)
        except RuntimeError:  # not in the view layer
            continue
    return chosen


def _snapshot_selection(bpy, scene) -> dict:
    """Selected object names and the active object, so live mode leaves the user's selection untouched."""
    selected = set()
    for o in list(scene.objects):
        try:
            if o.select_get():
                selected.add(o.name)
        except RuntimeError:  # not in the view layer
            continue
    view_layer = getattr(bpy.context, "view_layer", None)
    active = view_layer.objects.active if view_layer is not None else None
    return {"selected": selected, "active": active, "view_layer": view_layer}


def _restore_selection(scene, snap: dict) -> list[str]:
    """Best effort: returns descriptions of what could not be restored."""
    failures: list[str] = []
    for o in list(scene.objects):
        try:
            want = o.name in snap["selected"]
            if o.select_get() != want:
                o.select_set(want)
        except RuntimeError:
            continue
        except Exception as exc:
            failures.append(f"selection of {getattr(o, 'name', '?')}: {exc}")
    view_layer = snap["view_layer"]
    if view_layer is not None:
        try:
            if view_layer.objects.active is not snap["active"]:
                view_layer.objects.active = snap["active"]
        except Exception as exc:
            failures.append(f"active object: {exc}")
    return failures


def run(params: dict) -> dict:
    import bpy

    fmt = params["format"]
    path = params["out"]
    if os.path.exists(path) and not params.get("force"):
        raise OpError("WRITE_REFUSED", f"output exists: {path}")

    scene = bpy.context.scene
    objs = _collect(bpy, scene, params.get("collection"), params.get("objects"))
    pool = list(scene.objects) if objs is None else objs
    geometry = [o for o in pool if o.type in GEOMETRY_TYPES]
    if not geometry:
        what = "in the selection" if objs is not None else "in the scene"
        raise OpError("NOT_FOUND", f"no exportable objects {what} (need MESH, CURVE, SURFACE, META, FONT, CURVES or ARMATURE)")

    selected = objs is not None
    snap = _snapshot_selection(bpy, scene)
    try:
        if selected:
            chosen = _select(bpy, scene, objs)
            geometry = [o for o in chosen if o.type in GEOMETRY_TYPES]
            if not geometry:
                raise OpError("OP_FAILED", "none of the requested objects can be selected for export (hidden or excluded?)")

        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        _call(bpy, fmt, path, selected)
        if not os.path.isfile(path):
            raise OpError("OP_FAILED", f"the {fmt} exporter finished but wrote no file at {path}")
    finally:
        failures = _restore_selection(scene, snap)  # also on failure; never masks the original exception

    size = os.path.getsize(path)
    if fmt == "gltf":
        sidecar = os.path.splitext(path)[0] + ".bin"
        if os.path.isfile(sidecar):
            size += os.path.getsize(sidecar)
    result = {"path": path, "format": fmt, "objects": len(geometry), "size_bytes": size}
    if failures:
        result["restore_warnings"] = failures
    return result
