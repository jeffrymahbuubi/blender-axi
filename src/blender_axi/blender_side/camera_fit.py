"""`render --fit`: frame every visible geometry object without touching the file (plan U4, K8).

`Object.camera_fit_coords(depsgraph, coords)` (data API, no 3D-view operator) gives the location (and ortho
scale) that sees all coordinates with the camera's own rotation. The result is pushed back along the view
axis by the margin, the render runs, and `FitState.undo` puts the camera back (or deletes a temporary one).

Pure Python math only so the unit tests can drive it with plain stubs; `mathutils` is imported lazily and only
for a parented camera (`to_local`).
"""

import math

from .op_export import GEOMETRY_TYPES
from .ops import OpError

TEMP_CAMERA_NAME = "AxiFit"
TEMP_ROTATION_DEG = (63.6, 0.0, 46.7)  # Blender's default viewport angle


class FitState:
    """What `fit_scene` changed, so `undo` can reverse it (also when the render raises)."""

    def __init__(self):
        self.camera = None  # the fitted camera object (existing or temporary)
        self.location = None  # its location before the fit (existing camera only)
        self.ortho_scale = None  # its ortho_scale before the fit (orthographic only)
        self.temp_object = None
        self.temp_data = None

    def undo(self, bpy) -> list[str]:
        """Best effort; returns descriptions of what could not be restored."""
        failures: list[str] = []
        cam = self.camera
        if cam is not None and self.location is not None:
            try:
                cam.location = self.location
            except Exception as exc:
                failures.append(f"fit camera location: {exc}")
        if cam is not None and self.ortho_scale is not None:
            try:
                cam.data.ortho_scale = self.ortho_scale
            except Exception as exc:
                failures.append(f"fit camera ortho_scale: {exc}")
        if self.temp_object is not None:
            try:
                bpy.data.objects.remove(self.temp_object)
            except Exception as exc:
                failures.append(f"temporary camera object: {exc}")
        if self.temp_data is not None:
            try:
                bpy.data.cameras.remove(self.temp_data)
            except Exception as exc:
                failures.append(f"temporary camera data: {exc}")
        return failures


def _object_coords(obj, depsgraph) -> list[float] | None:
    """World-space bound-box corners (flat) of one visible geometry object, modifiers applied; None to skip it."""
    if obj.type not in GEOMETRY_TYPES or not obj.visible_get():
        return None
    ev = obj.evaluated_get(depsgraph)
    mw = ev.matrix_world
    return [sum(mw[i][j] * corner[j] for j in range(3)) + mw[i][3] for corner in ev.bound_box for i in range(3)]


def collect_coords(depsgraph, scene, objects=None) -> list[float]:
    """World-space bound-box corners (flat x, y, z, ...) of every visible geometry object (or of `objects`)."""
    flat: list[float] = []
    for obj in (scene.objects if objects is None else objects):
        coords = _object_coords(obj, depsgraph)
        if coords is not None:
            flat.extend(coords)
    return flat


def collect_entries(depsgraph, scene) -> list[tuple[str, list[float]]]:
    """Like `collect_coords`, kept per object as `(name, corners)` (the input of `subject.choose`)."""
    out = []
    for obj in scene.objects:
        coords = _object_coords(obj, depsgraph)
        if coords is not None:
            out.append((obj.name, coords))
    return out


def _centre(flat: list[float]) -> tuple[float, float, float]:
    axes = (flat[0::3], flat[1::3], flat[2::3])
    return tuple((min(a) + max(a)) / 2 for a in axes)


def _make_temp_camera(bpy, scene, state: FitState):
    data = bpy.data.cameras.new(TEMP_CAMERA_NAME)
    state.temp_data = data
    obj = bpy.data.objects.new(TEMP_CAMERA_NAME, data)
    state.temp_object = obj
    scene.collection.objects.link(obj)
    obj.rotation_euler = tuple(math.radians(d) for d in TEMP_ROTATION_DEG)
    scene.camera = obj
    return obj


def to_local(cam, world: tuple[float, float, float]) -> tuple[float, float, float]:
    """The `location` channel that puts `cam` at `world` (identity without a parent)."""
    parent = getattr(cam, "parent", None)
    if parent is None:
        return tuple(float(v) for v in world)
    from mathutils import Vector

    return tuple((parent.matrix_world @ cam.matrix_parent_inverse).inverted() @ Vector(world))


def fit_scene(bpy, scene, margin: float, temp_camera_ok: bool, state: FitState, coords: list[float] | None = None) -> dict:
    """Frame all visible geometry (or the given `coords`) with `scene.camera` (or a temporary one headless).
    Returns the `fit` result."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    if coords is None:
        coords = collect_coords(depsgraph, scene)
    if not coords:
        raise OpError("OP_FAILED", "nothing to fit: the scene has no visible geometry")
    cam = scene.camera
    temporary = False
    if cam is None:
        if not temp_camera_ok:
            raise OpError("NOT_FOUND", "scene has no active camera")
        cam = _make_temp_camera(bpy, scene, state)
        temporary = True
        bpy.context.view_layer.update()  # the new object needs a matrix before it can be fitted
        depsgraph = bpy.context.evaluated_depsgraph_get()
    state.camera = cam
    before = None if temporary else tuple(cam.location)
    state.location = before
    ortho = cam.data.type == "ORTHO"
    if ortho:
        state.ortho_scale = cam.data.ortho_scale

    loc, scale = cam.camera_fit_coords(depsgraph, coords)
    loc = tuple(loc)
    if ortho:
        cam.location = to_local(cam, loc)
        cam.data.ortho_scale = scale * margin
    else:
        mw = cam.matrix_world
        view = tuple(-mw[i][2] for i in range(3))  # the camera looks down its local -Z
        norm = math.sqrt(sum(v * v for v in view)) or 1.0
        view = tuple(v / norm for v in view)
        dist = math.dist(loc, _centre(coords))
        loc = tuple(loc[i] - view[i] * dist * (margin - 1) for i in range(3))
        cam.location = to_local(cam, loc)  # `loc` is a world position, the channel is relative to the parent
    return {
        "camera": cam.name, "temporary": temporary, "location": [float(v) for v in cam.location],
        "from": list(before) if before is not None else None,
        "ortho_scale": float(cam.data.ortho_scale) if ortho else None,
    }
