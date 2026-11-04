"""`export`: write the scene (or a collection / named objects) to a 3D interchange file (plan U5, 0.5.0 U1).

params: out (absolute path), format (glb|gltf|fbx|obj|stl|ply|usd|usdc|usda|abc), collection (name | None),
objects (list[str] | None), exclude (list[str] | None), include_hidden, apply_modifiers (default True),
dry_run, force, cameras (glb/gltf: also write the cameras).

The export set (0.5.0): pool (scene / collection / named objects) minus `exclude` minus hidden geometry (unless
`include_hidden`). The whole-scene exporter path is used only when nothing was filtered out of the scene;
otherwise the selection path (`_select` + `_override`) exports exactly the set.

`FORMAT_EXTS` / `GEOMETRY_TYPES` are pure data the CLI imports for validation.
"""

import contextlib
import os

from . import tris
from .ops import OpError

FORMAT_EXTS = {
    "glb": (".glb",), "gltf": (".gltf",), "fbx": (".fbx",), "obj": (".obj",), "stl": (".stl",),
    "ply": (".ply",), "usd": (".usd", ".usdc", ".usda"), "usdc": (".usdc",), "usda": (".usda",),
    "abc": (".abc",),
}
GEOMETRY_TYPES = {"MESH", "CURVE", "SURFACE", "META", "FONT", "CURVES", "ARMATURE"}
EVALUATED_FORMATS = ("usd", "usdc", "usda", "abc")  # no raw-mesh mode: they always export the evaluated scene
NODE_FORMATS = ("glb", "gltf", "fbx", "usd", "usdc", "usda", "abc")  # formats that write non-geometry nodes (Empties)


def _call(bpy, fmt: str, path: str, selected: bool, apply: bool = True, cameras: bool = False) -> None:
    """Invoke the format's exporter. Verified operator names/params on Blender 5.2."""
    ops = bpy.ops
    if fmt == "obj":
        ops.wm.obj_export(filepath=path, export_selected_objects=selected, apply_modifiers=apply)
    elif fmt in ("glb", "gltf"):
        ops.export_scene.gltf(
            filepath=path, export_format="GLB" if fmt == "glb" else "GLTF_SEPARATE", use_selection=selected,
            export_apply=apply, export_cameras=bool(cameras))
    elif fmt == "fbx":
        ops.export_scene.fbx(filepath=path, use_selection=selected, use_mesh_modifiers=apply)
    elif fmt == "stl":
        ops.wm.stl_export(filepath=path, export_selected_objects=selected, apply_modifiers=apply)
    elif fmt == "ply":
        ops.wm.ply_export(filepath=path, export_selected_objects=selected, apply_modifiers=apply)
    elif fmt in ("usd", "usdc", "usda"):
        ops.wm.usd_export(filepath=path, selected_objects_only=selected)
    elif fmt == "abc":
        ops.wm.alembic_export(filepath=path, selected=selected)
    else:
        raise OpError("OP_FAILED", f"unsupported export format {fmt!r}")


def _override(bpy, objs: list | None):
    """Context for the exporter call.

    A headless MCP server has `context.window` None (but a window in the window manager) and no
    `selected_objects`/`active_object`, so the C++ exporters fail `poll()` and glTF raises. Supply the missing
    window, and the selection when a selective export is requested. In the GUI and in headless non-live runs the
    window already exists, so nothing is overridden for whole-scene exports.
    """
    kw: dict = {}
    if getattr(bpy.context, "window", None) is None:
        windows = getattr(getattr(bpy.context, "window_manager", None), "windows", None)
        if windows is not None and len(windows):
            kw["window"] = windows[0]
    if objs:
        kw["selected_objects"] = objs
        kw["active_object"] = objs[0]
    return bpy.context.temp_override(**kw) if kw else contextlib.nullcontext()


def _pool(bpy, collection: str | None, names: list[str] | None) -> list | None:
    """Objects the user named (collection or names), or None for the whole scene."""
    if collection:
        coll = bpy.data.collections.get(collection)
        if coll is None:
            raise OpError("NOT_FOUND", f"collection not found: {collection}")
        return list(coll.all_objects)
    if names:
        return [bpy.data.objects.get(n) for n in names]
    return None


def _require_objects(bpy, names: list[str]) -> None:
    missing = [n for n in dict.fromkeys(names) if bpy.data.objects.get(n) is None]
    if missing:
        raise OpError("NOT_FOUND", "object not found: " + ", ".join(missing))


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


def _visible(obj) -> bool:
    try:
        return bool(obj.visible_get())
    except RuntimeError:  # not in the view layer
        return False


def _unhide(objs: list) -> list:
    """Make hidden objects selectable and exportable; returns the state to put back."""
    saved = []
    for o in objs:
        try:
            was_hidden = o.hide_get()
        except RuntimeError:  # not in the view layer (excluded collection): cannot be un-hidden
            continue
        saved.append((o, o.hide_viewport, was_hidden))
        o.hide_viewport = False
        o.hide_set(False)
    return saved


def _rehide(saved: list) -> list[str]:
    failures: list[str] = []
    for o, hide_viewport, was_hidden in saved:
        name = getattr(o, "name", "?")
        try:
            o.hide_set(was_hidden)
            o.hide_viewport = hide_viewport
        except Exception as exc:
            failures.append(f"hidden state of {name}: {exc}")
    return failures


def _modifier_mode(fmt: str, apply: bool) -> tuple[str, str | None]:
    if fmt in EVALUATED_FORMATS:
        note = None if apply else f"--no-apply-modifiers ignored: {fmt} exports the evaluated scene"
        return "evaluated", note
    return ("applied" if apply else "raw"), None


def _export_set(bpy, scene, params: dict) -> dict:
    """Pool minus excluded minus hidden. `objs` is None when the pool is the whole scene."""
    exclude = list(dict.fromkeys(params.get("exclude") or []))
    names = list(dict.fromkeys(params.get("objects") or [])) or None
    _require_objects(bpy, (names or []) + exclude)
    objs = _pool(bpy, params.get("collection"), names)
    pool = list(scene.objects) if objs is None else objs
    barred = set(exclude)
    remaining = [o for o in pool if o.name not in barred]
    hidden = []
    if not params.get("include_hidden"):
        hidden = [o for o in remaining if o.type in GEOMETRY_TYPES and not _visible(o)]
    hidden_ids = {id(o) for o in hidden}
    chosen = [o for o in remaining if id(o) not in hidden_ids]
    geometry = [o for o in chosen if o.type in GEOMETRY_TYPES]
    if not geometry:
        what = "in the selection" if objs is not None else "in the scene"
        extra = f" ({len(hidden)} hidden; add --include-hidden)" if hidden else ""
        raise OpError("NOT_FOUND", f"no exportable objects {what} "
                                    f"(need MESH, CURVE, SURFACE, META, FONT, CURVES or ARMATURE){extra}")
    return {"set": chosen, "geometry": geometry, "hidden": hidden, "excluded": exclude,
            "whole": objs is None and len(chosen) == len(pool)}


def _also(fmt: str, objs: list, cameras: bool) -> list[dict]:
    """Non-geometry objects of the set that the format writes as nodes: Empties, plus cameras under `cameras`."""
    types = (("EMPTY",) if fmt in NODE_FORMATS else ()) + (("CAMERA",) if cameras else ())
    return [{"name": o.name, "type": o.type} for o in objs if o.type in types]


def _triangles(bpy, objs: list, fmt: str, apply: bool) -> int:
    """Triangles the exporter writes: evaluated meshes when modifiers apply (or the format always evaluates)."""
    evaluated = apply or fmt in EVALUATED_FORMATS
    depsgraph = bpy.context.evaluated_depsgraph_get() if evaluated else None
    return sum(tris.triangles(o, depsgraph) or 0 for o in objs)


def run(params: dict) -> dict:
    import bpy

    fmt = params["format"]
    path = params["out"]
    apply = params.get("apply_modifiers", True)
    dry_run = bool(params.get("dry_run"))
    if not dry_run and os.path.exists(path) and not params.get("force"):
        raise OpError("WRITE_REFUSED", f"output exists: {path}")

    scene = bpy.context.scene
    plan = _export_set(bpy, scene, params)
    modifiers, note = _modifier_mode(fmt, apply)
    hidden_names = [o.name for o in plan["hidden"]]
    geometry = plan["geometry"]
    cameras = bool(params.get("cameras"))
    if dry_run:
        return {"format": fmt, "dry_run": True, "objects": len(geometry),
                "export_set": [{"name": o.name, "type": o.type} for o in geometry],
                "also": _also(fmt, plan["set"], cameras), "triangles": _triangles(bpy, geometry, fmt, apply),
                "skipped_hidden": hidden_names, "excluded": plan["excluded"],
                "non_geometry": [o.name for o in plan["set"] if o.type not in GEOMETRY_TYPES],
                "modifiers": modifiers, "modifiers_note": note}

    selected = not plan["whole"]
    snap = _snapshot_selection(bpy, scene)
    saved_hidden: list = []
    failures: list[str] = []
    try:
        if params.get("include_hidden"):  # S1: hidden members would otherwise be unselectable / skipped by the exporter
            saved_hidden = _unhide([o for o in plan["set"] if o.type in GEOMETRY_TYPES and not _visible(o)])
            view_layer = getattr(bpy.context, "view_layer", None)
            if saved_hidden and hasattr(view_layer, "update"):
                view_layer.update()
            stuck = [o for o in plan["set"] if o.type in GEOMETRY_TYPES and not _visible(o)]
            if stuck:  # e.g. in a collection excluded from the view layer: no exporter can reach them
                hidden_names = [o.name for o in stuck]
                selected = True
        chosen = None
        if selected:
            chosen = _select(bpy, scene, plan["set"])
            geometry = [o for o in chosen if o.type in GEOMETRY_TYPES]
            if not geometry:
                raise OpError("OP_FAILED", "none of the requested objects can be selected for export (hidden or excluded?)")

        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with _override(bpy, chosen):
            _call(bpy, fmt, path, selected, apply, cameras)
        if not os.path.isfile(path):
            raise OpError("OP_FAILED", f"the {fmt} exporter finished but wrote no file at {path}")
        triangle_count = _triangles(bpy, geometry, fmt, apply)  # while hidden members are still un-hidden
    finally:
        failures += _rehide(saved_hidden)  # hide state first: hide_set(True) deselects
        failures += _restore_selection(scene, snap)  # also on failure; never masks the original exception

    size = os.path.getsize(path)
    if fmt == "gltf":
        sidecar = os.path.splitext(path)[0] + ".bin"
        if os.path.isfile(sidecar):
            size += os.path.getsize(sidecar)
    result = {"path": path, "format": fmt, "objects": len(geometry), "triangles": triangle_count, "size_bytes": size,
              "skipped_hidden": hidden_names, "excluded": plan["excluded"],
              "modifiers": modifiers, "modifiers_note": note}
    if cameras:
        written = chosen if chosen is not None else plan["set"]
        result["cameras"] = [o.name for o in written if o.type == "CAMERA"]
    if failures:
        result["restore_warnings"] = failures
    return result
