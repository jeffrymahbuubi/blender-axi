"""`render --turntable` (0.6.0 plan U5): flag validation, params, run dir, texts and hints; the op with a fake bpy."""

import sys
import types
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from blender_axi import flags as flagparser
from blender_axi import paths
from blender_axi.blender_side import auto_light, camera_fit, op_render, turntable, video
from blender_axi.commands import render as cmd
from blender_axi.errors import AxiError
from blender_axi.runner import RunOutcome

WORK = Path(__file__).resolve().parents[2] / ".tmp" / "tests" / "unit_render_turntable"
TURN = {"steps": 4, "centre": [0.0, 0.0, 0.5], "camera": "Cam"}
MOVIE = {"codec": "H264", "fps": 12, "size_bytes": 38912}
FIT = {"camera": "Cam", "temporary": False, "location": [0.0, -17.2, 8.3], "from": [0.0, -8.0, 4.0], "ortho_scale": None}
AUTO = {"applied": True, "sun": "AxiSun", "fill": "world created"}


class FakeTransport:
    name = "fake"

    def __init__(self, fail=None, extra=None):
        self.calls, self.fail, self.extra, self.dir_during_call = [], fail, extra or {}, None

    def run(self, op, params, *, blend=None, save=None, timeout=None):
        self.calls.append((op, params, blend, timeout))
        if params.get("video"):
            self.dir_during_call = Path(params["video"]["frames_dir"]).is_dir()
        if self.fail:
            raise self.fail
        turn = params.get("turntable")
        mp4 = params.get("video")
        result = {"frames": list(range(1, (turn or 1) + 1)), "size": "48x48", "engine": "WORKBENCH", "samples": None,
                  "samples_ignored": False, "format": "PNG", "lighting": {"unlit": False}}
        result["files"] = [mp4["out"]] if mp4 else ([params["out"]] if not turn else
                                                      [f"{params['out']}-{i}" for i in range(1, turn + 1)])
        if turn:
            result["turntable"] = {**TURN, "steps": turn}
        if mp4:
            result["video"] = {**MOVIE, "fps": mp4["fps"]}
        result.update(self.extra)
        return RunOutcome(result=result, modified=False, saved=None, blender_version="5.2.0 LTS",
                          duration_ms=5, stdout="", warnings=[])


@pytest.fixture
def work(monkeypatch):
    WORK.mkdir(parents=True, exist_ok=True)
    (WORK / "scene.blend").write_bytes(b"")
    for stale in WORK.glob("*.mp4"):
        stale.unlink()
    for stale in WORK.glob("*.png"):
        stale.unlink()
    monkeypatch.chdir(WORK)
    return WORK


def invoke(argv, transport=None, keep_tmp=False, live=False):
    inv, wants_help = flagparser.parse(cmd.SPEC, argv)
    assert not wants_help
    inv.transport = transport or FakeTransport()
    inv.globals.keep_tmp, inv.globals.live = keep_tmp, live
    return cmd.run(inv), inv.transport


def run_dirs():
    root = paths.tmp_root() / "runs"
    return sorted(root.glob("turntable-*")) if root.is_dir() else []


# --- validation ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("argv, message", [
    (["--turntable", "1", "--out", "t.png"], "--turntable needs at least 2 steps"),
    (["--turntable", "0", "--out", "t.png"], "--turntable needs at least 2 steps"),
    (["--turntable", "4", "--frame", "3", "--out", "t.png"], "--turntable renders the current frame; drop --frame/--frames"),
    (["--turntable", "4", "--frames", "1..3", "--out", "t.png"], "--turntable renders the current frame; drop --frame/--frames"),
    (["--out", "t.mp4"], "--out name.mp4 needs --frames <a..b> or --turntable N"),
    (["--turntable", "4", "--out", "t.mp4", "--format", "PNG"], "--format does not apply to mp4 output"),
    (["--turntable", "4", "--out", "t.png", "--fps", "24"], "--fps applies to mp4 output"),
    (["--fps", "24", "--out", "t.png"], "--fps applies to mp4 output"),
    (["--turntable", "4", "--out", "t.mp4", "--fps", "0"], "--fps must be >= 1"),
])
def test_validation_errors_exit_2(work, argv, message):
    t = FakeTransport()
    with pytest.raises(AxiError) as err:
        invoke(["scene.blend", *argv], t)
    assert err.value.code == "VALIDATION_ERROR" and message in err.value.message and t.calls == []


def test_validation_makes_no_run_dir(work):
    before = run_dirs()
    with pytest.raises(AxiError):
        invoke(["scene.blend", "--out", "t.mp4"])
    assert run_dirs() == before


def test_a_range_still_renders_image_files_and_keeps_its_hint(work):
    res, t = invoke(["scene.blend", "--frames", "1..3", "--out", "r_####.png"])
    assert t.calls[0][1]["turntable"] is None and t.calls[0][1]["video"] is None
    assert res.help[0].startswith("Run `blender-axi render <file> --frames <a..b>")
    assert "turntable" not in res.data and "video" not in res.data


# --- png turntable ------------------------------------------------------------------------------------

def test_png_turntable_params_texts_and_hint(work):
    res, t = invoke(["scene.blend", "--turntable", "4", "--fit", "--out", "t_####.png"],
                    FakeTransport(extra={"fit": FIT, "auto_light": AUTO}))
    params = t.calls[0][1]
    assert params["turntable"] == 4 and params["video"] is None and params["frames"] is None
    assert params["out"] == str(WORK / "t_####.png")
    assert res.data["rendered"] == "4 frames" and res.data["output"].endswith("t_####.png")
    assert res.data["turntable"] == "4 steps around 0,0,0.5 (Cam)" and "video" not in res.data
    assert list(res.data) == ["rendered", "output", "size", "engine", "samples", "fit", "auto_light", "turntable", "duration_ms"]
    assert res.help[0].startswith("Run `blender-axi set Cam")  # the keep-framing hint stays first
    assert res.help[1] == "Run `blender-axi render <file> --turntable 12 --fit --out <dir/turn_####.png>` for a finer turn"
    assert not any("--frames" in h for h in res.help)


def test_png_without_hashes_gets_a_numbered_suffix(work):
    res, _ = invoke(["scene.blend", "--turntable", "3", "--out", "spin.png"])
    assert res.data["output"].endswith("spin_####.png")


def test_default_png_name_is_turn_numbered(work):
    _, t = invoke(["scene.blend", "--turntable", "3"])
    assert t.calls[0][1]["out"] == str(WORK / "scene_turn_####.png")


def test_existing_png_step_is_refused_before_the_transport(work):
    (WORK / "t_0003.png").write_bytes(b"x")
    t = FakeTransport()
    with pytest.raises(AxiError) as err:
        invoke(["scene.blend", "--turntable", "4", "--out", "t_####.png"], t)
    assert err.value.code == "WRITE_REFUSED" and "t_0003.png" in err.value.message and t.calls == []
    res, _ = invoke(["scene.blend", "--turntable", "4", "--out", "t_####.png", "--force"])
    assert res.data["rendered"] == "4 frames"


def test_live_hint_uses_the_live_suffix(work):
    res, _ = invoke(["--turntable", "3", "--out", "t_####.png"], live=True)
    assert res.help[0].endswith("--live` for a finer turn") and "<file>" in res.help[0]


# --- mp4 turntable --------------------------------------------------------------------------------------

def test_mp4_params_run_dir_and_texts(work):
    before = run_dirs()
    res, t = invoke(["scene.blend", "--turntable", "4", "--out", "t.mp4", "--fps", "24"])
    params = t.calls[0][1]
    video_params = params["video"]
    assert params["turntable"] == 4 and params["format"] is None
    assert video_params["out"] == str(WORK / "t.mp4") and video_params["fps"] == 24
    assert Path(video_params["frames_dir"]).parent == paths.tmp_root() / "runs"
    assert Path(video_params["frames_dir"]).name.startswith("turntable-") and t.dir_during_call is True
    assert run_dirs() == before  # cleaned
    assert res.data["rendered"] == "4 frames -> 1 video" and res.data["output"].endswith("t.mp4")
    assert res.data["video"] == "H264 24 fps, 38 KB"
    assert list(res.data)[-3:] == ["turntable", "video", "duration_ms"]
    assert res.help[0] == "Run `blender-axi render <file> --turntable 12 --fit --out <turn.mp4>` for a finer turn"


def test_mp4_default_fps_is_12(work):
    _, t = invoke(["scene.blend", "--turntable", "4", "--out", "t.mp4"])
    assert t.calls[0][1]["video"]["fps"] == 12


def test_run_dir_is_cleaned_when_the_transport_fails(work):
    before = run_dirs()
    with pytest.raises(AxiError):
        invoke(["scene.blend", "--turntable", "4", "--out", "t.mp4"], FakeTransport(fail=AxiError("OP_FAILED", "boom")))
    assert run_dirs() == before


def test_keep_tmp_keeps_the_run_dir(work):
    before = set(run_dirs())
    _, t = invoke(["scene.blend", "--turntable", "4", "--out", "t.mp4"], keep_tmp=True)
    kept = Path(t.calls[0][1]["video"]["frames_dir"])
    assert kept.is_dir() and kept not in before
    paths.cleanup(kept, False)


def test_existing_mp4_is_refused_and_force_allows_it(work):
    (WORK / "t.mp4").write_bytes(b"x")
    t = FakeTransport()
    with pytest.raises(AxiError) as err:
        invoke(["scene.blend", "--turntable", "4", "--out", "t.mp4"], t)
    assert err.value.code == "WRITE_REFUSED" and t.calls == []
    res, _ = invoke(["scene.blend", "--turntable", "4", "--out", "t.mp4", "--force"])
    assert res.data["rendered"] == "4 frames -> 1 video"


def test_small_video_is_shown_in_bytes(work):
    res, _ = invoke(["scene.blend", "--turntable", "2", "--out", "t.mp4"], FakeTransport(extra={"video": {**MOVIE, "size_bytes": 900}}))
    assert res.data["video"] == "H264 12 fps, 900 B"


def test_flags_are_declared():
    from blender_axi import helptext

    flags = {f["flag"].split()[0]: f for f in helptext.render(cmd.SPEC)["flags"]}
    assert "--turntable" in flags and "--fps" in flags and "mp4" in flags["--turntable"]["help"]
    assert "turntable" in cmd.SPEC.summary and "mp4" in cmd.SPEC.summary


# --- op with fake bpy -----------------------------------------------------------------------------------

class FakeMatrix:
    def __init__(self, rows):
        self.rows = [list(r) for r in rows]

    def __iter__(self):
        return iter(self.rows)


def sun_light():
    return NS(name="Sun", type="LIGHT", hide_render=False, visible_get=lambda: True, data=NS(energy=3.0))


def make_scene(bpy, monkeypatch, lights=(sun_light(),), coords=(-4.0, 0.0, 0.0, 4.0, 0.0, 1.0)):
    monkeypatch.setitem(sys.modules, "mathutils", types.SimpleNamespace(Matrix=FakeMatrix))
    monkeypatch.setattr(camera_fit, "collect_coords", lambda dg, scene: list(coords))
    bpy.data.materials = []
    bpy.context.view_layer = NS(update=lambda: None)
    bpy.context.evaluated_depsgraph_get = lambda: "dg"
    cam = NS(name="Cam", matrix_world=FakeMatrix([[1, 0, 0, 0], [0, 0, -1, -10], [0, 1, 0, 5], [0, 0, 0, 1]]),
             location=(0.0, -10.0, 5.0), rotation_euler=(1.1, 0.0, 0.0), scale=(1.0, 1.0, 1.0))
    render = NS(engine="BLENDER_WORKBENCH", resolution_x=48, resolution_y=48, resolution_percentage=100, filepath="",
                use_file_extension=True,
                image_settings=NS(file_format="PNG", color_mode="RGBA", color_depth="8", compression=15, quality=90))
    scene = NS(render=render, camera=cam, frame_current=7, frame_subframe=0.0, objects=list(lights), world=None,
               cycles=NS(samples=8), eevee=NS(taa_render_samples=8))
    scene.frame_calls = []
    scene.frame_set = lambda frame, subframe=0.0: (scene.frame_calls.append(frame), setattr(scene, "frame_current", frame))
    scene.seen = []

    def fake_render(write_still=False, **kw):
        Path(render.filepath).parent.mkdir(parents=True, exist_ok=True)
        Path(render.filepath).write_bytes(b"png")
        scene.seen.append((render.filepath, [list(r) for r in cam.matrix_world], scene.frame_current))

    bpy.context.scene = scene
    bpy.ops.render = NS(render=fake_render)
    return scene, cam


def op_dir():
    d = WORK / "op"
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_op_orbits_the_camera_per_step_without_changing_the_frame(fake_bpy, monkeypatch):
    scene, cam = make_scene(fake_bpy, monkeypatch)
    res = op_render.run({"out": str(op_dir() / "p_####.png"), "force": True, "turntable": 4})
    assert scene.frame_calls == [] and scene.frame_current == 7  # frame_set never called
    assert [Path(p).name for p, _, _ in scene.seen] == ["p_0001.png", "p_0002.png", "p_0003.png", "p_0004.png"]
    assert all(frame == 7 for _, _, frame in scene.seen)
    locs = {tuple(round(r[3], 4) for r in rows[:3]) for _, rows, _ in scene.seen}
    assert len(locs) == 4
    assert res["frames"] == [1, 2, 3, 4] and res["files"] == [p for p, _, _ in scene.seen]
    assert res["turntable"] == {"steps": 4, "centre": [0.0, 0.0, 0.5], "camera": "Cam"} and "video" not in res
    assert cam.location == (0.0, -10.0, 5.0) and cam.rotation_euler == (1.1, 0.0, 0.0)  # restored
    assert "restore_warnings" not in res


def test_op_restores_the_camera_when_the_render_raises(fake_bpy, monkeypatch):
    scene, cam = make_scene(fake_bpy, monkeypatch)
    calls = []

    def boom(write_still=False, **kw):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("render crashed")
        Path(scene.render.filepath).write_bytes(b"png")

    fake_bpy.ops.render = NS(render=boom)
    cam.location = (0.0, -10.0, 5.0)
    with pytest.raises(RuntimeError, match="render crashed"):
        op_render.run({"out": str(op_dir() / "q_####.png"), "force": True, "turntable": 3})
    assert cam.location == (0.0, -10.0, 5.0) and cam.rotation_euler == (1.1, 0.0, 0.0)


def test_op_undoes_the_orbit_after_the_light_and_before_the_fit(fake_bpy, monkeypatch):
    make_scene(fake_bpy, monkeypatch)
    order = []
    monkeypatch.setattr(auto_light.AutoLightState, "undo", lambda self, bpy, scn: order.append("light") or [])
    monkeypatch.setattr(turntable.OrbitState, "undo", lambda self, bpy: order.append("orbit") or [])
    monkeypatch.setattr(camera_fit.FitState, "undo", lambda self, bpy: order.append("fit") or [])
    op_render.run({"out": str(op_dir() / "r_####.png"), "force": True, "turntable": 2})
    assert order == ["light", "orbit", "fit"]


def test_op_without_geometry_fails_before_writing(fake_bpy, monkeypatch):
    make_scene(fake_bpy, monkeypatch, coords=())
    target = op_dir() / "none_####.png"
    from blender_axi.blender_side.ops import OpError

    with pytest.raises(OpError, match="nothing to orbit"):
        op_render.run({"out": str(target), "force": True, "turntable": 3})
    assert not list(op_dir().glob("none_*"))


def test_op_refuses_existing_steps_without_force(fake_bpy, monkeypatch):
    make_scene(fake_bpy, monkeypatch)
    (op_dir() / "ex_0002.png").write_bytes(b"x")
    from blender_axi.blender_side.ops import OpError

    with pytest.raises(OpError) as err:
        op_render.run({"out": str(op_dir() / "ex_####.png"), "turntable": 3})
    assert err.value.code == "WRITE_REFUSED"


def test_op_video_renders_the_stills_into_frames_dir_and_encodes_once(fake_bpy, monkeypatch):
    scene, cam = make_scene(fake_bpy, monkeypatch)
    frames_dir, out = op_dir() / "frames", op_dir() / "clip.mp4"
    encoded = []

    def fake_encode(bpy, stills, target, fps, size):
        encoded.append((list(stills), target, fps, size))
        return {"path": target, "size_bytes": 1234, "frames": len(stills), "codec": "H264"}

    monkeypatch.setattr(video, "encode", fake_encode)
    res = op_render.run({"out": str(out), "force": True, "turntable": 3,
                         "video": {"out": str(out), "fps": 24, "frames_dir": str(frames_dir)}})
    stills = [str(frames_dir / f"turn_{i:04d}.png") for i in (1, 2, 3)]
    assert encoded == [(stills, str(out), 24, (48, 48))]
    assert [p for p, _, _ in scene.seen] == stills and scene.frame_calls == []
    assert res["files"] == [str(out)] and res["video"] == {"codec": "H264", "fps": 24, "size_bytes": 1234}
    assert res["turntable"]["steps"] == 3 and res["format"] == "PNG"
    assert scene.render.image_settings.file_format == "PNG" and cam.location == (0.0, -10.0, 5.0)


def test_op_video_refuses_an_existing_mp4_without_force(fake_bpy, monkeypatch):
    make_scene(fake_bpy, monkeypatch)
    out = op_dir() / "there.mp4"
    out.write_bytes(b"x")
    from blender_axi.blender_side.ops import OpError

    with pytest.raises(OpError) as err:
        op_render.run({"out": str(out), "turntable": 2, "video": {"out": str(out), "fps": 12, "frames_dir": str(op_dir() / "f2")}})
    assert err.value.code == "WRITE_REFUSED"


def test_op_without_turntable_still_sets_the_frame(fake_bpy, monkeypatch):
    scene, _ = make_scene(fake_bpy, monkeypatch)
    res = op_render.run({"out": str(op_dir() / "n_####.png"), "force": True, "frames": [2, 3]})
    assert scene.frame_calls[:2] == [2, 3] and "turntable" not in res
