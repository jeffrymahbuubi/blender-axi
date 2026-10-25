"""v1.3 U1: `apply` (command level with a fake transport), the BATCH hooks of add/set/delete/material and `op_batch`."""

import importlib
import io
import json
import shutil
import sys
from pathlib import Path

import pytest

from blender_axi import flags as flagparser
from blender_axi import registry
from blender_axi.blender_side import op_batch, ops
from blender_axi.blender_side.ops import OpError
from blender_axi.commands import add as add_cmd
from blender_axi.commands import apply as cmd
from blender_axi.commands import delete as delete_cmd
from blender_axi.commands import material as material_cmd
from blender_axi.commands import set_cmd
from blender_axi.errors import AxiError
from blender_axi.runner import RunOutcome, SaveSpec
from blender_axi.spec import BatchSpec, CommandSpec, Flag

WORK = Path(__file__).resolve().parents[2] / ".tmp" / "tests" / "unit_cmd_apply"
LIVE = ["--live", "--host", "127.0.0.1", "--port", "9999"]
BATCH = {"add": "blender_axi.commands.add:BATCH", "set": "blender_axi.commands.set_cmd:BATCH",
         "delete": "blender_axi.commands.delete:BATCH", "material": "blender_axi.commands.material:BATCH"}
CANNED = {
    "add": {"name": "Box", "type": "MESH", "collection": "Collection", "location": [0, 0, 0], "dimensions": [2, 2, 2],
            "verts": 8, "faces": 6},
    "set": {"name": "Box", "changed": [{"field": "location", "from": [0, 0, 0], "to": [1, 2, 3]}]},
    "delete": {"deleted": ["Ghost"], "children_kept": [], "remaining": 3},
    "material": {"object": "Box", "material": "Red", "created": True, "color": [1, 0, 0, 1], "metallic": 0,
                 "roughness": 0.5, "slots": ["Red"]},
}


class Fake:
    name = "fake"

    def __init__(self, file="C:\\work\\open.blend", fail=None, **outcome_kw):
        self.file, self.fail, self.calls, self.kw = file, fail, [], outcome_kw

    def run(self, op, params, *, blend=None, save=SaveSpec("none"), timeout=None):
        self.calls.append({"op": op, "params": params, "blend": blend, "save": save, "timeout": timeout})
        if op == "ping":
            return RunOutcome({"file": self.file}, False, None, "5.2", 1, "")
        if self.fail is not None:
            raise self.fail
        result = ({"applied": len(params["steps"]),
                   "results": [{"op": s["op"], "result": CANNED[s["op"]]} for s in params["steps"]]}
                  if op == "batch" else CANNED[op])
        kw = {"modified": True, "saved": save.path if save.mode != "none" else None, **self.kw}
        return RunOutcome(result, kw.pop("modified"), kw.pop("saved"), "5.2.0 LTS", 33, "", **kw)

    @property
    def ops(self):
        return [c["op"] for c in self.calls]


@pytest.fixture(autouse=True)
def _batch_registry(monkeypatch):
    for name, target in BATCH.items():
        monkeypatch.setitem(registry.BATCH, name, target)


@pytest.fixture
def work(monkeypatch):
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(parents=True)
    (WORK / "scene.blend").write_bytes(b"BLEND")
    monkeypatch.chdir(WORK)
    return WORK


def steps_file(work, steps, name="steps.json"):
    (work / name).write_text(json.dumps(steps), encoding="utf-8")
    return name


def invoke(argv, transport=None):
    inv, wants_help = flagparser.parse(cmd.SPEC, argv)
    assert not wants_help
    inv.transport = transport or Fake()
    return cmd.run(inv), inv.transport


def fails(argv, transport=None):
    with pytest.raises(AxiError) as exc:
        invoke(argv, transport)
    return exc.value


def sent(t):
    return [(s["op"], s["params"]) for s in t.calls[0]["params"]["steps"]]


def test_spec_shape():
    assert cmd.SPEC.name == "apply"
    assert [p.name for p in cmd.SPEC.positionals] == ["steps", "file"] and cmd.SPEC.positionals[1].required is False
    assert [f.name for f in cmd.SPEC.flags] == ["--check", "--in-place", "--out", "--force"]
    assert any("--live" in e for e in cmd.SPEC.examples) and all(e.startswith("blender-axi") for e in cmd.SPEC.examples)


@pytest.mark.parametrize("wrap", [False, True], ids=["list", "steps-object"])
def test_document_forms(work, wrap):
    steps = ["add cube --name Box", "delete Ghost"]
    name = steps_file(work, {"steps": steps} if wrap else steps)
    res, t = invoke([name, "scene.blend", "--in-place"])
    assert res.data["steps"] == 2 and res.data["applied"] == 2 and [o for o, _ in sent(t)] == ["add", "delete"]


def test_bad_json_names_line_and_column(work):
    (work / "s.json").write_text('[\n  "add cube",\n  oops\n]', encoding="utf-8")
    t = Fake()
    err = fails(["s.json", "scene.blend", "--in-place"], t)
    assert err.exit_code == 2 and err.code == "VALIDATION_ERROR"
    assert err.message == "steps: invalid JSON at line 3 column 3" and t.calls == []


@pytest.mark.parametrize("doc,message", [([], "no steps"), ({"steps": []}, "no steps"),
                                         ({"cmd": "add"}, 'steps: expected a list of steps or {"steps": [...]}'),
                                         ("add cube", 'steps: expected a list of steps or {"steps": [...]}')])
def test_empty_or_wrong_shape_is_exit_2(work, doc, message):
    name = steps_file(work, doc)
    t = Fake()
    err = fails([name, "scene.blend", "--in-place"], t)
    assert err.exit_code == 2 and err.message == message and t.calls == []


def test_missing_steps_file_is_not_found(work):
    err = fails(["nope.json", "scene.blend", "--in-place"])
    assert err.code == "NOT_FOUND" and err.exit_code == 1 and err.message == "steps file not found: nope.json"


def test_stdin_and_bom(work, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO('["add cube --name Box", "delete Ghost"]'))
    res, t = invoke(["-", "scene.blend", "--in-place"])
    assert res.data["steps"] == 2 and len(sent(t)) == 2
    monkeypatch.setattr(sys, "stdin", io.StringIO('["set Cube"]'))
    assert fails(["-", "scene.blend", "--in-place"]).help[-1] == "Fix step 1 in stdin (nothing ran)"
    (work / "b.json").write_bytes(b"\xef\xbb\xbf" + json.dumps(["delete Ghost"]).encode())
    assert invoke(["b.json", "scene.blend", "--in-place"])[0].data["applied"] == 1


def test_string_step_with_quotes_and_leading_program_name(work):  # ASSUMED A7
    name = steps_file(work, ['blender-axi add cube --name "Big Box" --location 0,0,1', "set 'Big Box' --hide"])
    _, t = invoke([name, "scene.blend", "--in-place"])
    (_, add_params), (_, set_params) = sent(t)
    assert add_params["name"] == "Big Box" and add_params["location"] == [0.0, 0.0, 1.0]
    assert set_params["name"] == "Big Box" and set_params["hide"] is True


def test_unbalanced_quote_is_exit_2(work):
    name = steps_file(work, ['add cube --name "Big'])
    err = fails([name, "scene.blend", "--in-place"])
    assert err.exit_code == 2 and err.message.startswith("step 1: cannot split the step")


def test_object_step_values_and_positional_order(work):
    step = {"location": [0, 0, 1.5], "segments": 8, "cmd": "add", "radius": 0.5, "name": "Ball", "kind": "sphere",
            "collection": None, "type": None}
    name = steps_file(work, [step, {"cmd": "set", "name": "Ball", "hide": True},
                             {"cmd": "set", "name": "Ball", "--name": "Globe", "show": False, "scale": [2, 2, 2]},
                             {"cmd": "delete", "names": ["A", "B"]}])
    res, t = invoke([name, "scene.blend", "--check"])
    assert res.data["plan"] == [
        "1 add sphere --location 0,0,1.5 --segments 8 --radius 0.5 --name Ball",
        "2 set Ball --hide",
        "3 set Ball --name Globe --no-show --scale 2,2,2",
        "4 delete A,B"]
    assert t.calls == []
    _, t = invoke([name, "scene.blend", "--in-place"])
    ((_, add), (_, hide), (_, rename), (_, delete)) = sent(t)
    assert (add["kind"], add["name"], add["segments"], add["radius"], add["collection"]) == ("sphere", "Ball", 8, 0.5, None)
    assert hide["hide"] is True and rename["new_name"] == "Globe" and rename["scale"] == [2.0, 2.0, 2.0]
    assert delete["names"] == ["A", "B"]


def test_object_step_underscore_dash_keys_and_raw_args(work):
    name = steps_file(work, [{"cmd": "material", "object": "Box", "name": "Red", "emission_strength": 4},
                             {"cmd": "material", "object": "Box", "name": "Red", "emission-strength": 5},
                             {"cmd": "set", "name": "Ball", "args": ["--name", "Globe", "--scale", "2,2,2"]},  # name = positional
                             {"cmd": "set", "name": "Ball", "location": [1, 2, 3], "args": ["--hide"]}])
    res, t = invoke([name, "scene.blend", "--check"])
    assert res.data["plan"][2:] == ["3 set Ball --name Globe --scale 2,2,2", "4 set Ball --location 1,2,3 --hide"]
    _, t = invoke([name, "scene.blend", "--in-place"])
    assert [p.get("emission_strength") for _, p in sent(t)[:2]] == [4, 5]
    assert sent(t)[2][1]["name"] == "Ball" and sent(t)[2][1]["new_name"] == "Globe" and sent(t)[3][1]["hide"] is True
    for bad in ("--hide", [["x"]], [True]):
        err = fails([steps_file(work, [{"cmd": "set", "name": "A", "args": bad}]), "scene.blend", "--in-place"])
        assert err.exit_code == 2 and err.message == "step 1: 'args' must be a list of strings and numbers"


def test_object_step_needs_cmd_and_required_positionals(work):
    for step, message in [({"kind": "cube"}, 'step 1: an object step needs "cmd"'),
                          ({"cmd": "add", "name": "X"}, "step 1 (add): missing required argument <kind> for `add`"),
                          (7, "step 1: expected a string or an object, got int"),
                          ({"cmd": "add", "kind": {"a": 1}}, "step 1: the value of 'kind' must be a string, number or list"),
                          ({"cmd": "add", "kind": "cube", "location": [[1], 2, 3]},
                           "step 1: the list for 'location' may hold only strings and numbers")]:
        err = fails([steps_file(work, [step]), "scene.blend", "--in-place"])
        assert err.exit_code == 2 and err.message == message, (step, err.message)


def test_unknown_command_names_the_step(work):
    t = Fake()
    err = fails([steps_file(work, ["add cube", "render scene.blend"]), "scene.blend", "--in-place"], t)
    assert err.exit_code == 2 and t.calls == []
    assert err.message == "step 2: unknown command 'render'; apply supports add, set, delete, material, modifier, configure, import"
    assert err.help[-1] == "Fix step 2 in steps.json (nothing ran)"


@pytest.mark.parametrize("step,offender", [
    ("add cube --in-place", "--in-place"), ("add cube --out x.blend", "--out"), ("add cube --force", "--force"),
    ("add cube --live", "--live"), ("add cube --json", "--json"), ("add cube --timeout 5", "--timeout"),
    ("add cube --user-prefs", "--user-prefs"), ("add cube other.blend", "<file>"),
    ({"cmd": "add", "kind": "cube", "file": "other.blend"}, "<file>"),
    ({"cmd": "add", "kind": "cube", "in_place": True}, "--in-place"), ({"cmd": "delete", "names": "A", "port": 1}, "--port"),
])
def test_forbidden_tokens_exit_2_naming_the_step(work, step, offender):
    t = Fake()
    err = fails([steps_file(work, ["add plane", step]), "scene.blend", "--in-place"], t)
    assert err.exit_code == 2 and err.message == f"step 2: {offender} belongs on apply, not on a step" and t.calls == []
    assert err.help[-1] == "Fix step 2 in steps.json (nothing ran)"


def test_new_flag_and_help_are_refused_in_a_step(work, monkeypatch):
    import types
    # `--new` exists on `import` only (U3); a stand-in spec/BATCH proves apply refuses it before the batch hook runs
    spec = CommandSpec("imp", "stub", (), (Flag("--new", "bool"), *cmd.SPEC.flags), ("blender-axi imp",), lambda inv: None)
    monkeypatch.setitem(registry.COMMANDS, "imp", "tests_apply_stub:SPEC")
    monkeypatch.setitem(registry.BATCH, "imp", "tests_apply_stub:BATCH")
    stub = types.ModuleType("tests_apply_stub")
    stub.SPEC, stub.BATCH = spec, BatchSpec("import", lambda inv: {}, lambda r: "")
    monkeypatch.setitem(sys.modules, "tests_apply_stub", stub)
    err = fails([steps_file(work, ["imp --new"]), "scene.blend", "--in-place"])
    assert err.exit_code == 2 and err.message.startswith("step 1: --new cannot be used in a step")
    err = fails([steps_file(work, ["add cube --help"]), "scene.blend", "--in-place"])
    assert err.exit_code == 2 and err.message == "step 1 (add): --help is not available inside a step"


def test_invalid_step_reuses_the_commands_message_and_never_starts_blender(work):
    t = Fake()
    err = fails([steps_file(work, ["add cube", "set Cube", "delete Ghost"]), "scene.blend", "--in-place"], t)
    assert err.code == "VALIDATION_ERROR" and err.exit_code == 2
    assert err.message == "step 2 (set): set needs at least one change flag"
    assert "Change flags:" in err.help[0] and err.help[-1] == "Fix step 2 in steps.json (nothing ran)"
    err = fails([steps_file(work, ["set Cube"]), "scene.blend"], t)  # no write flag either: the step error wins
    assert err.message.startswith("step 1 (set)") and t.calls == []


def test_flag_parse_errors_are_prefixed_too(work):
    err = fails([steps_file(work, ["add cube --bogus 1"]), "scene.blend", "--in-place"])
    assert err.exit_code == 2 and err.message == "step 1 (add): unknown flag --bogus for `add`"
    err = fails([steps_file(work, ["add cube --segments 3"]), "scene.blend", "--in-place"])
    assert err.message == "step 1 (add): --segments applies to sphere, icosphere, cylinder and cone"


def test_check_prints_the_plan_without_the_transport(work):
    name = steps_file(work, ["add cube --name Box", {"cmd": "set", "name": "Box", "location": [0, 0, 1]}])
    res, t = invoke([name, "scene.blend", "--check"])
    assert res.exit_code == 0 and t.calls == []
    assert list(res.data) == ["file", "steps", "plan", "status"]
    assert res.data == {"file": "scene.blend", "steps": 2, "status": "valid",
                        "plan": ["1 add cube --name Box", "2 set Box --location 0,0,1"]}
    assert res.help == ["Run `blender-axi apply steps.json scene.blend --in-place` to apply them (a .blend1 backup is kept)",
                        "Run `blender-axi apply steps.json scene.blend --out <copy.blend>` to keep the original and write a copy"]


def test_check_with_a_write_flag_validates_it(work):
    res, t = invoke([steps_file(work, ["add cube"]), "scene.blend", "--check", "--in-place"])
    assert res.data["file"] == "scene.blend" and res.help[0].count("--in-place") == 1 and t.calls == []
    err = fails([steps_file(work, ["add cube"]), "scene.blend", "--check", "--in-place", "--out", "x.blend"])
    assert err.exit_code == 2 and "mutually exclusive" in err.message


def test_check_live_never_pings(work):
    res, t = invoke([steps_file(work, ["add cube"]), "--check", *LIVE])
    assert res.data["file"] == "(live session)" and t.calls == []
    assert res.help == ["Run `blender-axi apply steps.json --live --host 127.0.0.1 --port 9999` to apply them to the open session"]


def test_check_without_a_file_follows_the_cwd_rule(work):
    res, _ = invoke([steps_file(work, ["add cube"]), "--check"])
    assert res.data["file"] == "scene.blend"
    (work / "other.blend").write_bytes(b"BLEND")
    assert fails([steps_file(work, ["add cube"]), "--check"]).exit_code == 2


def test_success_sends_one_batch_with_the_per_command_params(work):
    name = steps_file(work, ["add cube --name Box --location 0,0,1", "set Box --rotation 0,0,45",
                             "material Box --name Red --color 1,0,0", "delete Ghost"])
    res, t = invoke([name, "scene.blend", "--in-place"])
    assert t.ops == ["batch"]
    call = t.calls[0]
    assert call["blend"] == (WORK / "scene.blend").resolve() and call["save"] == SaveSpec("in_place", str(call["blend"]))
    inv = lambda spec, argv: flagparser.parse(spec, argv)[0]
    assert sent(t) == [
        ("add", add_cmd.BATCH.params(inv(add_cmd.SPEC, ["cube", "--name", "Box", "--location", "0,0,1"]))),
        ("set", set_cmd.BATCH.params(inv(set_cmd.SPEC, ["Box", "--rotation", "0,0,45"]))),
        ("material", material_cmd.BATCH.params(inv(material_cmd.SPEC, ["Box", "--name", "Red", "--color", "1,0,0"]))),
        ("delete", {"names": ["Ghost"]})]
    assert list(res.data)[:4] == ["file", "steps", "applied", "results"]
    assert res.data["file"] == "scene.blend" and res.data["steps"] == 4 and res.data["applied"] == 4
    assert res.data["results"] == ["1 add: Box (MESH) in Collection", "2 set: Box (location)",
                                   "3 material: Box <- Red (created)", "4 delete: Ghost (remaining 3 objects)"]
    assert res.data["modified"] is True and res.data["saved"].endswith("scene.blend") and res.data["duration_ms"] == 33
    assert res.help[0] == "Run `blender-axi objects scene.blend` to list the objects"
    assert res.help[1] == "Run `blender-axi render scene.blend --engine WORKBENCH --out <path.png>` to preview it"


def test_out_writes_a_copy(work):
    res, t = invoke([steps_file(work, ["delete Ghost"]), "scene.blend", "--out", "copy.blend"])
    assert t.calls[0]["save"] == SaveSpec("copy", str((WORK / "copy.blend").resolve()))
    assert res.data["saved"] == "copy.blend" and res.help[1].startswith("Run `blender-axi render copy.blend")


def test_no_write_flag_is_exit_2_with_both_commands_and_no_blender(work):
    t = Fake()
    err = fails([steps_file(work, ["delete Ghost"]), "scene.blend"], t)
    assert err.exit_code == 2 and t.calls == []
    assert err.help[0] == "Run `blender-axi apply steps.json scene.blend --in-place` to write the file (a .blend1 backup is kept)"
    assert err.help[1].endswith("--out <copy.blend>` to keep the original and write a copy")


def test_results_are_capped_at_20_unless_full(work):
    name = steps_file(work, ["delete Ghost"] * 25)
    res, _ = invoke([name, "scene.blend", "--in-place"])
    assert len(res.data["results"]) == 21 and res.data["steps"] == 25 and res.data["applied"] == 25
    assert res.data["results"][19].startswith("20 delete: ") and res.data["results"][20] == "... (5 more; --full shows all)"
    res, _ = invoke([name, "scene.blend", "--in-place", "--full"])
    assert len(res.data["results"]) == 25 and res.data["results"][24].startswith("25 delete: ")
    res, _ = invoke([steps_file(work, ["delete Ghost"] * 20, "s20.json"), "scene.blend", "--in-place"])
    assert len(res.data["results"]) == 20


def test_default_timeout_is_600_and_overridable(work):
    name = steps_file(work, ["delete Ghost"])
    assert invoke([name, "scene.blend", "--in-place"])[1].calls[0]["timeout"] == 600.0
    assert invoke([name, "scene.blend", "--in-place", "--timeout", "30"])[1].calls[0]["timeout"] == 30.0


def test_headless_failure_says_all_or_nothing(work):
    boom = AxiError("NOT_FOUND", "step 2 (delete): object 'Ghost' not found (the file has 3 objects); nothing deleted",
                    help=["generic"])
    err = fails([steps_file(work, ["add cube", "delete Ghost"]), "scene.blend", "--in-place"], Fake(fail=boom))
    assert err.code == "NOT_FOUND" and err.exit_code == 1 and err.message.startswith("step 2 (delete): object 'Ghost'")
    assert err.help == ["Run `blender-axi objects scene.blend` to list object names",
                        "Nothing was written: the file is unchanged (all-or-nothing)",
                        "Fix the step in steps.json and rerun"]


def test_transport_errors_pass_through_untouched(work):
    boom = AxiError("TIMEOUT", "`batch` timed out after 600s", help=["Raise the limit with `--timeout <seconds>`"])
    err = fails([steps_file(work, ["delete Ghost"]), "scene.blend", "--in-place"], Fake(fail=boom))
    assert err is boom and err.help == ["Raise the limit with `--timeout <seconds>`"]


def test_live_pings_first_and_reports_the_undo_step(work):
    name = steps_file(work, ["add cube", "delete Ghost"])
    res, t = invoke([name, *LIVE], Fake(undo="pushed"))
    assert t.ops == ["ping", "batch"] and t.calls[1]["blend"] is None and t.calls[1]["save"] == SaveSpec("none")
    assert res.data["file"] == "C:/work/open.blend" and res.data["undo"] == "pushed (Ctrl+Z in Blender reverts)"
    assert res.data["saved"] == "none (live session, unsaved)"
    assert res.help[0] == "Run `blender-axi objects --live --host 127.0.0.1 --port 9999` to list the objects"
    assert res.help[-1].startswith("Changes are only in the open session")
    _, t = invoke([name, "--in-place", *LIVE], Fake())  # --in-place saves the open file
    assert t.calls[1]["save"] == SaveSpec("in_place", "C:\\work\\open.blend")


def test_live_rejects_out_and_a_file(work):
    name = steps_file(work, ["delete Ghost"])
    t = Fake()
    err = fails([name, "--out", "x.blend", *LIVE], t)
    assert err.exit_code == 2 and err.message.startswith("live mode has no copy")
    err = fails([name, "scene.blend", *LIVE], t)
    assert err.exit_code == 2 and err.message == "--live uses the file open in Blender; drop <file>"
    assert "batch" not in t.ops


def test_live_failure_says_earlier_steps_stay_applied(work):
    boom = AxiError("NOT_FOUND", "step 2 (delete): object 'Ghost' not found (the file has 3 objects); nothing deleted")
    err = fails([steps_file(work, ["add cube", "delete Ghost"]), *LIVE], Fake(fail=boom))
    assert err.help == ["Run `blender-axi objects --live --host 127.0.0.1 --port 9999` to list object names",
                        "Steps before the failing one were applied to the open session; if Blender pushed an undo step, "
                        "one Ctrl+Z reverts them",
                        "Fix the failing step in steps.json, then apply only the steps after it"]


@pytest.mark.parametrize("module,argv", [
    (add_cmd, ["sphere", "s.blend", "--name", "Ball", "--radius", "0.5", "--rotation", "0,0,90", "--in-place"]),
    (add_cmd, ["light", "s.blend", "--type", "SUN", "--energy", "3", "--collection", "Lights", "--in-place"]),
    (set_cmd, ["Cube", "s.blend", "--location", "1,2,3", "--parent", "NONE", "--hide", "--in-place"]),
    (set_cmd, ["Cube", "s.blend", "--name", "Crate", "--collection", "Props", "--show", "--in-place"]),
    (delete_cmd, ["Cube, Ball,Cube", "s.blend", "--in-place"]),
    (material_cmd, ["Cube", "s.blend", "--name", "Steel", "--color", "0.5,0.5,0.5", "--metallic", "1", "--in-place"]),
    (material_cmd, ["Cube", "s.blend", "--name", "Glow", "--emission", "1,0.8,0.4", "--emission-strength", "10",
                    "--slot", "add", "--in-place"]),
])
def test_batch_params_equal_what_run_sends(work, module, argv):
    (work / "s.blend").write_bytes(b"BLEND")
    inv, _ = flagparser.parse(module.SPEC, argv)
    expected = module.BATCH.params(inv)
    inv, _ = flagparser.parse(module.SPEC, argv)
    inv.transport = Fake()
    module.run(inv)
    assert inv.transport.calls[0]["params"] == expected
    assert module.BATCH.op == module.SPEC.name and callable(module.BATCH.line)


def test_batch_line_texts():
    assert add_cmd.BATCH.line(CANNED["add"]) == "Box (MESH) in Collection"
    assert set_cmd.BATCH.line(CANNED["set"]) == "Box (location)"
    assert set_cmd.BATCH.line({"name": "Box", "changed": [{"field": "name"}, {"field": "hide"}]}) == "Box (name, hide)"
    assert set_cmd.BATCH.line({"name": "Box", "changed": []}) == "Box (no change)"
    assert delete_cmd.BATCH.line(CANNED["delete"]) == "Ghost (remaining 3 objects)"
    assert delete_cmd.BATCH.line({"deleted": ["A", "B"], "remaining": 1}) == "A, B (remaining 1 object)"
    assert delete_cmd.BATCH.line({"deleted": ["A"], "remaining": 0}) == "A (remaining 0 objects)"
    assert material_cmd.BATCH.line(CANNED["material"]) == "Box <- Red (created)"
    assert material_cmd.BATCH.line({**CANNED["material"], "created": False}) == "Box <- Red (reused)"


def test_batch_param_errors_are_the_commands_own():
    for module, argv, message in [(add_cmd, ["blob"], "unknown kind 'blob'; one of"),
                                  (set_cmd, ["Cube"], "set needs at least one change flag"),
                                  (delete_cmd, [" , "], "no object names given"),
                                  (material_cmd, ["Cube", "--name", "R", "--color", "2,0,0"], "--color values must be between 0 and 1")]:
        inv, _ = flagparser.parse(module.SPEC, argv)
        with pytest.raises(AxiError) as exc:
            module.BATCH.params(inv)
        assert exc.value.exit_code == 2 and exc.value.message.startswith(message)


def test_every_registered_batch_entry_resolves_to_a_batchspec():
    """Auto-covers modifier, import and configure once the lead has filled registry.BATCH."""
    for name, target in registry.BATCH.items():
        module, _, attr = target.partition(":")
        batch = getattr(importlib.import_module(module), attr)
        assert isinstance(batch, BatchSpec) and callable(batch.params) and callable(batch.line), name
        assert name in registry.COMMANDS and batch.op in op_batch.ALLOWED, name


@pytest.fixture
def op_log(monkeypatch):
    log = []

    def fake_run(op, params):
        log.append((op, params))
        if params.get("explode"):
            raise OpError(params["explode"], "object 'Ghost' not found")
        return {"echo": op}

    monkeypatch.setattr(ops, "run", fake_run)
    return log


def test_op_batch_runs_steps_in_order(op_log):
    out = op_batch.run({"steps": [{"op": "add", "params": {"kind": "cube"}}, {"op": "set", "params": {"name": "Cube"}},
                                  {"op": "delete"}]})
    assert op_log == [("add", {"kind": "cube"}), ("set", {"name": "Cube"}), ("delete", {})]
    assert out == {"applied": 3, "results": [{"op": "add", "result": {"echo": "add"}},
                                             {"op": "set", "result": {"echo": "set"}},
                                             {"op": "delete", "result": {"echo": "delete"}}]}


def test_op_batch_reraises_the_failing_step_with_its_code(op_log):
    with pytest.raises(OpError) as exc:
        op_batch.run({"steps": [{"op": "add", "params": {}}, {"op": "set", "params": {"explode": "NOT_FOUND"}},
                                {"op": "delete", "params": {}}]})
    assert exc.value.code == "NOT_FOUND" and exc.value.message == "step 2 (set): object 'Ghost' not found"
    assert [o for o, _ in op_log] == ["add", "set"]  # step 3 never ran


def test_op_batch_refuses_a_disallowed_op_before_step_1(op_log):
    for bad in ("render", "exec", "batch", "export", None):
        with pytest.raises(OpError) as exc:
            op_batch.run({"steps": [{"op": "add", "params": {}}, {"op": bad, "params": {}}]})
        assert exc.value.code == "OP_FAILED" and exc.value.message.startswith(f"step 2: op {bad!r} is not allowed")
    assert op_log == []


def test_op_batch_needs_steps_and_has_the_plans_allowed_set(op_log):
    for steps in (None, [], "add", {}):
        with pytest.raises(OpError) as exc:
            op_batch.run({"steps": steps})
        assert exc.value.code == "OP_FAILED" and op_log == []
    assert set(op_batch.ALLOWED) == {"add", "set", "delete", "material", "modifier", "import", "configure"}

