"""`render --subject` and the turntable re-aim (0.7.0 plan U1): flag validation, params, texts and hints; the op with a fake bpy."""

import sys
import types
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from blender_axi import flags as flagparser
from blender_axi.blender_side import camera_fit, lighting, op_render, turntable
from blender_axi.blender_side.ops import OpError
from blender_axi.commands import render as cmd
from blender_axi.errors import AxiError
from blender_axi.runner import RunOutcome

WORK = Path(__file__).resolve().parents[2] / ".tmp" / "tests" / "unit_render_subject"
TURN = {"steps": 4, "centre": [0.0, 0.0, 0.9], "camera": "Cam"}
FIT = {"camera": "Cam", "temporary": False, "location": [0.0, -17.2, 8.3], "from": [0.0, -8.0, 4.0], "ortho_scale": None}
AIM = {"from": [63.6, 0.0, 46.7], "to": [65.1, 0.0, 46.7]}
AUTO = {"mode": "auto", "objects": ["TableTop", "Mug"], "excluded": ["Floor"], "centre": [0.0, 0.0, 0.9]}
NAMED = {"mode": "named", "objects": ["Mug", "MugHandle"], "excluded": [], "centre": [0.1, 0.2, 1.0]}
ALL = {"mode": "all", "objects": ["L", "R"], "excluded": [], "centre": [0.0, 0.0, 0.5]}


class FakeTransport:
    name = "fake"

    def __init__(self, fail=None, extra=None):
        self.calls, self.fail, self.extra = [], fail, extra or {}

    def run(self, op, params, *, blend=None, save=None, timeout=None):
        self.calls.append((op, params, blend, timeout))
        if self.fail:
            raise self.fail
        turn = params.get("turntable")
        result = {"frames": list(range(1, (turn or 1) + 1)), "size": "48x48", "engine": "WORKBENCH", "samples": None,
                  "samples_ignored": False, "format": "PNG", "lighting": {"unlit": False}}
        result["files"] = [params["out"]] if not turn else [f"{params['out']}-{i}" for i in range(1, turn + 1)]
        if turn:
            result["turntable"] = {**TURN, "steps": turn}
        result.update(self.extra)
        return RunOutcome(result=result, modified=False, saved=None, blender_version="5.2.0 LTS",
                          duration_ms=5, stdout="", warnings=[])


@pytest.fixture
def work(monkeypatch):
    WORK.mkdir(parents=True, exist_ok=True)
    (WORK / "scene.blend").write_bytes(b"")
    for stale in WORK.glob("*.png"):
        stale.unlink()
    monkeypatch.chdir(WORK)
    return WORK


def invoke(argv, transport=None, live=False):
    inv, wants_help = flagparser.parse(cmd.SPEC, argv)
    assert not wants_help
    inv.transport = transport or FakeTransport()
    inv.globals.live = live
    return cmd.run(inv), inv.transport


def fails(argv, transport=None):
    with pytest.raises(AxiError) as exc:
        invoke(["scene.blend", *argv], transport)
    return exc.value


# --- validation and params ------------------------------------------------------------------------------

def test_subject_without_turntable_or_fit_is_exit_2(work):
    t = FakeTransport()
    err = fails(["--subject", "Mug", "--out", "x.png"], t)
    assert err.code == "VALIDATION_ERROR" and err.exit_code == 2 and t.calls == []
    assert err.message == "--subject needs --turntable or --fit" and err.help


def test_empty_subject_is_exit_2(work):
    err = fails(["--turntable", "3", "--subject", "", "--out", "x.png"])
    assert err.code == "VALIDATION_ERROR" and "--subject" in err.message


def test_subject_is_parsed_as_a_list_and_sent_in_the_params(work):
    _, t = invoke(["scene.blend", "--turntable", "4", "--fit", "--subject", "Mug,MugHandle", "--out", "t_####.png"])
    assert t.calls[0][1]["subject"] == ["Mug", "MugHandle"] and t.calls[0][1]["turntable"] == 4


def test_subject_with_fit_alone_is_allowed(work):
    _, t = invoke(["scene.blend", "--fit", "--subject", "Mug", "--out", "m.png"])
    params = t.calls[0][1]
    assert params["subject"] == ["Mug"] and params["fit"] is True and params["turntable"] is None


def test_without_the_flag_the_subject_param_is_none(work):
    _, t = invoke(["scene.blend", "--turntable", "3", "--out", "t_####.png"])
    assert t.calls[0][1]["subject"] is None
    _, t = invoke(["scene.blend", "--fit", "--out", "f.png"])
    assert t.calls[0][1]["subject"] is None


def test_flag_is_declared_after_turntable():
    from blender_axi import helptext

    names = [f["flag"].split()[0] for f in helptext.render(cmd.SPEC)["flags"]]
    assert names.index("--subject") == names.index("--turntable") + 1
    flag = {f["flag"].split()[0]: f for f in helptext.render(cmd.SPEC)["flags"]}["--subject"]
    assert "ground" in flag["help"]


# --- texts ------------------------------------------------------------------------------------------------

def test_named_subject_text(work):
    res, _ = invoke(["scene.blend", "--turntable", "4", "--subject", "Mug,MugHandle", "--out", "t_####.png"],
                    FakeTransport(extra={"subject": NAMED}))
    assert res.data["subject"] == "2 named (Mug, MugHandle)"


def test_auto_subject_text_names_the_excluded_ground(work):
    res, _ = invoke(["scene.blend", "--turntable", "4", "--out", "t_####.png"], FakeTransport(extra={"subject": AUTO}))
    assert res.data["subject"] == "2 objects (ground excluded: Floor)"


def test_excluded_list_is_capped(work):
    many = {**AUTO, "objects": ["A"] * 3, "excluded": [f"G{i}" for i in range(7)]}
    res, _ = invoke(["scene.blend", "--turntable", "4", "--out", "t_####.png"], FakeTransport(extra={"subject": many}))
    assert res.data["subject"] == "3 objects (ground excluded: G0, G1, G2, G3, G4, +2 more)"


def test_named_list_is_capped(work):
    names = [f"N{i}" for i in range(7)]
    res, _ = invoke(["scene.blend", "--turntable", "4", "--subject", ",".join(names), "--out", "t_####.png"],
                    FakeTransport(extra={"subject": {**NAMED, "objects": names}}))
    assert res.data["subject"] == "7 named (N0, N1, N2, N3, N4, +2 more)"


def test_subject_is_omitted_when_nothing_was_excluded(work):
    res, _ = invoke(["scene.blend", "--turntable", "4", "--out", "t_####.png"], FakeTransport(extra={"subject": ALL}))
    assert "subject" not in res.data


def test_aim_text_names_the_camera_and_the_centre(work):
    res, _ = invoke(["scene.blend", "--turntable", "4", "--out", "t_####.png"],
                    FakeTransport(extra={"subject": AUTO, "aim": AIM}))
    assert res.data["aim"] == "Cam turned 63.6,0,46.7 -> 65.1,0,46.7 toward 0,0,0.9"


def test_aim_shows_even_when_the_subject_line_is_omitted(work):
    res, _ = invoke(["scene.blend", "--turntable", "4", "--out", "t_####.png"],
                    FakeTransport(extra={"subject": ALL, "aim": AIM}))
    assert "subject" not in res.data and res.data["aim"].startswith("Cam turned ")


def test_key_order_puts_subject_and_aim_before_turntable(work):
    res, _ = invoke(["scene.blend", "--turntable", "4", "--fit", "--out", "t_####.png"],
                    FakeTransport(extra={"fit": FIT, "subject": AUTO, "aim": AIM}))
    assert list(res.data) == ["rendered", "output", "size", "engine", "samples", "fit", "subject", "aim", "turntable",
                              "duration_ms"]


def test_a_plain_fit_render_has_no_subject_or_aim_keys(work):
    res, _ = invoke(["scene.blend", "--fit", "--out", "f.png"], FakeTransport(extra={"fit": FIT}))
    assert "subject" not in res.data and "aim" not in res.data and "turntable" not in res.data


def test_fit_with_a_named_subject_shows_the_subject_line_only(work):
    res, _ = invoke(["scene.blend", "--fit", "--subject", "Mug,MugHandle", "--out", "m.png"],
                    FakeTransport(extra={"fit": FIT, "subject": NAMED}))
    assert res.data["subject"] == "2 named (Mug, MugHandle)" and "aim" not in res.data


# --- hints ------------------------------------------------------------------------------------------------

def test_keep_hint_after_an_aim_carries_the_rotation(work):
    res, _ = invoke(["scene.blend", "--turntable", "4", "--fit", "--out", "t_####.png"],
                    FakeTransport(extra={"fit": FIT, "subject": AUTO, "aim": AIM}))
    assert res.help[0] == ("Run `blender-axi set Cam scene.blend --location 0,-17.2,8.3 --rotation 65.1,0,46.7 --in-place` "
                           "to keep this framing")


def test_keep_hint_prefers_the_local_rotation_of_a_parented_camera(work):
    aim = {**AIM, "local_to": [63.4, 0.0, 0.0]}
    res, _ = invoke(["scene.blend", "--turntable", "4", "--fit", "--out", "t_####.png"],
                    FakeTransport(extra={"fit": FIT, "subject": AUTO, "aim": aim}))
    assert res.help[0] == ("Run `blender-axi set Cam scene.blend --location 0,-17.2,8.3 --rotation 63.4,0,0 --in-place` "
                           "to keep this framing")
    assert res.data["aim"] == "Cam turned 63.6,0,46.7 -> 65.1,0,46.7 toward 0,0,0.9"  # the aim: text stays world


def test_keep_hint_without_an_aim_is_unchanged(work):
    res, _ = invoke(["scene.blend", "--fit", "--out", "f.png"], FakeTransport(extra={"fit": FIT}))
    assert res.help[0] == "Run `blender-axi set Cam scene.blend --location 0,-17.2,8.3 --in-place` to keep this framing"


def test_not_found_subject_points_at_objects(work):
    err = fails(["--turntable", "3", "--subject", "Ghost", "--out", "t_####.png"],
                FakeTransport(fail=AxiError("NOT_FOUND", "subject 'Ghost' not found (the file has 3 objects)", help=["generic"])))
    assert err.code == "NOT_FOUND" and err.help == ["Run `blender-axi objects <file>` to list objects"]


def test_not_found_subject_in_live_mode_drops_the_file(work):
    inv, _ = flagparser.parse(cmd.SPEC, ["--turntable", "3", "--subject", "Ghost", "--out", "t_####.png"])
    inv.transport = FakeTransport(fail=AxiError("NOT_FOUND", "subject 'Ghost' not found", help=["generic"]))
    inv.globals.live = True
    with pytest.raises(AxiError) as err:
        cmd.run(inv)
    assert "<file>" not in err.value.help[0] and err.value.help[0].endswith("to list objects")


# --- op with fake bpy -----------------------------------------------------------------------------------

class FakeMatrix(list):
    """A 4x4 world matrix: iterable rows, `.translation` and `.to_euler()` for the aim."""

    def __init__(self, rows, cam_ref=None):
        super().__init__([list(r) for r in rows])
        self.cam_ref = cam_ref

    @property
    def translation(self):
        return Vec([self[i][3] for i in range(3)])

    def to_euler(self):
        return self.cam_ref().rotation_euler if self.cam_ref else (0.0, 0.0, 0.0)


class Vec:
    def __init__(self, v):
        self.v = tuple(float(x) for x in v)

    def __sub__(self, other):
        return Vec(a - b for a, b in zip(self.v, other.v))

    def to_track_quat(self, track, up):
        return NS(to_euler=lambda mode: (1.25, 0.0, 0.75))


DESK = [
    ("Floor", [-6, -6, 0, -6, -6, 0, -6, 6, 0, -6, 6, 0, 6, -6, 0, 6, -6, 0, 6, 6, 0, 6, 6, 0]),
    ("Top", [-2, -1, 0.7, 2, 1, 0.86]),
    ("Mug", [0, 0, 0.9, 0.3, 0.3, 1.2]),
]
OBJECTS = [NS(name=n, type="MESH") for n in ("Floor", "Top", "Mug")] + [NS(name="Cam", type="CAMERA")]


def make_scene(bpy, monkeypatch, entries=DESK):
    monkeypatch.setitem(sys.modules, "mathutils", types.SimpleNamespace(Matrix=FakeMatrix, Vector=Vec))
    monkeypatch.setattr(camera_fit, "collect_entries", lambda dg, scene: list(entries))
    monkeypatch.setattr(camera_fit, "collect_coords", lambda dg, scene, objects=None: [c for _, e in entries for c in e])
    bpy.data.materials = []
    bpy.data.objects = list(OBJECTS)
    bpy.context.view_layer = NS(update=lambda: None)
    bpy.context.evaluated_depsgraph_get = lambda: "dg"
    cam = NS(name="Cam", location=(0.0, -10.0, 5.0), rotation_euler=(1.1, 0.0, 0.0), scale=(1.0, 1.0, 1.0),
             rotation_mode="XYZ", parent=None)
    cam.matrix_world = FakeMatrix([[1, 0, 0, 0], [0, 0, -1, -10], [0, 1, 0, 5], [0, 0, 0, 1]], lambda: cam)
    render = NS(engine="BLENDER_WORKBENCH", resolution_x=48, resolution_y=48, resolution_percentage=100, filepath="",
                use_file_extension=True,
                image_settings=NS(file_format="PNG", color_mode="RGBA", color_depth="8", compression=15, quality=90))
    sun = NS(name="Sun", type="LIGHT", hide_render=False, visible_get=lambda: True, data=NS(energy=3.0))
    scene = NS(render=render, camera=cam, frame_current=7, frame_subframe=0.0, objects=[sun], world=None,
               cycles=NS(samples=8), eevee=NS(taa_render_samples=8))
    scene.frame_set = lambda frame, subframe=0.0: setattr(scene, "frame_current", frame)
    scene.written = []

    def fake_render(write_still=False, **kw):
        Path(render.filepath).parent.mkdir(parents=True, exist_ok=True)
        Path(render.filepath).write_bytes(b"png")
        scene.written.append(render.filepath)

    bpy.context.scene = scene
    bpy.ops.render = NS(render=fake_render)
    return scene, cam


def op_dir():
    d = WORK / "op"
    d.mkdir(parents=True, exist_ok=True)
    return d


def spy(monkeypatch, order):
    """Record the order of aim, fit, lighting and plan; the real plan runs, aim and fit are stand-ins."""
    real_plan, real_assess = turntable.plan, lighting.assess
    calls = {}

    def fake_aim(bpy, cam, centre, state):
        order.append("aim")
        calls["aim"] = centre
        state.aim = AIM
        return AIM

    def fake_fit(bpy, scene, margin, temp_ok, state, coords=None):
        order.append("fit")
        calls["fit"] = coords
        return FIT

    def fake_plan(bpy, scene, n, state, coords=None):
        order.append("plan")
        calls["plan"] = coords
        return real_plan(bpy, scene, n, state, coords=coords)

    monkeypatch.setattr(turntable, "aim", fake_aim)
    monkeypatch.setattr(camera_fit, "fit_scene", fake_fit)
    monkeypatch.setattr(turntable, "plan", fake_plan)
    monkeypatch.setattr(lighting, "assess", lambda bpy, scene: order.append("lighting") or real_assess(bpy, scene))
    return calls


def test_op_orders_aim_fit_lighting_plan(fake_bpy, monkeypatch):
    make_scene(fake_bpy, monkeypatch)
    order = []
    calls = spy(monkeypatch, order)
    res = op_render.run({"out": str(op_dir() / "o_####.png"), "force": True, "turntable": 3, "fit": True})
    assert order == ["aim", "fit", "lighting", "plan"]
    # the floor is excluded: aim looks at the centre of the table top and the mug; plan gets the corners, fit their
    # sweep over the 3 steps (0.10.0 K6; step 1 is the corners themselves)
    corners = DESK[1][1] + DESK[2][1]
    assert calls["plan"] == corners
    assert calls["fit"] == turntable.sweep(corners, turntable.centre(corners), 3) and calls["fit"][:len(corners)] == corners
    assert calls["aim"] == [0.0, 0.0, 0.95]
    assert res["subject"] == {"mode": "auto", "objects": ["Top", "Mug"], "excluded": ["Floor"], "centre": [0.0, 0.0, 0.95]}
    assert res["aim"] == AIM and res["turntable"]["centre"] == [0.0, 0.0, 0.95]
    keys = list(res)
    assert keys.index("subject") < keys.index("aim") < keys.index("turntable")


def test_op_without_subject_or_turntable_does_not_touch_the_subject(fake_bpy, monkeypatch):
    make_scene(fake_bpy, monkeypatch)
    monkeypatch.setattr(camera_fit, "collect_entries", lambda dg, scene: pytest.fail("no subject was asked for"))
    order = []
    calls = spy(monkeypatch, order)
    res = op_render.run({"out": str(op_dir() / "p.png"), "force": True, "fit": True})
    assert order == ["fit", "lighting"] and calls["fit"] is None  # today's call: fit_scene collects everything itself
    assert "subject" not in res and "aim" not in res


def test_op_named_subject_with_fit_and_no_turntable_does_not_aim(fake_bpy, monkeypatch):
    make_scene(fake_bpy, monkeypatch)
    order = []
    calls = spy(monkeypatch, order)
    res = op_render.run({"out": str(op_dir() / "q.png"), "force": True, "fit": True, "subject": ["Mug"]})
    assert order == ["fit", "lighting"] and calls["fit"] == DESK[2][1]
    assert res["subject"] == {"mode": "named", "objects": ["Mug"], "excluded": [], "centre": [0.15, 0.15, 1.05]}
    assert "aim" not in res and "turntable" not in res


def test_op_does_not_aim_a_temporary_camera(fake_bpy, monkeypatch):
    scene, cam = make_scene(fake_bpy, monkeypatch)
    scene.camera = None  # headless file without a camera: --fit makes one, which keeps the default angle
    order = []
    spy(monkeypatch, order)

    def fit_with_temp(bpy, scn, margin, temp_ok, state, coords=None):
        order.append("fit")
        scn.camera = cam
        return {**FIT, "temporary": True, "from": None}

    monkeypatch.setattr(camera_fit, "fit_scene", fit_with_temp)
    res = op_render.run({"out": str(op_dir() / "t_####.png"), "force": True, "turntable": 2, "fit": True,
                         "temp_camera_ok": True})
    assert "aim" not in order and "aim" not in res and res["subject"]["mode"] == "auto"


def test_op_validates_subject_names_before_changing_anything(fake_bpy, monkeypatch):
    scene, cam = make_scene(fake_bpy, monkeypatch)
    monkeypatch.setattr(camera_fit, "fit_scene", lambda *a, **k: pytest.fail("the fit must not run"))
    monkeypatch.setattr(turntable, "aim", lambda *a, **k: pytest.fail("the aim must not run"))
    target = op_dir() / "none_####.png"
    with pytest.raises(OpError, match="subject 'Ghost' not found") as err:
        op_render.run({"out": str(target), "force": True, "turntable": 2, "fit": True, "subject": ["Mug", "Ghost"]})
    assert err.value.code == "NOT_FOUND" and not list(op_dir().glob("none_*"))
    assert cam.rotation_euler == (1.1, 0.0, 0.0) and scene.written == []


def test_op_subject_without_visible_geometry_fails(fake_bpy, monkeypatch):
    make_scene(fake_bpy, monkeypatch)
    with pytest.raises(OpError, match="subject has no visible geometry: Cam") as err:
        op_render.run({"out": str(op_dir() / "c.png"), "force": True, "fit": True, "subject": ["Cam"]})
    assert err.value.code == "OP_FAILED"


def test_op_all_mode_when_there_is_no_ground(fake_bpy, monkeypatch):
    make_scene(fake_bpy, monkeypatch, entries=DESK[1:])
    res = op_render.run({"out": str(op_dir() / "n_####.png"), "force": True, "turntable": 2, "fit": False})
    assert res["subject"]["mode"] == "all" and res["subject"]["excluded"] == [] and "aim" in res


def test_op_restores_location_and_rotation_when_the_render_raises(fake_bpy, monkeypatch):
    scene, cam = make_scene(fake_bpy, monkeypatch)
    calls = []

    def boom(write_still=False, **kw):
        calls.append(1)
        if len(calls) == 2:
            cam.location = (3.0, 3.0, 3.0)  # as if the orbit had moved it
            raise RuntimeError("render crashed")
        Path(scene.render.filepath).write_bytes(b"png")

    fake_bpy.ops.render = NS(render=boom)
    with pytest.raises(RuntimeError, match="render crashed"):
        op_render.run({"out": str(op_dir() / "r_####.png"), "force": True, "turntable": 3})
    assert cam.location == (0.0, -10.0, 5.0) and cam.rotation_euler == (1.1, 0.0, 0.0)  # real aim ran, then undo


def test_op_real_aim_changes_the_rotation_during_the_run_and_restores_it(fake_bpy, monkeypatch):
    scene, cam = make_scene(fake_bpy, monkeypatch)
    seen = []
    real_render = fake_bpy.ops.render.render

    def watch(write_still=False, **kw):
        seen.append(cam.rotation_euler)
        real_render(write_still=write_still)

    fake_bpy.ops.render = NS(render=watch)
    res = op_render.run({"out": str(op_dir() / "w_####.png"), "force": True, "turntable": 2})
    assert seen[0] == (1.25, 0.0, 0.75)  # aimed
    assert cam.rotation_euler == (1.1, 0.0, 0.0)  # and put back
    assert res["aim"]["from"] == [63.0254, 0.0, 0.0] and "restore_warnings" not in res
