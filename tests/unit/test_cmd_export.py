"""Unit tests for `export`: flag validation, format inference, write refusal, op param mapping with fake bpy."""

import contextlib
import types
from pathlib import Path

import pytest

from blender_axi import flags as flagparser
from blender_axi.blender_side import op_export
from blender_axi.blender_side.ops import OpError
from blender_axi.commands import export as cmd
from blender_axi.errors import AxiError
from blender_axi.runner import RunOutcome

WORK = Path(__file__).resolve().parents[2] / ".tmp" / "tests" / "unit_export"


class FakeTransport:
    name = "fake"

    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def run(self, op, params, *, blend=None, save=None, timeout=None):
        self.calls.append((op, params, blend, timeout))
        if self.error:
            raise self.error
        result = {"path": params["out"], "format": params["format"], "objects": 2, "size_bytes": 2048}
        return RunOutcome(result=result, modified=False, saved=None, blender_version="5.2.0 LTS",
                          duration_ms=99, stdout="", warnings=[])


@pytest.fixture
def work(monkeypatch):
    WORK.mkdir(parents=True, exist_ok=True)
    for p in WORK.iterdir():
        if p.is_file():
            p.unlink()
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


@pytest.mark.parametrize("name,fmt", [
    ("a.glb", "glb"), ("a.gltf", "gltf"), ("a.FBX", "fbx"), ("a.obj", "obj"), ("a.stl", "stl"), ("a.ply", "ply"),
    ("a.usd", "usd"), ("a.usdc", "usdc"), ("a.usda", "usda"), ("a.abc", "abc"),
])
def test_format_inferred_from_extension(work, name, fmt):
    res, t = invoke(["--out", name])
    assert t.calls[0][1]["format"] == fmt and res.data["format"] == fmt
    assert t.calls[0][1]["out"] == str(work / name)


def test_result_shape_and_hint(work):
    res, t = invoke(["--out", "sub/m.glb"])
    assert list(res.data) == ["file", "exported", "format", "objects", "size_kb", "duration_ms"]
    assert res.data["file"] == "scene.blend" and res.data["exported"] == "sub/m.glb"
    assert res.data["size_kb"] == 2.0 and res.data["objects"] == 2
    assert t.calls[0][0] == "export" and t.calls[0][2] == work / "scene.blend"
    assert res.help and "render <file> --out" in res.help[0]


def test_out_is_required():
    inv_err = None
    try:
        flagparser.parse(cmd.SPEC, ["scene.blend"])
    except AxiError as e:
        inv_err = e
    assert inv_err and inv_err.exit_code == 2 and "--out" in inv_err.message


def test_format_flag_case_insensitive_and_appends_extension(work):
    _, t = invoke(["--out", "model", "--format", "GLB"])
    assert t.calls[0][1]["out"] == str(work / "model.glb") and t.calls[0][1]["format"] == "glb"


@pytest.mark.parametrize("argv,needle", [
    (["--out", "a.xyz"], "cannot infer"),
    (["--out", "noext"], "no extension"),
    (["--out", "a.glb", "--format", "obj"], "does not match"),
    (["--out", "a.usd", "--format", "usda"], "does not match"),
    (["--out", "a.glb", "--format", "dae"], "one of"),
    (["--out", "a.glb", "--collection", "Props", "--objects", "Cube"], "mutually exclusive"),
])
def test_validation_errors_exit_2(work, argv, needle):
    e = fails(argv)
    assert e.exit_code == 2 and e.code == "VALIDATION_ERROR" and needle in e.message


def test_usd_format_accepts_usdc_extension(work):
    _, t = invoke(["--out", "a.usdc", "--format", "usd"])
    assert t.calls[0][1]["format"] == "usd"


def test_collection_and_objects_params(work):
    _, t = invoke(["--out", "a.glb", "--collection", "Props"])
    assert t.calls[0][1]["collection"] == "Props" and t.calls[0][1]["objects"] is None
    _, t = invoke(["--out", "b.glb", "--objects", "Cube,Ball"])
    assert t.calls[0][1]["objects"] == ["Cube", "Ball"] and t.calls[0][1]["collection"] is None


def test_write_refused_without_starting_blender(work):
    (work / "a.glb").write_bytes(b"x")
    t = FakeTransport()
    e = fails(["--out", "a.glb"], t)
    assert e.code == "WRITE_REFUSED" and e.exit_code == 1 and t.calls == []
    assert any("--force" in h and "--out a.glb" in h for h in e.help)


def test_force_passes_through(work):
    (work / "a.glb").write_bytes(b"x")
    _, t = invoke(["--out", "a.glb", "--force"])
    assert t.calls[0][1]["force"] is True


def test_not_found_gets_objects_hint(work):
    e = fails(["--out", "a.glb", "--collection", "Nope"],
              FakeTransport(AxiError("NOT_FOUND", "collection not found: Nope")))
    assert e.code == "NOT_FOUND" and any("objects <file>" in h for h in e.help)


def test_missing_blend_and_live_hints(work):
    assert fails(["nope.blend", "--out", "a.glb"]).code == "NOT_FOUND"
    inv, _ = flagparser.parse(cmd.SPEC, ["--live", "--out", "a.glb"])
    inv.transport = FakeTransport()
    res = cmd.run(inv)
    assert inv.transport.calls[0][2] is None and "--live" in res.help[0]


# --- op with fake bpy ------------------------------------------------------------------------------

class FakeObj:
    def __init__(self, name, type="MESH", selectable=True):
        self.name, self.type, self.selectable, self.selected = name, type, selectable, False

    def select_set(self, state):
        if state and not self.selectable:
            raise RuntimeError("not in view layer")
        self.selected = state

    def select_get(self):
        return self.selected


class Lookup(dict):
    pass


@pytest.fixture
def scene(fake_bpy):
    cube, ball, anchor, cam = FakeObj("Cube"), FakeObj("Ball"), FakeObj("Anchor", "EMPTY"), FakeObj("Camera", "CAMERA")
    objs = [cube, cam, ball, anchor]
    fake_bpy.context.scene = types.SimpleNamespace(objects=objs)
    fake_bpy.data.objects = types.SimpleNamespace(get={o.name: o for o in objs}.get)
    props = types.SimpleNamespace(all_objects=[ball, anchor])
    empty = types.SimpleNamespace(all_objects=[])
    fake_bpy.data.collections = types.SimpleNamespace(get={"Props": props, "Empty": empty}.get)
    calls = []
    selection_at_call = []  # names selected at the moment the exporter ran
    fake_bpy.context.view_layer = types.SimpleNamespace(objects=types.SimpleNamespace(active=None))

    overrides, depth, seen = [], [0], []  # temp_override kwargs, nesting, "was an override active at the exporter"

    @contextlib.contextmanager
    def temp_override(**kw):
        overrides.append(kw)
        depth[0] += 1
        try:
            yield
        finally:
            depth[0] -= 1

    ctx = fake_bpy.context  # a headless MCP server: no window, one window in the window manager
    ctx.window, ctx.window_manager, ctx.temp_override = None, types.SimpleNamespace(windows=["win0"]), temp_override
    fake_bpy.overrides, fake_bpy.override_depth, fake_bpy.override_seen_by_exporter = overrides, depth, seen

    def recorder(name):
        def call(**kw):
            calls.append((name, kw))
            seen.append(depth[0] > 0)
            selection_at_call.append([o.name for o in objs if o.selected])
            Path(kw["filepath"]).write_bytes(b"x" * 1500)
        return call

    fake_bpy.ops.wm = types.SimpleNamespace(
        obj_export=recorder("obj"), stl_export=recorder("stl"), ply_export=recorder("ply"),
        usd_export=recorder("usd"), alembic_export=recorder("abc"))
    fake_bpy.ops.export_scene = types.SimpleNamespace(gltf=recorder("gltf"), fbx=recorder("fbx"))
    fake_bpy.calls = calls
    fake_bpy.selection_at_call = selection_at_call
    fake_bpy.objs = {o.name: o for o in objs}
    return fake_bpy


def out_path(name):
    path = WORK / "op" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    return str(path)


@pytest.mark.parametrize("fmt,call,selected_key", [
    ("obj", "obj", "export_selected_objects"), ("stl", "stl", "export_selected_objects"),
    ("ply", "ply", "export_selected_objects"), ("usd", "usd", "selected_objects_only"),
    ("usda", "usd", "selected_objects_only"), ("abc", "abc", "selected"),
    ("fbx", "fbx", "use_selection"), ("glb", "gltf", "use_selection"), ("gltf", "gltf", "use_selection"),
])
def test_operator_mapping_whole_scene(scene, fmt, call, selected_key):
    path = out_path(f"scene.{fmt}")
    res = op_export.run({"out": path, "format": fmt})
    name, kw = scene.calls[0]
    assert name == call and kw["filepath"] == path and kw[selected_key] is False
    assert res == {"path": path, "format": fmt, "objects": 2, "size_bytes": 1500}
    if fmt == "glb":
        assert kw["export_format"] == "GLB"
    if fmt == "gltf":
        assert kw["export_format"] == "GLTF_SEPARATE"


def test_collection_selects_only_its_objects(scene):
    path = out_path("props.glb")
    res = op_export.run({"out": path, "format": "glb", "collection": "Props"})
    assert scene.calls[0][1]["use_selection"] is True
    assert res["objects"] == 1  # Ball is geometry; Anchor is an empty
    assert scene.selection_at_call == [["Ball", "Anchor"]]  # selected only while exporting
    assert not any(o.selected for o in scene.objs.values())  # and put back afterwards


def test_objects_selection_and_missing(scene):
    path = out_path("two.fbx")
    res = op_export.run({"out": path, "format": "fbx", "objects": ["Cube", "Ball"]})
    assert res["objects"] == 2 and scene.selection_at_call == [["Cube", "Ball"]]
    assert not any(o.selected for o in scene.objs.values())
    with pytest.raises(OpError) as e:
        op_export.run({"out": out_path("x.fbx"), "format": "fbx", "objects": ["Cube", "Ghost", "Spook"]})
    assert e.value.code == "NOT_FOUND" and "Ghost, Spook" in e.value.message


def test_unknown_and_empty_collection(scene):
    with pytest.raises(OpError) as e:
        op_export.run({"out": out_path("a.glb"), "format": "glb", "collection": "Nope"})
    assert e.value.code == "NOT_FOUND" and "Nope" in e.value.message
    with pytest.raises(OpError) as e:
        op_export.run({"out": out_path("b.glb"), "format": "glb", "collection": "Empty"})
    assert e.value.code == "NOT_FOUND" and "no exportable" in e.value.message and scene.calls == []


def test_empty_scene_has_nothing_to_export(scene):
    scene.context.scene.objects = [FakeObj("Camera", "CAMERA")]
    with pytest.raises(OpError) as e:
        op_export.run({"out": out_path("c.glb"), "format": "glb"})
    assert e.value.code == "NOT_FOUND" and "in the scene" in e.value.message


def test_unselectable_objects_are_an_error(scene):
    scene.objs["Cube"].selectable = False
    with pytest.raises(OpError) as e:
        op_export.run({"out": out_path("d.glb"), "format": "glb", "objects": ["Cube"]})
    assert e.value.code == "OP_FAILED" and "selected" in e.value.message


def test_write_refused_and_force(scene):
    path = out_path("e.obj")
    Path(path).write_bytes(b"old")
    with pytest.raises(OpError) as e:
        op_export.run({"out": path, "format": "obj"})
    assert e.value.code == "WRITE_REFUSED"
    op_export.run({"out": path, "format": "obj", "force": True})
    assert len(Path(path).read_bytes()) == 1500


def test_gltf_size_includes_bin_sidecar(scene):
    path = out_path("f.gltf")
    Path(path[:-5] + ".bin").write_bytes(b"y" * 500)
    assert op_export.run({"out": path, "format": "gltf"})["size_bytes"] == 2000


# --- live side-effect safety (U11): the user's selection and active object survive ---------------------

def user_selection(scene):
    return [n for n, o in scene.objs.items() if o.selected], scene.context.view_layer.objects.active


def set_user_selection(scene, names, active):
    for n in names:
        scene.objs[n].selected = True
    scene.context.view_layer.objects.active = scene.objs[active]


def test_selection_and_active_restored_on_success(scene):
    set_user_selection(scene, ["Cube", "Camera"], "Camera")
    before = user_selection(scene)
    op_export.run({"out": out_path("s1.glb"), "format": "glb", "collection": "Props"})
    assert scene.selection_at_call == [["Ball", "Anchor"]]
    assert user_selection(scene) == before and before[1] is scene.objs["Camera"]
    op_export.run({"out": out_path("s2.fbx"), "format": "fbx", "objects": ["Cube", "Ball"]})
    assert scene.selection_at_call[1] == ["Cube", "Ball"] and user_selection(scene) == before


def test_whole_scene_export_keeps_selection(scene):
    set_user_selection(scene, ["Ball"], "Ball")
    before = user_selection(scene)
    op_export.run({"out": out_path("s3.obj"), "format": "obj"})
    assert user_selection(scene) == before


def test_selection_and_active_restored_when_exporter_raises(scene):
    set_user_selection(scene, ["Cube"], "Cube")
    before = user_selection(scene)

    def boom(**kw):
        scene.context.view_layer.objects.active = scene.objs["Ball"]  # exporters may move the active object
        raise RuntimeError("exporter crashed")

    scene.ops.export_scene.gltf = boom
    with pytest.raises(RuntimeError, match="exporter crashed"):
        op_export.run({"out": out_path("s4.glb"), "format": "glb", "objects": ["Ball", "Anchor"]})
    assert user_selection(scene) == before


def test_selection_restored_when_no_file_written_and_when_unselectable(scene):
    set_user_selection(scene, ["Cube"], "Cube")
    before = user_selection(scene)
    scene.ops.wm.obj_export = lambda **kw: None
    with pytest.raises(OpError) as e:
        op_export.run({"out": out_path("s5.obj"), "format": "obj", "collection": "Props"})
    assert e.value.code == "OP_FAILED" and user_selection(scene) == before
    scene.objs["Ball"].selectable = False
    scene.objs["Anchor"].selectable = False
    with pytest.raises(OpError) as e:  # _select already deselected everything when it gives up
        op_export.run({"out": out_path("s6.glb"), "format": "glb", "collection": "Props"})
    assert "selected" in e.value.message and user_selection(scene) == before


def test_active_none_is_restored(scene):
    scene.objs["Cube"].selected = True
    scene.context.view_layer.objects.active = None
    op_export.run({"out": out_path("s7.glb"), "format": "glb", "objects": ["Ball"]})
    assert scene.context.view_layer.objects.active is None and scene.objs["Cube"].selected
    assert not scene.objs["Ball"].selected


# --- context override for the exporter call (headless MCP server: no window, no selection context) ------

def test_headless_server_gets_window_override_for_whole_scene(scene):
    # context.window is None but the window manager has one window (F1): glTF/obj/stl need it
    op_export.run({"out": out_path("w.glb"), "format": "glb"})
    assert scene.overrides == [{"window": "win0"}]
    assert scene.override_seen_by_exporter == [True]
    assert scene.override_depth[0] == 0  # context left again


def test_gui_or_headless_with_a_window_needs_no_override_for_whole_scene(scene):
    scene.context.window = "real window"
    op_export.run({"out": out_path("w2.glb"), "format": "glb"})
    assert scene.overrides == [] and scene.override_seen_by_exporter == [False]


def test_no_window_anywhere_adds_no_window_key(scene):
    scene.context.window_manager = types.SimpleNamespace(windows=[])
    op_export.run({"out": out_path("w3.obj"), "format": "obj"})
    assert scene.overrides == []
    op_export.run({"out": out_path("w4.fbx"), "format": "fbx", "objects": ["Cube"]})
    assert list(scene.overrides[0]) == ["selected_objects", "active_object"]


def test_selective_export_overrides_selection_and_window(scene):
    op_export.run({"out": out_path("w5.fbx"), "format": "fbx", "objects": ["Cube", "Ball"]})
    kw = scene.overrides[0]
    assert kw["window"] == "win0"
    assert [o.name for o in kw["selected_objects"]] == ["Cube", "Ball"] and kw["active_object"].name == "Cube"
    scene.context.window = "real window"
    op_export.run({"out": out_path("w6.glb"), "format": "glb", "collection": "Props"})
    kw = scene.overrides[1]
    assert "window" not in kw and [o.name for o in kw["selected_objects"]] == ["Ball", "Anchor"]


def test_override_is_exited_and_selection_restored_when_the_exporter_raises(scene):
    set_user_selection(scene, ["Cube"], "Cube")
    before = user_selection(scene)

    def boom(**kw):
        raise RuntimeError("exporter crashed")

    scene.ops.export_scene.gltf = boom
    with pytest.raises(RuntimeError, match="exporter crashed"):
        op_export.run({"out": out_path("w7.glb"), "format": "glb", "objects": ["Ball"]})
    assert scene.overrides and scene.override_depth[0] == 0 and user_selection(scene) == before
