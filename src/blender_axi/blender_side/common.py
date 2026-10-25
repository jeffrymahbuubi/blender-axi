"""Helpers shared by the ops (data API only; `bpy` is passed in so unit tests can use a fake)."""

import contextlib
import difflib

from .ops import OpError

SCENE_COLLECTION = "Scene Collection"


def find_collection(bpy, scene, wanted):
    """The collection to link to / move into: the active one for no name, else by name (NOT_FOUND when missing)."""
    if not wanted:
        coll = getattr(bpy.context, "collection", None)
        return coll if coll is not None else scene.collection
    if wanted == SCENE_COLLECTION or scene.collection.name == wanted:
        return scene.collection
    for coll in bpy.data.collections:
        if coll.name == wanted:
            return coll
    names = [c.name for c in bpy.data.collections]
    raise OpError("NOT_FOUND", f"collection '{wanted}' not found"
                  + (f" (available: {', '.join(names)})" if names else " (the file has no collections)"))


def count(n: int, noun: str) -> str:
    """`1 object`, `0 objects`, `4 objects` (adds 's' unless n == 1)."""
    return f"{n} {noun}{'' if n == 1 else 's'}"


def find_object(bpy, name, label: str = "object"):
    for candidate in bpy.data.objects:
        if candidate.name == name:
            return candidate
    names = [o.name for o in bpy.data.objects]
    close = difflib.get_close_matches(str(name), names, n=3, cutoff=0.5)
    hint = f" (did you mean: {', '.join(close)}?)" if close else f" (the file has {count(len(names), 'object')})"
    raise OpError("NOT_FOUND", f"{label} '{name}' not found{hint}")


def window_override(bpy, objs: list | None = None):
    """Context for an importer/exporter operator call.

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


def snapshot_selection(bpy, scene) -> dict:
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


def restore_selection(scene, snap: dict) -> list[str]:
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
