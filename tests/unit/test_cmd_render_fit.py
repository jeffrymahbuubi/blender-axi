"""Unit tests for `render --fit` (plan U4): CLI flags, params, text and hints; op and camera_fit with fake bpy."""

import math
import types
from pathlib import Path

import pytest

from blender_axi import flags as flagparser
from blender_axi.blender_side import camera_fit, op_render
from blender_axi.blender_side.ops import OpError
from blender_axi.commands import render as cmd
from blender_axi.errors import AxiError
from blender_axi.runner import RunOutcome

WORK = Path(__file__).resolve().parents[2] / ".tmp" / "tests" / "unit_render_fit"

FIT = {"camera": "Cam", "temporary": False, "location": [0.0, -17.2, 8.3], "from": [0.0, -8.0, 4.0], "ortho_scale": None}
TEMP = {"camera": "AxiFit", "temporary": True, "location": [0.0, -17.2, 8.3], "from": None, "ortho_scale": None}


class FakeTransport:
    name = "fake"

    def __init__(self, fit=None, error=None):
        self.calls, self.fit, self.error = [], fit, error

    def run(self, op, params, *, blend=None, save=None, timeout=None):
        self.calls.append((op, params, blend, timeout))
        if self.error:
            raise self.error
        result = {"frames": [1], "files": [params["out"]], "size": "64x64", "engine": "WORKBENCH", "samples": None,
                  "samples_ignored": False, "format": "PNG"}
        if self.fit:
            result["fit"] = self.fit
        return RunOutcome(result=result, modified=False, saved=None, blender_version="5.2.0 LTS",
                          duration_ms=5, stdout="", warnings=[])


@pytest.fixture
def work(monkeypatch):
    WORK.mkdir(parents=True, exist_ok=True)
    (WORK / "scene.blend").write_bytes(b"")
    monkeypatch.chdir(WORK)
    return WORK


def invoke(argv, transport=None):
    inv, wants_help = flagparser.parse(cmd.SPEC, argv)
    assert not wants_help
    inv.transport = transport or FakeTransport()
    return cmd.run(inv), inv.transport


def fails(argv, transport=None):
    with pytest.raises(AxiError) as exc:
        invoke(argv, transport)
    return exc.value


# --- CLI ---------------------------------------------------------------------------------------------

def test_fit_flag_validation(work):
    for argv in (["--fit", "--fit-margin", "0.5"], ["--fit-margin", "0.5"], ["--fit-margin", "1.3"]):
        err = fails(argv)
        assert err.exit_code == 2 and err.code == "VALIDATION_ERROR"
    assert "--fit-margin must be >= 1" in fails(["--fit", "--fit-margin", "0.9"]).message
    assert "needs --fit" in fails(["--fit-margin", "1.5"]).message
    assert fails(["--fit-margin", "1.5"], FakeTransport()).help  # actionable example


def test_fit_margin_one_is_allowed(work):
    _, t = invoke(["--fit", "--fit-margin", "1", "--out", "a.png"])
    assert t.calls[0][1]["fit_margin"] == 1.0


def test_params_carry_fit(work):
    _, t = invoke(["--out", "a.png"])
    p = t.calls[0][1]
    assert p["fit"] is False and p["fit_margin"] == 1.1 and p["temp_camera_ok"] is True
    _, t = invoke(["--fit", "--fit-margin", "1.3", "--out", "b.png"])
    p = t.calls[0][1]
    assert p["fit"] is True and p["fit_margin"] == 1.3 and p["temp_camera_ok"] is True


def test_live_never_allows_a_temporary_camera(work):
    _, t = invoke(["--live", "--fit", "--out", "a.png"])
    assert t.calls[0][1]["temp_camera_ok"] is False


def test_no_fit_output_is_unchanged(work):
    res, _ = invoke(["--out", "a.png"])
    assert "fit" not in res.data and len(res.help) == 2


def test_fit_text_and_hint_for_a_moved_camera(work):
    res, _ = invoke(["scene.blend", "--fit", "--out", "a.png"], FakeTransport(fit=FIT))
    assert list(res.data) == ["rendered", "output", "size", "engine", "samples", "fit", "duration_ms"]
    assert res.data["fit"] == "Cam moved 0,-8,4 -> 0,-17.2,8.3"
    assert res.help[0] == "Run `blender-axi set Cam scene.blend --location 0,-17.2,8.3 --in-place` to keep this framing"
    assert len(res.help) == 3


def test_fit_text_and_hint_for_a_temporary_camera(work):
    res, _ = invoke(["scene.blend", "--fit", "--out", "a.png"], FakeTransport(fit=TEMP))
    assert res.data["fit"] == "temporary camera at 0,-17.2,8.3 (rotation 63.6,0,46.7)"
    assert res.help[0] == ("Run `blender-axi add camera scene.blend --name Cam --location 0,-17.2,8.3 "
                           "--rotation 63.6,0,46.7 --in-place` to keep it")


def test_live_hints_drop_file_and_in_place(work):
    res, _ = invoke(["--live", "--fit", "--out", "a.png"], FakeTransport(fit=FIT))
    assert res.help[0] == "Run `blender-axi set Cam --location 0,-17.2,8.3 --live` to keep this framing"


def test_ortho_fit_names_the_scale_and_gives_no_location_only_hint(work):
    fit = {**FIT, "ortho_scale": 14.5}
    res, _ = invoke(["scene.blend", "--fit", "--out", "a.png"], FakeTransport(fit=fit))
    assert res.data["fit"].endswith(", ortho scale 14.5") and len(res.help) == 2


def test_live_without_camera_hint(work):
    err = fails(["--live", "--fit", "--out", "a.png"], FakeTransport(error=AxiError("NOT_FOUND", "scene has no active camera")))
    assert err.code == "NOT_FOUND"
    assert err.help == ["Run `blender-axi add camera --live` then rerun with --fit"]


def test_no_camera_without_fit_keeps_the_old_help(work):
    err = fails(["--out", "a.png"], FakeTransport(error=AxiError("NOT_FOUND", "scene has no active camera")))
    assert "objects <file> --type CAMERA" in err.help[0]


def test_spec_lists_fit_flags():
    from blender_axi import helptext

    shown = " ".join(f["flag"] for f in helptext.render(cmd.SPEC)["flags"])
    assert "--fit" in shown and "--fit-margin <n>" in shown


# --- camera_fit / op with fake bpy -------------------------------------------------------------------

def cube_corners(cx, cy, cz, h=1.0):
    return [(cx + dx * h, cy + dy * h, cz + dz * h) for dx in (-1, 1) for dy in (-1, 1) for dz in (-1, 1)]


IDENTITY = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]


class Obj(types.SimpleNamespace):
    def visible_get(self):
        return self.visible

    def evaluated_get(self, depsgraph):
        return self


def mesh(name, centre, visible=True, type="MESH", translate=(0, 0, 0)):
    """bound_box in local space (around the origin); matrix_world carries the placement."""
    mw = [row[:] for row in IDENTITY]
    for i in range(3):
        mw[i][3] = centre[i] + translate[i]
    return Obj(name=name, type=type, visible=visible, bound_box=cube_corners(0, 0, 0), matrix_world=mw)


def make_cam(ortho=False):
    # looks down world +Y: view = -(column 2) = (0, 1, 0)
    mw = [[1, 0, 0, 0], [0, 0, -1, -8], [0, 1, 0, 4], [0, 0, 0, 1]]
    cam = types.SimpleNamespace(name="Cam", type="CAMERA", matrix_world=mw, location=(0.0, -8.0, 4.0),
                                data=types.SimpleNamespace(type="ORTHO" if ortho else "PERSP", ortho_scale=6.0))
    cam.fit_calls = []

    def camera_fit_coords(depsgraph, coords):
        cam.fit_calls.append(list(coords))
        return (0.0, -20.0, 4.0), 12.0

    cam.camera_fit_coords = camera_fit_coords
    return cam


class Collection(list):
    def link(self, obj):
        self.append(obj)


def make_world(bpy, objects, camera, engine="BLENDER_WORKBENCH"):
    render = types.SimpleNamespace(engine=engine, resolution_x=64, resolution_y=64, resolution_percentage=100,
                                   filepath="", use_file_extension=True,
                                   image_settings=types.SimpleNamespace(file_format="PNG", color_mode="RGBA",
                                                                        color_depth="8", compression=15, quality=90))
    scene_objects = list(objects) + ([camera] if camera is not None else [])
    scene = types.SimpleNamespace(render=render, camera=camera, frame_current=1, frame_subframe=0.0,
                                  cycles=types.SimpleNamespace(samples=8),
                                  eevee=types.SimpleNamespace(taa_render_samples=8), objects=scene_objects,
                                  collection=types.SimpleNamespace(objects=Collection(scene_objects)))
    scene.frame_set = lambda frame, subframe=0.0: setattr(scene, "frame_current", frame)
    bpy.context.scene = scene
    bpy.context.evaluated_depsgraph_get = lambda: "depsgraph"
    bpy.context.view_layer = types.SimpleNamespace(update=lambda: None)
    removed = []

    def new_object(name, data):
        obj = types.SimpleNamespace(name=name, type="CAMERA", data=data, rotation_euler=(0, 0, 0), location=(0.0, 0.0, 0.0),
                                    matrix_world=[[1, 0, 0, 0], [0, 0, -1, 0], [0, 1, 0, 0], [0, 0, 0, 1]])
        obj.camera_fit_calls = []
        obj.camera_fit_coords = lambda dg, coords: ((1.0, -10.0, 5.0), 3.0)
        scene.created = obj
        return obj

    bpy.data.objects = types.SimpleNamespace(
        get=lambda n: next((o for o in scene_objects if o.name == n), None), new=new_object,
        remove=lambda o: (removed.append(("object", o.name)), o in scene.collection.objects and scene.collection.objects.remove(o)))
    bpy.data.cameras = types.SimpleNamespace(
        new=lambda name: types.SimpleNamespace(name=name, type="PERSP", ortho_scale=6.0),
        remove=lambda d: removed.append(("camera", d.name)))
    seen = []

    def fake_render(write_still):
        Path(render.filepath).write_bytes(b"png")
        cam = scene.camera
        seen.append({"camera": cam, "location": tuple(cam.location), "ortho": cam.data.ortho_scale})

    bpy.ops.render = types.SimpleNamespace(render=fake_render)
    return scene, seen, removed


def params(name, **extra):
    d = WORK / "op"
    d.mkdir(parents=True, exist_ok=True)
    return {"out": str(d / name), "force": True, "fit": True, "fit_margin": 1.1, **extra}


def two_cubes():
    return [mesh("L", (-6, 0, 4)), mesh("R", (6, 0, 4))]


def test_coords_skip_hidden_and_non_geometry(fake_bpy):
    scene, _, _ = make_world(fake_bpy, [mesh("A", (0, 0, 0)), mesh("Hid", (50, 0, 0), visible=False),
                                        mesh("Light", (60, 0, 0), type="LIGHT"), mesh("Empty", (70, 0, 0), type="EMPTY")],
                             make_cam())
    flat = camera_fit.collect_coords("dg", scene)
    assert len(flat) == 8 * 3 and max(flat[0::3]) == 1.0  # only A (the camera is not geometry)


def test_coords_are_world_space_with_the_object_matrix(fake_bpy):
    scene, _, _ = make_world(fake_bpy, [mesh("A", (10, 0, 0))], make_cam())
    flat = camera_fit.collect_coords("dg", scene)
    assert sorted(set(flat[0::3])) == [9.0, 11.0]


def test_perspective_margin_moves_back_along_minus_view_and_restores(fake_bpy):
    cam = make_cam()
    scene, seen, _ = make_world(fake_bpy, two_cubes(), cam)
    res = op_render.run(params("p.png"))
    # camera_fit_coords -> (0, -20, 4); centre of the cubes (0, 0, 4): distance 20; view = +Y; margin .1 -> 2 further back
    assert seen[0]["location"] == pytest.approx((0.0, -22.0, 4.0))
    assert cam.location == (0.0, -8.0, 4.0)  # restored
    assert res["fit"]["camera"] == "Cam" and res["fit"]["temporary"] is False and res["fit"]["ortho_scale"] is None
    assert res["fit"]["from"] == [0.0, -8.0, 4.0] and res["fit"]["location"] == pytest.approx([0.0, -22.0, 4.0])
    assert len(cam.fit_calls[0]) == 16 * 3


def test_margin_one_is_tight(fake_bpy):
    cam = make_cam()
    _, seen, _ = make_world(fake_bpy, two_cubes(), cam)
    op_render.run(params("tight.png", fit_margin=1.0))
    assert seen[0]["location"] == pytest.approx((0.0, -20.0, 4.0))


def test_orthographic_sets_scale_and_location_and_restores(fake_bpy):
    cam = make_cam(ortho=True)
    _, seen, _ = make_world(fake_bpy, two_cubes(), cam)
    res = op_render.run(params("o.png"))
    assert seen[0]["location"] == (0.0, -20.0, 4.0) and seen[0]["ortho"] == pytest.approx(12.0 * 1.1)
    assert cam.location == (0.0, -8.0, 4.0) and cam.data.ortho_scale == 6.0
    assert res["fit"]["ortho_scale"] == pytest.approx(13.2)


def test_camera_is_restored_when_the_render_raises(fake_bpy):
    cam = make_cam()
    _, _, _ = make_world(fake_bpy, two_cubes(), cam)

    def boom(write_still):
        raise RuntimeError("render crashed")

    fake_bpy.ops.render = types.SimpleNamespace(render=boom)
    with pytest.raises(RuntimeError, match="render crashed"):
        op_render.run(params("boom.png"))
    assert cam.location == (0.0, -8.0, 4.0)


def test_camera_is_restored_when_the_fit_itself_fails(fake_bpy):
    cam = make_cam()
    make_world(fake_bpy, two_cubes(), cam)

    def bad(depsgraph, coords):
        raise RuntimeError("fit failed")

    cam.camera_fit_coords = bad
    with pytest.raises(RuntimeError, match="fit failed"):
        op_render.run(params("bad.png"))
    assert cam.location == (0.0, -8.0, 4.0)


def test_named_camera_is_the_one_fitted(fake_bpy):
    cam = make_cam()
    other = make_cam()
    other.name = "Other"
    scene, seen, _ = make_world(fake_bpy, two_cubes() + [other], cam)
    res = op_render.run(params("n.png", camera="Other"))
    assert seen[0]["camera"] is other and res["fit"]["camera"] == "Other" and scene.camera is cam
    assert other.location == (0.0, -8.0, 4.0)


def test_nothing_to_fit(fake_bpy):
    cam = make_cam()
    make_world(fake_bpy, [mesh("Hid", (0, 0, 0), visible=False), mesh("Light", (0, 0, 0), type="LIGHT")], cam)
    with pytest.raises(OpError) as e:
        op_render.run(params("none.png"))
    assert e.value.code == "OP_FAILED" and "nothing to fit" in e.value.message
    assert cam.location == (0.0, -8.0, 4.0)


def test_temporary_camera_headless_is_created_used_and_removed(fake_bpy):
    scene, seen, removed = make_world(fake_bpy, two_cubes(), None)
    res = op_render.run(params("t.png", temp_camera_ok=True))
    created = scene.created
    assert seen[0]["camera"] is created and created.name == "AxiFit"
    assert created.rotation_euler == pytest.approx(tuple(math.radians(d) for d in (63.6, 0.0, 46.7)))
    # fit -> (1, -10, 5); centre (0, 0, 4); distance sqrt(102); margin .1 further back along +Y's opposite
    assert seen[0]["location"] == pytest.approx((1.0, -10.0 - math.sqrt(102) * 0.1, 5.0))
    assert res["fit"]["temporary"] is True and res["fit"]["from"] is None and res["fit"]["camera"] == "AxiFit"
    assert scene.camera is None  # scene camera put back
    assert removed == [("object", "AxiFit"), ("camera", "AxiFit")]
    assert created not in scene.collection.objects


def test_temporary_camera_is_removed_when_the_render_raises(fake_bpy):
    scene, _, removed = make_world(fake_bpy, two_cubes(), None)
    fake_bpy.ops.render = types.SimpleNamespace(render=lambda write_still: (_ for _ in ()).throw(RuntimeError("x")))
    with pytest.raises(RuntimeError):
        op_render.run(params("t2.png", temp_camera_ok=True))
    assert scene.camera is None and removed == [("object", "AxiFit"), ("camera", "AxiFit")]


def test_no_camera_and_no_temp_permission_is_not_found(fake_bpy):
    scene, _, removed = make_world(fake_bpy, two_cubes(), None)
    with pytest.raises(OpError) as e:
        op_render.run(params("live.png", temp_camera_ok=False))
    assert e.value.code == "NOT_FOUND" and "no active camera" in e.value.message and removed == []


def test_nothing_to_fit_creates_no_temporary_camera(fake_bpy):
    scene, _, removed = make_world(fake_bpy, [], None)
    with pytest.raises(OpError) as e:
        op_render.run(params("empty.png", temp_camera_ok=True))
    assert "nothing to fit" in e.value.message and removed == [] and not hasattr(scene, "created")


def test_without_fit_the_op_is_untouched(fake_bpy):
    cam = make_cam()
    _, seen, _ = make_world(fake_bpy, two_cubes(), cam)
    res = op_render.run({"out": str(WORK / "op" / "nofit.png"), "force": True})
    assert "fit" not in res and cam.fit_calls == [] and seen[0]["location"] == (0.0, -8.0, 4.0)
