"""0.5.0 U1: `export` set (exclude / hidden), modifier switch, --dry-run, repeatable --object. Fake transport + fake bpy."""

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

WORK = Path(__file__).resolve().parents[2] / ".tmp" / "tests" / "unit_export_set"


class FakeTransport:
    name = "fake"

    def __init__(self, **result):
        self.calls = []
        self.result = result

    def run(self, op, params, *, blend=None, save=None, timeout=None):
        self.calls.append((op, params, blend, timeout))
        if params["dry_run"]:
            res = {"format": params["format"], "dry_run": True, "objects": 2,
                   "export_set": [{"name": "Ring", "type": "MESH"}, {"name": "Floor", "type": "MESH"}],
                   "skipped_hidden": [], "excluded": [], "non_geometry": [], "modifiers": "applied",
                   "modifiers_note": None}
        else:
            res = {"path": params["out"], "format": params["format"], "objects": 2, "size_bytes": 2048,
                   "skipped_hidden": [], "excluded": [], "modifiers": "applied", "modifiers_note": None}
        res.update(self.result)
        return RunOutcome(result=res, modified=False, saved=None, blender_version="5.2.0 LTS",
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


def invoke(argv, **result):
    inv, _ = flagparser.parse(cmd.SPEC, argv)
    inv.transport = FakeTransport(**result)
    return cmd.run(inv), inv.transport


def fails(argv):
    with pytest.raises(AxiError) as exc:
        invoke(argv)
    return exc.value


# --- CLI -----------------------------------------------------------------------------------------------

def test_new_params_default_and_flags(work):
    _, t = invoke(["--out", "a.glb"])
    p = t.calls[0][1]
    assert p["exclude"] is None and p["include_hidden"] is False and p["apply_modifiers"] is True
    assert p["dry_run"] is False
    _, t = invoke(["--out", "a.glb", "--exclude", "Floor,Cube", "--include-hidden", "--no-apply-modifiers"])
    p = t.calls[0][1]
    assert p["exclude"] == ["Floor", "Cube"] and p["include_hidden"] is True and p["apply_modifiers"] is False


def test_object_flag_is_repeatable_and_verbatim_and_merges_with_objects(work):
    _, t = invoke(["--out", "a.obj", "--object", "A,B", "--object", "C"])
    assert t.calls[0][1]["objects"] == ["A,B", "C"]
    _, t = invoke(["--out", "b.obj", "--objects", "X,Y", "--object", "A,B", "--object", "X"])
    assert t.calls[0][1]["objects"] == ["X", "Y", "A,B"]  # order kept, duplicates dropped


@pytest.mark.parametrize("argv,needle", [
    (["--collection", "Props", "--object", "Cube"], "--collection and --objects are mutually exclusive"),
    (["--exclude", "Floor", "--objects", "Cube"], "--exclude and --objects are mutually exclusive"),
    (["--exclude", "Floor", "--objects", "Cube", "--object", "Ball"], "a keep-list already leaves the rest out"),
])
def test_mutual_exclusion_exit_2(work, argv, needle):
    e = fails(["--out", "a.glb", *argv])
    assert e.exit_code == 2 and e.code == "VALIDATION_ERROR" and needle in e.message


def test_exclude_may_narrow_object_names(work):
    """Deviation from plan 3.2 wording, per the section 8 contract line (`--exclude Floor --object "A,B" --dry-run`)."""
    _, t = invoke(["--out", "a.glb", "--object", "A,B", "--object", "Floor", "--exclude", "Floor"])
    assert t.calls[0][1]["objects"] == ["A,B", "Floor"] and t.calls[0][1]["exclude"] == ["Floor"]


def test_exclude_works_with_collection(work):
    _, t = invoke(["--out", "a.glb", "--collection", "Props", "--exclude", "Ball"])
    assert t.calls[0][1]["collection"] == "Props" and t.calls[0][1]["exclude"] == ["Ball"]


def test_data_order_modifiers_and_skipped_texts(work):
    res, _ = invoke(["--out", "a.glb"], skipped_hidden=["Hole"], excluded=["A", "B"])
    assert list(res.data) == ["file", "exported", "format", "objects", "modifiers", "skipped", "size_kb", "duration_ms"]
    assert res.data["modifiers"] == "applied"
    assert res.data["skipped"] == "1 hidden (Hole), 2 excluded (A, B)"
    assert res.help[0].startswith("Run `blender-axi render") and res.help[1].startswith("Hidden objects are skipped")
    assert "--include-hidden" in res.help[1]


def test_skipped_names_are_capped(work):
    res, _ = invoke(["--out", "a.glb"], excluded=[f"O{i}" for i in range(7)])
    assert res.data["skipped"] == "7 excluded (O0, O1, O2, O3, O4, +2 more)"


def test_hidden_hint_only_when_something_was_skipped(work):
    res, _ = invoke(["--out", "a.glb"], excluded=["Floor"])
    assert len(res.help) == 1 and res.data["skipped"] == "1 excluded (Floor)"


def test_raw_and_evaluated_texts_and_warning(work):
    res, _ = invoke(["--out", "a.glb", "--no-apply-modifiers"], modifiers="raw")
    assert res.data["modifiers"] == "raw" and "warning" not in res.data
    res, _ = invoke(["--out", "a.usda", "--no-apply-modifiers"], modifiers="evaluated",
                    modifiers_note="--no-apply-modifiers ignored: usda exports the evaluated scene")
    assert res.data["modifiers"] == "evaluated (usda exports the evaluated scene)"
    assert res.data["warning"] == "--no-apply-modifiers ignored: usda exports the evaluated scene"


def test_dry_run_output_and_replay_hint(work):
    res, t = invoke(["--out", "a.glb", "--dry-run", "--exclude", "Hole", "--no-apply-modifiers"])
    assert t.calls[0][1]["dry_run"] is True
    assert list(res.data) == ["file", "format", "dry_run", "would_export", "export_set", "skipped", "modifiers",
                              "duration_ms"]
    assert res.data["dry_run"] is True and res.data["would_export"] == "2 (Ring, Floor)"
    assert res.data["export_set"] == [{"name": "Ring", "type": "MESH"}, {"name": "Floor", "type": "MESH"}]
    assert res.data["skipped"] == "none" and "exported" not in res.data and "size_kb" not in res.data
    assert res.help == ["Run `blender-axi export --out a.glb --exclude Hole --no-apply-modifiers` to write it"]


def test_dry_run_hint_keeps_object_flags_and_live(work):
    inv, _ = flagparser.parse(cmd.SPEC, ["scene.blend", "--out", "a.obj", "--dry-run", "--object", "A,B",
                                        "--object", "C d", "--include-hidden"])
    inv.transport = FakeTransport()
    res = cmd.run(inv)
    assert res.help[0] == 'Run `blender-axi export scene.blend --out a.obj --object A,B --object "C d" --include-hidden` to write it'
    inv, _ = flagparser.parse(cmd.SPEC, ["--live", "--out", "a.obj", "--dry-run"])
    inv.transport = FakeTransport()
    assert cmd.run(inv).help[0].endswith("--out a.obj --live` to write it")


def test_dry_run_ignores_an_existing_output(work):
    (work / "a.glb").write_bytes(b"x")
    res, t = invoke(["--out", "a.glb", "--dry-run"])
    assert t.calls and res.data["dry_run"] is True
    e = fails(["--out", "a.glb"])
    assert e.code == "WRITE_REFUSED"


def test_dry_run_truncates_the_set_unless_full(work):
    many = [{"name": f"O{i}", "type": "MESH"} for i in range(25)]
    res, _ = invoke(["--out", "a.glb", "--dry-run"], export_set=many, objects=25)
    assert len(res.data["export_set"]) == 20 and res.data["would_export"].startswith("25 (O0")
    assert any("20 of 25" in h and "--full" in h for h in res.help)
    inv, _ = flagparser.parse(cmd.SPEC, ["--out", "a.glb", "--dry-run"])
    inv.globals.full = True
    inv.transport = FakeTransport(export_set=many, objects=25)
    assert len(cmd.run(inv).data["export_set"]) == 25


def test_dry_run_reports_hidden_skipped_with_hint(work):
    res, _ = invoke(["--out", "a.glb", "--dry-run"], skipped_hidden=["Hole"])
    assert res.data["skipped"] == "1 hidden (Hole)" and any("--include-hidden" in h for h in res.help)


# --- op with fake bpy -----------------------------------------------------------------------------------

class Obj:
    def __init__(self, name, type="MESH", hidden=False, view_layer=True):
        self.name, self.type, self.hidden, self.in_view_layer = name, type, hidden, view_layer
        self.hide_viewport = False
        self.selected = False
        self.viewport_hidden = False  # hide_set state
        self.hide_viewport = hidden
        self.data = types.SimpleNamespace(polygons=[types.SimpleNamespace(vertices=(0, 1, 2, 3))])  # one quad: 2 tris

    def evaluated_get(self, depsgraph):
        return self

    def to_mesh(self):
        return self.data

    def to_mesh_clear(self):
        pass

    def visible_get(self):
        return not (self.hide_viewport or self.viewport_hidden) and self.in_view_layer

    def hide_get(self):
        if not self.in_view_layer:
            raise RuntimeError("not in view layer")
        return self.viewport_hidden

    def hide_set(self, state):
        if not self.in_view_layer:
            raise RuntimeError("not in view layer")
        self.viewport_hidden = state
        if state:
            self.selected = False

    def select_set(self, state):
        if state and (not self.visible_get() or not self.in_view_layer):
            raise RuntimeError("cannot select")
        self.selected = state

    def select_get(self):
        return self.selected


@pytest.fixture
def scene(fake_bpy):
    ring, hole, floor = Obj("Ring"), Obj("Hole", hidden=True), Obj("Floor")
    ab, cam = Obj("A,B"), Obj("Camera", "CAMERA")
    objs = [ring, hole, floor, ab, cam]
    ctx = fake_bpy.context
    ctx.evaluated_depsgraph_get = lambda: "depsgraph"
    ctx.scene = types.SimpleNamespace(objects=objs)
    ctx.view_layer = types.SimpleNamespace(objects=types.SimpleNamespace(active=None))
    fake_bpy.data.objects = types.SimpleNamespace(get={o.name: o for o in objs}.get)
    fake_bpy.data.collections = types.SimpleNamespace(get={
        "Props": types.SimpleNamespace(all_objects=[floor, hole])}.get)
    calls, at_call = [], []
    overrides = []

    @contextlib.contextmanager
    def temp_override(**kw):
        overrides.append(kw)
        yield

    ctx.window, ctx.temp_override = "win", temp_override

    def make(name):
        def call(**kw):
            calls.append((name, kw))
            at_call.append({"selected": [o.name for o in objs if o.selected],
                            "visible": [o.name for o in objs if o.visible_get()]})
            Path(kw["filepath"]).write_bytes(b"x" * 100)
        return call

    fake_bpy.ops.wm = types.SimpleNamespace(
        obj_export=make("obj"), stl_export=make("stl"), ply_export=make("ply"),
        usd_export=make("usd"), alembic_export=make("abc"))
    fake_bpy.ops.export_scene = types.SimpleNamespace(gltf=make("gltf"), fbx=make("fbx"))
    fake_bpy.calls, fake_bpy.at_call, fake_bpy.overrides = calls, at_call, overrides
    fake_bpy.objs = {o.name: o for o in objs}
    return fake_bpy


def out_path(name):
    path = WORK / "op" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    return str(path)


@pytest.mark.parametrize("fmt,call,kwarg", [
    ("glb", "gltf", "export_apply"), ("gltf", "gltf", "export_apply"), ("fbx", "fbx", "use_mesh_modifiers"),
    ("obj", "obj", "apply_modifiers"), ("stl", "stl", "apply_modifiers"), ("ply", "ply", "apply_modifiers"),
])
@pytest.mark.parametrize("apply,mode", [(True, "applied"), (False, "raw")])
def test_modifier_kwarg_per_format(scene, fmt, call, kwarg, apply, mode):
    res = op_export.run({"out": out_path(f"m.{fmt}"), "format": fmt, "apply_modifiers": apply})
    name, kw = scene.calls[0]
    assert name == call and kw[kwarg] is apply
    assert res["modifiers"] == mode and res["modifiers_note"] is None


@pytest.mark.parametrize("fmt,call", [("usd", "usd"), ("usdc", "usd"), ("usda", "usd"), ("abc", "abc")])
def test_usd_and_abc_have_no_modifier_kwarg(scene, fmt, call):
    res = op_export.run({"out": out_path(f"m.{fmt}"), "format": fmt, "apply_modifiers": False})
    name, kw = scene.calls[0]
    assert name == call and not any("modif" in k or "apply" in k for k in kw)
    assert res["modifiers"] == "evaluated" and f"--no-apply-modifiers ignored: {fmt}" in res["modifiers_note"]
    res = op_export.run({"out": out_path(f"n.{fmt}"), "format": fmt})
    assert res["modifiers"] == "evaluated" and res["modifiers_note"] is None


def test_apply_modifiers_defaults_to_true(scene):
    op_export.run({"out": out_path("d.glb"), "format": "glb"})
    assert scene.calls[0][1]["export_apply"] is True


def test_hidden_geometry_is_skipped_by_default_via_selection_path(scene):
    res = op_export.run({"out": out_path("h.obj"), "format": "obj"})
    _, kw = scene.calls[0]
    assert kw["export_selected_objects"] is True  # something was filtered: selection path
    assert scene.at_call[0]["selected"] == ["Ring", "Floor", "A,B", "Camera"]
    assert res["objects"] == 3 and res["skipped_hidden"] == ["Hole"] and res["excluded"] == []
    assert scene.overrides[-1]["selected_objects"][0].name == "Ring"
    assert not any(o.selected for o in scene.objs.values())


def test_no_filter_uses_whole_scene_path(scene):
    scene.objs["Hole"].hide_viewport = False  # nothing hidden any more
    res = op_export.run({"out": out_path("w.obj"), "format": "obj"})
    _, kw = scene.calls[0]
    assert kw["export_selected_objects"] is False and scene.at_call[0]["selected"] == []
    assert scene.overrides == [] and res["objects"] == 4 and res["skipped_hidden"] == []


def test_hidden_non_geometry_does_not_count_as_skipped(scene):
    scene.objs["Hole"].hide_viewport = False
    scene.objs["Camera"].hide_viewport = True
    res = op_export.run({"out": out_path("c.obj"), "format": "obj"})
    assert scene.calls[0][1]["export_selected_objects"] is False and res["skipped_hidden"] == []


def test_exclude_uses_selection_path_and_is_reported(scene):
    res = op_export.run({"out": out_path("x.glb"), "format": "glb", "exclude": ["Floor"], "include_hidden": True})
    assert scene.calls[0][1]["use_selection"] is True
    assert "Floor" not in scene.at_call[0]["selected"] and "Ring" in scene.at_call[0]["selected"]
    assert res["excluded"] == ["Floor"] and res["skipped_hidden"] == [] and res["objects"] == 3


def test_exclude_outside_the_pool_is_listed_without_error(scene):
    res = op_export.run({"out": out_path("y.glb"), "format": "glb", "collection": "Props", "exclude": ["Ring"]})
    assert res["excluded"] == ["Ring"] and res["skipped_hidden"] == ["Hole"] and res["objects"] == 1


def test_exclude_unknown_object_is_not_found_and_writes_nothing(scene):
    with pytest.raises(OpError) as e:
        op_export.run({"out": out_path("z.glb"), "format": "glb", "exclude": ["Floor", "Nope"]})
    assert e.value.code == "NOT_FOUND" and e.value.message == "object not found: Nope" and scene.calls == []
    with pytest.raises(OpError) as e:
        op_export.run({"out": out_path("z2.glb"), "format": "glb", "objects": ["Ring", "Ghost"]})
    assert e.value.message == "object not found: Ghost"


def test_everything_hidden_gets_the_include_hidden_hint(scene):
    with pytest.raises(OpError) as e:
        op_export.run({"out": out_path("e.glb"), "format": "glb", "objects": ["Hole"]})
    assert e.value.code == "NOT_FOUND" and e.value.message.endswith("(1 hidden; add --include-hidden)")
    assert "in the selection" in e.value.message and scene.calls == []


def test_named_hidden_object_with_include_hidden_is_unhidden_then_restored(scene):
    hole = scene.objs["Hole"]
    res = op_export.run({"out": out_path("s1.glb"), "format": "glb", "objects": ["Hole"], "include_hidden": True})
    assert scene.at_call[0]["selected"] == ["Hole"] and res["objects"] == 1 and res["skipped_hidden"] == []
    assert hole.hide_viewport is True and hole.viewport_hidden is False  # S1: both flags restored
    assert hole.visible_get() is False


def test_hide_state_is_restored_when_the_exporter_raises(scene):
    hole = scene.objs["Hole"]
    hole.viewport_hidden = True  # hidden both ways

    def boom(**kw):
        raise RuntimeError("exporter crashed")

    scene.ops.export_scene.gltf = boom
    with pytest.raises(RuntimeError, match="exporter crashed"):
        op_export.run({"out": out_path("s2.glb"), "format": "glb", "include_hidden": True})
    assert hole.hide_viewport is True and hole.viewport_hidden is True and not hole.selected


def test_include_hidden_whole_scene_unhides_for_the_call_only(scene):
    res = op_export.run({"out": out_path("s3.obj"), "format": "obj", "include_hidden": True})
    assert scene.calls[0][1]["export_selected_objects"] is False and "Hole" in scene.at_call[0]["visible"]
    assert res["objects"] == 4 and scene.objs["Hole"].hide_viewport is True


def test_object_in_an_excluded_collection_cannot_be_unhidden(scene):
    scene.objs["Hole"].in_view_layer = False
    res = op_export.run({"out": out_path("s4.glb"), "format": "glb", "include_hidden": True})
    assert res["objects"] == 3 and "Hole" not in scene.at_call[0]["selected"]
    assert res["skipped_hidden"] == ["Hole"]  # include_hidden could not reach it: still reported
    assert scene.objs["Hole"].hide_viewport is True


def test_dry_run_calls_no_exporter_and_writes_nothing(scene):
    path = out_path("dry.glb")
    res = op_export.run({"out": path, "format": "glb", "dry_run": True, "exclude": ["Floor"]})
    assert scene.calls == [] and not Path(path).exists()
    assert res == {"format": "glb", "dry_run": True, "objects": 2,
                   "export_set": [{"name": "Ring", "type": "MESH"}, {"name": "A,B", "type": "MESH"}],
                   "also": [], "triangles": 4,
                   "skipped_hidden": ["Hole"], "excluded": ["Floor"], "non_geometry": ["Camera"],
                   "modifiers": "applied", "modifiers_note": None}
    assert not any(o.selected for o in scene.objs.values())


def test_dry_run_skips_the_write_refusal_and_reports_the_usd_note(scene):
    path = out_path("dry.usda")
    Path(path).write_bytes(b"old")
    res = op_export.run({"out": path, "format": "usda", "dry_run": True, "apply_modifiers": False})
    assert res["modifiers"] == "evaluated" and "ignored" in res["modifiers_note"] and Path(path).read_bytes() == b"old"
    with pytest.raises(OpError) as e:
        op_export.run({"out": path, "format": "usda"})
    assert e.value.code == "WRITE_REFUSED"


def test_dry_run_still_errors_on_an_empty_set(scene):
    with pytest.raises(OpError) as e:
        op_export.run({"out": out_path("d2.glb"), "format": "glb", "dry_run": True, "objects": ["Hole"]})
    assert e.value.code == "NOT_FOUND" and "include-hidden" in e.value.message
