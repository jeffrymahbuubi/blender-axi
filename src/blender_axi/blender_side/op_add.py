"""`add`: create a primitive, camera, light or empty with the data API (no operators, so no context is needed).

params: kind, name, location[3], rotation_deg[3], scale[3], size, radius, depth, segments, collection, light_type,
energy (all but `kind` may be None = the default). Meshes are built with `bmesh.ops.create_*`; `size` is the full
edge of a cube/plane, `segments` the UV segments (sphere, cylinder, cone) or the subdivisions (icosphere).
Result: {name, type, kind, collection, location, dimensions, verts, faces, scene_camera_set, scene_camera} (verts/faces
None for non-meshes). A camera becomes the scene camera only when the scene has none (`scene_camera_set`);
`scene_camera` is the scene camera's name after the op (None when there is none).
"""

import math

from .common import find_collection
from .ops import OpError

MESH_KINDS = ("cube", "plane", "sphere", "icosphere", "cylinder", "cone", "monkey")
KINDS = MESH_KINDS + ("camera", "light", "empty")
LIGHT_TYPES = ("POINT", "SUN", "SPOT", "AREA")
DEFAULT_NAMES = {"cube": "Cube", "plane": "Plane", "sphere": "Sphere", "icosphere": "Icosphere", "cylinder": "Cylinder",
                 "cone": "Cone", "monkey": "Suzanne", "camera": "Camera", "empty": "Empty"}


def _vec(v) -> list:
    return [round(float(x), 3) + 0.0 for x in v]


def _number(params: dict, key: str, default: float) -> float:
    value = params.get(key)
    return float(default if value is None else value)


def _fill(bm, kind: str, params: dict) -> None:
    """Run the bmesh creator for `kind` with Blender's own defaults for whatever `params` leaves out."""
    import bmesh

    ops = bmesh.ops
    radius = _number(params, "radius", 1.0)
    segments = int(_number(params, "segments", 32))
    if kind == "cube":
        ops.create_cube(bm, size=_number(params, "size", 2.0))
    elif kind == "plane":  # create_grid's size is the half-extent
        ops.create_grid(bm, x_segments=1, y_segments=1, size=_number(params, "size", 2.0) / 2)
    elif kind == "sphere":
        ops.create_uvsphere(bm, u_segments=segments, v_segments=max(segments // 2, 2), radius=radius)
    elif kind == "icosphere":
        ops.create_icosphere(bm, subdivisions=int(_number(params, "segments", 2)), radius=radius)
    elif kind in ("cylinder", "cone"):
        ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=segments, radius1=radius,
                        radius2=radius if kind == "cylinder" else 0.0, depth=_number(params, "depth", 2.0))
    else:
        ops.create_monkey(bm)


def _new_object(bpy, kind: str, name: str | None, params: dict):
    """(object, mesh data or None) for `kind`; the object's final name may carry Blender's `.001` suffix."""
    if kind in MESH_KINDS:
        import bmesh

        label = name or DEFAULT_NAMES[kind]
        mesh = bpy.data.meshes.new(label)
        bm = bmesh.new()
        try:
            _fill(bm, kind, params)
            bm.to_mesh(mesh)
        finally:
            bm.free()
        return bpy.data.objects.new(label, mesh), mesh
    if kind == "camera":
        label = name or DEFAULT_NAMES[kind]
        return bpy.data.objects.new(label, bpy.data.cameras.new(label)), None
    if kind == "light":
        light_type = params.get("light_type") or "POINT"
        if light_type not in LIGHT_TYPES:
            raise OpError("OP_FAILED", f"unknown light type {light_type!r}")
        label = name or light_type.title()
        light = bpy.data.lights.new(label, light_type)
        if params.get("energy") is not None:
            light.energy = float(params["energy"])
        return bpy.data.objects.new(label, light), None
    return bpy.data.objects.new(name or DEFAULT_NAMES["empty"], None), None


def run(params: dict) -> dict:
    import bpy

    kind = params.get("kind")
    if kind not in KINDS:
        raise OpError("OP_FAILED", f"unknown kind {kind!r}; one of {', '.join(KINDS)}")
    scene = bpy.context.scene
    coll = find_collection(bpy, scene, params.get("collection"))  # before creating anything: a bad name leaves no orphan data
    obj, mesh = _new_object(bpy, kind, params.get("name"), params)
    if params.get("location") is not None:
        obj.location = tuple(float(x) for x in params["location"])
    if params.get("rotation_deg") is not None:
        obj.rotation_euler = tuple(math.radians(float(x)) for x in params["rotation_deg"])
    if params.get("scale") is not None:
        obj.scale = tuple(float(x) for x in params["scale"])
    coll.objects.link(obj)
    made_active = False
    if obj.type == "CAMERA" and scene.camera is None:  # the first camera makes `render` work; an existing one is kept
        scene.camera = obj
        made_active = True
    view_layer = getattr(bpy.context, "view_layer", None)
    if view_layer is not None:
        view_layer.update()  # object.dimensions reads the world matrix, which is stale until the depsgraph runs
    return {
        "name": obj.name, "type": obj.type, "kind": kind, "collection": coll.name,
        "location": _vec(obj.location), "dimensions": _vec(obj.dimensions),
        "verts": len(mesh.vertices) if mesh is not None else None,
        "faces": len(mesh.polygons) if mesh is not None else None,
        "scene_camera_set": made_active,
        "scene_camera": scene.camera.name if scene.camera is not None else None,
    }
