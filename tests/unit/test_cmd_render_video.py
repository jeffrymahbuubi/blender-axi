"""`render --frames a..b --out x.mp4` and `render --sheet` (0.7.0 plan U2): validation, params, run dir, texts, hints, and
the op with a fake bpy (stills into the run dir, one encode / one compose, scene fps)."""

import sys
import types
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from blender_axi import flags as flagparser
from blender_axi import paths
from blender_axi.blender_side import op_render, sheet, video
from blender_axi.blender_side.ops import OpError
from blender_axi.commands import render as cmd
from blender_axi.errors import AxiError
from blender_axi.runner import RunOutcome

WORK = Path(__file__).resolve().parents[2] / ".tmp" / "tests" / "unit_render_video"
MOVIE = {"codec": "H264", "fps": 24, "size_bytes": 38912}


class FakeTransport:
    name = "fake"

    def __init__(self, fail=None, extra=None):
        self.calls, self.fail, self.extra, self.dir_during_call = [], fail, extra or {}, None

    def run(self, op, params, *, blend=None, save=None, timeout=None):
        self.calls.append((op, params, blend, timeout))
        pack = params.get("video") or params.get("sheet")
        if pack:
            self.dir_during_call = Path(pack["frames_dir"]).is_dir()
        if self.fail:
            raise self.fail
        turn = params.get("turntable")
        frames = list(range(1, turn + 1)) if turn else (params.get("frames") or [1])
        result = {"frames": frames, "size": "48x48", "engine": "WORKBENCH", "samples": None, "samples_ignored": False,
                  "format": "PNG", "lighting": {"unlit": False}, "files": [pack["out"]] if pack else [params["out"]]}
        if turn:
            result["turntable"] = {"steps": turn, "centre": [0.0, 0.0, 0.5], "camera": "Cam"}
        if params.get("video"):
            result["video"] = {**MOVIE, "fps": params["video"]["fps"] or 25}
        if params.get("sheet"):
            cols = params["sheet"]["cols"]
            result["sheet"] = {"cols": cols, "rows": -(-len(frames) // cols), "width": 144, "height": 96,
                               "size_bytes": 8192, "tiles": len(frames)}
        result.update(self.extra)
        return RunOutcome(result=result, modified=False, saved=None, blender_version="5.2.0 LTS",
                          duration_ms=5, stdout="", warnings=[])


@pytest.fixture
def work(monkeypatch):
    WORK.mkdir(parents=True, exist_ok=True)
    (WORK / "scene.blend").write_bytes(b"")
    for stale in [*WORK.glob("*.mp4"), *WORK.glob("*.png")]:
        stale.unlink()
    monkeypatch.chdir(WORK)
    return WORK


def invoke(argv, transport=None, keep_tmp=False, live=False):
    inv, wants_help = flagparser.parse(cmd.SPEC, argv)
    assert not wants_help
    inv.transport = transport or FakeTransport()
    inv.globals.keep_tmp, inv.globals.live = keep_tmp, live
    return cmd.run(inv), inv.transport


def run_dirs(prefix):
    root = paths.tmp_root() / "runs"
    return sorted(root.glob(f"{prefix}-*")) if root.is_dir() else []


def fails(argv, message):
    t = FakeTransport()
    with pytest.raises(AxiError) as err:
        invoke(["scene.blend", *argv], t)
    assert err.value.code == "VALIDATION_ERROR" and message in err.value.message and t.calls == []
    return err.value


# --- mp4 for a frame range -----------------------------------------------------------------------------------

def test_range_mp4_params_run_dir_and_texts(work):
    before = run_dirs("frames")
    res, t = invoke(["scene.blend", "--frames", "1..6", "--out", "c.mp4"])
    params = t.calls[0][1]
    assert params["frames"] == [1, 2, 3, 4, 5, 6] and params["turntable"] is None and params["sheet"] is None
    assert params["video"]["out"] == str(WORK / "c.mp4") and params["video"]["fps"] is None  # None: the op uses the scene fps
    assert params["format"] is None
    frames_dir = Path(params["video"]["frames_dir"])
    assert frames_dir.parent == paths.tmp_root() / "runs" and frames_dir.name.startswith("frames-") and t.dir_during_call is True
    assert run_dirs("frames") == before  # cleaned
    assert res.data["rendered"] == "6 frames -> 1 video" and res.data["output"].endswith("c.mp4")
    assert res.data["video"] == "H264 25 fps, 38 KB" and "turntable" not in res.data
    assert list(res.data)[-2:] == ["video", "duration_ms"]
    assert res.help[0] == "Run `blender-axi render <file> --frames <a..b> --out <clip.mp4>` to render a range"


def test_range_mp4_fps_flag_overrides_the_scene_rate(work):
    res, t = invoke(["scene.blend", "--frames", "1..3", "--out", "c.mp4", "--fps", "12"])
    assert t.calls[0][1]["video"]["fps"] == 12 and res.data["video"].startswith("H264 12 fps")


def test_turntable_mp4_keeps_its_default_of_12_fps(work):
    _, t = invoke(["scene.blend", "--turntable", "4", "--out", "t.mp4"])
    assert t.calls[0][1]["video"]["fps"] == 12
    assert Path(t.calls[0][1]["video"]["frames_dir"]).name.startswith("turntable-")


def test_range_mp4_live_hint_and_run_dir_kept_with_keep_tmp(work):
    res, t = invoke(["--frames", "2..4", "--out", "c.mp4"], live=True, keep_tmp=True)
    assert "<file>" not in res.help[0]
    assert res.help[0].startswith("Run `blender-axi render --frames <a..b> --out <clip.mp4>")
    assert res.help[0].endswith("--live` to render a range")
    kept = Path(t.calls[0][1]["video"]["frames_dir"])
    assert kept.is_dir()
    paths.cleanup(kept, False)


def test_run_dir_is_cleaned_when_the_transport_fails(work):
    before = run_dirs("frames")
    with pytest.raises(AxiError):
        invoke(["scene.blend", "--frames", "1..3", "--out", "c.mp4"], FakeTransport(fail=AxiError("OP_FAILED", "boom")))
    assert run_dirs("frames") == before


def test_existing_range_mp4_is_refused_and_force_allows_it(work):
    (WORK / "c.mp4").write_bytes(b"x")
    t = FakeTransport()
    with pytest.raises(AxiError) as err:
        invoke(["scene.blend", "--frames", "1..3", "--out", "c.mp4"], t)
    assert err.value.code == "WRITE_REFUSED" and t.calls == []
    res, _ = invoke(["scene.blend", "--frames", "1..3", "--out", "c.mp4", "--force"])
    assert res.data["rendered"] == "3 frames -> 1 video"


def test_single_frame_with_mp4_is_exit_2(work):
    err = fails(["--frame", "3", "--out", "x.mp4"], "--out name.mp4 needs --frames <a..b> or --turntable N")
    assert err.help == ["Example: blender-axi render <file> --frames 1..24 --out clip.mp4",
                        "Example: blender-axi render <file> --turntable 12 --fit --out turn.mp4"]
    fails(["--out", "x.mp4"], "--out name.mp4 needs --frames <a..b> or --turntable N")


def test_format_still_does_not_apply_to_range_mp4(work):
    fails(["--frames", "1..3", "--out", "c.mp4", "--format", "PNG"], "--format does not apply to mp4 output")


def test_fps_needs_mp4(work):
    fails(["--frames", "1..3", "--out", "r_####.png", "--fps", "24"], "--fps applies to mp4 output")
    fails(["--frames", "1..3", "--sheet", "--fps", "24"], "--fps applies to mp4 output")


# --- contact sheet ----------------------------------------------------------------------------------------------

def test_turntable_sheet_params_texts_and_hint(work):
    before = run_dirs("turntable")
    res, t = invoke(["scene.blend", "--turntable", "6", "--fit", "--sheet", "--out", "s.png"])
    params = t.calls[0][1]
    assert params["video"] is None and params["turntable"] == 6
    assert params["sheet"]["out"] == str(WORK / "s.png") and params["sheet"]["cols"] == 3
    assert Path(params["sheet"]["frames_dir"]).name.startswith("turntable-") and t.dir_during_call is True
    assert run_dirs("turntable") == before
    assert res.data["rendered"] == "6 frames -> 1 sheet" and res.data["output"].endswith("s.png")
    assert res.data["sheet"] == "3x2 tiles, 144x96, 8 KB" and "video" not in res.data
    assert list(res.data)[-3:] == ["turntable", "sheet", "duration_ms"]
    assert res.help[0] == "Run `blender-axi render <file> --turntable 12 --fit --sheet --out <sheet.png>` for a finer turn"


def test_range_sheet_params_texts_and_hint(work):
    res, t = invoke(["scene.blend", "--frames", "1..3", "--sheet", "--out", "s.png"])
    params = t.calls[0][1]
    assert params["sheet"]["cols"] == 2 and Path(params["sheet"]["frames_dir"]).name.startswith("frames-")
    assert res.data["rendered"] == "3 frames -> 1 sheet" and res.data["sheet"] == "2x2 tiles, 144x96, 8 KB"
    assert res.help[0] == "Run `blender-axi render <file> --frames <a..b> --sheet --out <sheet.png>` to render a range"


@pytest.mark.parametrize("argv, name", [(["--turntable", "4", "--sheet"], "scene_turn_sheet.png"),
                                        (["--frames", "1..4", "--sheet"], "scene_sheet.png")])
def test_default_sheet_names(work, argv, name):
    _, t = invoke(["scene.blend", *argv])
    assert t.calls[0][1]["sheet"]["out"] == str(WORK / name)


def test_sheet_out_without_extension_gets_png(work):
    _, t = invoke(["scene.blend", "--turntable", "4", "--sheet", "--out", "grid"])
    assert t.calls[0][1]["sheet"]["out"] == str(WORK / "grid.png")


def test_sheet_with_a_jpeg_extension_is_allowed(work):
    _, t = invoke(["scene.blend", "--turntable", "4", "--sheet", "--out", "grid.jpg"])
    assert t.calls[0][1]["sheet"]["out"] == str(WORK / "grid.jpg")


def test_sheet_exclusivity_errors(work):
    fails(["--sheet", "--out", "s.png"], "--sheet needs --frames <a..b> or --turntable N")
    fails(["--frame", "3", "--sheet"], "--sheet needs --frames <a..b> or --turntable N")
    fails(["--frame", "3", "--sheet", "--out", "x.mp4"], "--sheet needs --frames <a..b> or --turntable N")
    fails(["--turntable", "4", "--sheet", "--out", "x.mp4"], "--sheet writes one image grid; use --out name.png")
    fails(["--frames", "1..4", "--sheet", "--out", "x.mp4"], "--sheet writes one image grid; use --out name.png")


def test_existing_sheet_is_refused_and_force_allows_it(work):
    (WORK / "s.png").write_bytes(b"x")
    t = FakeTransport()
    with pytest.raises(AxiError) as err:
        invoke(["scene.blend", "--turntable", "4", "--sheet", "--out", "s.png"], t)
    assert err.value.code == "WRITE_REFUSED" and t.calls == []
    res, _ = invoke(["scene.blend", "--turntable", "4", "--sheet", "--out", "s.png", "--force"])
    assert res.data["rendered"] == "4 frames -> 1 sheet"


def test_validation_makes_no_run_dir(work):
    before = run_dirs("frames") + run_dirs("turntable")
    with pytest.raises(AxiError):
        invoke(["scene.blend", "--frame", "2", "--out", "x.mp4"])
    assert run_dirs("frames") + run_dirs("turntable") == before


def test_plain_range_png_is_unchanged(work):
    res, t = invoke(["scene.blend", "--frames", "1..3", "--out", "r_####.png"])
    assert t.calls[0][1]["video"] is None and t.calls[0][1]["sheet"] is None
    assert res.help[0] == "Run `blender-axi render <file> --frames <a..b> --out <dir/name_####.png>` to render a range"
    assert "sheet" not in res.data and "video" not in res.data


def test_no_labels_sets_the_sheet_labels_param(work):
    _, t = invoke(["scene.blend", "--turntable", "4", "--sheet", "--no-labels", "--out", "s.png"])
    assert t.calls[0][1]["sheet"]["labels"] is False
    _, t = invoke(["scene.blend", "--turntable", "4", "--sheet", "--out", "s.png", "--force"])
    assert t.calls[0][1]["sheet"]["labels"] is True
    _, t = invoke(["scene.blend", "--turntable", "4", "--sheet", "--labels", "--out", "s.png", "--force"])
    assert t.calls[0][1]["sheet"]["labels"] is True


def test_no_labels_needs_sheet(work):
    fails(["--frames", "1..3", "--no-labels", "--out", "x_####.png"], "--no-labels applies to --sheet")
    fails(["--turntable", "4", "--no-labels", "--out", "x.mp4"], "--no-labels applies to --sheet")


def test_flags_are_declared():
    from blender_axi import helptext

    flags = {f["flag"].split()[0]: f for f in helptext.render(cmd.SPEC)["flags"]}
    assert "--no-labels" in flags["--labels"]["help"] and "numbers" in flags["--labels"]["help"]
    assert "--sheet" in flags and "scene fps" in flags["--fps"]["help"] and "12" in flags["--fps"]["help"]


# --- op with fake bpy -----------------------------------------------------------------------------------------

class FakeMatrix:
    def __init__(self, rows):
        self.rows = [list(r) for r in rows]

    def __iter__(self):
        return iter(self.rows)


def make_scene(bpy, monkeypatch, fps=24, fps_base=1.0):
    from blender_axi.blender_side import camera_fit

    monkeypatch.setitem(sys.modules, "mathutils", types.SimpleNamespace(Matrix=FakeMatrix))
    monkeypatch.setattr(camera_fit, "collect_coords", lambda dg, scene, objects=None: [-4.0, 0.0, 0.0, 4.0, 0.0, 1.0])
    bpy.data.materials = []
    bpy.context.view_layer = NS(update=lambda: None)
    bpy.context.evaluated_depsgraph_get = lambda: "dg"
    light = NS(name="Sun", type="LIGHT", hide_render=False, visible_get=lambda: True, data=NS(energy=3.0))
    cam = NS(name="Cam", matrix_world=FakeMatrix([[1, 0, 0, 0], [0, 0, -1, -10], [0, 1, 0, 5], [0, 0, 0, 1]]),
             location=(0.0, -10.0, 5.0), rotation_euler=(1.1, 0.0, 0.0), scale=(1.0, 1.0, 1.0))
    render = NS(engine="BLENDER_WORKBENCH", resolution_x=48, resolution_y=48, resolution_percentage=100, filepath="",
                use_file_extension=True, fps=fps, fps_base=fps_base,
                image_settings=NS(file_format="PNG", color_mode="RGBA", color_depth="8", compression=15, quality=90))
    scene = NS(render=render, camera=cam, frame_current=7, frame_subframe=0.0, objects=[light], world=None,
               cycles=NS(samples=8), eevee=NS(taa_render_samples=8))
    scene.frame_calls, scene.seen = [], []
    scene.frame_set = lambda frame, subframe=0.0: (scene.frame_calls.append(frame), setattr(scene, "frame_current", frame))

    def fake_render(write_still=False, **kw):
        Path(render.filepath).parent.mkdir(parents=True, exist_ok=True)
        Path(render.filepath).write_bytes(b"png")
        scene.seen.append((render.filepath, scene.frame_current, render.image_settings.file_format))

    bpy.context.scene = scene
    bpy.ops.render = NS(render=fake_render)
    return scene


def op_dir():
    d = WORK / "op"
    d.mkdir(parents=True, exist_ok=True)
    return d


def stub_encode(monkeypatch):
    encoded = []

    def fake_encode(bpy, stills, target, fps, size):
        encoded.append((list(stills), target, fps, size))
        return {"path": target, "size_bytes": 1234, "frames": len(stills), "codec": "H264"}

    monkeypatch.setattr(video, "encode", fake_encode)
    return encoded


def test_op_range_video_renders_scene_frames_into_frames_dir_and_encodes_once(fake_bpy, monkeypatch):
    scene = make_scene(fake_bpy, monkeypatch, fps=30, fps_base=1.001)  # 29.97 -> 30
    encoded = stub_encode(monkeypatch)
    frames_dir, out = op_dir() / "rframes", op_dir() / "range.mp4"
    res = op_render.run({"out": str(out), "force": True, "frames": [3, 4, 5], "turntable": None,
                         "video": {"out": str(out), "fps": None, "frames_dir": str(frames_dir)}})
    stills = [str(frames_dir / f"frame_{i:04d}.png") for i in (3, 4, 5)]
    assert scene.frame_calls[:3] == [3, 4, 5] and [p for p, _, _ in scene.seen] == stills
    assert encoded == [(stills, str(out), 30, (48, 48))]
    assert res["files"] == [str(out)] and res["frames"] == [3, 4, 5]
    assert res["video"] == {"codec": "H264", "fps": 30, "size_bytes": 1234} and "turntable" not in res and "sheet" not in res
    assert scene.frame_current == 7 and scene.render.image_settings.file_format == "PNG"  # restored


def test_op_range_video_explicit_fps_wins(fake_bpy, monkeypatch):
    make_scene(fake_bpy, monkeypatch, fps=24)
    encoded = stub_encode(monkeypatch)
    out = op_dir() / "fps.mp4"
    res = op_render.run({"out": str(out), "force": True, "frames": [1, 2], "video": {"out": str(out), "fps": 10,
                                                                                    "frames_dir": str(op_dir() / "f10")}})
    assert encoded[0][2] == 10 and res["video"]["fps"] == 10


def test_op_range_stills_are_png_even_for_a_jpeg_sheet(fake_bpy, monkeypatch):
    scene = make_scene(fake_bpy, monkeypatch)
    out, frames_dir = op_dir() / "pack.jpg", op_dir() / "jf"
    composed = []
    monkeypatch.setattr(sheet, "compose", lambda bpy, files, target, cols, labels=None: composed.append((files, target, cols)) or
                        {"path": target, "cols": cols, "rows": 1, "width": 96, "height": 48, "size_bytes": 5, "tiles": len(files)})
    op_render.run({"out": str(out), "force": True, "frames": [1, 2], "sheet": {"out": str(out), "cols": 2, "frames_dir": str(frames_dir)}})
    assert {fmt for _, _, fmt in scene.seen} == {"PNG"} and composed[0][1] == str(out)


def test_op_range_sheet_composes_once_and_reports_one_file(fake_bpy, monkeypatch):
    scene = make_scene(fake_bpy, monkeypatch)
    composed = []

    def fake_compose(bpy, files, target, cols, labels=None):
        composed.append((list(files), target, cols, labels))
        return {"path": target, "cols": cols, "rows": 2, "width": 96, "height": 96, "size_bytes": 777, "tiles": len(files), "labels": 3}

    monkeypatch.setattr(sheet, "compose", fake_compose)
    monkeypatch.setattr(video, "encode", lambda *a, **k: pytest.fail("no video was asked for"))
    frames_dir, out = op_dir() / "sframes", op_dir() / "sheet.png"
    res = op_render.run({"out": str(out), "force": True, "frames": [1, 2, 3], "turntable": None,
                         "sheet": {"out": str(out), "cols": 2, "frames_dir": str(frames_dir)}})
    stills = [str(frames_dir / f"frame_{i:04d}.png") for i in (1, 2, 3)]
    assert composed == [(stills, str(out), 2, ["1", "2", "3"])] and scene.frame_calls[:3] == [1, 2, 3]  # labels = the scene frame numbers
    assert res["files"] == [str(out)]
    assert res["sheet"] == {"cols": 2, "rows": 2, "width": 96, "height": 96, "size_bytes": 777, "tiles": 3}  # `labels` is not surfaced
    assert "video" not in res


def test_op_range_sheet_labels_are_the_scene_frame_numbers_not_positions(fake_bpy, monkeypatch):
    make_scene(fake_bpy, monkeypatch)
    seen = []

    def spy(bpy, files, target, cols, labels=None):
        seen.append(labels)
        return {"path": target, "cols": cols, "rows": 2, "width": 96, "height": 96, "size_bytes": 1, "tiles": len(files)}

    monkeypatch.setattr(sheet, "compose", spy)
    out = op_dir() / "lab.png"
    op_render.run({"out": str(out), "force": True, "frames": [3, 4, 5], "sheet": {"out": str(out), "cols": 2, "frames_dir": str(op_dir() / "lf")}})
    assert seen == [["3", "4", "5"]]


def test_op_turntable_sheet_uses_turn_stills_and_keeps_the_frame(fake_bpy, monkeypatch):
    scene = make_scene(fake_bpy, monkeypatch)
    composed = []
    monkeypatch.setattr(sheet, "compose", lambda bpy, files, target, cols, labels=None: composed.append((list(files), labels)) or
                        {"path": target, "cols": cols, "rows": 2, "width": 96, "height": 96, "size_bytes": 9, "tiles": len(files)})
    frames_dir, out = op_dir() / "tframes", op_dir() / "tsheet.png"
    res = op_render.run({"out": str(out), "force": True, "turntable": 4,
                         "sheet": {"out": str(out), "cols": 2, "frames_dir": str(frames_dir)}})
    assert composed == [([str(frames_dir / f"turn_{i:04d}.png") for i in (1, 2, 3, 4)], ["1", "2", "3", "4"])]  # labels = step numbers
    assert scene.frame_calls == [] and res["files"] == [str(out)] and res["turntable"]["steps"] == 4 and "sheet" in res


def test_op_sheet_refuses_an_existing_out_without_force_before_rendering(fake_bpy, monkeypatch):
    scene = make_scene(fake_bpy, monkeypatch)
    out = op_dir() / "there.png"
    out.write_bytes(b"x")
    with pytest.raises(OpError) as err:
        op_render.run({"out": str(out), "frames": [1, 2], "sheet": {"out": str(out), "cols": 2, "frames_dir": str(op_dir() / "ff")}})
    assert err.value.code == "WRITE_REFUSED" and scene.seen == []


def test_op_compose_failure_still_restores_the_scene(fake_bpy, monkeypatch):
    scene = make_scene(fake_bpy, monkeypatch)

    def boom(bpy, files, target, cols, labels=None):
        raise OpError("OP_FAILED", "contact sheet failed: nope")

    monkeypatch.setattr(sheet, "compose", boom)
    out = op_dir() / "boom.png"
    with pytest.raises(OpError, match="contact sheet failed"):
        op_render.run({"out": str(out), "force": True, "frames": [1, 2], "sheet": {"out": str(out), "cols": 2, "frames_dir": str(op_dir() / "bf")}})
    assert scene.frame_current == 7 and scene.render.use_file_extension is True


def test_op_sheet_without_labels_composes_with_labels_none(fake_bpy, monkeypatch):
    make_scene(fake_bpy, monkeypatch)
    seen = []
    def spy(bpy, files, target, cols, labels="unset"):
        seen.append(labels)
        return {"path": target, "cols": cols, "rows": 1, "width": 96, "height": 48, "size_bytes": 1, "tiles": len(files)}

    monkeypatch.setattr(sheet, "compose", spy)
    out = op_dir() / "nolab.png"  # labels False -> None; True or absent -> the frame numbers
    for key, want in (({"labels": False}, None), ({"labels": True}, ["1", "2"]), ({}, ["1", "2"])):
        op_render.run({"out": str(out), "force": True, "frames": [1, 2],
                       "sheet": {"out": str(out), "cols": 2, "frames_dir": str(op_dir() / "nl"), **key}})
        assert seen.pop() == want
