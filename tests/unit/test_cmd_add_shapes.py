"""0.10.0 U4: `add torus`, the torus flags and `add cube --dimensions` (command level, fake transport)."""

import json
import shutil
from pathlib import Path

import pytest

from blender_axi import cli
from blender_axi import flags as flagparser
from blender_axi.commands import add as cmd
from blender_axi.commands import apply as apply_cmd
from blender_axi.errors import AxiError
from blender_axi.runner import RunOutcome, SaveSpec

WORK = Path(__file__).resolve().parents[2] / ".tmp" / "tests" / "unit_cmd_add_shapes"
RESULT = {"name": "Ring", "type": "MESH", "kind": "torus", "collection": "Collection", "location": [0, 0, 0],
          "dimensions": [2.5, 2.5, 0.5], "verts": 576, "faces": 576, "scene_camera_set": False, "scene_camera": None}


class Fake:
    name = "fake"

    def __init__(self):
        self.calls = []

    def run(self, op, params, *, blend=None, save=SaveSpec("none"), timeout=None):
        self.calls.append({"op": op, "params": params})
        return RunOutcome(dict(RESULT), True, save.path if save.mode != "none" else None, "5.2.0 LTS", 33, "")


@pytest.fixture
def work(monkeypatch):
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(parents=True)
    (WORK / "scene.blend").write_bytes(b"BLEND")
    monkeypatch.chdir(WORK)
    return WORK


def invoke(argv):
    inv, wants_help = flagparser.parse(cmd.SPEC, argv)
    assert not wants_help
    inv.transport = Fake()
    return cmd.run(inv), inv.transport


def fails(argv):
    with pytest.raises(AxiError) as exc:
        invoke(argv)
    return exc.value


def test_torus_params_reach_the_op(work):
    res, t = invoke(["torus", "scene.blend", "--radius", "2", "--minor-radius", "0.3", "--segments", "24",
                     "--minor-segments", "8", "--in-place"])
    p = t.calls[0]["params"]
    assert p["kind"] == "torus" and p["radius"] == 2.0 and p["minor_radius"] == 0.3
    assert p["segments"] == 24 and p["minor_segments"] == 8 and p["dimensions"] is None
    assert res.data["added"] == "Ring" and res.data["verts"] == 576 and res.data["dimensions"] == "2.5,2.5,0.5"


def test_torus_defaults_are_left_to_the_op(work):
    _, t = invoke(["torus", "scene.blend", "--in-place"])
    p = t.calls[0]["params"]
    assert p["kind"] == "torus" and p["minor_radius"] is None and p["minor_segments"] is None and p["segments"] is None


@pytest.mark.parametrize("kind,flag,value,message", [
    ("cube", "--minor-radius", "0.3", "--minor-radius applies to torus"),
    ("sphere", "--minor-segments", "8", "--minor-segments applies to torus"),
    ("sphere", "--dimensions", "1,2,3", "--dimensions applies to cube"),
    ("plane", "--dimensions", "1,2,3", "--dimensions applies to cube"),
])
def test_torus_and_dimensions_flags_are_per_kind(work, kind, flag, value, message):
    err = fails([kind, "scene.blend", flag, value, "--in-place"])
    assert err.exit_code == 2 and err.message == message


@pytest.mark.parametrize("argv,message", [
    (["cube", "--dimensions", "1,2,3", "--size", "2"], "--dimensions replaces --size"),
    (["cube", "--dimensions", "0,1,1"], "--dimensions must all be greater than 0, got 0,1,1"),
    (["cube", "--dimensions", "1,-2,1"], "--dimensions must all be greater than 0, got 1,-2,1"),
    (["cube", "--dimensions", "1,2"], "--dimensions expects x,y,z, got '1,2'"),
    (["torus", "--minor-radius", "0"], "--minor-radius must be greater than 0, got 0"),
    (["torus", "--minor-segments", "2"], "--minor-segments must be 3-512, got 2"),
    (["torus", "--minor-segments", "513"], "--minor-segments must be 3-512, got 513"),
    (["torus", "--segments", "2"], "--segments must be 3-512 for torus, got 2"),
])
def test_value_errors_exit_2(work, argv, message):
    err = fails([*argv, "scene.blend", "--in-place"])
    assert err.code == "VALIDATION_ERROR" and err.exit_code == 2 and err.message == message


def test_dimensions_reach_the_op_as_floats(work):
    _, t = invoke(["cube", "scene.blend", "--dimensions", "14,4.4,0.5", "--scale", "1,1,2", "--in-place"])
    p = t.calls[0]["params"]
    assert p["dimensions"] == [14.0, 4.4, 0.5] and p["scale"] == [1.0, 1.0, 2.0] and p["size"] is None


def test_help_lists_torus_and_the_three_flags(capsys):
    assert cli.main(["add", "--help"]) == 0
    out = capsys.readouterr().out
    assert "torus" in out and "--minor-radius" in out and "--minor-segments" in out and "--dimensions" in out
    assert "ring radius of a torus" in out and "ring segments of a torus" in out


def check(work, steps):
    (work / "steps.json").write_text(json.dumps(steps), encoding="utf-8")
    inv, wants_help = flagparser.parse(apply_cmd.SPEC, ["steps.json", "scene.blend", "--check"])
    assert not wants_help
    inv.transport = Fake()
    return apply_cmd.run(inv), inv.transport


def test_apply_check_accepts_the_new_step_keys(work):
    res, t = check(work, [{"cmd": "add", "kind": "torus", "minor_radius": 0.3},
                          {"cmd": "add", "kind": "cube", "dimensions": [14, 4.4, 0.5]}])
    assert res.data["steps"] == 2 and res.data["commands"] == "add 2" and t.calls == []
    assert any("--minor-radius 0.3" in line for line in res.data["plan"] or [])
    assert any("--dimensions 14,4.4,0.5" in line for line in res.data["plan"] or [])


def test_apply_check_rejects_dimensions_on_a_sphere(work):
    (work / "steps.json").write_text(json.dumps([{"cmd": "add", "kind": "sphere", "dimensions": [1, 2, 3]}]),
                                     encoding="utf-8")
    inv, _ = flagparser.parse(apply_cmd.SPEC, ["steps.json", "scene.blend", "--check"])
    inv.transport = Fake()
    with pytest.raises(AxiError) as exc:
        apply_cmd.run(inv)
    assert exc.value.message == "step 1 (add): --dimensions applies to cube"
