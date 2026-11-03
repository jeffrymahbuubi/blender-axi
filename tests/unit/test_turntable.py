"""`blender_side.turntable` (0.6.0 plan U5): pure orbit math, `plan`, `apply` (mathutils stub) and `OrbitState.undo`."""

import math
import sys
import types
from types import SimpleNamespace as NS

import pytest

from blender_axi.blender_side import camera_fit, turntable
from blender_axi.blender_side.ops import OpError

IDENTITY_ROT = [[1, 0, 0], [0, 0, -1], [0, 1, 0]]  # camera looking down +Y (rotation 90,0,0)


def matrix(loc, rot=IDENTITY_ROT):
    return [[*rot[0], loc[0]], [*rot[1], loc[1]], [*rot[2], loc[2]], [0, 0, 0, 1]]


def close(a, b, tol=1e-9):
    return all(abs(x - y) <= tol for ra, rb in zip(a, b) for x, y in zip(ra, rb))


# --- pure math ----------------------------------------------------------------------------------------

def test_angle_zero_is_the_original_matrix():
    m0 = matrix((0, -10, 5))
    assert close(turntable.orbit_rows(m0, (1, 2, 3), 0.0), m0)


def test_half_turn_mirrors_the_location_through_the_centre_and_keeps_z():
    out = turntable.orbit_rows(matrix((0, -10, 5)), (2, 1, 0.5), math.pi)
    assert out[0][3] == pytest.approx(4.0) and out[1][3] == pytest.approx(12.0) and out[2][3] == pytest.approx(5.0)
    assert out[3] == [0.0, 0.0, 0.0, 1.0]


def test_quarter_turn_turns_the_view_direction_too():
    out = turntable.orbit_rows(matrix((0, -10, 0)), (0, 0, 0), math.pi / 2)
    assert out[0][3] == pytest.approx(10.0) and out[1][3] == pytest.approx(0.0)
    view = [-out[i][2] for i in range(3)]  # camera looks down its local -Z; it started looking at +... toward the centre
    towards_centre = [-out[i][3] for i in range(3)]
    assert sum(a * b for a, b in zip(view, towards_centre)) > 0


def test_steps_are_distinct_and_the_distance_to_the_centre_is_constant():
    centre = (1.0, -2.0, 0.5)
    m0 = matrix((0, -10, 5))
    rows = [turntable.orbit_rows(m0, centre, 2 * math.pi * i / 4) for i in range(4)]
    assert len({tuple(round(v, 6) for v in r[0] + r[1] + r[2]) for r in rows}) == 4
    dists = [math.dist((r[0][3], r[1][3], r[2][3]), centre) for r in rows]
    assert max(dists) - min(dists) < 1e-6


def test_rotation_part_stays_orthonormal():
    out = turntable.orbit_rows(matrix((3, -4, 2)), (0, 0, 0), 1.234)
    cols = [[out[i][j] for i in range(3)] for j in range(3)]
    for a in range(3):
        for b in range(3):
            dot = sum(x * y for x, y in zip(cols[a], cols[b]))
            assert dot == pytest.approx(1.0 if a == b else 0.0, abs=1e-9)


def test_centre_is_the_bounding_box_centre():
    assert turntable.centre([0, 0, 0, 4, 2, 6, 2, 1, 1]) == (2.0, 1.0, 3.0)


# --- plan / apply / undo with stubs ---------------------------------------------------------------------

def make_bpy(monkeypatch, coords, cam=None):
    monkeypatch.setattr(camera_fit, "collect_coords", lambda depsgraph, scene: coords)
    cam = cam or NS(name="Cam", matrix_world=matrix((0, -10, 5)), location=(0.0, -10.0, 5.0),
                    rotation_euler=(1.0, 0.0, 0.0), rotation_quaternion=(1.0, 0.0, 0.0, 0.0), scale=(1.0, 1.0, 1.0))
    scene = NS(camera=cam)
    updates = []
    bpy = NS(context=NS(scene=scene, view_layer=NS(update=lambda: updates.append(1)),
                        evaluated_depsgraph_get=lambda: "dg"))
    return bpy, scene, cam, updates


def test_plan_gives_n_steps_from_the_current_view(monkeypatch):
    bpy, scene, cam, updates = make_bpy(monkeypatch, [-4, 0, 0, 4, 0, 1])
    state = turntable.OrbitState()
    steps = turntable.plan(bpy, scene, 4, state)
    assert [s.index for s in steps] == [1, 2, 3, 4] and close(steps[0].rows, cam.matrix_world)
    assert state.centre == (0.0, 0.0, 0.5) and state.camera is cam and updates  # the matrix was refreshed first
    assert not close(steps[1].rows, steps[2].rows)


def test_plan_without_geometry_is_an_op_failure(monkeypatch):
    bpy, scene, _, _ = make_bpy(monkeypatch, [])
    with pytest.raises(OpError, match="nothing to orbit: the scene has no visible geometry") as err:
        turntable.plan(bpy, scene, 4, turntable.OrbitState())
    assert err.value.code == "OP_FAILED"


def test_plan_without_a_camera(monkeypatch):
    bpy, scene, _, _ = make_bpy(monkeypatch, [0, 0, 0])
    scene.camera = None
    with pytest.raises(OpError, match="no active camera"):
        turntable.plan(bpy, scene, 3, turntable.OrbitState())


def test_apply_assigns_a_mathutils_matrix(monkeypatch):
    class Matrix:
        def __init__(self, rows):
            self.rows = rows

    monkeypatch.setitem(sys.modules, "mathutils", types.SimpleNamespace(Matrix=Matrix))
    bpy, scene, cam, _ = make_bpy(monkeypatch, [0, 0, 0, 1, 1, 1])
    step = turntable.Step(2, matrix((1, 2, 3)))
    turntable.apply(bpy, step)
    assert isinstance(cam.matrix_world, Matrix) and cam.matrix_world.rows == step.rows


def test_undo_restores_the_channels_and_is_best_effort(monkeypatch):
    bpy, scene, cam, _ = make_bpy(monkeypatch, [0, 0, 0, 1, 1, 1])
    state = turntable.OrbitState()
    turntable.plan(bpy, scene, 3, state)
    cam.location, cam.rotation_euler = (9.0, 9.0, 9.0), (0.0, 0.0, 0.0)
    assert state.undo(bpy) == []
    assert cam.location == (0.0, -10.0, 5.0) and cam.rotation_euler == (1.0, 0.0, 0.0)

    class Locked(NS):
        def __setattr__(self, name, value):
            if name == "scale":
                raise RuntimeError("locked")
            super().__setattr__(name, value)

    bpy2, scene2, cam2, _ = make_bpy(monkeypatch, [0, 0, 0, 1, 1, 1], Locked(
        name="C", matrix_world=matrix((0, 0, 0)), location=(0.0, 0.0, 0.0), scale=(1.0, 1.0, 1.0)))
    state2 = turntable.OrbitState()
    turntable.plan(bpy2, scene2, 2, state2)
    cam2.location = (5.0, 5.0, 5.0)
    failures = state2.undo(bpy2)
    assert failures == ["turntable camera scale: locked"] and cam2.location == (0.0, 0.0, 0.0)


def test_undo_before_plan_is_a_noop():
    assert turntable.OrbitState().undo(NS()) == []
