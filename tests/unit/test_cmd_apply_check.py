"""0.5.0 U2: `apply --check` without a file, the summary-first output, unknown top-level keys and `--strict`."""

import json
import shutil
from pathlib import Path

import pytest

from blender_axi import flags as flagparser
from blender_axi.blender_side import op_add
from blender_axi.commands import apply as cmd
from blender_axi.commands import modifier as modifier_cmd
from blender_axi.errors import AxiError
from blender_axi.runner import RunOutcome, SaveSpec

WORK = Path(__file__).resolve().parents[2] / ".tmp" / "tests" / "unit_cmd_apply_check"
LIVE = ["--live", "--host", "127.0.0.1", "--port", "9999"]
LISTING = {"objects": [{"name": "Ring", "type": "MESH"}, {"name": "Hole", "type": "MESH"}, {"name": "Cam", "type": "CAMERA"}],
           "collections": ["Scene Collection", "Parts"], "materials": ["Red"], "scene_camera": "Cam"}


class Fake:
    name = "fake"

    def __init__(self, listing=None):
        self.listing, self.calls = listing or LISTING, []

    def run(self, op, params, *, blend=None, save=SaveSpec("none"), timeout=None):
        self.calls.append({"op": op, "params": params, "blend": blend, "save": save})
        if op == "ping":
            return RunOutcome({"file": "C:\\work\\open.blend"}, False, None, "5.2", 1, "")
        if op == "names":
            return RunOutcome(self.listing, False, None, "5.2.0 LTS", 5, "")
        canned = {"name": "X", "type": "MESH", "collection": "C", "changed": [], "deleted": ["X"], "remaining": 1,
                  "object": "X", "material": "M", "created": True, "modifier": "Mod", "added": ["X"],
                  "source_name": "m.glb", "scene": "Scene"}
        results = [{"op": s["op"], "result": canned} for s in params["steps"]]
        return RunOutcome({"applied": len(results), "results": results}, True, save.path, "5.2.0 LTS", 33, "")

    @property
    def ops(self):
        return [c["op"] for c in self.calls]


@pytest.fixture
def work(monkeypatch):
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(parents=True)
    (WORK / "scene.blend").write_bytes(b"BLEND")
    (WORK / "m.glb").write_bytes(b"glTF")
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
    transport = transport or Fake()
    with pytest.raises(AxiError) as exc:
        invoke(argv, transport)
    return exc.value, transport


# -- --check without a file -------------------------------------------------------------------------------------------

def test_missing_file_is_labelled_not_found_and_the_hint_uses_a_placeholder(work):
    res, t = invoke([steps_file(work, ["add cube"]), "missing.blend", "--check"])
    assert res.exit_code == 0 and t.calls == []
    assert res.data["file"] == "missing.blend (not found)"
    assert res.help == ["Run `blender-axi apply steps.json <file.blend> --in-place` to apply them"]


def test_no_file_and_no_single_blend_is_none(work):
    (work / "scene.blend").unlink()
    res, t = invoke([steps_file(work, ["add cube"]), "--check"])
    assert res.data["file"] == "(none)" and t.calls == []
    assert res.help == ["Run `blender-axi apply steps.json <file.blend> --in-place` to apply them"]


def test_single_cwd_file_keeps_the_hint_as_typed(work):
    res, _ = invoke([steps_file(work, ["add cube"]), "--check"])
    assert res.data["file"] == "scene.blend"
    assert res.help == ["Run `blender-axi apply steps.json --in-place` to apply them"]


def test_check_hint_drops_the_check_strict_and_force_flags_and_keeps_the_rest(work):
    res, t = invoke([steps_file(work, ["add cube"]), "scene.blend", "--check", "--strict", "--out", "x.blend", "--force"], Fake())
    assert res.help == ["Run `blender-axi apply steps.json scene.blend --in-place` to apply them"]
    assert t.ops == ["names"]


def test_check_live_label_and_hint(work):
    res, t = invoke([steps_file(work, ["add cube"]), "--check", *LIVE])
    assert res.data["file"] == "(live session)" and t.calls == []
    assert len(res.help) == 1 and res.help[0].endswith("--live --host 127.0.0.1 --port 9999` to apply them to the open session")
    err, _ = fails([steps_file(work, ["add cube"]), "scene.blend", "--check", *LIVE])
    assert err.exit_code == 2 and err.message == "--live uses the file open in Blender; drop <file>"


def test_check_data_order_commands_and_one_hint(work):
    steps = ["add cube --name A", "set A --hide", "add sphere --name B", "material A --name Red --color 1,0,0",
             "modifier A subdivision", "import m.glb", "delete B"]
    res, t = invoke([steps_file(work, steps), "scene.blend", "--check"])
    assert list(res.data) == ["file", "steps", "commands", "targets", "plan", "status"]
    assert res.data["steps"] == 7 and res.data["commands"] == "add 2, set 1, delete 1, material 1, modifier 1, import 1"
    assert res.data["plan"][0] == "1 add cube --name A" and len(res.data["plan"]) == 7 and res.data["status"] == "valid"
    assert len(res.help) == 1 and t.calls == []


def test_check_plan_is_capped_at_20_lines_unless_full(work):
    name = steps_file(work, ["delete Ghost"] * 25)
    res, _ = invoke([name, "scene.blend", "--check"])
    assert len(res.data["plan"]) == 21 and res.data["plan"][19].startswith("20 delete Ghost")
    assert res.data["plan"][20] == "... (5 more; --full shows all)"
    res, _ = invoke([name, "scene.blend", "--check", "--full"])
    assert len(res.data["plan"]) == 25
    res, _ = invoke([steps_file(work, ["delete Ghost"] * 20, "s20.json"), "scene.blend", "--check"])
    assert len(res.data["plan"]) == 20


def test_check_with_in_place_still_needs_the_file(work):
    err, _ = fails([steps_file(work, ["add cube"]), "missing.blend", "--check", "--in-place"])
    assert err.code == "NOT_FOUND"


# -- unknown top-level keys ---------------------------------------------------------------------------------------------

def test_unknown_top_level_keys_warn_by_default(work):
    doc = {"steps": ["add cube"], "typo": 1, "export": {}}
    res, _ = invoke([steps_file(work, doc), "scene.blend", "--check"])
    assert res.data["warning"] == 'ignored top-level keys: export, typo (only "steps" is read)'
    assert list(res.data) == ["file", "steps", "commands", "targets", "warning", "plan", "status"]
    res, t = invoke([steps_file(work, doc), "scene.blend", "--in-place"])
    assert res.data["warning"] == 'ignored top-level keys: export, typo (only "steps" is read)' and res.data["applied"] == 1


def test_a_list_document_has_no_keys_to_warn_about(work):
    res, _ = invoke([steps_file(work, ["add cube"]), "scene.blend", "--check"])
    assert "warning" not in res.data


def test_strict_makes_unknown_keys_exit_2_before_blender(work):
    err, t = fails([steps_file(work, {"steps": ["add cube"], "typo": 1, "export": {}}), "scene.blend", "--check", "--strict"], Fake())
    assert err.exit_code == 2 and err.message == 'steps: unknown top-level key(s) export, typo; only "steps" is read'
    assert err.help[-1] == "Drop --strict to ignore them" and t.calls == []


# -- --strict ----------------------------------------------------------------------------------------------------------

def strict(work, steps, extra=(), listing=None):
    return invoke([steps_file(work, steps), "scene.blend", "--check", "--strict", *extra], Fake(listing))


def test_strict_happy_path_reports_the_counts_and_sends_names_only(work):
    res, t = strict(work, ["set Ring --hide", "material Hole --name Red", "configure --camera Cam", "add cube --collection Parts"])
    assert res.data["targets"] == "verified against scene.blend (3 objects, 2 collections)"
    assert "warning" not in res.data and t.ops == ["names"]
    assert t.calls[0]["blend"] == (WORK / "scene.blend").resolve() and t.calls[0]["params"] == {}


def test_strict_add_creates_default_and_explicit_names(work):
    steps = ["add cube", "add monkey", "add camera", "add empty", "add light", "add light --type SUN", "add cube --name Box",
             "set Cube --hide", "set Suzanne --hide", "set Camera --hide", "set Empty --hide", "set Point --hide",
             "set Sun --hide", "set Box --hide"]
    res, _ = strict(work, steps)
    assert res.data["targets"].startswith("verified")
    assert op_add.DEFAULT_NAMES["monkey"] == "Suzanne"  # A5: the simulation reads the op's own table


def test_strict_a_second_add_gets_blenders_suffix(work):
    res, _ = strict(work, ["add cube", "add cube", "set Cube.001 --hide"])
    assert res.data["targets"].startswith("verified")
    err, _ = fails([steps_file(work, ["add cube", "add cube", "set Cube.002 --hide"]), "scene.blend", "--check", "--strict"], Fake())
    assert err.message.startswith("step 3 (set): object 'Cube.002' not found")


def test_strict_rename_and_delete_change_the_known_names(work):
    res, _ = strict(work, ["set Ring --name Band", "set Band --hide", "delete Hole", "add cube --name Hole", "set Hole --hide"])
    assert res.data["targets"].startswith("verified")
    for steps, bad in ((["set Ring --name Band", "set Ring --hide"], "Ring"), (["delete Hole", "material Hole --name Red"], "Hole"),
                       (["delete Hole,Ring", "delete Ring"], "Ring")):
        err, _ = fails([steps_file(work, steps), "scene.blend", "--check", "--strict"], Fake())
        assert err.code == "NOT_FOUND" and f"object '{bad}' not found" in err.message


def test_strict_error_wording_and_help(work):
    err, t = fails([steps_file(work, ["add cube", "material Ghost --name Red"], "r.json"), "scene.blend", "--check", "--strict"], Fake())
    assert err.exit_code == 1 and err.code == "NOT_FOUND"
    assert err.message == "step 2 (material): object 'Ghost' not found in scene.blend (nor created by an earlier step)"
    assert err.help == ["Run `blender-axi objects scene.blend` to list object names", "Fix step 2 in r.json (nothing ran)"]
    assert t.ops == ["names"]


def test_strict_bad_add_collection_uses_the_collection_wording(work):
    err, _ = fails([steps_file(work, ["add cube --collection Nope"]), "scene.blend", "--check", "--strict"], Fake())
    assert err.message == "step 1 (add): collection 'Nope' not found in scene.blend (nor created by an earlier step)"
    res, _ = strict(work, ["add cube --collection Parts", "add cube --collection \"Scene Collection\""])
    assert res.data["targets"].startswith("verified")


def test_strict_modifier_object_and_mirror_object_are_checked(work):
    res, _ = strict(work, ["modifier Ring boolean --object Hole", "modifier Ring mirror --object Hole"])
    assert res.data["targets"].startswith("verified")
    for line, bad in (("modifier Ring boolean --object Ghost", "Ghost"), ("modifier Ring mirror --object Ghost", "Ghost"),
                      ("modifier Ghost subdivision", "Ghost")):
        err, _ = fails([steps_file(work, [line]), "scene.blend", "--check", "--strict"], Fake())
        assert f"object '{bad}' not found" in err.message and err.message.startswith("step 1 (modifier)")


@pytest.mark.skipif("--cutter" not in [f.name for f in modifier_cmd.SPEC.flags], reason="modifier --cutter comes with unit U3")
def test_strict_checks_the_cutter(work):
    res, _ = strict(work, [{"cmd": "modifier", "object": "Ring", "kind": "boolean", "cutter": "Hole"}])
    assert res.data["targets"].startswith("verified")
    err, _ = fails([steps_file(work, [{"cmd": "modifier", "object": "Ring", "kind": "boolean", "cutter": "Ghost"}]),
                    "scene.blend", "--check", "--strict"], Fake())
    assert "object 'Ghost' not found" in err.message


def test_strict_configure_camera_and_set_parent_and_collection(work):
    err, _ = fails([steps_file(work, ["configure --camera Ghost"]), "scene.blend", "--check", "--strict"], Fake())
    assert err.message.startswith("step 1 (configure): object 'Ghost' not found")
    err, _ = fails([steps_file(work, ["set Ring --parent Ghost"]), "scene.blend", "--check", "--strict"], Fake())
    assert "object 'Ghost' not found" in err.message
    err, _ = fails([steps_file(work, ["set Ring --collection Nope"]), "scene.blend", "--check", "--strict"], Fake())
    assert "collection 'Nope' not found" in err.message
    res, _ = strict(work, ["set Ring --parent none", "set Ring --parent Hole --collection Parts"])
    assert res.data["targets"].startswith("verified")


def test_strict_after_an_import_a_miss_is_a_warning(work):
    res, _ = strict(work, ["import m.glb --collection Imported", "material Ghost --name Red", "material Ghost --name Red",
                           "add cube --collection Imported", "set Phantom --collection Elsewhere"])
    assert res.data["warning"] == ("step 2 (material): object 'Ghost' is not in scene.blend; it may come from the import in step 1; "
                                   "step 3 (material): object 'Ghost' is not in scene.blend; it may come from the import in step 1; "
                                   "step 5 (set): object 'Phantom' is not in scene.blend; it may come from the import in step 1; "
                                   "step 5 (set): collection 'Elsewhere' is not in scene.blend; it may come from the import in step 1")
    assert res.data["targets"].startswith("verified")
    err, _ = fails([steps_file(work, ["material Ghost --name Red", "import m.glb"]), "scene.blend", "--check", "--strict"], Fake())
    assert err.code == "NOT_FOUND"  # the miss comes before the import


def test_strict_with_a_missing_file_is_not_found(work):
    err, t = fails([steps_file(work, ["add cube"]), "missing.blend", "--check", "--strict"], Fake())
    assert err.code == "NOT_FOUND" and t.calls == []


def test_strict_run_sends_names_before_batch_and_applies(work):
    res, t = invoke([steps_file(work, ["set Ring --hide"]), "scene.blend", "--in-place", "--strict"])
    assert t.ops == ["names", "batch"] and res.data["applied"] == 1
    assert "targets" not in res.data


def test_strict_run_stops_before_batch_on_a_bad_target(work):
    err, t = fails([steps_file(work, ["set Ghost --hide"]), "scene.blend", "--in-place", "--strict"], Fake())
    assert err.code == "NOT_FOUND" and t.ops == ["names"]


def test_strict_run_keeps_the_verify_warning(work):
    res, t = invoke([steps_file(work, ["import m.glb", "material Ghost --name Red"]), "scene.blend", "--in-place", "--strict"])
    assert t.ops == ["names", "batch"] and "may come from the import in step 1" in res.data["warning"]


def test_strict_live_pings_then_asks_the_open_session(work):
    res, t = invoke([steps_file(work, ["set Ring --hide"]), "--check", "--strict", *LIVE])
    assert t.ops == ["ping", "names"] and t.calls[1]["blend"] is None
    assert res.data["file"] == "(live session)" and res.data["targets"] == "verified against (live session) (3 objects, 2 collections)"
    _, t = invoke([steps_file(work, ["set Ring --hide"]), "--strict", *LIVE])
    assert t.ops == ["ping", "names", "batch"]


def test_plain_check_never_contacts_blender(work):
    _, t = invoke([steps_file(work, ["add cube"]), "scene.blend", "--check"])
    assert t.calls == []
    _, t = invoke([steps_file(work, ["add cube"]), "--check", *LIVE])
    assert t.calls == []


def test_strict_material_glob_needs_a_match(work):
    res, _ = strict(work, ["material Ri* --name Red", "add cube --name RegionA", "material Region* --name Blue"])
    assert res.data["targets"].startswith("verified")
    err, _ = fails([steps_file(work, ["material Region* --name Red"]), "scene.blend", "--check", "--strict"], Fake())
    assert err.code == "NOT_FOUND" and err.message == "step 1 (material): no object matches 'Region*' in scene.blend"
    assert err.help[-1] == "Fix step 1 in steps.json (nothing ran)"
    err, _ = fails([steps_file(work, ["delete Ring", "material Ri* --name Red"]), "scene.blend", "--check", "--strict"], Fake())
    assert err.message == "step 2 (material): no object matches 'Ri*' in scene.blend"
