"""v1.2 U3: `material` (command level with a fake transport) and `op_material` (fake `bpy`)."""

import shutil
import types
from pathlib import Path

import pytest

from blender_axi import flags as flagparser
from blender_axi.blender_side import op_material
from blender_axi.blender_side.ops import OpError
from blender_axi.commands import material as cmd
from blender_axi.errors import AxiError
from blender_axi.runner import RunOutcome, SaveSpec

WORK = Path(__file__).resolve().parents[2] / ".tmp" / "tests" / "unit_cmd_material"
LIVE = ["--live", "--host", "127.0.0.1", "--port", "9999"]
RESULT = {"object": "Cube", "material": "Red", "created": True, "color": [1, 0, 0, 1], "metallic": 0.0,
          "roughness": 0.5, "slots": ["Red"]}


class Fake:
    name = "fake"

    def __init__(self, result=None, file="C:\\work\\open.blend", **outcome_kw):
        self.result = dict(RESULT if result is None else result)
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


# -- spec -------------------------------------------------------------------------------------------------------------

def test_spec_shape():
    assert cmd.SPEC.name == "material"
    assert [p.name for p in cmd.SPEC.positionals] == ["object", "file"] and cmd.SPEC.positionals[1].required is False
    assert [f.name for f in cmd.SPEC.flags] == [
        "--name", "--color", "--metallic", "--roughness", "--emission", "--emission-strength", "--slot",
        "--in-place", "--out", "--force"]
    by_name = {f.name: f for f in cmd.SPEC.flags}
    assert by_name["--name"].required is True and by_name["--slot"].choices == ("replace", "add")
    assert by_name["--slot"].default == "replace"


def test_name_is_required_by_the_parser():
    with pytest.raises(AxiError) as exc:
        flagparser.parse(cmd.SPEC, ["Cube", "x.blend", "--in-place"])
    assert exc.value.exit_code == 2 and "--name" in exc.value.message


def test_slot_choices_are_enforced_by_the_parser():
    with pytest.raises(AxiError) as exc:
        flagparser.parse(cmd.SPEC, ["Cube", "x.blend", "--name", "R", "--slot", "all"])
    assert exc.value.exit_code == 2 and "replace, add" in exc.value.message


# -- validation -------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("argv,message", [
    (["--color", "1,0"], "--color expects 3 or 4 comma-separated numbers"),
    (["--color", "1,0,0,1,1"], "--color expects 3 or 4 comma-separated numbers"),
    (["--color", "red"], "--color expects 3 or 4 comma-separated numbers"),
    (["--color", "1.5,0,0"], "--color values must be between 0 and 1, got 1.5,0,0"),
    (["--color", "0,-0.1,0"], "--color values must be between 0 and 1"),
    (["--color", "0,0,0,2"], "--color values must be between 0 and 1"),
    (["--metallic", "1.5"], "--metallic must be between 0 and 1, got 1.5"),
    (["--roughness", "-0.1"], "--roughness must be between 0 and 1, got -0.1"),
    (["--emission", "1,2"], "--emission expects x,y,z"),
    (["--emission", "1,1,4"], "--emission values must be between 0 and 1"),
    (["--emission-strength", "-1"], "--emission-strength must be 0 or more, got -1"),
])
def test_value_errors_exit_2_without_requests(work, argv, message):
    t = Fake()
    err = fails(["Cube", "scene.blend", "--name", "Red", *argv, "--in-place"], t)
    assert err.code == "VALIDATION_ERROR" and err.exit_code == 2 and t.calls == []
    assert message in err.message


def test_empty_name_exit_2(work):
    t = Fake()
    err = fails(["Cube", "scene.blend", "--name", "  ", "--in-place"], t)
    assert err.exit_code == 2 and err.message == "--name is empty" and t.calls == []


def test_boundaries_are_allowed(work):
    _, t = invoke(["Cube", "scene.blend", "--name", "R", "--color", "0,1,0.5,0", "--metallic", "1", "--roughness", "0",
                   "--emission", "0,0,1", "--emission-strength", "100", "--in-place"])
    assert t.ops == ["material"]


# -- params and result ------------------------------------------------------------------------------------------------

def test_params_defaults(work):
    _, t = invoke(["Cube", "scene.blend", "--name", "Red", "--in-place"])
    call = t.calls[0]
    assert call["op"] == "material" and call["params"] == {
        "object": "Cube", "name": "Red", "color": None, "metallic": None, "roughness": None, "emission": None,
        "emission_strength": None, "slot": "replace"}
    assert call["blend"] == (WORK / "scene.blend").resolve() and call["save"] == SaveSpec("in_place", str(call["blend"]))


def test_params_color_is_padded_to_four(work):
    _, t = invoke(["Cube", "scene.blend", "--name", "Red", "--color", "1,0,0", "--in-place"])
    assert t.calls[0]["params"]["color"] == [1.0, 0.0, 0.0, 1.0]
    _, t = invoke(["Cube", "scene.blend", "--name", "Red", "--color", "1,0,0,0.25", "--in-place"])
    assert t.calls[0]["params"]["color"] == [1.0, 0.0, 0.0, 0.25]


def test_params_passthrough(work):
    _, t = invoke(["Cube", "scene.blend", "--name", "Glow", "--metallic", "0.5", "--roughness", "0.25",
                   "--emission", "1,0.8,0.4", "--emission-strength", "10", "--slot", "add", "--out", "o.blend"])
    assert t.calls[0]["params"] == {
        "object": "Cube", "name": "Glow", "color": None, "metallic": 0.5, "roughness": 0.25,
        "emission": [1.0, 0.8, 0.4], "emission_strength": 10.0, "slot": "add"}
    assert t.calls[0]["save"] == SaveSpec("copy", str((WORK / "o.blend").resolve()))


def test_slot_is_case_insensitive(work):
    _, t = invoke(["Cube", "scene.blend", "--name", "R", "--slot", "ADD", "--in-place"])
    assert t.calls[0]["params"]["slot"] == "add"


def test_data_order_and_hints_headless(work):
    res, _ = invoke(["Cube", "scene.blend", "--name", "Red", "--color", "1,0,0", "--in-place"],
                    Fake({**RESULT, "color": [1, 0, 0, 1], "metallic": 0.25, "roughness": 0.5, "slots": ["Red", "Wood"]}))
    assert list(res.data) == ["file", "object", "material", "created", "color", "metallic", "roughness", "slots",
                              "modified", "saved", "duration_ms"]
    d = res.data
    assert d["file"] == "scene.blend" and d["object"] == "Cube" and d["material"] == "Red" and d["created"] is True
    assert d["color"] == "1,0,0,1" and d["metallic"] == "0.25" and d["roughness"] == "0.5"
    assert d["slots"] == "2 (Red, Wood)" and d["modified"] is True
    assert res.help == [
        "Run `blender-axi objects scene.blend` to list the objects",
        "Run `blender-axi render scene.blend --engine WORKBENCH --out <path.png>` to preview it"]


def test_unmodified_reports_no_changes(work):
    res, _ = invoke(["Cube", "scene.blend", "--name", "Red", "--in-place"], Fake(modified=False, saved=None))
    assert res.data["modified"] is False and res.data["saved"] == "none (no changes)"


def test_out_hints_use_the_written_copy(work):
    res, _ = invoke(["Cube", "scene.blend", "--name", "Red", "--out", "copy.blend"])
    assert res.data["saved"] == "copy.blend"
    assert res.help[1] == "Run `blender-axi render copy.blend --engine WORKBENCH --out <path.png>` to preview it"


def test_hints_carry_user_prefs(work):
    res, _ = invoke(["Cube", "scene.blend", "--name", "Red", "--in-place", "--user-prefs"])
    assert all(h.split("`")[1].endswith("--user-prefs") for h in res.help)


def test_neither_flag_exit_2_with_both_commands(work):
    t = Fake()
    err = fails(["Cube", "scene.blend", "--name", "Red", "--color", "1,0,0"], t)
    assert err.exit_code == 2 and t.calls == []
    assert err.help[0] == ("Run `blender-axi material Cube scene.blend --name Red --color 1,0,0 --in-place` "
                           "to write the file (a .blend1 backup is kept)")
    assert err.help[1] == ("Run `blender-axi material Cube scene.blend --name Red --color 1,0,0 --out <copy.blend>` "
                           "to keep the original and write a copy")


def test_object_not_found_gets_an_actionable_hint(work):
    class Missing(Fake):
        def run(self, op, params, **kw):
            raise AxiError("NOT_FOUND", "object 'Cub' not found (did you mean: Cube?)", help=["generic"])
    err = fails(["Cub", "scene.blend", "--name", "Red", "--in-place"], Missing())
    assert err.code == "NOT_FOUND" and err.help == ["Run `blender-axi objects scene.blend` to list the objects"]


def test_op_failed_passes_through(work):
    class Bad(Fake):
        def run(self, op, params, **kw):
            raise AxiError("OP_FAILED", "'Light' (LIGHT) cannot take a material")
    err = fails(["Light", "scene.blend", "--name", "X", "--in-place"], Bad())
    assert err.code == "OP_FAILED" and err.exit_code == 1


# -- live -------------------------------------------------------------------------------------------------------------

def test_live_unsaved_change():
    res, t = invoke(["Cube", *LIVE, "--name", "Blue", "--color", "0,0,1"], Fake(undo="unavailable", saved=None))
    assert t.ops == ["ping", "material"] and t.calls[1]["blend"] is None and t.calls[1]["save"] == SaveSpec("none")
    d = res.data
    assert d["file"] == "C:/work/open.blend" and d["modified"] is True
    assert d["saved"] == "none (live session, unsaved)" and d["undo"] == "unavailable (headless server)"
    assert res.help[0] == "Run `blender-axi objects --live --host 127.0.0.1 --port 9999` to list the objects"
    assert res.help[1] == ("Run `blender-axi render --engine WORKBENCH --out <path.png> --live --host 127.0.0.1 "
                           "--port 9999` to preview it")
    assert res.help[-1].startswith("Changes are only in the open session")


def test_live_refusals():
    for argv in (["Cube", "x.blend", *LIVE, "--name", "R"], ["Cube", *LIVE, "--name", "R", "--out", "o.blend"]):
        t = Fake()
        err = fails(argv, t)
        assert err.exit_code == 2 and t.calls == []


# -- op_material (fake bpy) -------------------------------------------------------------------------------------------

class Socket:
    def __init__(self, value):
        self.default_value = value


class Node:
    def __init__(self, kind="BSDF_PRINCIPLED"):
        self.type = kind
        self.inputs = {"Base Color": Socket((0.8, 0.8, 0.8, 1.0)), "Metallic": Socket(0.0), "Roughness": Socket(0.5),
                       "Emission Color": Socket((0.0, 0.0, 0.0, 1.0)), "Emission Strength": Socket(0.0)}


def make_material(name, nodes=None):
    nodes = [Node("OUTPUT_MATERIAL"), Node()] if nodes is None else nodes
    return types.SimpleNamespace(name=name, node_tree=types.SimpleNamespace(nodes=nodes), use_nodes=True,
                                 diffuse_color=(0.8, 0.8, 0.8, 1.0), metallic=0.0, roughness=0.5)


class Materials(list):
    def __init__(self, items=()):
        super().__init__(items)
        self.removed = []

    def new(self, name):
        mat = make_material(name)
        self.append(mat)
        return mat

    def remove(self, mat):
        self.removed.append(mat)
        super().remove(mat)


def make_object(name="Cube", kind="MESH", slots=()):
    data = types.SimpleNamespace(materials=list(slots)) if kind in ("MESH", "CURVE") else (
        types.SimpleNamespace() if kind in ("LIGHT", "CAMERA") else None)
    return types.SimpleNamespace(name=name, type=kind, data=data)


@pytest.fixture
def world(fake_bpy):
    fake_bpy.data = types.SimpleNamespace(objects=[make_object()], materials=Materials(), collections=[])
    return types.SimpleNamespace(bpy=fake_bpy, cube=fake_bpy.data.objects[0],
                                 materials=fake_bpy.data.materials)


def material_op(**params):
    base = {"object": "Cube", "name": "Red", "color": None, "metallic": None, "roughness": None, "emission": None,
            "emission_strength": None, "slot": "replace"}
    return op_material.run({**base, **params})


def principled(mat):
    return next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")


def test_op_creates_the_material_and_sets_the_given_values(world):
    r = material_op(color=[1, 0, 0, 1], metallic=0.25, roughness=0.75, emission=[1, 0.5, 0], emission_strength=4)
    mat = world.materials[0]
    node = principled(mat)
    assert r == {"object": "Cube", "material": "Red", "created": True, "color": [1.0, 0.0, 0.0, 1.0],
                 "metallic": 0.25, "roughness": 0.75, "slots": ["Red"]}
    assert node.inputs["Base Color"].default_value == (1.0, 0.0, 0.0, 1.0)
    assert (node.inputs["Metallic"].default_value, node.inputs["Roughness"].default_value) == (0.25, 0.75)
    assert node.inputs["Emission Color"].default_value == (1.0, 0.5, 0.0, 1.0)
    assert node.inputs["Emission Strength"].default_value == 4.0
    assert world.cube.data.materials == [mat]


def test_op_also_sets_the_viewport_fields(world):
    material_op(color=[0, 0, 1, 0.5], metallic=1, roughness=0.1)
    mat = world.materials[0]
    assert mat.diffuse_color == (0.0, 0.0, 1.0, 0.5) and mat.metallic == 1.0 and mat.roughness == 0.1


def test_op_sets_only_what_is_given(world):
    r = material_op(roughness=0.2)
    node = principled(world.materials[0])
    assert node.inputs["Base Color"].default_value == (0.8, 0.8, 0.8, 1.0)
    assert node.inputs["Metallic"].default_value == 0.0 and node.inputs["Emission Strength"].default_value == 0.0
    assert node.inputs["Emission Color"].default_value == (0.0, 0.0, 0.0, 1.0)
    assert world.materials[0].diffuse_color == (0.8, 0.8, 0.8, 1.0) and world.materials[0].metallic == 0.0
    assert r["color"] == [0.8, 0.8, 0.8, 1.0] and r["roughness"] == 0.2


def test_op_reuses_an_existing_material(world):
    existing = make_material("Red")
    world.materials.append(existing)
    r = material_op(roughness=0.1)
    assert r["created"] is False and len(world.materials) == 1
    assert principled(existing).inputs["Roughness"].default_value == 0.1 and world.cube.data.materials == [existing]


def test_op_replace_swaps_slot_zero_and_keeps_the_rest(world):
    old, other = make_material("Old"), make_material("Other")
    world.cube.data.materials = [old, other]
    r = material_op()
    assert r["slots"] == ["Red", "Other"] and world.cube.data.materials[1] is other


def test_op_replace_fills_an_empty_slot_list(world):
    assert material_op()["slots"] == ["Red"]
    world.cube.data.materials = [None]
    assert material_op()["slots"] == ["Red"] and len(world.cube.data.materials) == 1


def test_op_add_appends_and_does_not_duplicate(world):
    first = make_material("Wood")
    world.cube.data.materials = [first]
    r = material_op(slot="add")
    assert r["slots"] == ["Wood", "Red"]
    assert material_op(slot="add")["slots"] == ["Wood", "Red"]


def test_op_light_camera_and_empty_cannot_take_a_material(world):
    for kind in ("LIGHT", "CAMERA", "EMPTY"):
        world.bpy.data.objects.append(make_object("X" + kind, kind))
        with pytest.raises(OpError) as exc:
            material_op(object="X" + kind)
        assert exc.value.code == "OP_FAILED" and exc.value.message == f"'X{kind}' ({kind}) cannot take a material"
    assert len(world.materials) == 0  # nothing was created for the refused objects


def test_op_missing_object_is_not_found(world):
    with pytest.raises(OpError) as exc:
        material_op(object="Cub")
    assert exc.value.code == "NOT_FOUND" and "Cub" in exc.value.message and "Cube" in exc.value.message
    assert len(world.materials) == 0


def test_op_reused_material_without_principled_fails_untouched(world):
    broken = make_material("Red", nodes=[Node("OUTPUT_MATERIAL")])
    world.materials.append(broken)
    with pytest.raises(OpError) as exc:
        material_op(color=[1, 0, 0, 1])
    assert exc.value.code == "OP_FAILED" and "Principled" in exc.value.message
    assert world.materials == [broken] and world.cube.data.materials == [] and broken.diffuse_color == (0.8, 0.8, 0.8, 1.0)


def test_op_builds_the_node_tree_when_a_reused_material_has_none(world):
    class Bare:  # enabling use_nodes builds the default tree, as Blender does
        name, node_tree, diffuse_color, metallic, roughness = "Red", None, (0.8, 0.8, 0.8, 1.0), 0.0, 0.5

        @property
        def use_nodes(self):
            return self.node_tree is not None

        @use_nodes.setter
        def use_nodes(self, value):
            self.node_tree = make_material("t").node_tree

    bare = Bare()
    world.materials.append(bare)
    assert material_op(roughness=0.3)["created"] is False and bare.node_tree is not None


def test_op_missing_socket_is_op_failed_and_removes_a_new_material(world, monkeypatch):
    original = Materials.new

    def new_without_emission(self, name):
        mat = original(self, name)
        del principled(mat).inputs["Emission Color"]
        return mat
    monkeypatch.setattr(Materials, "new", new_without_emission)
    with pytest.raises(OpError) as exc:
        material_op()
    assert exc.value.code == "OP_FAILED" and "Emission Color" in exc.value.message
    assert len(world.materials) == 0 and len(world.materials.removed) == 1
