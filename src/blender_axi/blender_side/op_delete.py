"""`delete`: remove objects by name, keeping their children in place (unparented, world transform preserved).

params: names [str, ...]. All-or-nothing: every missing name is reported in one NOT_FOUND and nothing is removed.
Result: {deleted: [names], children_kept: [names of children that survive], remaining: object count after}.
"""

import difflib

from .common import count
from .ops import OpError


def run(params: dict) -> dict:
    import bpy

    wanted = []
    for name in params.get("names") or []:
        if name not in wanted:
            wanted.append(name)
    if not wanted:
        raise OpError("OP_FAILED", "no object names given")
    by_name = {o.name: o for o in bpy.data.objects}
    missing = [n for n in wanted if n not in by_name]
    if missing:
        close = {m: difflib.get_close_matches(str(m), list(by_name), n=1, cutoff=0.5) for m in missing}
        shown = ", ".join(f"'{m}' (did you mean: {close[m][0]}?)" if close[m] else f"'{m}'" for m in missing)
        label = "object" if len(missing) == 1 else "objects"
        raise OpError("NOT_FOUND", f"{label} {shown} not found (the file has {count(len(by_name), 'object')}); nothing deleted")

    doomed = [by_name[n] for n in wanted]
    doomed_names = set(wanted)
    view_layer = getattr(bpy.context, "view_layer", None)
    if view_layer is not None:
        view_layer.update()  # matrix_world is stale until the depsgraph runs
    kept: list[str] = []
    for ob in doomed:
        for child in list(ob.children):
            if child.name in doomed_names:
                continue
            world = child.matrix_world.copy()
            child.parent = None
            child.matrix_world = world
            if child.name not in kept:
                kept.append(child.name)
    for ob in doomed:
        bpy.data.objects.remove(ob)
    return {"deleted": wanted, "children_kept": sorted(kept), "remaining": len(bpy.data.objects)}
