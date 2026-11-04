"""`render --columns` and `--transparent` (0.10.0 plan U2): validation, params, result text, and the op with a fake bpy
(film transparent + RGBA set and restored, the turntable fit gets the swept corners)."""

import sys
import types
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from blender_axi import flags as flagparser
from blender_axi import helptext
from blender_axi.blender_side import camera_fit, op_render, sheet, turntable
from blender_axi.commands import render as cmd
from blender_axi.errors import AxiError
from blender_axi.runner import RunOutcome

WORK = Path(__file__).resolve().parents[2] / ".tmp" / "tests" / "unit_render_sheet"


class FakeTransport:
    name = "fake"

    def __init__(self, extra=None):
        self.calls, self.extra = [], extra or {}

    def run(self, op, params, *, blend=None, save=None, timeout=None):
        self.calls.append((op, params, blend, timeout))
        turn = params.get("turntable")
        frames = list(range(1, turn + 1)) if turn else (params.get("frames") or [1])
        pack = params.get("sheet")
        result = {"frames": frames, "size": "48x48", "engine": "WORKBENCH", "samples": None, "samples_ignored": False,
                  "format": "PNG", "lighting": {"unlit": False}, "files": [pack["out"]] if pack else [params["out"]]}
        if turn:
            result["turntable"] = {"steps": turn, "centre": [0.0, 0.0, 0.5], "camera": "Cam"}
        if pack:
            cols = pack["cols"]
            result["sheet"] = {"cols": cols, "rows": -(-len(frames) // cols), "width": 144, "height": 96,
                               "size_bytes": 8192, "tiles": len(frames)}
        if params.get("transparent"):
            result["transparent"] = True
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


def invoke(argv, transport=None):
    inv, wants_help = flagparser.parse(cmd.SPEC, argv)
    assert not wants_help
    inv.transport = transport or FakeTransport()
    inv.globals.keep_tmp, inv.globals.live = False, False
    return cmd.run(inv), inv.transport


def fails(argv, message):
    t = FakeTransport()
    with pytest.raises(AxiError) as err:
        invoke(["scene.blend", *argv], t)
    assert err.value.code == "VALIDATION_ERROR" and message in err.value.message and t.calls == []


# --- --columns ------------------------------------------------------------------------------------------------

def test_columns_sets_the_sheet_columns(work):
    _, t = invoke(["scene.blend", "--turntable", "8", "--sheet", "--columns", "4", "--out", "s.png"])
    assert t.calls[0][1]["sheet"]["cols"] == 4
    _, t = invoke(["scene.blend", "--turntable", "8", "--sheet", "--columns", "3", "--out", "s.png"])
    assert t.calls[0][1]["sheet"]["cols"] == 3


def test_default_columns_follow_the_new_grid_rule(work):
    _, t = invoke(["scene.blend", "--turntable", "8", "--sheet", "--out", "s.png"])
    assert t.calls[0][1]["sheet"]["cols"] == 4  # 8 tiles -> 4x2, no empty tile
    _, t = invoke(["scene.blend", "--turntable", "9", "--sheet", "--out", "s.png"])
    assert t.calls[0][1]["sheet"]["cols"] == 3


def test_columns_needs_sheet_and_at_least_one(work):
    fails(["--turntable", "4", "--columns", "2", "--out", "x.mp4"], "--columns applies to --sheet")
    fails(["--frames", "1..3", "--columns", "2", "--out", "x_####.png"], "--columns applies to --sheet")
    fails(["--turntable", "4", "--sheet", "--columns", "0", "--out", "x.png"], "--columns must be >= 1")


# --- --transparent --------------------------------------------------------------------------------------------

def test_transparent_reaches_the_params_and_prints_the_background(work):
    res, t = invoke(["scene.blend", "--transparent", "--out", "t.png"])
    assert t.calls[0][1]["transparent"] is True
    keys = list(res.data)
    assert res.data["background"] == "transparent" and keys.index("background") == keys.index("engine") + 1


def test_without_transparent_nothing_changes(work):
    res, t = invoke(["scene.blend", "--out", "t.png"])
    assert t.calls[0][1]["transparent"] is False and "background" not in res.data


@pytest.mark.parametrize("name", ["t.png", "t.webp", "t.exr", "t.tif", "t.bmp", "t.tga"])
def test_transparent_accepts_formats_with_alpha(work, name):
    _, t = invoke(["scene.blend", "--transparent", "--out", name, "--force"])
    assert t.calls[0][1]["transparent"] is True


def test_transparent_works_with_a_sheet(work):
    res, t = invoke(["scene.blend", "--turntable", "4", "--sheet", "--transparent", "--out", "s.png"])
    assert t.calls[0][1]["transparent"] is True and res.data["background"] == "transparent"


def test_transparent_refuses_jpeg_and_mp4(work):
    msg = "--transparent needs a format with alpha (png, webp, exr, tif, bmp, tga)"
    fails(["--transparent", "--out", "x.jpg"], msg)
    fails(["--transparent", "--out", "x.jpeg"], msg)
    fails(["--transparent", "--format", "JPEG", "--out", "x"], msg)
    fails(["--transparent", "--turntable", "4", "--sheet", "--out", "x.jpg"], msg)
    fails(["--transparent", "--turntable", "4", "--out", "x.mp4"], "--transparent does not apply to mp4 output")


def test_flags_are_declared():
    flags = {f["flag"].split()[0]: f for f in helptext.render(cmd.SPEC)["flags"]}
    assert "empty tiles" in flags["--columns"]["help"] and "--transparent" in flags
    assert "alpha" in flags["--transparent"]["help"] or "RGBA" in flags["--transparent"]["help"]


# --- op with a fake bpy ---------------------------------------------------------------------------------------

class FakeMatrix:
    def __init__(self, rows):
        self.rows = [list(r) for r in rows]

    def __iter__(self):
        return iter(self.rows)


def make_scene(bpy, monkeypatch, transparent=False, color_mode="RGB"):
    monkeypatch.setitem(sys.modules, "mathutils", types.SimpleNamespace(Matrix=FakeMatrix))
    monkeypatch.setattr(camera_fit, "collect_coords", lambda dg, scene, objects=None: [-4.0, 0.0, 0.0, 4.0, 0.0, 1.0])
    bpy.data.materials = []
    bpy.context.view_layer = NS(update=lambda: None)
    bpy.context.evaluated_depsgraph_get = lambda: "dg"
    light = NS(name="Sun", type="LIGHT", hide_render=False, visible_get=lambda: True, data=NS(energy=3.0))
    cam = NS(name="Cam", matrix_world=FakeMatrix([[1, 0, 0, 0], [0, 0, -1, -10], [0, 1, 0, 5], [0, 0, 0, 1]]),
             location=(0.0, -10.0, 5.0), rotation_euler=(1.1, 0.0, 0.0), scale=(1.0, 1.0, 1.0))
    render = NS(engine="BLENDER_WORKBENCH", resolution_x=48, resolution_y=48, resolution_percentage=100, filepath="",
                use_file_extension=True, film_transparent=transparent,
                image_settings=NS(file_format="PNG", color_mode=color_mode, color_depth="8", compression=15, quality=90))
    scene = NS(render=render, camera=cam, frame_current=7, frame_subframe=0.0, objects=[light], world=None,
               cycles=NS(samples=8), eevee=NS(taa_render_samples=8))
    scene.frame_set = lambda frame, subframe=0.0: setattr(scene, "frame_current", frame)
    scene.seen = []

    def fake_render(write_still=False, **kw):
        Path(render.filepath).parent.mkdir(parents=True, exist_ok=True)
        Path(render.filepath).write_bytes(b"png")
        scene.seen.append((render.film_transparent, render.image_settings.color_mode))

    bpy.context.scene = scene
    bpy.ops.render = NS(render=fake_render)
    return scene


def op_dir():
    WORK.mkdir(parents=True, exist_ok=True)
    return WORK


def test_op_transparent_sets_film_and_rgba_for_the_render_and_restores_both(fake_bpy, monkeypatch):
    scene = make_scene(fake_bpy, monkeypatch)
    res = op_render.run({"out": str(op_dir() / "op_t.png"), "force": True, "transparent": True})
    assert scene.seen == [(True, "RGBA")]
    assert scene.render.film_transparent is False and scene.render.image_settings.color_mode == "RGB"  # restored
    assert res["transparent"] is True and "restore_warnings" not in res


def test_op_transparent_restores_a_scene_that_was_already_transparent(fake_bpy, monkeypatch):
    scene = make_scene(fake_bpy, monkeypatch, transparent=True, color_mode="RGBA")
    op_render.run({"out": str(op_dir() / "op_t2.png"), "force": True, "transparent": True})
    assert scene.render.film_transparent is True and scene.render.image_settings.color_mode == "RGBA"


def test_op_transparent_is_restored_when_the_render_fails(fake_bpy, monkeypatch):
    scene = make_scene(fake_bpy, monkeypatch)
    scene_render = fake_bpy.ops.render.render

    def boom(**kw):
        scene_render(**kw)
        raise RuntimeError("render failed")

    fake_bpy.ops.render = NS(render=boom)
    with pytest.raises(RuntimeError):
        op_render.run({"out": str(op_dir() / "op_t3.png"), "force": True, "transparent": True})
    assert scene.render.film_transparent is False and scene.render.image_settings.color_mode == "RGB"


def test_op_without_transparent_leaves_both_alone(fake_bpy, monkeypatch):
    scene = make_scene(fake_bpy, monkeypatch)
    res = op_render.run({"out": str(op_dir() / "op_n.png"), "force": True})
    assert scene.seen == [(False, "RGB")] and "transparent" not in res


def test_op_transparent_sheet_stills_are_rgba_pngs(fake_bpy, monkeypatch):
    scene = make_scene(fake_bpy, monkeypatch)
    monkeypatch.setattr(sheet, "compose", lambda bpy, files, target, cols, labels=None: {
        "path": target, "cols": cols, "rows": 1, "width": 96, "height": 48, "size_bytes": 5, "tiles": len(files)})
    out = op_dir() / "op_sheet.png"
    op_render.run({"out": str(out), "force": True, "frames": [1, 2], "transparent": True,
                   "sheet": {"out": str(out), "cols": 2, "frames_dir": str(op_dir() / "op_sf")}})
    assert scene.seen == [(True, "RGBA")] * 2


def test_op_turntable_fit_gets_the_swept_corners_and_plan_the_plain_ones(fake_bpy, monkeypatch):
    make_scene(fake_bpy, monkeypatch)
    corners = [-4.0, 0.0, 0.0, 4.0, 0.0, 1.0]
    seen = {}

    def fake_fit(bpy, scene, margin, temp_ok, state, coords=None):
        seen["fit"] = coords
        return {"camera": "Cam", "temporary": False, "location": [0.0, -12.0, 5.0], "from": [0.0, -10.0, 5.0], "ortho_scale": None}

    real_plan = turntable.plan
    monkeypatch.setattr(camera_fit, "fit_scene", fake_fit)
    monkeypatch.setattr(turntable, "plan", lambda bpy, scene, n, state, coords=None: seen.update(plan=coords) or
                        real_plan(bpy, scene, n, state, coords=coords))
    monkeypatch.setattr(op_render, "_choose_subject", lambda bpy, scene, params, turning, state: (None, list(corners), None))
    op_render.run({"out": str(op_dir() / "op_w_####.png"), "force": True, "turntable": 4, "fit": True})
    assert seen["plan"] == corners
    assert len(seen["fit"]) == 4 * len(corners) and seen["fit"][:len(corners)] == corners
    assert seen["fit"] == turntable.sweep(corners, turntable.centre(corners), 4)


def test_op_plain_fit_without_turntable_keeps_the_unswept_corners(fake_bpy, monkeypatch):
    make_scene(fake_bpy, monkeypatch)
    corners = [-4.0, 0.0, 0.0, 4.0, 0.0, 1.0]
    seen = {}

    def fake_fit(bpy, scene, margin, temp_ok, state, coords=None):
        seen["fit"] = coords
        return {"camera": "Cam", "temporary": False, "location": [0.0, -12.0, 5.0], "from": [0.0, -10.0, 5.0], "ortho_scale": None}

    monkeypatch.setattr(camera_fit, "fit_scene", fake_fit)
    monkeypatch.setattr(op_render, "_choose_subject", lambda bpy, scene, params, turning, state: (None, list(corners), None))
    op_render.run({"out": str(op_dir() / "op_p.png"), "force": True, "fit": True, "subject": ["X"]})
    assert seen["fit"] == corners
