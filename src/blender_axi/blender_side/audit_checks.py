"""The `audit` checks (0.6.0 plan §3.4, K3): a fixed, ordered registry of read-only asset checks.

Object checks take `(bpy, obj) -> str | None` (the item text, or None when the object is fine or not applicable);
scene checks take `(bpy, scene) -> list[str]`. Data API only: `bpy` is passed in, and `bmesh` is imported lazily, so
unit tests drive everything with stubs. Nothing here writes to the file or the session.
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import lighting
from .common import count
from .op_export import GEOMETRY_TYPES

CHECK_IDS = ("camera", "lighting", "scale", "materials", "ngons", "nonmanifold", "loose", "unused", "visibility",
             "files", "collections")
BMESH_IDS = ("nonmanifold", "loose")
MATERIAL_TYPES = {"MESH", "CURVE", "FONT", "SURFACE", "META"}
SCALE_TOLERANCE = 1e-4
ENGINE_LABELS = {"BLENDER_EEVEE": "EEVEE", "BLENDER_EEVEE_NEXT": "EEVEE", "CYCLES": "CYCLES"}
UNUSED_KINDS = (("meshes", "mesh"), ("materials", "material"), ("images", "image"), ("textures", "texture"),
                ("worlds", "world"), ("cameras", "camera"), ("lights", "light"), ("actions", "action"),
                ("node_groups", "node group"), ("collections", "collection"))
FILE_SOURCES = ("FILE", "SEQUENCE", "MOVIE")


@dataclass(frozen=True)
class Check:
    level: str  # "warn" | "info"
    scope: str  # "scene" | "object"
    fn: Callable


def _num(value: float) -> str:
    text = f"{round(float(value), 3):.3f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


# -- scene checks ---------------------------------------------------------------------------------

def check_camera(bpy, scene) -> list[str]:
    return ["no scene camera"] if scene.camera is None else []


def check_lighting(bpy, scene) -> list[str]:
    assessment = lighting.assess(bpy, scene)
    text = lighting.warning(assessment, ENGINE_LABELS.get(assessment["engine"], assessment["engine"]))
    return [text] if text else []


def check_unused(bpy, scene) -> list[str]:
    items = []
    for attr, word in UNUSED_KINDS:
        for datablock in getattr(bpy.data, attr, ()):
            if datablock.users == 0:
                items.append(f"{word} {datablock.name}")
    return items


def _missing_path(bpy, filepath: str) -> str | None:
    """The absolute posix path when `filepath` does not exist (relative `//` paths resolved), else None."""
    if not filepath:
        return "no path"
    resolved = os.path.normpath(bpy.path.abspath(filepath))
    return None if os.path.exists(resolved) else Path(resolved).as_posix()


def check_files(bpy, scene) -> list[str]:
    items = []
    for image in bpy.data.images:
        if image.source in FILE_SOURCES and not image.packed_file:
            missing = _missing_path(bpy, image.filepath)
            if missing is not None:
                items.append(f"image {image.name} (missing: {missing})")
    for library in getattr(bpy.data, "libraries", ()):
        missing = _missing_path(bpy, library.filepath)
        if missing is not None:
            items.append(f"library {library.name} (missing: {missing})")
    return items


def check_collections(bpy, scene) -> list[str]:
    return [c.name for c in bpy.data.collections if not c.objects and not c.children]


# -- object checks --------------------------------------------------------------------------------

def check_scale(bpy, obj) -> str | None:
    if obj.type not in GEOMETRY_TYPES:
        return None
    scale = [float(s) for s in obj.scale]
    if all(abs(s - 1.0) <= SCALE_TOLERANCE for s in scale):
        return None
    vec = ",".join(_num(s) for s in scale)
    return f"{obj.name} ({vec}, negative)" if any(s < 0 for s in scale) else f"{obj.name} ({vec})"


def check_materials(bpy, obj) -> str | None:
    if obj.type not in MATERIAL_TYPES:
        return None
    return None if any(slot.material is not None for slot in obj.material_slots) else obj.name


def check_ngons(bpy, obj) -> str | None:
    if obj.type != "MESH":
        return None
    n = sum(1 for poly in obj.data.polygons if poly.loop_total > 4)
    return f"{obj.name} ({count(n, 'face')})" if n else None


_STATS: dict[str, tuple[int, int]] = {}


def _mesh_stats(obj) -> tuple[int, int]:
    """(non-manifold edges, loose vertices) from ONE bmesh per mesh object, shared by both checks."""
    stats = _STATS.get(obj.name)
    if stats is None:
        import bmesh

        bm = bmesh.new()
        try:
            bm.from_mesh(obj.data)
            stats = (sum(1 for e in bm.edges if not e.is_manifold), sum(1 for v in bm.verts if not v.link_edges))
        finally:
            bm.free()
        _STATS[obj.name] = stats
    return stats


def check_nonmanifold(bpy, obj) -> str | None:
    if obj.type != "MESH":
        return None
    n = _mesh_stats(obj)[0]
    return f"{obj.name} ({count(n, 'edge')})" if n else None


def check_loose(bpy, obj) -> str | None:
    if obj.type != "MESH":
        return None
    n = _mesh_stats(obj)[1]
    return f"{obj.name} ({count(n, 'vert')})" if n else None


def check_visibility(bpy, obj) -> str | None:
    if obj.hide_render:
        return f"{obj.name} (render off)"
    try:
        visible = bool(obj.visible_get())
    except RuntimeError:  # not in the view layer
        visible = not obj.hide_viewport
    return None if visible else f"{obj.name} (hidden, renders)"


CHECKS: dict[str, Check] = {
    "camera": Check("warn", "scene", check_camera),
    "lighting": Check("warn", "scene", check_lighting),
    "scale": Check("warn", "object", check_scale),
    "materials": Check("warn", "object", check_materials),
    "ngons": Check("info", "object", check_ngons),
    "nonmanifold": Check("warn", "object", check_nonmanifold),
    "loose": Check("info", "object", check_loose),
    "unused": Check("info", "scene", check_unused),
    "visibility": Check("info", "object", check_visibility),
    "files": Check("warn", "scene", check_files),
    "collections": Check("info", "scene", check_collections),
}
SCENE_IDS = frozenset(k for k, c in CHECKS.items() if c.scope == "scene")


def run_checks(bpy, scene, ids: list[str], objects: list, scene_level: bool = True) -> dict[str, list[str]]:
    """Item texts per check id (every id of `ids` present, possibly with an empty list), in CHECK_IDS order.

    Scene checks run only when `scene_level` (no object/collection filter); object checks run over `objects`.
    """
    _STATS.clear()
    found: dict[str, list[str]] = {}
    try:
        wanted = [i for i in CHECK_IDS if i in ids and (scene_level or i not in SCENE_IDS)]
        for check_id in wanted:
            check = CHECKS[check_id]
            if check.scope == "scene":
                found[check_id] = check.fn(bpy, scene)
        object_ids = [i for i in wanted if CHECKS[i].scope == "object"]
        for check_id in object_ids:
            found[check_id] = []
        for obj in objects:
            for check_id in object_ids:
                item = CHECKS[check_id].fn(bpy, obj)
                if item is not None:
                    found[check_id].append(item)
    finally:
        _STATS.clear()
    return {i: found[i] for i in wanted}
