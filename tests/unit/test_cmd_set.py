"""v1.2 U2: `set` (command level with a fake transport) and `op_set` (fake `bpy`)."""

import math
import shutil
import types
from pathlib import Path

import pytest

from blender_axi import flags as flagparser
from blender_axi.blender_side import common, op_set
from blender_axi.blender_side.ops import OpError
from blender_axi.commands import set_cmd as cmd
from blender_axi.errors import AxiError
from blender_axi.runner import RunOutcome, SaveSpec

WORK = Path(__file__).resolve().parents[2] / ".tmp" / "tests" / "unit_cmd_set"
LIVE = ["--live", "--host", "127.0.0.1", "--port", "9999"]
LOC = {"field": "location", "from": [0, 0, 0], "to": [1, 2, 3]}


class Fake:
    name = "fake"

    def __init__(self, changed=None, file="C:\\work\\open.blend", **outcome_kw):
        self.result = {"name": "Cube", "changed": [LOC] if changed is None else changed}
        self.file, self.calls, self.kw = file, [], outcome_kw

    def run(self, op, params, *, blend=None, save=SaveSpec("none"), timeout=None):
        self.calls.append({"op": op, "params": params, "blend": blend, "save": save, "timeout": timeout})
        if op == "ping":
            return RunOutcome({"file": self.file}, False, None, "5.2", 1, "")
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
    assert cmd.SPEC.name == "set"
    assert [p.name for p in cmd.SPEC.positionals] == ["name", "file"] and cmd.SPEC.positionals[1].required is False
    assert [f.name for f in cmd.SPEC.flags] == ["--name", "--location", "--rotation", "--scale", "--parent",
                                                 "--collection", "--hide", "--show", "--in-place", "--out", "--force"]
    assert all(f.kind == "bool" for f in cmd.SPEC.flags if f.name in ("--hide", "--show"))
    assert any("--live" in e for e in cmd.SPEC.examples)


def test_no_change_flag_is_exit_2(work):
    t = Fake()
    err = fails(["Cube", "scene.blend", "--in-place"], t)
    assert err.code == "VALIDATION_ERROR" and err.exit_code == 2 and t.calls == []
    assert err.message == "set needs at least one change flag" and "--location" in err.help[0]


def test_hide_with_show_is_exit_2(work):
    t = Fake()
    err = fails(["Cube", "scene.blend", "--hide", "--show", "--in-place"], t)
    assert err.exit_code == 2 and "mutually exclusive" in err.message and t.calls == []


@pytest.mark.parametrize("argv", [
    ["--location", "1,2"], ["--rotation", "a,b,c"], ["--scale", "1,1,1,1"], ["--name", " "], ["--parent", " "],
    ["--collection", ""],
])
def test_value_errors_exit_2_without_requests(work, argv):
    t = Fake()
    err = fails(["Cube", "scene.blend", *argv, "--in-place"], t)
    assert err.code == "VALIDATION_ERROR" and err.exit_code == 2 and t.calls == []


def test_neither_flag_exit_2_with_both_commands(work):
    t = Fake()
    err = fails(["Cube", "scene.blend", "--location", "1,2,3"], t)
    assert err.exit_code == 2 and t.calls == []
    assert err.help[0] == "Run `blender-axi set Cube scene.blend --location 1,2,3 --in-place` to write the file (a .blend1 backup is kept)"
    assert err.help[1] == "Run `blender-axi set Cube scene.blend --location 1,2,3 --out <copy.blend>` to keep the original and write a copy"


# -- params and result ------------------------------------------------------------------------------------------------

def test_params_shape_defaults_to_none(work):
    _, t = invoke(["Cube", "scene.blend", "--location", "1,2,3", "--in-place"])
    call = t.calls[0]
    assert call["op"] == "set" and call["params"] == {
        "name": "Cube", "new_name": None, "location": [1.0, 2.0, 3.0], "rotation_deg": None, "scale": None,
        "parent": None, "collection": None, "hide": None}
    assert call["blend"] == (WORK / "scene.blend").resolve() and call["save"] == SaveSpec("in_place", str(call["blend"]))


def test_params_passthrough_with_degrees(work):
    _, t = invoke(["Cube", "scene.blend", "--name", "Crate", "--location", "-1,2,3", "--rotation", "0,0,90",
                   "--scale", "2,2,2", "--parent", "Anchor", "--collection", "Props", "--out", "o.blend"])
    assert t.calls[0]["params"] == {
        "name": "Cube", "new_name": "Crate", "location": [-1.0, 2.0, 3.0], "rotation_deg": [0.0, 0.0, 90.0],
        "scale": [2.0, 2.0, 2.0], "parent": "Anchor", "collection": "Props", "hide": None}
    assert t.calls[0]["save"] == SaveSpec("copy", str((WORK / "o.blend").resolve()))


@pytest.mark.parametrize("given", ["none", "None", "NONE"])
def test_parent_none_passes_none_string(work, given):
    _, t = invoke(["Cube", "scene.blend", "--parent", given, "--in-place"])
    assert t.calls[0]["params"]["parent"] == "none"


def test_hide_and_show_params(work):
    assert invoke(["Cube", "scene.blend", "--hide", "--in-place"])[1].calls[0]["params"]["hide"] is True
    assert invoke(["Cube", "scene.blend", "--show", "--in-place"])[1].calls[0]["params"]["hide"] is False


def test_data_order_changed_strings_and_hints(work):
    t = Fake([LOC, {"field": "rotation", "from": [0, 0, 0], "to": [0, 0, 90]},
              {"field": "parent", "from": None, "to": "Anchor"}, {"field": "collection", "from": "Collection", "to": "Props"},
              {"field": "hidden", "from": False, "to": True}, {"field": "name", "from": "Cube", "to": "Crate"}])
    t.result["name"] = "Crate"
    res, _ = invoke(["Cube", "scene.blend", "--location", "1,2,3", "--in-place"], t)
    assert list(res.data) == ["file", "object", "changed", "modified", "saved", "duration_ms"]
    assert res.data["file"] == "scene.blend" and res.data["object"] == "Crate" and res.data["modified"] is True
    assert res.data["changed"] == [
        "location: 0,0,0 -> 1,2,3", "rotation: 0,0,0 -> 0,0,90", "parent: none -> Anchor",
        "collection: Collection -> Props", "hidden: false -> true", "name: Cube -> Crate"]
    assert res.help == ["Run `blender-axi objects scene.blend` to list the objects",
                        "Run `blender-axi object Crate scene.blend` to check the result"]


def test_unchanged_state_string_and_no_save(work):
    res, _ = invoke(["Cube", "scene.blend", "--location", "0,0,0", "--in-place"], Fake([], modified=False, saved=None))
    assert res.data["changed"] == "none (values already as requested)"
    assert res.data["modified"] is False and res.data["saved"] == "none (no changes)" and "backup" not in res.data


def test_vectors_use_three_decimals(work):
    res, _ = invoke(["Cube", "scene.blend", "--location", "1,2,3", "--in-place"],
                    Fake([{"field": "location", "from": [0.12345, 0, -0.0], "to": [1, 2, 3.5]}]))
    assert res.data["changed"] == ["location: 0.123,0,0 -> 1,2,3.5"]


def test_hint_names_with_spaces_are_quoted(work):
    t = Fake()
    t.result["name"] = "My Box"
    res, _ = invoke(["Cube", "scene.blend", "--location", "1,2,3", "--in-place"], t)
    assert res.help[1] == 'Run `blender-axi object "My Box" scene.blend` to check the result'


def test_out_hints_use_the_written_copy(work):
    res, _ = invoke(["Cube", "scene.blend", "--location", "1,2,3", "--out", "copy.blend"])
    assert res.data["saved"] == "copy.blend" and res.help[0] == "Run `blender-axi objects copy.blend` to list the objects"
    assert res.help[1] == "Run `blender-axi object Cube copy.blend` to check the result"


def test_hints_carry_user_prefs(work):
    res, _ = invoke(["Cube", "scene.blend", "--location", "1,2,3", "--in-place", "--user-prefs"])
    assert all(h.split("`")[1].endswith("--user-prefs") for h in res.help)


def test_not_found_gets_an_actionable_hint(work):
    class Missing(Fake):
        def run(self, op, params, **kw):
            raise AxiError("NOT_FOUND", "object 'X' not found (did you mean: Cube?)", help=["generic"])
    err = fails(["X", "scene.blend", "--location", "1,2,3", "--in-place"], Missing())
    assert err.code == "NOT_FOUND" and err.help == ["Run `blender-axi objects scene.blend` to list object names"]


def test_missing_file_keeps_its_own_hint(work):
    err = fails(["Cube", "nope.blend", "--location", "1,2,3", "--in-place"])
    assert err.code == "NOT_FOUND" and err.message.startswith("file not found")
    assert not any("list object names" in h for h in err.help)


# -- live -------------------------------------------------------------------------------------------------------------

def test_live_unsaved_change():
    res, t = invoke(["Cube", *LIVE, "--location", "1,2,3"], Fake(undo="unavailable", saved=None))
    assert t.ops == ["ping", "set"] and t.calls[1]["blend"] is None and t.calls[1]["save"] == SaveSpec("none")
    d = res.data
    assert d["file"] == "C:/work/open.blend" and d["modified"] is True
    assert d["saved"] == "none (live session, unsaved)" and d["undo"] == "unavailable (headless server)"
    assert res.help[0] == "Run `blender-axi objects --live --host 127.0.0.1 --port 9999` to list the objects"
    assert res.help[1] == "Run `blender-axi object Cube --live --host 127.0.0.1 --port 9999` to check the result"
    assert res.help[-1].startswith("Changes are only in the open session")


def test_live_not_found_hint_has_no_file():
    class Missing(Fake):
        def run(self, op, params, **kw):
            if op == "ping":
                return super().run(op, params, **kw)
            raise AxiError("NOT_FOUND", "object 'X' not found", help=["generic"])
    err = fails(["X", *LIVE, "--location", "1,2,3"], Missing())
    assert err.help == ["Run `blender-axi objects --live --host 127.0.0.1 --port 9999` to list object names"]


def test_live_in_place_and_refusals():
    res, t = invoke(["Cube", *LIVE, "--location", "1,2,3", "--in-place"], Fake(undo="pushed"))
    assert t.calls[1]["save"] == SaveSpec("in_place", "C:\\work\\open.blend") and res.data["saved"].endswith("open.blend")
    for argv in (["Cube", "x.blend", *LIVE, "--hide"], ["Cube", *LIVE, "--hide", "--out", "o.blend"]):
        t = Fake()
        err = fails(argv, t)
        assert err.exit_code == 2 and t.calls == []


# -- op_set (fake bpy) ------------------------------------------------------------------------------------------------

class Mat:
    """Just enough of mathutils.Matrix: copy() and inverted()."""

    def __init__(self, tag="m"):
        self.tag = tag

    def copy(self):
        return Mat(self.tag + "'")

    def inverted(self):
        return Mat(self.tag + "^-1")


class Coll:
    def __init__(self, name):
        self.name = name
        self.members = []
        self.objects = types.SimpleNamespace(link=lambda o: (self.members.append(o), o.users_collection.append(self))[0],
                                             unlink=lambda o: (self.members.remove(o), o.users_collection.remove(self))[0])


def make_object(name, parent=None, coll=None):
    ob = types.SimpleNamespace(
        name=name, parent=parent, location=(0.0, 0.0, 0.0), rotation_euler=(0.0, 0.0, 0.0), scale=(1.0, 1.0, 1.0),
        matrix_world=Mat(name), matrix_parent_inverse=Mat("I"), users_collection=[], hide_viewport=False,
        hide_render=False, hide_calls=[])
    ob.hide_get = lambda: False
    ob.hide_set = lambda state: ob.hide_calls.append(state)
    if coll is not None:
        coll.objects.link(ob)
    return ob


@pytest.fixture
def world(fake_bpy):
    scene_coll, props, main = Coll("Scene Collection"), Coll("Props"), Coll("Collection")
    cube, anchor = make_object("Cube", coll=main), make_object("Anchor", coll=props)
    updates = []
    fake_bpy.data = types.SimpleNamespace(objects=[cube, anchor], collections=[props, main])
    fake_bpy.context = types.SimpleNamespace(
        scene=types.SimpleNamespace(collection=scene_coll), collection=main,
        view_layer=types.SimpleNamespace(update=lambda: updates.append(1)))
    return types.SimpleNamespace(bpy=fake_bpy, cube=cube, anchor=anchor, props=props, main=main, scene=scene_coll,
                                 updates=updates)


def set_op(**params):
    base = {k: None for k in ("new_name", "location", "rotation_deg", "scale", "parent", "collection", "hide")}
    return op_set.run({"name": "Cube", **base, **params})


def test_op_location_rotation_scale(world):
    r = set_op(location=[1, 2, 3], rotation_deg=[0, 0, 90], scale=[2, 2, 2])
    assert world.cube.location == (1.0, 2.0, 3.0) and world.cube.scale == (2.0, 2.0, 2.0)
    assert world.cube.rotation_euler == pytest.approx((0.0, 0.0, math.pi / 2))
    assert r["name"] == "Cube" and r["changed"] == [
        {"field": "location", "from": [0.0, 0.0, 0.0], "to": [1.0, 2.0, 3.0]},
        {"field": "rotation", "from": [0.0, 0.0, 0.0], "to": [0.0, 0.0, 90.0]},
        {"field": "scale", "from": [1.0, 1.0, 1.0], "to": [2.0, 2.0, 2.0]}]


def test_op_only_differing_fields_are_reported_and_assigned(world):
    world.cube.location = (1.0, 2.0, 3.0)
    world.cube.rotation_euler = (0.0, 0.0, math.radians(90))
    sentinel = world.cube.rotation_euler
    r = set_op(location=[1, 2, 3], rotation_deg=[0, 0, 90], scale=[3, 1, 1])
    assert [c["field"] for c in r["changed"]] == ["scale"]
    assert world.cube.rotation_euler is sentinel  # not reassigned: no float noise, no spurious "modified"
    assert set_op(location=[1, 2, 3], rotation_deg=[0, 0, 90], scale=[3, 1, 1])["changed"] == []


def test_op_rename_reports_the_final_name(world):
    r = set_op(new_name="Crate")
    assert world.cube.name == "Crate" and r["name"] == "Crate"
    assert r["changed"] == [{"field": "name", "from": "Cube", "to": "Crate"}]
    assert set_op(name="Crate", new_name="Crate")["changed"] == []


def test_op_parent_sets_inverse_matrix_and_keeps_world_only_when_reparenting(world):
    r = set_op(parent="Anchor")
    assert world.cube.parent is world.anchor and world.cube.matrix_parent_inverse.tag == "Anchor^-1"
    assert world.cube.matrix_world.tag == "Cube"  # first parent: the inverse already keeps it in place
    assert r["changed"] == [{"field": "parent", "from": None, "to": "Anchor"}]
    assert world.updates  # matrix_world is read after a depsgraph update
    other = make_object("Other", coll=world.main)
    world.bpy.data.objects.append(other)
    set_op(parent="Other")
    assert world.cube.parent is other and world.cube.matrix_world.tag.endswith("'")  # world matrix restored


def test_op_parent_none_keeps_world_matrix(world):
    world.cube.parent = world.anchor
    r = set_op(parent="none")
    assert world.cube.parent is None and world.cube.matrix_world.tag == "Cube'"
    assert r["changed"] == [{"field": "parent", "from": "Anchor", "to": None}]
    assert set_op(parent="none")["changed"] == []  # already unparented


def test_op_parent_same_or_loop_is_op_failed_and_changes_nothing(world):
    with pytest.raises(OpError) as exc:
        set_op(parent="Cube", location=[9, 9, 9])
    assert exc.value.code == "OP_FAILED" and "same object" in exc.value.message and world.cube.location == (0.0, 0.0, 0.0)
    world.anchor.parent = world.cube
    with pytest.raises(OpError) as exc:
        set_op(parent="Anchor")
    assert "loop" in exc.value.message and world.cube.parent is None


def test_op_missing_parent_collection_or_object_is_not_found_and_changes_nothing(world):
    with pytest.raises(OpError) as exc:
        set_op(parent="Nope", location=[9, 9, 9], new_name="X")
    assert exc.value.code == "NOT_FOUND" and "parent object 'Nope'" in exc.value.message
    with pytest.raises(OpError) as exc:
        set_op(collection="Nope", location=[9, 9, 9])
    assert exc.value.code == "NOT_FOUND" and "collection 'Nope'" in exc.value.message and "Props" in exc.value.message
    with pytest.raises(OpError) as exc:
        set_op(name="Cub")
    assert exc.value.code == "NOT_FOUND" and "did you mean: Cube" in exc.value.message
    assert world.cube.location == (0.0, 0.0, 0.0) and world.cube.name == "Cube"


def test_op_set_uses_the_shared_collection_lookup_and_counts_in_the_singular(world):
    assert op_set.find_collection is common.find_collection and not hasattr(op_set, "_collection")
    with pytest.raises(OpError) as exc:
        set_op(name="Qqqqqq")
    assert "(the file has 2 objects)" in exc.value.message
    world.bpy.data.objects[:] = [world.cube]
    with pytest.raises(OpError) as exc:
        set_op(name="Qqqqqq")
    assert "(the file has 1 object)" in exc.value.message


def test_op_collection_move_unlinks_every_old_collection(world):
    world.props.objects.link(world.cube)  # now in Collection and Props
    r = set_op(collection="Scene Collection")
    assert world.cube.users_collection == [world.scene] and world.main.members == [] and world.props.members == [world.anchor]
    assert r["changed"] == [{"field": "collection", "from": "Collection|Props", "to": "Scene Collection"}]
    assert set_op(collection="Scene Collection")["changed"] == []


def test_op_hide_sets_both_flags_and_show_tries_hide_set(world):
    r = set_op(hide=True)
    assert world.cube.hide_viewport and world.cube.hide_render and world.cube.hide_calls == []
    assert r["changed"] == [{"field": "hidden", "from": False, "to": True}]
    assert set_op(hide=True)["changed"] == []
    r = set_op(hide=False)
    assert not world.cube.hide_viewport and not world.cube.hide_render and world.cube.hide_calls == [False]
    assert r["changed"] == [{"field": "hidden", "from": True, "to": False}]
    assert set_op(hide=False)["changed"] == []


def test_op_show_survives_hide_set_failing(world):
    def boom(state):
        raise RuntimeError("no view layer context")

    world.cube.hide_viewport = True
    world.cube.hide_set = boom
    r = set_op(hide=False)
    assert world.cube.hide_viewport is False and r["changed"][0]["to"] is False


def test_op_show_also_reacts_to_view_layer_hiding(world):
    world.cube.hide_get = lambda: True
    r = set_op(hide=False)
    assert r["changed"] == [{"field": "hidden", "from": True, "to": False}] and world.cube.hide_calls == [False]


def test_op_partly_hidden_object_gets_fully_hidden(world):
    world.cube.hide_viewport = True
    r = set_op(hide=True)
    assert world.cube.hide_render is True and r["changed"][0]["field"] == "hidden"
