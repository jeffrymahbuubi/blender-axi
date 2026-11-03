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


def plan(bpy, scene, n: int, state: OrbitState) -> list[Step]:
    """The camera matrices for `n` evenly spaced angles over a full turn (step 1 = the current view)."""
    cam = scene.camera
    if cam is None:
        raise OpError("NOT_FOUND", "scene has no active camera")
    bpy.context.view_layer.update()  # a fitted or new camera needs its matrix_world refreshed first
    coords = camera_fit.collect_coords(bpy.context.evaluated_depsgraph_get(), scene)
    if not coords:
        raise OpError("OP_FAILED", "nothing to orbit: the scene has no visible geometry")
    c = centre(coords)
    m0 = [list(row) for row in cam.matrix_world]
    state.camera = cam
    state.matrix = [list(row) for row in m0]
    state.centre = c
    state.channels = {name: _copy(getattr(cam, name)) for name in _CHANNELS if hasattr(cam, name)}
    return [Step(i + 1, orbit_rows(m0, c, 2 * math.pi * i / n)) for i in range(n)]


def apply(bpy, step: Step) -> None:
    from mathutils import Matrix

    bpy.context.scene.camera.matrix_world = Matrix(step.rows)
