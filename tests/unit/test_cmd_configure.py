"""v1.3 U4: `configure` (command level with a fake transport) and `op_configure` (fake `bpy`)."""

import shutil
import types
from pathlib import Path

import pytest

from blender_axi import flags as flagparser
from blender_axi import toon
from blender_axi.blender_side import op_configure
from blender_axi.blender_side.ops import OpError
from blender_axi.commands import configure as cmd
from blender_axi.errors import AxiError
from blender_axi.runner import RunOutcome, SaveSpec
from blender_axi.spec import BatchSpec

WORK = Path(__file__).resolve().parents[2] / ".tmp" / "tests" / "unit_cmd_configure"
LIVE = ["--live", "--host", "127.0.0.1", "--port", "9999"]


class Fake:
    name = "fake"

    def __init__(self, changed=None, samples_ignored=False, engine="CYCLES", error=None, **outcome_kw):
        self.result = {"scene": "Scene", "engine": engine, "samples_ignored": samples_ignored,
                       "changed": [{"field": "engine", "from": "EEVEE", "to": "CYCLES"}] if changed is None else changed}
        self.error, self.calls, self.kw = error, [], outcome_kw

    def run(self, op, params, *, blend=None, save=SaveSpec("none"), timeout=None):
        self.calls.append({"op": op, "params": params, "blend": blend, "save": save, "timeout": timeout})
        if op == "ping":
            return RunOutcome({"file": "C:\\work\\open.blend"}, False, None, "5.2", 1, "")
        if self.error:
            raise self.error
        kw = {"modified": True, "saved": save.path if save.mode != "none" else None, **self.kw}
        return RunOutcome(self.result, kw.pop("modified"), kw.pop("saved"), "5.2.0 LTS", 33, "", **kw)


@pytest.fixture
def work(monkeypatch):
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(parents=True)
    (WORK / "scene.blend").write_bytes(b"BLEND")
    monkeypatch.chdir(WORK)
    return WORK


def invoke(argv, transport=None):
    inv, wants_help = flagparser.parse(cmd.SPEC, argv)
    assert not wants_help
    inv.transport = transport or Fake()
    return cmd.run(inv), inv.transport


def fails(argv, transport=None):
    with pytest.raises(AxiError) as exc:
        invoke(argv, transport)
    return exc.value


# -- spec and validation ----------------------------------------------------------------------------------------------

def test_spec_shape():
    assert cmd.SPEC.name == "configure"
    assert [p.name for p in cmd.SPEC.positionals] == ["file"] and cmd.SPEC.positionals[0].required is False
    assert [f.name for f in cmd.SPEC.flags] == [
        "--engine", "--resolution", "--percent", "--frames", "--fps", "--samples", "--camera", "--world",
        "--world-strength", "--in-place", "--out", "--force"]
    assert "--frame" not in [f.name for f in cmd.SPEC.flags]
    assert cmd.SPEC.flags[0].choices == ("WORKBENCH", "EEVEE", "CYCLES")
    assert any("--live" in e for e in cmd.SPEC.examples)
    assert isinstance(cmd.BATCH, BatchSpec) and cmd.BATCH.op == "configure"


def test_no_setting_flag_is_exit_2(work):
    t = Fake()
    err = fails(["scene.blend", "--in-place"], t)
    assert err.code == "VALIDATION_ERROR" and err.exit_code == 2 and t.calls == []
    assert err.message == "configure needs at least one setting flag" and "--engine" in err.help[0]


@pytest.mark.parametrize("argv, message", [
    (["--resolution", "1920"], "--resolution expects <width>x<height> (1-65536), got '1920'"),
    (["--resolution", "0x100"], "--resolution expects"),
    (["--frames", "5..1"], "--frames expects <a..b> with a <= b, got '5..1'"),
    (["--frames", "1-5"], "--frames expects"),
    (["--fps", "0"], "--fps must be > 0, got 0"),
    (["--percent", "0"], "--percent must be 1-32767, got 0"),
    (["--percent", "40000"], "--percent must be 1-32767"),
    (["--samples", "0"], "--samples must be >= 1, got 0"),
    (["--world", "2,0,0"], "--world values must be between 0 and 1"),
    (["--world", "1,0"], "--world expects x,y,z, got '1,0'"),
    (["--world-strength", "-1"], "--world-strength must be >= 0"),
    (["--camera", " "], "--camera is empty"),
])
def test_validators_exit_2_and_never_contact_blender(work, argv, message):
    t = Fake()
    err = fails(["scene.blend", "--in-place", *argv], t)
    assert err.code == "VALIDATION_ERROR" and err.exit_code == 2 and err.message.startswith(message)
    assert t.calls == []


def test_engine_choice_is_checked_by_the_parser(work):
    with pytest.raises(AxiError) as exc:
        flagparser.parse(cmd.SPEC, ["--engine", "BOGUS"])
    assert exc.value.exit_code == 2


def test_frame_flag_does_not_exist(work):
    with pytest.raises(AxiError) as exc:
        flagparser.parse(cmd.SPEC, ["--frame", "5"])
    assert exc.value.exit_code == 2


def test_write_mode_required_headless(work):
    t = Fake()
    err = fails(["scene.blend", "--engine", "CYCLES"], t)
    assert err.exit_code == 2 and "say where to write it" in err.message and t.calls == []


# -- params and output -------------------------------------------------------------------------------------------------

def test_params_shape(work):
    res, t = invoke(["scene.blend", "--engine", "cycles", "--resolution", "1920X1080", "--percent", "50",
                     "--frames", "1..48", "--fps", "29.97", "--samples", "8", "--camera", "Cam",
                     "--world", "0.1,0.1,0.2", "--world-strength", "0.5", "--in-place"])
    call = t.calls[0]
    assert call["op"] == "configure" and call["save"].mode == "in_place"
    assert call["params"] == {
        "engine": "CYCLES", "resolution": [1920, 1080], "percent": 50, "frame_start": 1, "frame_end": 48,
        "fps": 29.97, "samples": 8, "camera": "Cam", "world_color": [0.1, 0.1, 0.2], "world_strength": 0.5}
    assert res.data["file"] == "scene.blend"


def test_only_given_settings_are_sent(work):
    _, t = invoke(["scene.blend", "--fps", "30", "--in-place"])
    assert {k: v for k, v in t.calls[0]["params"].items() if v is not None} == {"fps": 30.0}


def test_changed_formatting_and_data_order(work):
    changed = [
        {"field": "engine", "from": "EEVEE", "to": "CYCLES"},
        {"field": "resolution", "from": "64x64", "to": "1920x1080"},
        {"field": "frames", "from": "1-24", "to": "1-48"},
        {"field": "fps", "from": 24, "to": 29.97},
        {"field": "camera", "from": "Camera", "to": "Cam2"},
        {"field": "world", "from": "none", "to": "World"},
        {"field": "world_color", "from": [0.051, 0.051, 0.051], "to": [0.1, 0.1, 0.2]},
    ]
    res, _ = invoke(["scene.blend", "--engine", "CYCLES", "--in-place"], Fake(changed=changed))
    d = res.data
    assert list(d)[:3] == ["file", "scene", "changed"]
    assert d["changed"] == ["engine: EEVEE -> CYCLES", "resolution: 64x64 -> 1920x1080", "frames: 1-24 -> 1-48",
                            "fps: 24 -> 29.97", "camera: Camera -> Cam2", "world: none -> World",
                            "world_color: 0.051,0.051,0.051 -> 0.1,0.1,0.2"]
    assert d["modified"] is True and d["saved"].endswith("scene.blend") and "duration_ms" in d
    assert "warning" not in d
    toon.encode({**d, "help": res.help})  # list of strings, encodable
    assert [h.split("`")[1] for h in res.help] == [
        "blender-axi objects scene.blend", "blender-axi scene scene.blend",
        "blender-axi render scene.blend --out <path.png>"]


def test_no_change_text(work):
    res, _ = invoke(["scene.blend", "--engine", "CYCLES", "--in-place"], Fake(changed=[], modified=False, saved=None))
    assert res.data["changed"] == "none (values already as requested)"
    assert res.data["modified"] is False and res.data["saved"] == "none (no changes)"


def test_samples_ignored_becomes_a_warning(work):
    res, _ = invoke(["scene.blend", "--samples", "8", "--in-place"],
                    Fake(changed=[], samples_ignored=True, engine="WORKBENCH"))
    assert res.data["warning"] == "--samples ignored: WORKBENCH has no sample setting"


def test_out_copy_and_hints_use_the_copy(work):
    res, t = invoke(["scene.blend", "--engine", "EEVEE", "--out", "copy.blend"])
    assert t.calls[0]["save"].mode == "copy"
    assert res.help[1].startswith("Run `blender-axi scene ") and "copy.blend" in res.help[1]


def test_live(work):
    res, t = invoke([*LIVE, "--resolution", "32x32"])
    assert [c["op"] for c in t.calls] == ["ping", "configure"] and t.calls[1]["blend"] is None
    assert res.data["saved"] == "none (live session, unsaved)"
    assert all("--live" in h for h in res.help if h.startswith("Run `blender-axi"))
    assert all("<file>" not in h and "scene.blend" not in h for h in res.help)
    assert fails([*LIVE, "scene.blend", "--fps", "30"]).message == "--live uses the file open in Blender; drop <file>"


def test_camera_not_found_help(work):
    err = fails(["scene.blend", "--camera", "Nope", "--in-place"],
                Fake(error=AxiError("NOT_FOUND", "camera 'Nope' not found (the file has 3 objects)")))
    assert err.code == "NOT_FOUND" and err.help == [
        "Run `blender-axi objects scene.blend --type CAMERA` to list cameras"]


def test_missing_file_is_passed_through(work):
    err = fails(["nope.blend", "--fps", "30", "--in-place"])
    assert err.code == "NOT_FOUND" and err.message.startswith("file not found")


def test_batch_params_and_line(work):
    inv, _ = flagparser.parse(cmd.SPEC, ["--engine", "WORKBENCH", "--world", "0,0,1"])
    assert cmd.BATCH.params(inv)["world_color"] == [0.0, 0.0, 1.0]
    assert cmd.BATCH.line({"scene": "Scene", "changed": [{"field": "engine"}, {"field": "world_color"}]}) == \
        "Scene (engine, world_color)"
    assert cmd.BATCH.line({"scene": "Scene", "changed": []}) == "Scene (no change)"
    bad, _ = flagparser.parse(cmd.SPEC, [])
    with pytest.raises(AxiError) as exc:
        cmd.BATCH.params(bad)
    assert exc.value.exit_code == 2


# -- op with a fake bpy --------------------------------------------------------------------------------------------------

class Socket:
    def __init__(self, value):
        self.default_value = value


class World:
    def __init__(self, name="World", color=(0.051, 0.051, 0.051, 1.0), strength=1.0, background=True):
        self.name, self.color = name, (0.05, 0.05, 0.05)
        nodes = [types.SimpleNamespace(type="OUTPUT_WORLD", inputs={})]
        if background:
            nodes.append(types.SimpleNamespace(type="BACKGROUND",
                                               inputs={"Color": Socket(color), "Strength": Socket(strength)}))
        self.node_tree = types.SimpleNamespace(nodes=nodes)


class Scene:
    def __init__(self, world=None, camera=None):
        self.name = "Scene"
        self.render = types.SimpleNamespace(engine="BLENDER_EEVEE", resolution_x=64, resolution_y=64,
                                            resolution_percentage=100, fps=24, fps_base=1.0)
        self.cycles = types.SimpleNamespace(samples=4096)
        self.eevee = types.SimpleNamespace(taa_render_samples=64)
        self.frame_start, self.frame_end, self.frame_current = 1, 24, 1
        self.world, self.camera = world, camera


@pytest.fixture
def op(fake_bpy):
    cam = types.SimpleNamespace(name="Camera", type="CAMERA")
    cam2 = types.SimpleNamespace(name="Cam2", type="CAMERA")
    cube = types.SimpleNamespace(name="Cube", type="MESH")
    scene = Scene(world=World(), camera=cam)
    fake_bpy.context.scene = scene
    fake_bpy.data.objects = [cam, cam2, cube]
    created = []

    def new_world(name):
        world = World(name)
        created.append(world)
        return world

    fake_bpy.data.worlds = types.SimpleNamespace(new=new_world)
    return types.SimpleNamespace(bpy=fake_bpy, scene=scene, cam=cam, cam2=cam2, created=created)


def fields(result):
    return {c["field"]: (c["from"], c["to"]) for c in result["changed"]}


def test_op_records_only_differing_fields(op):
    r = op_configure.run({"engine": "EEVEE", "resolution": [64, 64], "percent": 100, "frame_start": 1,
                          "frame_end": 24, "fps": 24, "camera": "Camera", "samples": None})
    assert r == {"scene": "Scene", "engine": "EEVEE", "changed": [], "samples_ignored": False}


def test_op_full_change(op):
    r = op_configure.run({"engine": "CYCLES", "resolution": [1920, 1080], "percent": 50, "frame_start": 1,
                          "frame_end": 48, "fps": 30, "samples": 8, "camera": "Cam2"})
    assert fields(r) == {
        "engine": ("EEVEE", "CYCLES"), "resolution": ("64x64", "1920x1080"), "percent": (100, 50),
        "frames": ("1-24", "1-48"), "fps": (24, 30), "samples": (4096, 8), "camera": ("Camera", "Cam2")}
    s = op.scene
    assert s.render.engine == "CYCLES" and (s.render.resolution_x, s.render.resolution_y) == (1920, 1080)
    assert s.cycles.samples == 8 and s.camera is op.cam2 and (s.frame_start, s.frame_end) == (1, 48)
    assert s.frame_current == 1
    assert r["engine"] == "CYCLES"


def test_op_engine_type_error_is_op_failed(op):
    class Render(types.SimpleNamespace):
        def __setattr__(self, key, value):
            if key == "engine":
                raise TypeError("enum not found")
            super().__setattr__(key, value)

    op.scene.render = Render(engine="BLENDER_EEVEE", resolution_x=64, resolution_y=64, resolution_percentage=100,
                             fps=24, fps_base=1.0)
    with pytest.raises(OpError) as exc:
        op_configure.run({"engine": "CYCLES"})
    assert exc.value.code == "OP_FAILED" and "engine CYCLES is not available" in exc.value.message


def test_op_samples_follow_the_resulting_engine(op):
    r = op_configure.run({"samples": 32})  # EEVEE
    assert fields(r) == {"samples": (64, 32)} and op.scene.eevee.taa_render_samples == 32
    r = op_configure.run({"engine": "WORKBENCH", "samples": 8})
    assert r["samples_ignored"] is True and fields(r) == {"engine": ("EEVEE", "WORKBENCH")}
    assert r["engine"] == "WORKBENCH"


def test_op_frames_start_then_end(op):
    r = op_configure.run({"frame_start": 30, "frame_end": 48})
    assert fields(r) == {"frames": ("1-24", "30-48")} and (op.scene.frame_start, op.scene.frame_end) == (30, 48)


def test_op_fps_float_sets_fps_base(op):
    r = op_configure.run({"fps": 29.97})
    assert op.scene.render.fps == 30 and abs(op.scene.render.fps_base - 30 / 29.97) < 1e-9
    assert fields(r) == {"fps": (24, 29.97)}
    assert op_configure.run({"fps": 29.97})["changed"] == []  # idempotent
    r = op_configure.run({"fps": 30.0})
    assert op.scene.render.fps_base == 1.0 and fields(r) == {"fps": (29.97, 30)}


def test_op_camera_must_be_a_camera(op):
    with pytest.raises(OpError) as exc:
        op_configure.run({"camera": "Cube"})
    assert exc.value.code == "OP_FAILED" and "'Cube' is a MESH, not a CAMERA" in exc.value.message
    with pytest.raises(OpError) as exc:
        op_configure.run({"camera": "Camra"})
    assert exc.value.code == "NOT_FOUND" and "camera 'Camra' not found" in exc.value.message and "Camera" in exc.value.message


def test_op_bad_camera_changes_nothing(op):
    with pytest.raises(OpError):
        op_configure.run({"engine": "CYCLES", "camera": "Cube"})
    assert op.scene.render.engine == "BLENDER_EEVEE"


def test_op_world_colour_and_strength_on_existing_world(op):
    r = op_configure.run({"world_color": [0.1, 0.1, 0.2], "world_strength": 2})
    node = op.scene.world.node_tree.nodes[1]
    assert tuple(node.inputs["Color"].default_value) == (0.1, 0.1, 0.2, 1.0) and op.scene.world.color == (0.1, 0.1, 0.2)
    assert node.inputs["Strength"].default_value == 2.0
    assert fields(r) == {"world_color": ([0.051, 0.051, 0.051], [0.1, 0.1, 0.2]), "world_strength": (1.0, 2.0)}
    assert op_configure.run({"world_color": [0.1, 0.1, 0.2], "world_strength": 2})["changed"] == []


def test_op_world_created_when_missing(op):
    op.scene.world = None
    r = op_configure.run({"world_color": [0, 0, 1]})
    assert op.scene.world is op.created[0] and op.scene.world.name == "World"
    assert fields(r)["world"] == ("none", "World") and fields(r)["world_color"][1] == [0.0, 0.0, 1.0]


def test_op_world_without_background_node(op):
    op.scene.world = World(background=False)
    with pytest.raises(OpError) as exc:
        op_configure.run({"world_strength": 1, "engine": "CYCLES"})
    assert exc.value.code == "OP_FAILED" and "no Background node" in exc.value.message
    assert op.scene.render.engine == "BLENDER_EEVEE"  # checked before anything changed
