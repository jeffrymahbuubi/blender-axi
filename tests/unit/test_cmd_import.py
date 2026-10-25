"""v1.3 U3: `import` (command level with a fake transport) and `op_import` (fake `bpy`)."""

import shutil
import types
from pathlib import Path

import pytest

from blender_axi import flags as flagparser
from blender_axi.blender_side import op_import
from blender_axi.blender_side.ops import OpError
from blender_axi.commands import import_cmd as cmd
from blender_axi.errors import AxiError
from blender_axi.runner import RunOutcome, SaveSpec
from blender_axi.spec import BatchSpec

WORK = Path(__file__).resolve().parents[2] / ".tmp" / "tests" / "unit_cmd_import"
LIVE = ["--live", "--host", "127.0.0.1", "--port", "9999"]
RESULT = {"source": "", "source_name": "model.glb", "format": "glb", "added": ["Cube", "Light"],
          "types": {"MESH": 1, "LIGHT": 1}, "materials_added": 2, "collection": None, "collection_created": False}


class Fake:
    name = "fake"

    def __init__(self, result=None, file="C:\\work\\open.blend", **outcome_kw):
        self.result = dict(RESULT if result is None else result)
        self.file, self.calls, self.kw = file, [], outcome_kw

    def run(self, op, params, *, blend=None, save=SaveSpec("none"), timeout=None):
        self.calls.append({"op": op, "params": params, "blend": blend, "save": save, "timeout": timeout})
        if op == "ping":
            return RunOutcome({"file": self.file}, False, None, "5.2", 1, "")
        self.result["source"] = params["source"]
        kw = {"modified": True, "saved": save.path if save.mode != "none" else None, **self.kw}
        return RunOutcome(self.result, kw.pop("modified"), kw.pop("saved"), "5.2.0 LTS", 33, "", **kw)

    @property
    def ops(self):
        return [c["op"] for c in self.calls]


@pytest.fixture
def work(monkeypatch):
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(parents=True)
    (WORK / "scene.blend").write_bytes(b"BLEND")
    for name in ("model.glb", "model.usdz", "model.usdc", "model.fbx", "model.xyz", "model"):
        (WORK / name).write_bytes(b"x")
    monkeypatch.chdir(WORK)
    return WORK


def invoke(argv, transport=None):
    inv, wants_help = flagparser.parse(cmd.SPEC, argv)
    assert not wants_help
    inv.transport = transport or Fake()
    return cmd.run(inv), inv.transport


def fails(argv, transport=None):
    transport = transport or Fake()
    with pytest.raises(AxiError) as exc:
        invoke(argv, transport)
    return exc.value, transport


# -- spec -------------------------------------------------------------------------------------------------------------

def test_spec_shape():
    assert cmd.SPEC.name == "import"
    assert cmd.SPEC.summary == ("Import glb, gltf, fbx, obj, stl, ply, usd or abc into a .blend "
                                "(or a new one with --new, or the open session with --live)")
    assert [p.name for p in cmd.SPEC.positionals] == ["source", "file"]
    assert [p.required for p in cmd.SPEC.positionals] == [True, False]
    assert [f.name for f in cmd.SPEC.flags] == ["--collection", "--format", "--new", "--in-place", "--out", "--force"]
    fmt = next(f for f in cmd.SPEC.flags if f.name == "--format")
    assert fmt.choices == ("glb", "gltf", "fbx", "obj", "stl", "ply", "usd", "usdc", "usda", "abc")
    assert isinstance(cmd.BATCH, BatchSpec) and cmd.BATCH.op == "import"


# -- format -----------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("name,fmt", [("model.glb", "glb"), ("model.usdz", "usd"), ("model.usdc", "usdc"),
                                      ("model.fbx", "fbx")])
def test_format_from_the_extension(work, name, fmt):
    res, t = invoke([name, "scene.blend", "--in-place"])
    assert t.calls[-1]["params"]["format"] == fmt and res.data["format"] == "glb"  # the fake echoes its own result


def test_format_flag_must_match_the_extension(work):
    err, t = fails(["model.glb", "scene.blend", "--format", "fbx", "--in-place"])
    assert err.exit_code == 2 and "does not match --format fbx" in err.message and t.calls == []
    res, t = invoke(["model.usdz", "scene.blend", "--format", "usd", "--in-place"])
    assert t.calls[-1]["params"]["format"] == "usd"


def test_unknown_or_missing_extension_is_exit_2(work):
    for name in ("model.xyz", "model"):
        err, t = fails([name, "scene.blend", "--in-place"])
        assert err.exit_code == 2 and err.code == "VALIDATION_ERROR" and t.calls == []
    assert "cannot infer the import format from extension '.xyz'" in fails(["model.xyz", "scene.blend", "--in-place"])[0].message


def test_missing_source_is_not_found_before_any_transport_call(work):
    err, t = fails(["nope.glb", "scene.blend", "--in-place"])
    assert err.code == "NOT_FOUND" and err.exit_code == 1 and "nope.glb" in err.message and t.calls == []
    err, t = fails(["nope.glb", *LIVE])
    assert err.code == "NOT_FOUND" and t.calls == []


# -- params and output ------------------------------------------------------------------------------------------------

def test_params_shape_and_data_order(work):
    res, t = invoke(["model.glb", "scene.blend", "--in-place"])
    call = t.calls[0]
    assert call["op"] == "import" and call["blend"] == (WORK / "scene.blend").resolve()
    assert call["params"] == {"source": str(WORK / "model.glb"), "format": "glb", "collection": None, "fresh": False}
    assert call["save"].mode == "in_place"
    assert list(res.data) == ["file", "imported", "format", "added", "types", "materials", "modified", "saved",
                              "duration_ms"]
    assert res.data["file"] == "scene.blend" and res.data["imported"] == "model.glb"
    assert res.data["added"] == "2 (Cube, Light)" and res.data["types"] == "LIGHT 1, MESH 1"  # ties sort by name
    assert res.data["materials"] == 2 and "collection" not in res.data
    assert any("objects scene.blend" in h for h in res.help)
    assert any("render scene.blend --engine WORKBENCH --out <path.png>" in h for h in res.help)


def test_types_sorted_by_count_and_none_when_empty(work):
    res, _ = invoke(["model.glb", "scene.blend", "--in-place"],
                    Fake({**RESULT, "types": {"EMPTY": 1, "MESH": 3}, "added": ["a", "b", "c", "d"]}))
    assert res.data["types"] == "MESH 3, EMPTY 1"
    res, _ = invoke(["model.glb", "scene.blend", "--in-place"],
                    Fake({**RESULT, "types": {}, "added": []}, modified=False, saved=None))
    assert res.data["added"] == "0" and res.data["types"] == "none" and res.data["modified"] is False
    assert res.data["saved"] == "none (no changes)"


def test_collection_text_marks_a_created_collection(work):
    res, t = invoke(["model.glb", "scene.blend", "--collection", "Props", "--in-place"],
                    Fake({**RESULT, "collection": "Props", "collection_created": True}))
    assert t.calls[0]["params"]["collection"] == "Props" and res.data["collection"] == "Props (created)"
    assert list(res.data).index("collection") == list(res.data).index("materials") + 1
    res, _ = invoke(["model.glb", "scene.blend", "--collection", "Props", "--in-place"],
                    Fake({**RESULT, "collection": "Props"}))
    assert res.data["collection"] == "Props"


def test_empty_collection_name_is_exit_2(work):
    err, t = fails(["model.glb", "scene.blend", "--collection", " ", "--in-place"])
    assert err.exit_code == 2 and t.calls == []


def test_restore_warnings_become_a_warning(work):
    res, _ = invoke(["model.glb", "scene.blend", "--in-place"], Fake({**RESULT, "restore_warnings": ["active object: x"]}))
    assert res.data["warning"] == "active object: x"


def test_write_rule_is_shared_with_the_other_modeling_commands(work):
    err, t = fails(["model.glb", "scene.blend"])
    assert err.exit_code == 2 and "say where to write it" in err.message and t.calls == []
    assert any("import model.glb scene.blend --in-place" in h for h in err.help)
    res, t = invoke(["model.glb", "scene.blend", "--out", "copy.blend"])
    assert t.calls[0]["save"] == SaveSpec("copy", str((WORK / "copy.blend").resolve()))
    assert any("objects copy.blend" in h for h in res.help)


# -- --new ------------------------------------------------------------------------------------------------------------

def test_new_imports_into_a_fresh_file(work):
    res, t = invoke(["model.glb", "--new", "--out", "m.blend"])
    call = t.calls[0]
    assert call["blend"] is None and call["save"] == SaveSpec("copy", str((WORK / "m.blend").resolve()))
    assert call["params"]["fresh"] is True
    assert res.data["file"] == "(new)" and res.data["saved"].endswith("m.blend")
    assert any("objects m.blend" in h for h in res.help) and any("render m.blend" in h for h in res.help)


@pytest.mark.parametrize("argv,needle", [
    (["model.glb", "--new", "--out", "m.blend", *LIVE], "--new creates a file on disk"),
    (["model.glb", "scene.blend", "--new", "--out", "m.blend"], "--new starts from an empty file; drop <file>"),
    (["model.glb", "--new", "--in-place"], "--new has no file to change in place"),
    (["model.glb", "--new"], "--new needs --out"),
    (["model.glb", "--new", "--out", "m.fbx"], "--out must be a .blend path"),
])
def test_new_rule_violations_are_exit_2_without_blender(work, argv, needle):
    err, t = fails(argv)
    assert err.exit_code == 2 and needle in err.message and t.calls == []


def test_new_refuses_an_existing_out_unless_forced(work):
    (WORK / "m.blend").write_bytes(b"old")
    err, t = fails(["model.glb", "--new", "--out", "m.blend"])
    assert err.code == "WRITE_REFUSED" and err.exit_code == 1 and t.calls == []
    assert any(h.startswith("Run `blender-axi import model.glb --new --out m.blend --force`") for h in err.help)
    res, t = invoke(["model.glb", "--new", "--out", "m.blend", "--force"])
    assert t.ops == ["import"] and res.data["saved"].endswith("m.blend")


# -- live -------------------------------------------------------------------------------------------------------------

def test_live_import_stays_in_the_session(work):
    res, t = invoke(["model.glb", *LIVE])
    assert t.ops == ["ping", "import"] and t.calls[1]["blend"] is None and t.calls[1]["save"] == SaveSpec("none")
    assert res.data["saved"] == "none (live session, unsaved)" and res.data["file"] == "C:/work/open.blend"
    assert any("Changes are only in the open session" in h for h in res.help)
    assert any(h.startswith("Run `blender-axi objects --live --host 127.0.0.1") for h in res.help)


def test_live_refusals(work):
    err, t = fails(["model.glb", "scene.blend", *LIVE])
    assert err.exit_code == 2 and "drop <file>" in err.message
    err, t = fails(["model.glb", *LIVE, "--out", "x.blend"])
    assert err.exit_code == 2 and "no copy" in err.message


# -- BATCH ------------------------------------------------------------------------------------------------------------

def _inv(argv):
    inv, _ = flagparser.parse(cmd.SPEC, argv)
    return inv


def test_batch_params_equal_what_run_sends(work):
    argv = ["model.glb", "--collection", "Props"]
    _, t = invoke([*argv, "scene.blend", "--in-place"])
    assert cmd.BATCH.params(_inv(argv)) == t.calls[0]["params"]


def test_batch_params_reject_new_and_missing_sources(work):
    with pytest.raises(AxiError) as exc:
        cmd.BATCH.params(_inv(["model.glb", "--new"]))
    assert exc.value.exit_code == 2 and "--new" in exc.value.message
    with pytest.raises(AxiError) as exc:
        cmd.BATCH.params(_inv(["nope.glb"]))
    assert exc.value.code == "NOT_FOUND"
    with pytest.raises(AxiError) as exc:
        cmd.BATCH.params(_inv(["model.xyz"]))
    assert exc.value.exit_code == 2


def test_batch_line_texts():
    assert cmd.BATCH.line({"added": ["A", "B"], "source_name": "m.glb"}) == "2 objects from m.glb"
    assert cmd.BATCH.line({"added": ["A"], "source_name": "m.glb"}) == "1 object from m.glb"
    assert cmd.BATCH.line({"added": [], "source_name": "m.obj"}) == "0 objects from m.obj"


# -- op_import with a fake bpy ----------------------------------------------------------------------------------------

class Coll:
    def __init__(self, name):
        self.name = name
        self.objects = Members(self)
        self.children = types.SimpleNamespace(link=lambda c: self.kids.append(c))
        self.kids = []


class Members(list):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def link(self, obj):
        self.append(obj)
        obj.users_collection = (*obj.users_collection, self.owner)

    def unlink(self, obj):
        self.remove(obj)
        obj.users_collection = tuple(c for c in obj.users_collection if c is not self.owner)


class Obj:
    def __init__(self, name, type_="MESH"):
        self.name, self.type, self.users_collection, self.selected = name, type_, (), False

    def select_get(self):
        return self.selected

    def select_set(self, state):
        self.selected = state


@pytest.fixture
def world(fake_bpy):
    master = Coll("Scene Collection")
    active = Coll("Collection")
    master.kids.append(active)
    existing = Obj("Existing")
    existing.selected = True
    master.objects.link(existing)
    fake_bpy.data.objects = [existing]
    fake_bpy.data.materials = []
    scene = types.SimpleNamespace(collection=master, objects=fake_bpy.data.objects)
    view_layer = types.SimpleNamespace(objects=types.SimpleNamespace(active=existing))
    log = {"override": [], "calls": [], "reset": []}

    class Colls(list):
        def new(self, name):
            c = Coll(name)
            self.append(c)
            return c

    fake_bpy.data.collections = Colls([active])
    window = object()

    class Override:
        def __init__(self, kw):
            log["override"].append(kw)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    fake_bpy.context = types.SimpleNamespace(
        scene=scene, view_layer=view_layer, collection=active, window=None,
        window_manager=types.SimpleNamespace(windows=[window]), temp_override=lambda **kw: Override(kw))

    def importer(label, *new_objects, status=None, materials=()):
        def call(filepath):
            log["calls"].append((label, filepath))
            for o in new_objects:
                o.selected = True
                active.objects.link(o)
                fake_bpy.data.objects.append(o)
            fake_bpy.data.materials.extend(types.SimpleNamespace(name=m) for m in materials)
            return status or {"FINISHED"}
        return call

    def reset(use_empty):
        log["reset"].append(use_empty)
        for o in list(fake_bpy.data.objects):
            for c in o.users_collection:
                c.objects.remove(o)
        fake_bpy.data.objects.clear()
        existing.users_collection = ()

    fake_bpy.ops = types.SimpleNamespace(
        wm=types.SimpleNamespace(read_factory_settings=reset),
        import_scene=types.SimpleNamespace(), )
    return types.SimpleNamespace(bpy=fake_bpy, log=log, active=active, master=master, existing=existing,
                                 importer=importer, window=window)


def test_op_runs_the_operator_of_each_format(world, tmp_path):
    src = tmp_path / "m.x"
    src.write_bytes(b"x")
    ops = world.bpy.ops
    table = {"glb": (ops.import_scene, "gltf"), "gltf": (ops.import_scene, "gltf"), "fbx": (ops.import_scene, "fbx"),
             "obj": (ops.wm, "obj_import"), "stl": (ops.wm, "stl_import"), "ply": (ops.wm, "ply_import"),
             "usd": (ops.wm, "usd_import"), "usdc": (ops.wm, "usd_import"), "usda": (ops.wm, "usd_import"),
             "abc": (ops.wm, "alembic_import")}
    for fmt, (ns, attr) in table.items():
        setattr(ns, attr, world.importer(attr))
        world.log["calls"].clear()
        op_import.run({"source": str(src), "format": fmt})
        assert world.log["calls"] == [(attr, str(src))], fmt


def test_op_diffs_objects_and_materials_and_restores_the_selection(world, tmp_path):
    src = tmp_path / "m.glb"
    src.write_bytes(b"x")
    cube, lamp = Obj("Cube"), Obj("Lamp", "LIGHT")
    world.bpy.ops.import_scene.gltf = world.importer("gltf", cube, lamp, materials=("Red", "Blue"))
    res = op_import.run({"source": str(src), "format": "glb", "collection": None, "fresh": False})
    assert res["added"] == ["Cube", "Lamp"] and res["types"] == {"MESH": 1, "LIGHT": 1} and res["materials_added"] == 2
    assert res["collection"] is None and res["collection_created"] is False and "restore_warnings" not in res
    assert res["source_name"] == "m.glb" and res["format"] == "glb"
    assert world.existing.selected is True and cube.selected is False and lamp.selected is False  # restored
    assert cube.users_collection == (world.active,)  # untouched without --collection


def test_op_uses_the_window_override_when_the_context_has_no_window(world, tmp_path):
    src = tmp_path / "m.glb"
    src.write_bytes(b"x")
    world.bpy.ops.import_scene.gltf = world.importer("gltf", Obj("Cube"))
    op_import.run({"source": str(src), "format": "glb"})
    assert world.log["override"] == [{"window": world.window}]
    world.log["override"].clear()
    world.bpy.context.window = object()
    op_import.run({"source": str(src), "format": "glb"})
    assert world.log["override"] == []


def test_op_collection_is_reused_or_created_and_objects_are_moved(world, tmp_path):
    src = tmp_path / "m.obj"
    src.write_bytes(b"x")
    cube = Obj("Cube")
    world.bpy.ops.wm.obj_import = world.importer("obj", cube)
    res = op_import.run({"source": str(src), "format": "obj", "collection": "Collection"})
    assert res["collection"] == "Collection" and res["collection_created"] is False
    assert cube.users_collection == (world.active,)

    other = Obj("Other")
    world.bpy.ops.wm.obj_import = world.importer("obj", other)
    res = op_import.run({"source": str(src), "format": "obj", "collection": "Props"})
    props = next(c for c in world.bpy.data.collections if c.name == "Props")
    assert res["collection"] == "Props" and res["collection_created"] is True
    assert props in world.master.kids and other.users_collection == (props,)
    assert other not in world.active.objects  # unlinked from the importer's collection

    third = Obj("Third")
    world.bpy.ops.wm.obj_import = world.importer("obj", third)
    res = op_import.run({"source": str(src), "format": "obj", "collection": "Props"})
    assert res["collection_created"] is False and third.users_collection == (props,)
    assert len([c for c in world.bpy.data.collections if c.name == "Props"]) == 1


def test_op_scene_collection_name_means_the_master_collection(world, tmp_path):
    src = tmp_path / "m.obj"
    src.write_bytes(b"x")
    cube = Obj("Cube")
    world.bpy.ops.wm.obj_import = world.importer("obj", cube)
    res = op_import.run({"source": str(src), "format": "obj", "collection": "Scene Collection"})
    assert res["collection"] == "Scene Collection" and res["collection_created"] is False
    assert cube.users_collection == (world.master,)


def test_op_fresh_starts_from_an_empty_file_first(world, tmp_path):
    src = tmp_path / "m.glb"
    src.write_bytes(b"x")
    world.bpy.ops.import_scene.gltf = world.importer("gltf", Obj("Cube"))
    res = op_import.run({"source": str(src), "format": "glb", "fresh": True})
    assert world.log["reset"] == [True] and res["added"] == ["Cube"]  # "Existing" was wiped, not counted as added
    assert [o.name for o in world.bpy.data.objects] == ["Cube"]


def test_op_zero_objects_is_not_an_error(world, tmp_path):
    src = tmp_path / "m.obj"
    src.write_bytes(b"# nothing\n")
    world.bpy.ops.wm.obj_import = world.importer("obj")
    res = op_import.run({"source": str(src), "format": "obj", "collection": "Props"})
    assert res["added"] == [] and res["types"] == {} and res["materials_added"] == 0 and res["collection_created"] is True


def test_op_non_finished_is_op_failed_and_the_selection_is_still_restored(world, tmp_path):
    src = tmp_path / "m.glb"
    src.write_bytes(b"x")
    cube = Obj("Cube")
    world.bpy.ops.import_scene.gltf = world.importer("gltf", cube, status={"CANCELLED"})
    with pytest.raises(OpError) as exc:
        op_import.run({"source": str(src), "format": "glb"})
    assert exc.value.code == "OP_FAILED" and "CANCELLED" in exc.value.message
    assert cube.selected is False and world.existing.selected is True


def test_op_missing_source_or_unknown_format(world, tmp_path):
    with pytest.raises(OpError) as exc:
        op_import.run({"source": str(tmp_path / "nope.glb"), "format": "glb"})
    assert exc.value.code == "NOT_FOUND"
    with pytest.raises(OpError) as exc:
        op_import.run({"source": str(tmp_path / "nope.glb"), "format": "dxf"})
    assert exc.value.code == "OP_FAILED"


def test_op_restore_failures_are_reported(world, tmp_path):
    src = tmp_path / "m.glb"
    src.write_bytes(b"x")

    class Stubborn(Obj):
        def select_set(self, state):
            raise ValueError("boom")

    world.bpy.ops.import_scene.gltf = world.importer("gltf", Stubborn("Cube"))
    res = op_import.run({"source": str(src), "format": "glb"})
    assert res["restore_warnings"] == ["selection of Cube: boom"]
