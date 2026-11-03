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


# --- subject coords, aim and record (0.7.0 plan U1) -------------------------------------------------------

class Q:
    """A quaternion stand-in that records how it was made."""

    def __init__(self, name, euler=(1.2, 0.0, 0.5)):
        self.name, self.euler = name, euler

    def __matmul__(self, other):
        return Q(f"({self.name}@{other.name})", self.euler)

    def inverted(self):
        return Q(f"inv({self.name})", self.euler)

    def to_euler(self, mode):
        self.mode = mode
        return self.euler

    def to_axis_angle(self):
        return 0.75, (0.0, 0.0, 1.0)


class Vec:
    def __init__(self, v):
        self.v = tuple(float(x) for x in v)

    def __sub__(self, other):
        return Vec(a - b for a, b in zip(self.v, other.v))

    def to_track_quat(self, track, up):
        self.track = (track, up)
        return Q(f"track{tuple(round(x, 3) for x in self.v)}")


class WorldMatrix(list):
    def __init__(self, rows, cam_getter, loc):
        super().__init__(rows)
        self.translation = Vec(loc)
        self._cam = cam_getter

    def to_euler(self):
        return self._cam().rotation_euler


@pytest.fixture
def mathutils_stub(monkeypatch):
    monkeypatch.setitem(sys.modules, "mathutils", types.SimpleNamespace(Vector=Vec, Matrix=lambda rows: rows))


def aim_rig(monkeypatch, mode="XYZ", parent=None):
    bpy, scene, cam, updates = make_bpy(monkeypatch, [-4, 0, 0, 4, 0, 1])
    cam.matrix_world = WorldMatrix(matrix((0, -10, 5)), lambda: cam, (0.0, -10.0, 5.0))
    cam.rotation_mode, cam.rotation_axis_angle, cam.parent = mode, (0.0, 0.0, 1.0, 0.0), parent
    return bpy, scene, cam, updates


def test_aim_sets_the_euler_channels_and_reports_degrees(monkeypatch, mathutils_stub):
    bpy, scene, cam, updates = aim_rig(monkeypatch)
    state = turntable.OrbitState()
    out = turntable.aim(bpy, cam, (0.0, 0.0, 0.5), state)
    assert cam.rotation_euler == (1.2, 0.0, 0.5) and updates  # the world matrix was refreshed
    to = [round(math.degrees(1.2), 4), 0.0, round(math.degrees(0.5), 4)]
    assert out == {"from": [round(math.degrees(1.0), 4), 0.0, 0.0], "to": to, "local_to": to}  # unparented: local == world
    assert state.aim == out and state.camera is cam


def test_aim_looks_from_the_camera_position_toward_the_centre(monkeypatch, mathutils_stub):
    bpy, _, cam, _ = aim_rig(monkeypatch)
    seen = []
    monkeypatch.setattr(Vec, "to_track_quat", lambda self, t, u: seen.append((self.v, t, u)) or Q("q"))
    turntable.aim(bpy, cam, (0.0, 0.0, 0.5), turntable.OrbitState())
    assert seen == [((0.0, 10.0, -4.5), "-Z", "Y")]  # the camera looks down -Z with Y up (Z stays up in the world)


def test_aim_uses_the_rotation_mode(monkeypatch, mathutils_stub):
    bpy, _, cam, _ = aim_rig(monkeypatch, mode="ZXY")
    q = Q("q")
    monkeypatch.setattr(Vec, "to_track_quat", lambda self, t, u: q)
    turntable.aim(bpy, cam, (0, 0, 0), turntable.OrbitState())
    assert q.mode == "ZXY" and cam.rotation_euler == q.euler

    bpy, _, cam, _ = aim_rig(monkeypatch, mode="QUATERNION")
    cam.matrix_basis = NS(to_euler=lambda: (0.0, 0.0, math.pi / 2))
    out = turntable.aim(bpy, cam, (0, 0, 0), turntable.OrbitState())
    assert cam.rotation_quaternion is q and cam.rotation_euler == (1.0, 0.0, 0.0)  # the euler channel is untouched
    assert out["local_to"] == [0.0, 0.0, 90.0]  # from matrix_basis.to_euler(), not rotation_euler (A2)

    bpy, _, cam, _ = aim_rig(monkeypatch, mode="AXIS_ANGLE")
    cam.matrix_basis = NS(to_euler=lambda: (math.pi / 2, 0.0, 0.0))
    out = turntable.aim(bpy, cam, (0, 0, 0), turntable.OrbitState())
    assert cam.rotation_axis_angle == (0.75, 0.0, 0.0, 1.0) and out["local_to"] == [90.0, 0.0, 0.0]


def test_aim_of_a_parented_camera_removes_the_parent_rotation(monkeypatch, mathutils_stub):
    parent = NS(matrix_world=NS(to_quaternion=lambda: Q("P")))
    bpy, _, cam, _ = aim_rig(monkeypatch, parent=parent)
    cam.matrix_parent_inverse = NS(to_quaternion=lambda: Q("I"))
    seen = []
    monkeypatch.setattr(Q, "to_euler", lambda self, mode: seen.append(self.name) or (0.0, 0.0, 0.0))
    out = turntable.aim(bpy, cam, (0, 0, 0), turntable.OrbitState())
    assert seen == ["(inv((P@I))@track(0.0, 10.0, -5.0))"]
    assert out["local_to"] == [0.0, 0.0, 0.0] and out["to"] == [0.0, 0.0, 0.0]  # local channel = the local euler just set


def test_aim_local_to_is_the_channel_while_to_is_the_world_rotation(monkeypatch, mathutils_stub):
    parent = NS(matrix_world=NS(to_quaternion=lambda: Q("P")))
    bpy, _, cam, _ = aim_rig(monkeypatch, parent=parent)
    cam.matrix_parent_inverse = NS(to_quaternion=lambda: Q("I"))
    world = (math.radians(63.435), 0.0, math.radians(30.0))
    cam.matrix_world = WorldMatrix(matrix((0, -10, 5)), lambda: NS(rotation_euler=world), (0.0, -10.0, 5.0))
    state = turntable.OrbitState()
    out = turntable.aim(bpy, cam, (0, 0, 0), state)  # the stub Q.to_euler sets the local channel to (1.2, 0, 0.5)
    assert out["to"] == [63.435, 0.0, 30.0] and out["local_to"] == [round(math.degrees(1.2), 4), 0.0, round(math.degrees(0.5), 4)]
    assert state.aim == out


def test_record_keeps_the_first_channels_only(monkeypatch):
    _, _, cam, _ = make_bpy(monkeypatch, [0, 0, 0, 1, 1, 1])
    state = turntable.OrbitState()
    turntable.record(state, cam)
    first = dict(state.channels)
    cam.location = (7.0, 7.0, 7.0)
    turntable.record(state, cam)
    assert state.channels == first and state.channels["location"] == (0.0, -10.0, 5.0)


def test_plan_after_aim_keeps_the_pre_aim_channels_and_undo_restores_them(monkeypatch, mathutils_stub):
    bpy, scene, cam, _ = aim_rig(monkeypatch)
    state = turntable.OrbitState()
    turntable.aim(bpy, cam, (0.0, 0.0, 0.5), state)
    assert cam.rotation_euler == (1.2, 0.0, 0.5)
    turntable.plan(bpy, scene, 3, state)
    assert state.channels["rotation_euler"] == (1.0, 0.0, 0.0)  # plan did not overwrite what aim recorded
    cam.location = (9.0, 9.0, 9.0)  # as if the fit and the orbit had moved it
    assert state.undo(bpy) == []
    assert cam.location == (0.0, -10.0, 5.0) and cam.rotation_euler == (1.0, 0.0, 0.0)


def test_undo_after_a_failed_render_restores_location_and_rotation(monkeypatch, mathutils_stub):
    # the op-level path: aim, a render that raises, then the finally block (see test_cmd_render_subject for the op)
    bpy, scene, cam, _ = aim_rig(monkeypatch)
    state = turntable.OrbitState()
    turntable.aim(bpy, cam, (0.0, 0.0, 0.5), state)
    cam.location = (5.0, 5.0, 5.0)
    try:
        raise RuntimeError("render crashed")
    except RuntimeError:
        pass
    finally:
        assert state.undo(bpy) == []
    assert cam.location == (0.0, -10.0, 5.0) and cam.rotation_euler == (1.0, 0.0, 0.0)


def test_plan_with_coords_uses_them_and_does_not_collect(monkeypatch):
    bpy, scene, cam, _ = make_bpy(monkeypatch, [-100, 0, 0, 100, 0, 1])  # what collect_coords would say
    monkeypatch.setattr(camera_fit, "collect_coords", lambda *a, **k: pytest.fail("collect_coords must not run"))
    state = turntable.OrbitState()
    steps = turntable.plan(bpy, scene, 2, state, coords=[0, 0, 0, 2, 2, 4])
    assert state.centre == (1.0, 1.0, 2.0) and len(steps) == 2


def test_plan_with_empty_coords_names_the_subject(monkeypatch):
    bpy, scene, _, _ = make_bpy(monkeypatch, [0, 0, 0])
    with pytest.raises(OpError, match="nothing to orbit: the subject has no visible geometry") as err:
        turntable.plan(bpy, scene, 2, turntable.OrbitState(), coords=[])
    assert err.value.code == "OP_FAILED"
