"""Unit tests for `render`: flag validation, .blend inference, write refusal, param mapping, op with fake bpy."""

import os
import types
from pathlib import Path

import pytest

from blender_axi import flags as flagparser
from blender_axi.blender_side import op_render
from blender_axi.blender_side.ops import OpError
from blender_axi.commands import render as cmd
from blender_axi.errors import AxiError
from blender_axi.runner import RunOutcome

WORK = Path(__file__).resolve().parents[2] / ".tmp" / "tests" / "unit_render"


class FakeTransport:
    name = "fake"

    def __init__(self, result=None, error=None):
        self.calls = []
        self.result = result
        self.error = error

    def run(self, op, params, *, blend=None, save=None, timeout=None):
        self.calls.append((op, params, blend, timeout))
        if self.error:
            raise self.error
        result = self.result or {
            "frames": [1], "files": [params["out"]], "size": "64x64", "engine": "WORKBENCH", "samples": None,
            "samples_ignored": False, "format": "PNG"}
        return RunOutcome(result=result, modified=False, saved=None, blender_version="5.2.0 LTS",
                          duration_ms=321, stdout="", warnings=[])


@pytest.fixture
def work(monkeypatch):
    d = WORK
    if d.exists():
        for p in d.iterdir():
            p.unlink() if p.is_file() else None
    d.mkdir(parents=True, exist_ok=True)
    (d / "scene.blend").write_bytes(b"")
    monkeypatch.chdir(d)
    return d


def invoke(argv, transport=None):
    inv, wants_help = flagparser.parse(cmd.SPEC, argv)
    assert not wants_help
    inv.transport = transport or FakeTransport()
    return cmd.run(inv), inv.transport


def fails(argv, transport=None):
    with pytest.raises(AxiError) as exc:
        invoke(argv, transport)
    return exc.value


def test_defaults_infer_single_blend_and_echo_file(work):
    res, t = invoke([])
    op, params, blend, timeout = t.calls[0]
    assert op == "render" and blend == work / "scene.blend" and timeout is None
    assert params["out"] == str(work / "scene_f####.png") and params["frames"] is None
    assert res.data["file"] == "scene.blend"
    assert list(res.data) == ["file", "rendered", "output", "size", "engine", "samples", "duration_ms"]
    assert res.data["samples"] == "n/a" and res.data["duration_ms"] == 321
    assert res.data["rendered"] == "1 frame"


def test_explicit_file_is_not_echoed(work):
    res, t = invoke(["scene.blend", "--out", "a.png"])
    assert "file" not in res.data and res.data["output"] == "a.png"
    assert t.calls[0][1]["out"] == str(work / "a.png")


def test_no_blend_in_cwd_is_exit_2(work):
    (work / "scene.blend").unlink()
    e = fails([])
    assert e.code == "VALIDATION_ERROR" and e.exit_code == 2 and "no .blend" in e.message


def test_two_blends_list_candidates(work):
    (work / "b.blend").write_bytes(b"")
    e = fails([])
    assert e.exit_code == 2 and "b.blend" in e.message and "scene.blend" in e.message
    assert any("b.blend" in h for h in e.help)


def test_missing_file_not_found_and_non_blend(work):
    assert fails(["nope.blend"]).code == "NOT_FOUND"
    (work / "x.txt").write_text("x")
    e = fails(["x.txt"])
    assert e.code == "VALIDATION_ERROR" and e.exit_code == 2


def test_live_without_file_uses_open_scene(work):
    inv, _ = flagparser.parse(cmd.SPEC, ["--live", "--out", "a.png"])
    inv.transport = FakeTransport()
    cmd.run(inv)
    assert inv.transport.calls[0][2] is None


def test_blend_candidates_ignore_backups(work):
    (work / "scene.blend1").write_bytes(b"")
    res, _ = invoke([])
    assert res.data["file"] == "scene.blend"


@pytest.mark.parametrize("argv,needle", [
    (["--engine", "OCTANE"], "one of"),
    (["--resolution", "64"], "<width>x<height>"),
    (["--resolution", "0x10"], "<width>x<height>"),
    (["--frames", "5..2"], "a <= b"),
    (["--frames", "3"], "<a..b>"),
    (["--frame", "1", "--frames", "1..2"], "mutually exclusive"),
    (["--frame", "-3"], ">= 0"),
    (["--percent", "0"], "--percent"),
    (["--samples", "0"], "--samples"),
    (["--out", "a.png", "--format", "JPEG"], "does not match"),
    (["--out", "a.xyz"], "unrecognised"),
    (["--format", "GIF"], "one of"),
    (["--percent", "abc"], "integer"),
])
def test_validation_errors_exit_2(work, argv, needle):
    e = fails(argv)
    assert e.exit_code == 2 and e.code == "VALIDATION_ERROR" and needle in e.message


def test_engine_is_case_insensitive_and_normalised(work):
    _, t = invoke(["--engine", "workbench", "--out", "a.png"])
    assert t.calls[0][1]["engine"] == "WORKBENCH"


def test_resolution_parse_and_param_mapping(work):
    _, t = invoke(["--resolution", "64X32", "--percent", "50", "--samples", "4", "--camera", "Cam",
                   "--frame", "7", "--out", "r.png"])
    params = t.calls[0][1]
    assert params["resolution"] == [64, 32] and params["percent"] == 50 and params["samples"] == 4
    assert params["camera"] == "Cam" and params["frames"] == [7] and t.calls[0][3] is None


def test_global_timeout_forwarded(work):
    inv, _ = flagparser.parse(cmd.SPEC, ["--timeout", "30", "--out", "a.png"])
    inv.transport = FakeTransport()
    cmd.run(inv)
    assert inv.transport.calls[0][3] == 30.0


def test_frames_range_output_pattern(work):
    t = FakeTransport({"frames": [1, 2, 3], "files": ["a", "b", "c"], "size": "64x64", "engine": "CYCLES",
                       "samples": 16, "samples_ignored": False, "format": "PNG"})
    res, _ = invoke(["--frames", "1..3", "--out", "shots/s.png"], t)
    assert t.calls[0][1]["frames"] == [1, 2, 3]
    assert res.data["rendered"] == "3 frames" and res.data["output"] == "shots/s_####.png"
    assert res.data["samples"] == 16


def test_write_refused_before_blender(work):
    (work / "a.png").write_bytes(b"x")
    t = FakeTransport()
    e = fails(["--out", "a.png"], t)
    assert e.code == "WRITE_REFUSED" and e.exit_code == 1 and t.calls == []
    assert any("--force" in h and "--out a.png" in h for h in e.help)


def test_write_refused_for_known_frame_pattern(work):
    (work / "a_0002.png").write_bytes(b"x")
    t = FakeTransport()
    assert fails(["--frames", "1..3", "--out", "a_####.png"], t).code == "WRITE_REFUSED"
    assert t.calls == []


def test_force_skips_precheck(work):
    (work / "a.png").write_bytes(b"x")
    _, t = invoke(["--out", "a.png", "--force"])
    assert t.calls[0][1]["force"] is True


def test_unknown_frame_defers_to_op_which_refuses(work):
    t = FakeTransport(error=AxiError("WRITE_REFUSED", "output exists: " + str(work / "s_f0001.png")))
    e = fails([], t)
    assert e.code == "WRITE_REFUSED" and any("--force" in h for h in e.help)


def test_camera_not_found_gets_hint(work):
    e = fails(["--camera", "Zed", "--out", "a.png"], FakeTransport(error=AxiError("NOT_FOUND", "camera not found: Zed")))
    assert e.code == "NOT_FOUND" and any("--type CAMERA" in h for h in e.help)


def test_samples_ignored_becomes_warning(work):
    t = FakeTransport({"frames": [1], "files": ["x"], "size": "64x64", "engine": "WORKBENCH", "samples": None,
                       "samples_ignored": True, "format": "PNG"})
    res, _ = invoke(["--samples", "4", "--out", "a.png"], t)
    assert "ignored" in res.data["warning"]


def test_help_hints_carry_live_flags(work):
    inv, _ = flagparser.parse(cmd.SPEC, ["--live", "--port", "9999", "--out", "a.png"])
    inv.transport = FakeTransport()
    res = cmd.run(inv)
    assert all("--live --port 9999" in h for h in res.help)
    res2, _ = invoke(["--out", "b.png"])
    assert all("--live" not in h for h in res2.help)


def test_spec_help_renders():
    from blender_axi import helptext

    out = helptext.render(cmd.SPEC)
    shown = " ".join(f["flag"] for f in out["flags"])
    assert all(name in shown for name in ("--frame ", "--frames ", "--out ", "--engine ", "--force"))


# --- op with fake bpy ------------------------------------------------------------------------------

def test_output_paths():
    assert op_render.output_paths("a_####.png", [1, 12]) == ["a_0001.png", "a_0012.png"]
    assert op_render.output_paths("a.png", [3]) == ["a.png"]
    assert op_render.output_paths("a.png", [1, 2]) == ["a_0001.png", "a_0002.png"]
    assert op_render.output_paths("a#.png", [1, 12]) == ["a1.png", "a12.png"]


class FakeImageSettings:
    """Like Blender: choosing JPEG coerces the colour mode to RGB and the depth to 8."""

    def __init__(self):
        self._format, self.color_mode, self.color_depth, self.compression, self.quality = "PNG", "RGBA", "16", 15, 90

    @property
    def file_format(self):
        return self._format

    @file_format.setter
    def file_format(self, value):
        self._format = value
        if value == "JPEG":
            self.color_mode, self.color_depth = "RGB", "8"


def make_scene(bpy, engine="BLENDER_EEVEE", camera=True):
    render = types.SimpleNamespace(engine=engine, resolution_x=1920, resolution_y=1080, resolution_percentage=100,
                                   filepath="/user/own.png", use_file_extension=True, image_settings=FakeImageSettings())
    scene = types.SimpleNamespace(render=render, camera=object() if camera else None, frame_current=5,
                                  frame_subframe=0.0, cycles=types.SimpleNamespace(samples=128),
                                  eevee=types.SimpleNamespace(taa_render_samples=64), frames_set=[])

    def frame_set(frame, subframe=0.0):
        scene.frames_set.append(frame)
        scene.frame_current, scene.frame_subframe = frame, subframe

    scene.frame_set = frame_set
    bpy.context.scene = scene
    rendered = []
    scene.seen = []  # settings as the render sees them

    def fake_render(write_still):
        assert write_still
        Path(render.filepath).write_bytes(b"png")
        rendered.append(render.filepath)
        scene.seen.append(dict(
            engine=render.engine, res=(render.resolution_x, render.resolution_y, render.resolution_percentage),
            fmt=render.image_settings.file_format, ext=render.use_file_extension, camera=scene.camera,
            cycles=scene.cycles.samples, eevee=scene.eevee.taa_render_samples))

    bpy.ops.render = types.SimpleNamespace(render=fake_render)
    return scene, rendered


class FakeObjects(dict):
    pass


def test_op_renders_and_maps_params(fake_bpy):
    scene, rendered = make_scene(fake_bpy)
    WORK.mkdir(parents=True, exist_ok=True)
    out = WORK / "op" / "o_####.jpg"
    for f in (WORK / "op").glob("*") if (WORK / "op").exists() else []:
        f.unlink()
    res = op_render.run({"out": str(out), "frames": [2, 3], "engine": "CYCLES", "resolution": [64, 32],
                         "percent": 50, "samples": 8, "force": False})
    assert scene.seen[0]["engine"] == "CYCLES" and scene.seen[0]["cycles"] == 8
    assert scene.seen[0]["res"] == (64, 32, 50) and scene.seen[0]["fmt"] == "JPEG" and scene.seen[0]["ext"] is False
    assert scene.frames_set == [2, 3, 5]  # 5 = the user's frame, restored
    assert rendered == [str(WORK / "op" / "o_0002.jpg"), str(WORK / "op" / "o_0003.jpg")]
    assert res["size"] == "32x16" and res["engine"] == "CYCLES" and res["samples"] == 8 and res["files"] == rendered


def test_op_defaults_to_current_frame_and_scene_engine(fake_bpy):
    scene, rendered = make_scene(fake_bpy)
    out = WORK / "op2" / "x.png"
    if out.exists():
        out.unlink()
    res = op_render.run({"out": str(out)})
    assert scene.frames_set == [5] and res["engine"] == "EEVEE" and res["samples"] == 64
    assert res["samples_ignored"] is False


def test_op_workbench_ignores_samples(fake_bpy):
    scene, _ = make_scene(fake_bpy, engine="BLENDER_WORKBENCH")
    out = WORK / "op3" / "x.png"
    if out.exists():
        out.unlink()
    res = op_render.run({"out": str(out), "samples": 4})
    assert res["samples"] is None and res["samples_ignored"] is True


def test_op_write_refused_and_force(fake_bpy):
    make_scene(fake_bpy)
    out = WORK / "op4" / "x.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(b"old")
    with pytest.raises(OpError) as e:
        op_render.run({"out": str(out)})
    assert e.value.code == "WRITE_REFUSED"
    op_render.run({"out": str(out), "force": True})
    assert out.read_bytes() == b"png"


def test_op_camera_errors(fake_bpy):
    make_scene(fake_bpy, camera=False)
    out = str(WORK / "op5" / "x.png")
    with pytest.raises(OpError) as e:
        op_render.run({"out": out})
    assert e.value.code == "NOT_FOUND" and "no active camera" in e.value.message
    fake_bpy.data.objects = types.SimpleNamespace(get=lambda n: types.SimpleNamespace(type="MESH") if n == "Cube" else None)
    for name in ("Cube", "Missing"):
        with pytest.raises(OpError) as e:
            op_render.run({"out": out, "camera": name})
        assert e.value.code == "NOT_FOUND" and name in e.value.message


def test_op_selects_named_camera(fake_bpy):
    scene, _ = make_scene(fake_bpy, camera=False)
    cam = types.SimpleNamespace(type="CAMERA")
    fake_bpy.data.objects = types.SimpleNamespace(get=lambda n: cam if n == "Cam2" else None)
    out = WORK / "op6" / "x.png"
    if out.exists():
        out.unlink()
    op_render.run({"out": str(out), "camera": "Cam2"})
    assert scene.seen[0]["camera"] is cam and scene.camera is None  # used for the render, then put back


# --- live side-effect safety (U11): the user's session is left exactly as found ------------------------

def user_state(scene):
    r, im = scene.render, scene.render.image_settings
    return dict(engine=r.engine, res=(r.resolution_x, r.resolution_y, r.resolution_percentage), path=r.filepath,
                ext=r.use_file_extension, camera=scene.camera, frame=scene.frame_current, sub=scene.frame_subframe,
                fmt=im.file_format, mode=im.color_mode, depth=im.color_depth, comp=im.compression,
                quality=im.quality, cycles=scene.cycles.samples, eevee=scene.eevee.taa_render_samples)


ALL_PARAMS = {"engine": "CYCLES", "resolution": [64, 32], "percent": 50, "samples": 8, "camera": "Cam2",
              "frames": [2, 3]}


def with_cam2(bpy):
    cam = types.SimpleNamespace(type="CAMERA")
    bpy.data.objects = types.SimpleNamespace(get=lambda n: cam if n == "Cam2" else None)


def test_op_restores_every_setting_on_success(fake_bpy):
    scene, _ = make_scene(fake_bpy)
    scene.frame_subframe = 0.25
    with_cam2(fake_bpy)
    before = user_state(scene)
    out = WORK / "restore_ok" / "o_####.jpg"
    out.parent.mkdir(parents=True, exist_ok=True)
    op_render.run({"out": str(out), "force": True, **ALL_PARAMS})
    assert scene.seen and scene.seen[0]["fmt"] == "JPEG"  # it really changed things while rendering
    assert user_state(scene) == before
    assert scene.cycles.samples == 128 and scene.eevee.taa_render_samples == 64
    assert scene.render.image_settings.color_mode == "RGBA" and scene.render.image_settings.color_depth == "16"


def test_op_restores_eevee_samples(fake_bpy):
    scene, _ = make_scene(fake_bpy)
    out = WORK / "restore_eevee" / "x.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    op_render.run({"out": str(out), "force": True, "engine": "EEVEE", "samples": 7})
    assert scene.seen[0]["eevee"] == 7 and scene.eevee.taa_render_samples == 64


def test_op_restores_when_the_render_raises(fake_bpy):
    scene, _ = make_scene(fake_bpy)
    with_cam2(fake_bpy)
    before = user_state(scene)

    def boom(write_still):
        raise RuntimeError("render crashed")

    fake_bpy.ops.render = types.SimpleNamespace(render=boom)
    out = WORK / "restore_boom" / "o_####.jpg"
    with pytest.raises(RuntimeError, match="render crashed"):
        op_render.run({"out": str(out), "force": True, **ALL_PARAMS})
    assert user_state(scene) == before


def test_op_restores_when_no_file_is_written(fake_bpy):
    scene, _ = make_scene(fake_bpy)
    before = user_state(scene)
    fake_bpy.ops.render = types.SimpleNamespace(render=lambda write_still: None)
    with pytest.raises(OpError) as e:
        op_render.run({"out": str(WORK / "restore_nofile" / "o.png"), "force": True, "engine": "CYCLES",
                       "resolution": [8, 8], "frames": [9]})
    assert e.value.code == "OP_FAILED" and user_state(scene) == before


def test_op_restores_on_validation_errors_after_assignments(fake_bpy):
    scene, _ = make_scene(fake_bpy)
    before = user_state(scene)
    out = WORK / "restore_val" / "x.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(b"old")
    with pytest.raises(OpError) as e:  # settings are assigned before the output-exists refusal
        op_render.run({"out": str(out), "engine": "CYCLES", "resolution": [8, 8], "percent": 10, "samples": 2})
    assert e.value.code == "WRITE_REFUSED" and user_state(scene) == before
    fake_bpy.data.objects = types.SimpleNamespace(get=lambda n: None)
    with pytest.raises(OpError) as e:  # camera lookup fails after engine/resolution were assigned
        op_render.run({"out": str(out), "engine": "CYCLES", "resolution": [8, 8], "camera": "Ghost"})
    assert e.value.code == "NOT_FOUND" and user_state(scene) == before


def test_op_restore_failure_is_reported_not_raised(fake_bpy):
    scene, _ = make_scene(fake_bpy)

    class Locked(types.SimpleNamespace):
        def __setattr__(self, name, value):
            if name == "resolution_x" and getattr(self, "_locked", False):
                raise RuntimeError("read-only now")
            super().__setattr__(name, value)

    scene.render = Locked(**vars(scene.render))
    out = WORK / "restore_warn" / "x.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    fake_bpy.ops.render = types.SimpleNamespace(render=lambda write_still: (
        Path(scene.render.filepath).write_bytes(b"png"), setattr(scene.render, "_locked", True)))
    res = op_render.run({"out": str(out), "force": True, "resolution": [8, 8]})
    assert res["restore_warnings"] and "resolution_x" in res["restore_warnings"][0]
