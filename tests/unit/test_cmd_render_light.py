"""`render` unlit warning and `--auto-light` (0.6.0 plan U2): CLI params, texts, hints; op order with fake bpy."""

from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from blender_axi import flags as flagparser
from blender_axi.blender_side import auto_light, camera_fit, op_render
from blender_axi.commands import render as cmd
from blender_axi.runner import RunOutcome

WORK = Path(__file__).resolve().parents[2] / ".tmp" / "tests" / "unit_render_light"
UNLIT = {"engine": "BLENDER_EEVEE", "needs_light": True, "lights": [], "world": "none", "emissive": [], "unlit": True}
LIT = {**UNLIT, "lights": ["Sun"], "unlit": False}
UNLIT_WARNING = "unlit scene: no light, no world background and no emissive material; EEVEE renders black"
FIT = {"camera": "Cam", "temporary": False, "location": [0.0, -17.2, 8.3], "from": [0.0, -8.0, 4.0], "ortho_scale": None}


class FakeTransport:
    name = "fake"

    def __init__(self, lighting=None, auto=None, fit=None, engine="EEVEE"):
        self.calls, self.lighting, self.auto, self.fit, self.engine = [], lighting, auto, fit, engine

    def run(self, op, params, *, blend=None, save=None, timeout=None):
        self.calls.append((op, params, blend, timeout))
        result = {"frames": [1], "files": [params["out"]], "size": "64x64", "engine": self.engine, "samples": 64,
                  "samples_ignored": False, "format": "PNG"}
        if self.lighting is not None:
            result["lighting"] = self.lighting
        if self.auto is not None:
            result["auto_light"] = self.auto
        if self.fit is not None:
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


APPLIED = {"applied": True, "sun": "AxiSun", "fill": "world created"}
KEPT = {"applied": True, "sun": "AxiSun", "fill": "none (world kept)"}
SKIPPED = {"applied": False, "sun": None, "fill": None}


# --- CLI ---------------------------------------------------------------------------------------------

def test_flag_is_declared_with_help():
    from blender_axi import helptext

    flag = next(f for f in helptext.render(cmd.SPEC)["flags"] if f["flag"].startswith("--auto-light"))
    assert "temporary Sun" in flag["help"]


def test_params_carry_auto_light(work):
    _, t = invoke(["--out", "a.png"])
    assert t.calls[0][1]["auto_light"] is False
    _, t = invoke(["--auto-light", "--out", "a.png"])
    assert t.calls[0][1]["auto_light"] is True


def test_unlit_scene_warns_and_hints_headless(work):
    res, _ = invoke(["scene.blend", "--out", "a.png"], FakeTransport(lighting=UNLIT))
    assert res.data["warning"] == UNLIT_WARNING and "auto_light" not in res.data
    assert list(res.data) == ["rendered", "output", "size", "engine", "samples", "duration_ms", "warning"]
    assert res.help[0] == ("Rerun with --auto-light for a lit preview, or run "
                           "`blender-axi add light scene.blend --type SUN --in-place` to light the file")
    assert len(res.help) == 3  # + the range and export hints, which stay


def test_unlit_hint_live_form(work):
    res, _ = invoke(["--live", "--out", "a.png"], FakeTransport(lighting=UNLIT))
    assert res.help[0] == ("Rerun with --auto-light for a lit preview, or run "
                           "`blender-axi add light --type SUN --live` to light the file")


def test_unlit_hint_goes_after_the_keep_framing_hint(work):
    res, _ = invoke(["scene.blend", "--fit", "--out", "a.png"], FakeTransport(lighting=UNLIT, fit=FIT))
    assert res.help[0].startswith("Run `blender-axi set Cam") and res.help[1].startswith("Rerun with --auto-light")
    assert list(res.data)[-3:] == ["fit", "duration_ms", "warning"]


def test_lit_scene_has_no_warning_and_no_extra_hint(work):
    res, _ = invoke(["scene.blend", "--out", "a.png"], FakeTransport(lighting=LIT))
    assert "warning" not in res.data and len(res.help) == 2


def test_missing_lighting_in_older_results_is_tolerated(work):
    res, _ = invoke(["scene.blend", "--out", "a.png"], FakeTransport())
    assert "warning" not in res.data and len(res.help) == 2


def test_auto_light_text_world_created_has_no_warning_or_hint(work):
    res, _ = invoke(["scene.blend", "--auto-light", "--out", "a.png"], FakeTransport(lighting=UNLIT, auto=APPLIED))
    assert res.data["auto_light"] == "temporary Sun + world fill (removed after)"
    assert "warning" not in res.data and len(res.help) == 2
    assert list(res.data) == ["rendered", "output", "size", "engine", "samples", "auto_light", "duration_ms"]


def test_auto_light_text_world_kept(work):
    res, _ = invoke(["scene.blend", "--auto-light", "--out", "a.png"], FakeTransport(lighting=UNLIT, auto=KEPT))
    assert res.data["auto_light"] == "temporary Sun (removed after; the file's world is black)"
    assert "warning" not in res.data


def test_auto_light_not_needed_on_a_lit_scene(work):
    res, _ = invoke(["scene.blend", "--auto-light", "--out", "a.png"], FakeTransport(lighting=LIT, auto=SKIPPED))
    assert res.data["auto_light"] == "not needed (scene is lit)" and "warning" not in res.data


def test_auto_light_follows_fit_in_the_data_order(work):
    t = FakeTransport(lighting=UNLIT, auto=APPLIED, fit=FIT)
    res, _ = invoke(["scene.blend", "--fit", "--auto-light", "--out", "a.png"], t)
    assert list(res.data) == ["rendered", "output", "size", "engine", "samples", "fit", "auto_light", "duration_ms"]


def test_auto_light_key_absent_without_the_flag(work):
    res, _ = invoke(["scene.blend", "--out", "a.png"], FakeTransport(lighting=LIT))
    assert "auto_light" not in res.data


# --- op with fake bpy --------------------------------------------------------------------------------

class Registry:
    def __init__(self, factory):
        self.factory, self.removed = factory, []

    def new(self, *args):
        return self.factory(*args)

    def remove(self, item):
        self.removed.append(item)


class Linked(list):
    def link(self, obj):
        self.append(obj)


def sun_light(energy=3.0):
    return NS(name="Sun", type="LIGHT", hide_render=False, visible_get=lambda: True, data=NS(energy=energy))


def make_scene(bpy, engine="BLENDER_EEVEE", lights=(), world=None, camera=True, emissive=False):
    bg = NS(type="BACKGROUND", inputs={"Color": NS(default_value=(0, 0, 0, 1)), "Strength": NS(default_value=1.0)})
    bpy.data.lights = Registry(lambda name, kind: NS(name=name, type=kind, energy=1.0))
    bpy.data.objects = Registry(lambda name, d: NS(name=name, data=d, rotation_euler=(0, 0, 0)))
    bpy.data.worlds = Registry(lambda name: NS(name=name, use_nodes=False, node_tree=NS(nodes=[bg])))
    bpy.data.materials = []
    bpy.context.view_layer = NS(update=lambda: None)
    render = NS(engine=engine, resolution_x=64, resolution_y=64, resolution_percentage=100, filepath="",
                use_file_extension=True,
                image_settings=NS(file_format="PNG", color_mode="RGBA", color_depth="8", compression=15, quality=90))
    scene = NS(render=render, camera=object() if camera else None, frame_current=1, frame_subframe=0.0,
               cycles=NS(samples=8), eevee=NS(taa_render_samples=8), objects=list(lights), world=world,
               collection=NS(objects=Linked()))
    scene.frame_set = lambda frame, subframe=0.0: setattr(scene, "frame_current", frame)
    scene.seen = []

    def fake_render(write_still):
        Path(render.filepath).write_bytes(b"png")
        scene.seen.append({"world": scene.world, "suns": list(scene.collection.objects)})

    bpy.context.scene = scene
    bpy.ops.render = NS(render=fake_render)
    return scene


def out_path(name):
    d = WORK / "op"
    d.mkdir(parents=True, exist_ok=True)
    return str(d / name)


def test_op_reports_lighting_without_touching_anything(fake_bpy):
    scene = make_scene(fake_bpy)
    res = op_render.run({"out": out_path("a.png"), "force": True})
    assert res["lighting"]["unlit"] is True and "auto_light" not in res
    assert scene.collection.objects == [] and scene.seen[0]["world"] is None


def test_op_auto_light_lights_the_render_then_cleans_up(fake_bpy):
    scene = make_scene(fake_bpy)
    res = op_render.run({"out": out_path("b.png"), "force": True, "auto_light": True})
    assert res["lighting"]["unlit"] is True  # the state found, not the lit one
    assert res["auto_light"] == {"applied": True, "sun": "AxiSun", "fill": "world created"}
    seen = scene.seen[0]
    assert [o.name for o in seen["suns"]] == ["AxiSun"] and seen["world"].name == "AxiFill"
    assert scene.world is None  # restored (also None)
    assert len(fake_bpy.data.objects.removed) == len(fake_bpy.data.lights.removed) == len(fake_bpy.data.worlds.removed) == 1
    assert "restore_warnings" not in res


def test_op_auto_light_keeps_an_existing_black_world(fake_bpy):
    black = NS(name="World", use_nodes=True, node_tree=NS(nodes=[NS(
        type="BACKGROUND", inputs={"Color": NS(default_value=(0, 0, 0, 1)), "Strength": NS(default_value=1.0)})]))
    scene = make_scene(fake_bpy, world=black)
    res = op_render.run({"out": out_path("c.png"), "force": True, "auto_light": True})
    assert res["lighting"]["world"] == "black" and res["auto_light"]["fill"] == "none (world kept)"
    assert scene.world is black and fake_bpy.data.worlds.removed == []


@pytest.mark.parametrize("kwargs", [{"lights": [sun_light()]}, {"engine": "BLENDER_WORKBENCH"}])
def test_op_auto_light_is_a_noop_when_lit(fake_bpy, kwargs):
    scene = make_scene(fake_bpy, **kwargs)
    res = op_render.run({"out": out_path("d.png"), "force": True, "auto_light": True})
    assert res["lighting"]["unlit"] is False
    assert res["auto_light"] == {"applied": False, "sun": None, "fill": None}
    assert scene.collection.objects == [] and fake_bpy.data.objects.removed == []


def test_op_cleans_up_when_the_render_raises(fake_bpy):
    scene = make_scene(fake_bpy)

    def boom(write_still):
        raise RuntimeError("render crashed")

    fake_bpy.ops.render = NS(render=boom)
    with pytest.raises(RuntimeError, match="render crashed"):
        op_render.run({"out": out_path("e.png"), "force": True, "auto_light": True})
    assert scene.world is None and len(fake_bpy.data.objects.removed) == 1 and len(fake_bpy.data.worlds.removed) == 1


def test_op_assesses_after_the_fit_and_undoes_the_light_before_the_fit(fake_bpy, monkeypatch):
    scene = make_scene(fake_bpy, camera=False)
    temp_cam, order = object(), []

    def fake_fit(bpy, scn, margin, ok, state):
        scn.camera = temp_cam
        return {"camera": "AxiFit", "temporary": True, "location": [0, 0, 0], "from": None, "ortho_scale": None}

    real_assess = op_render.lighting.assess

    def spy_assess(bpy, scn, engine=None):
        order.append(("assess", scn.camera))
        return real_assess(bpy, scn, engine)

    monkeypatch.setattr(camera_fit, "fit_scene", fake_fit)
    monkeypatch.setattr(op_render.lighting, "assess", spy_assess)
    monkeypatch.setattr(auto_light.AutoLightState, "undo", lambda self, bpy, scn: order.append("light undo") or [])
    monkeypatch.setattr(camera_fit.FitState, "undo", lambda self, bpy: order.append("fit undo") or [])
    op_render.run({"out": out_path("f.png"), "force": True, "fit": True, "temp_camera_ok": True, "auto_light": True})
    assert order[0] == ("assess", temp_cam) and order[1:] == ["light undo", "fit undo"]
    assert scene.camera is None  # restored by the snapshot


def test_op_reports_an_undo_failure_instead_of_raising(fake_bpy, monkeypatch):
    make_scene(fake_bpy)
    monkeypatch.setattr(auto_light.AutoLightState, "undo", lambda self, bpy, scn: ["auto-light world: nope"])
    res = op_render.run({"out": out_path("g.png"), "force": True, "auto_light": True})
    assert res["restore_warnings"] == ["auto-light world: nope"]
