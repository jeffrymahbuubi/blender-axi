"""0.10.0 U6: `material` with a 4th colour value (Alpha socket) and a glob object positional (command + op level)."""

import shutil
import types
from pathlib import Path

import pytest

from blender_axi import flags as flagparser
from blender_axi.blender_side import op_material
from blender_axi.blender_side.ops import OpError
from blender_axi.commands import material as cmd
from blender_axi.runner import RunOutcome, SaveSpec

WORK = Path(__file__).resolve().parents[2] / ".tmp" / "tests" / "unit_cmd_material_glob"
RESULT = {"object": "Cube", "objects": ["Cube"], "material": "Red", "created": True, "color": [1, 0, 0, 1],
          "metallic": 0.0, "roughness": 0.5, "slots": ["Red"]}


class Fake:
    name = "fake"

    def __init__(self, result=None):
        self.result, self.calls = dict(RESULT if result is None else result), []

    def run(self, op, params, *, blend=None, save=SaveSpec("none"), timeout=None):
        self.calls.append({"op": op, "params": params})
        return RunOutcome(self.result, True, save.path if save.mode != "none" else None, "5.2.0 LTS", 33, "")


@pytest.fixture
def work(monkeypatch):
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(parents=True)
    (WORK / "scene.blend").write_bytes(b"BLEND")
    monkeypatch.chdir(WORK)
    return WORK


def invoke(argv, result=None):
    inv, wants_help = flagparser.parse(cmd.SPEC, argv)
    assert not wants_help
    inv.transport = Fake(result)
    return cmd.run(inv), inv.transport


# -- command level ----------------------------------------------------------------------------------------------------

def test_four_value_color_sets_alpha_param(work):
    _, t = invoke(["Cube", "scene.blend", "--name", "Red", "--color", "1,0,0,0.3", "--in-place"])
    assert t.calls[0]["params"]["alpha"] == 0.3 and t.calls[0]["params"]["color"] == [1.0, 0.0, 0.0, 0.3]
    _, t = invoke(["Cube", "scene.blend", "--name", "Red", "--color", "1,0,0", "--in-place"])
    assert t.calls[0]["params"]["alpha"] is None and t.calls[0]["params"]["color"] == [1.0, 0.0, 0.0, 1.0]
    _, t = invoke(["Cube", "scene.blend", "--name", "Red", "--in-place"])
    assert t.calls[0]["params"]["alpha"] is None


def test_alpha_line_only_below_one(work):
    res, _ = invoke(["Cube", "scene.blend", "--name", "Red", "--color", "1,0,0,0.3", "--in-place"],
                    {**RESULT, "color": [1, 0, 0, 0.3], "alpha": 0.3})
    assert list(res.data)[:6] == ["file", "object", "material", "created", "color", "alpha"]
    assert res.data["alpha"] == "0.3" and res.data["color"] == "1,0,0,0.3"
    for result in ({**RESULT, "alpha": 1.0}, RESULT):
        res, _ = invoke(["Cube", "scene.blend", "--name", "Red", "--color", "1,0,0,1", "--in-place"], result)
        assert "alpha" not in res.data
        assert list(res.data)[:7] == ["file", "object", "material", "created", "color", "metallic", "roughness"]


def test_pattern_prints_objects_and_no_object_line(work):
    names = ["A1", "A2", "A3"]
    res, t = invoke(["A*", "scene.blend", "--name", "Red", "--in-place"],
                    {**RESULT, "object": "A1", "objects": names, "pattern": "A*"})
    assert t.calls[0]["params"]["object"] == "A*"
    assert res.data["objects"] == "3 (A1, A2, A3)" and "object" not in res.data
    assert list(res.data)[:3] == ["file", "objects", "material"]


def test_pattern_with_many_matches_caps_the_names(work):
    names = [f"RegionA{i}" for i in range(1, 33)]
    res, _ = invoke(["Region*", "scene.blend", "--name", "Red", "--in-place"],
                    {**RESULT, "object": names[0], "objects": names, "pattern": "Region*"})
    assert res.data["objects"] == "32 (RegionA1, RegionA2, RegionA3, RegionA4, RegionA5, +27 more)"


def test_plain_name_keeps_the_object_line(work):
    res, _ = invoke(["Cube", "scene.blend", "--name", "Red", "--in-place"])
    assert res.data["object"] == "Cube" and "objects" not in res.data


def test_batch_line_text():
    assert cmd.BATCH.line(RESULT) == "Cube <- Red (created)"
    assert cmd.BATCH.line({**RESULT, "created": False}) == "Cube <- Red (reused)"
    r = {**RESULT, "object": "RegionA1", "objects": [f"RegionA{i}" for i in range(32)], "pattern": "Region*",
         "material": "RegionAMat"}
    assert cmd.BATCH.line(r) == "Region* (32) <- RegionAMat (created)"


def test_help_mentions_glob_and_alpha():
    by_name = {f.name: f for f in cmd.SPEC.flags}
    assert "Alpha" in by_name["--color"].help
    assert "glob" in cmd.SPEC.positionals[0].help


# -- op level (fake bpy) ----------------------------------------------------------------------------------------------

class Socket:
    def __init__(self, value):
        self.default_value = value


class Node:
    type = "BSDF_PRINCIPLED"

    def __init__(self):
        self.inputs = {"Base Color": Socket((0.8, 0.8, 0.8, 1.0)), "Metallic": Socket(0.0), "Roughness": Socket(0.5),
                       "Emission Color": Socket((0.0, 0.0, 0.0, 1.0)), "Emission Strength": Socket(0.0),
                       "Alpha": Socket(1.0)}


class Materials(list):
    def new(self, name):
        mat = types.SimpleNamespace(name=name, node_tree=types.SimpleNamespace(nodes=[Node()]), use_nodes=True,
                                    diffuse_color=(0.8, 0.8, 0.8, 1.0), metallic=0.0, roughness=0.5)
        self.append(mat)
        return mat

    def remove(self, mat):
        super().remove(mat)


def make_object(name, kind="MESH"):
    data = types.SimpleNamespace(materials=[]) if kind == "MESH" else types.SimpleNamespace()
    return types.SimpleNamespace(name=name, type=kind, data=data)


@pytest.fixture
def world(fake_bpy):
    objs = [make_object(n) for n in ("B2", "A2", "A1", "A3", "Ab")] + [make_object("Lamp", "LIGHT")]
    fake_bpy.data = types.SimpleNamespace(objects=objs, materials=Materials(), collections=[])
    return fake_bpy


def material_op(**params):
    base = {"object": "A1", "name": "Red", "color": None, "alpha": None, "metallic": None, "roughness": None,
            "emission": None, "emission_strength": None, "slot": "replace"}
    return op_material.run({**base, **params})


def alpha_socket(world):
    return world.data.materials[0].node_tree.nodes[0].inputs["Alpha"].default_value


def test_op_alpha_written_only_with_alpha(world):
    r = material_op(color=[1, 0, 0, 1])
    assert alpha_socket(world) == 1.0 and "alpha" not in r
    r = material_op(color=[1, 0, 0, 0.25], alpha=0.25)
    assert alpha_socket(world) == 0.25 and r["alpha"] == 0.25 and r["color"] == [1.0, 0.0, 0.0, 0.25]
    assert r["created"] is False


def test_op_alpha_one_resets_the_socket(world):
    material_op(color=[1, 0, 0, 0.25], alpha=0.25)
    material_op(color=[1, 0, 0, 1], alpha=1.0)
    assert alpha_socket(world) == 1.0


def test_op_plain_name_result_has_objects_and_no_pattern(world):
    r = material_op()
    assert r["object"] == "A1" and r["objects"] == ["A1"] and "pattern" not in r


def test_op_pattern_matches_sorted_names_and_shares_one_material(world):
    r = material_op(object="A*")
    assert r["objects"] == ["A1", "A2", "A3", "Ab"] and r["object"] == "A1" and r["pattern"] == "A*"
    assert len(world.data.materials) == 1
    objs = {o.name: o for o in world.data.objects}
    assert all(objs[n].data.materials == [world.data.materials[0]] for n in r["objects"])
    assert objs["B2"].data.materials == []
    assert r["slots"] == ["Red"]


def test_op_pattern_is_case_sensitive_and_supports_question_mark_and_brackets(world):
    with pytest.raises(OpError):
        material_op(object="a*")
    assert material_op(object="A?", name="Two")["objects"] == ["A1", "A2", "A3", "Ab"]
    assert material_op(object="A[12]", name="Three")["objects"] == ["A1", "A2"]


@pytest.mark.parametrize("name,expected", [("A*", True), ("A?", True), ("A[1]", True), ("A1", False), ("Cube.001", False)])
def test_is_pattern(name, expected):
    assert op_material.is_pattern(name) is expected


def test_op_pattern_without_match_is_not_found(world):
    with pytest.raises(OpError) as exc:
        material_op(object="Region*")
    assert exc.value.code == "NOT_FOUND" and exc.value.message == "no object matches 'Region*'"
    assert len(world.data.materials) == 0


def test_op_pattern_hitting_a_light_fails_before_creating_anything(world):
    with pytest.raises(OpError) as exc:
        material_op(object="[AL]*")
    assert exc.value.code == "OP_FAILED" and "Lamp" in exc.value.message
    assert len(world.data.materials) == 0 and world.data.objects[2].data.materials == []


def test_op_plain_missing_name_keeps_the_old_message(world):
    with pytest.raises(OpError) as exc:
        material_op(object="Ghost")
    assert exc.value.code == "NOT_FOUND" and exc.value.message.startswith("object 'Ghost' not found")
