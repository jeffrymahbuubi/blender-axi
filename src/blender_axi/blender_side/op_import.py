"""`import`: bring a glb/gltf/fbx/obj/stl/ply/usd/abc file into the scene with Blender's importer operators.

params: source (absolute path), format (glb|gltf|fbx|obj|stl|ply|usd|usdc|usda|abc), collection (name | None; created
under the Scene Collection when missing), fresh (bool: start from an empty file first, for `import --new`).
Result: {source, source_name, format, added: [names], types: {type: n}, materials_added, collection, collection_created,
restore_warnings?}. The importers link the new objects to the active collection; with `collection` they are moved.

`FORMAT_EXTS` is pure data the CLI imports for validation (export's table, plus `.usdz`).
"""

import os

from .common import find_collection, restore_selection, snapshot_selection, window_override
from .ops import OpError

FORMAT_EXTS = {
    "glb": (".glb",), "gltf": (".gltf",), "fbx": (".fbx",), "obj": (".obj",), "stl": (".stl",),
    "ply": (".ply",), "usd": (".usd", ".usdc", ".usda", ".usdz"), "usdc": (".usdc",), "usda": (".usda",),
    "abc": (".abc",),
}


def _call(bpy, fmt: str, path: str):
    """Invoke the format's importer operator and return its result set."""
    ops = bpy.ops
    if fmt in ("glb", "gltf"):
        return ops.import_scene.gltf(filepath=path)
    if fmt == "fbx":
        return ops.import_scene.fbx(filepath=path)
    if fmt == "obj":
        return ops.wm.obj_import(filepath=path)
    if fmt == "stl":
        return ops.wm.stl_import(filepath=path)
    if fmt == "ply":
        return ops.wm.ply_import(filepath=path)
    if fmt in ("usd", "usdc", "usda"):
        return ops.wm.usd_import(filepath=path)
    if fmt == "abc":
        return ops.wm.alembic_import(filepath=path)
    raise OpError("OP_FAILED", f"unsupported import format {fmt!r}")


def _target_collection(bpy, scene, wanted: str):
    """(collection, created): the named collection, created under the Scene Collection when it does not exist."""
    try:
        return find_collection(bpy, scene, wanted), False
    except OpError as exc:
        if exc.code != "NOT_FOUND":
            raise
    coll = bpy.data.collections.new(wanted)
    scene.collection.children.link(coll)
    return coll, True


def _move(objs: list, coll) -> None:
    for obj in objs:
        for old in list(obj.users_collection):
            if old is not coll:
                old.objects.unlink(obj)
        if coll not in list(obj.users_collection):
            coll.objects.link(obj)


def run(params: dict) -> dict:
    import bpy

    fmt, source = params["format"], params["source"]
    if fmt not in FORMAT_EXTS:
        raise OpError("OP_FAILED", f"unsupported import format {fmt!r}")
    if not os.path.isfile(source):
        raise OpError("NOT_FOUND", f"file not found: {source}")
    if params.get("fresh"):
        bpy.ops.wm.read_factory_settings(use_empty=True)

    scene = bpy.context.scene
    objects_before = {o.name for o in bpy.data.objects}
    materials_before = {m.name for m in bpy.data.materials}
    snap = snapshot_selection(bpy, scene)
    failures: list[str] = []
    try:
        with window_override(bpy):
            status = _call(bpy, fmt, source)
        if "FINISHED" not in status:
            raise OpError("OP_FAILED", f"the {fmt} importer did not finish ({', '.join(sorted(status)) or 'no status'})")
        added = [o for o in bpy.data.objects if o.name not in objects_before]
        coll, created = None, False
        if params.get("collection"):
            coll, created = _target_collection(bpy, scene, params["collection"])
            _move(added, coll)
    finally:
        failures = restore_selection(scene, snap)  # also on failure; never masks the original exception

    types: dict[str, int] = {}
    for obj in added:
        types[obj.type] = types.get(obj.type, 0) + 1
    result = {
        "source": source, "source_name": os.path.basename(source), "format": fmt,
        "added": [o.name for o in added], "types": types,
        "materials_added": len([m for m in bpy.data.materials if m.name not in materials_before]),
        "collection": coll.name if coll is not None else None, "collection_created": created,
    }
    if failures:
        result["restore_warnings"] = failures
    return result
