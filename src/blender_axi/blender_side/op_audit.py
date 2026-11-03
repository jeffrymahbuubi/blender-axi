"""`audit`: read-only asset checks over the active scene (0.6.0 plan §3.4, K3).

params: checks (list[str] | None = all), objects (list[str] | None), collection (str | None).
Result: {filepath, objects_checked, checks_run, scene_checks_skipped, findings[{check, level, count, items}]}
(only checks with count > 0, in CHECK_IDS order) and, when something was unavailable, warnings[str].
Scene-level checks are skipped when an object or collection filter is given.
"""

from .common import find_collection, find_object
from .ops import OpError


def _objects(bpy, scene, names, collection):
    if names:
        objs = [find_object(bpy, n) for n in names]
    else:
        objs = list(scene.objects)
    if collection:
        members = {o.name for o in find_collection(bpy, scene, collection).all_objects}
        objs = [o for o in objs if o.name in members]
    return objs


def run(params: dict) -> dict:
    import bpy

    from . import audit_checks as ac

    scene = bpy.context.scene
    requested = params.get("checks") or list(ac.CHECK_IDS)
    unknown = [c for c in requested if c not in ac.CHECKS]
    if unknown:
        raise OpError("OP_FAILED", f"unknown check {unknown[0]!r} (valid: {', '.join(ac.CHECK_IDS)})")
    names, collection = params.get("objects") or None, params.get("collection") or None
    filtered = bool(names or collection)
    objs = _objects(bpy, scene, names, collection)

    warnings: list[str] = []
    ids = [c for c in ac.CHECK_IDS if c in requested and (not filtered or c not in ac.SCENE_IDS)]
    needs_bmesh = [c for c in ids if c in ac.BMESH_IDS]
    if needs_bmesh:
        try:
            import bmesh  # noqa: F401
        except ImportError:
            warnings.append(f"mesh checks unavailable in this session: {', '.join(needs_bmesh)}")
            ids = [c for c in ids if c not in ac.BMESH_IDS]

    found = ac.run_checks(bpy, scene, ids, objs, scene_level=not filtered)
    findings = [{"check": c, "level": ac.CHECKS[c].level, "count": len(items), "items": items}
                for c, items in found.items() if items]
    result = {"filepath": bpy.data.filepath, "objects_checked": len(objs), "checks_run": ids,
              "scene_checks_skipped": filtered, "findings": findings}
    if warnings:
        result["warnings"] = warnings
    return result
