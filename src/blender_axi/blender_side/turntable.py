"""`render --turntable`: orbit the camera around the visible geometry, one still per step (0.6.0 plan K5, §3.3b).

The orbit turns the camera about the world Z axis through the centre of the visible geometry:
`M' = T(c) · Rz(angle) · T(-c) · M0`. The rows are built in pure Python and assigned as a `mathutils.Matrix`
(a plain tuple of tuples is accepted by `matrix_world` but silently gives a wrong transform, requirements F9).
`OrbitState.undo` puts the camera's transform channels back (also when the render raised), exactly as they were.

Data API only; `bpy` is passed in so the unit tests can drive it with plain stubs (`mathutils` is imported lazily).
"""

import math
from dataclasses import dataclass

from . import camera_fit
from .ops import OpError

_CHANNELS = ("location", "rotation_euler", "rotation_quaternion", "rotation_axis_angle", "scale")


@dataclass(frozen=True)
class Step:
    index: int  # 1-based step number (the `#` in the output name)
    rows: list  # the camera's 4x4 matrix_world for this step, 4 lists of 4 floats


class OrbitState:
    """What `plan` recorded, so `undo` can put the camera back (also when `plan` or the render fails later)."""

    def __init__(self):
        self.camera = None
        self.matrix = None  # the camera's matrix_world rows before the orbit
        self.channels: dict = {}
        self.centre = None
        self.aim: dict | None = None  # {"from", "to", "local_to": [deg x3]} when `aim` ran

    def undo(self, bpy) -> list[str]:
        """Best effort; returns descriptions of what could not be restored."""
        failures: list[str] = []
        if self.camera is None:
            return failures
        for name, value in self.channels.items():
            try:
                setattr(self.camera, name, value)
            except Exception as exc:
                failures.append(f"turntable camera {name}: {exc}")
        return failures


def centre(coords: list[float]) -> tuple[float, float, float]:
    """Bounding-box centre of flat x, y, z, ... coordinates."""
    axes = (coords[0::3], coords[1::3], coords[2::3])
    return tuple((min(a) + max(a)) / 2 for a in axes)


def orbit_rows(m0: list, c, angle: float) -> list[list[float]]:
    """`T(c) · Rz(angle) · T(-c) · M0` for a 4x4 matrix given as rows; pure Python."""
    cos, sin = math.cos(angle), math.sin(angle)
    rz = ((cos, -sin, 0.0), (sin, cos, 0.0), (0.0, 0.0, 1.0))
    rows = [[float(v) for v in row] for row in m0]
    out = [[0.0] * 4 for _ in range(4)]
    for i in range(3):
        for j in range(3):
            out[i][j] = sum(rz[i][k] * rows[k][j] for k in range(3))
        shifted = [rows[k][3] - c[k] for k in range(3)]
        out[i][3] = c[i] + sum(rz[i][k] * shifted[k] for k in range(3))
    out[3] = [float(v) for v in rows[3]]
    return out


def _copy(value):
    try:
        return tuple(value)
    except TypeError:
        return value


def record(state: OrbitState, cam) -> None:
    """Remember the camera's transform channels once (the first call wins), so `undo` restores the original
    even after `aim` changed the rotation."""
    if state.camera is not None:
        return
    state.camera = cam
    state.channels = {name: _copy(getattr(cam, name)) for name in _CHANNELS if hasattr(cam, name)}


def _euler_deg(cam) -> list[float]:
    return [round(math.degrees(v), 4) for v in cam.matrix_world.to_euler()]


def _local_deg(cam) -> list[float]:
    """The camera's own rotation channel in degrees (same rule as `op_snapshot._rotation_deg`)."""
    euler = cam.rotation_euler if cam.rotation_mode not in ("QUATERNION", "AXIS_ANGLE") else cam.matrix_basis.to_euler()
    return [round(math.degrees(v), 4) for v in euler]


def aim(bpy, cam, centre_: tuple[float, float, float], state: OrbitState) -> dict:
    """Turn `cam` (position unchanged, Z up) so it looks at `centre_`; the original channels are recorded first.
    Returns `{"from": [deg x3], "to": [deg x3], "local_to": [deg x3]}`: `from`/`to` are the world rotation before and
    after (what `aim:` prints), `local_to` is the camera's own channel after (what `set --rotation` takes)."""
    from mathutils import Vector

    record(state, cam)
    before = _euler_deg(cam)
    q = (Vector(centre_) - cam.matrix_world.translation).to_track_quat("-Z", "Y")
    parent = getattr(cam, "parent", None)
    if parent is not None:  # the channels are relative to the parent: strip its world rotation and the parent-inverse
        local = parent.matrix_world.to_quaternion() @ cam.matrix_parent_inverse.to_quaternion()
        q = local.inverted() @ q
    mode = cam.rotation_mode
    if mode == "QUATERNION":
        cam.rotation_quaternion = q
    elif mode == "AXIS_ANGLE":
        angle, axis = q.to_axis_angle()
        cam.rotation_axis_angle = (angle, *axis)
    else:
        cam.rotation_euler = q.to_euler(mode)
    bpy.context.view_layer.update()
    state.aim = {"from": before, "to": _euler_deg(cam), "local_to": _local_deg(cam)}
    return state.aim


def plan(bpy, scene, n: int, state: OrbitState, coords: list[float] | None = None) -> list[Step]:
    """The camera matrices for `n` evenly spaced angles over a full turn (step 1 = the current view).
    `coords` (the subject's bound-box corners) replaces the all-visible-geometry default."""
    cam = scene.camera
    if cam is None:
        raise OpError("NOT_FOUND", "scene has no active camera")
    bpy.context.view_layer.update()  # a fitted or new camera needs its matrix_world refreshed first
    what = "subject" if coords is not None else "scene"
    if coords is None:
        coords = camera_fit.collect_coords(bpy.context.evaluated_depsgraph_get(), scene)
    if not coords:
        raise OpError("OP_FAILED", f"nothing to orbit: the {what} has no visible geometry")
    c = centre(coords)
    m0 = [list(row) for row in cam.matrix_world]
    record(state, cam)
    state.matrix = [list(row) for row in m0]
    state.centre = c
    return [Step(i + 1, orbit_rows(m0, c, 2 * math.pi * i / n)) for i in range(n)]


def apply(bpy, step: Step) -> None:
    from mathutils import Matrix

    bpy.context.scene.camera.matrix_world = Matrix(step.rows)
