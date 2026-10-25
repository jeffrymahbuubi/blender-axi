"""v1.3 U2: `modifier` at command level with a fake transport (the op is in test_op_modifier.py)."""

import math
import shutil
from pathlib import Path

import pytest

from blender_axi import flags as flagparser
from blender_axi.commands import modifier as cmd
from blender_axi.commands.set_cmd import NO_CHANGE
from blender_axi.errors import AxiError
from blender_axi.runner import RunOutcome, SaveSpec
from blender_axi.spec import BatchSpec

WORK = Path(__file__).resolve().parents[2] / ".tmp" / "tests" / "unit_cmd_modifier"
LIVE = ["--live", "--host", "127.0.0.1", "--port", "9999"]
RESULT = {"object": "Cube", "modifier": "Bevel", "type": "BEVEL", "created": True, "removed": False,
          "changed": [{"field": "width", "from": 0.1, "to": 0.05}], "stack": ["Bevel"]}


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


def parse(argv):
    inv, wants_help = flagparser.parse(cmd.SPEC, argv)
    assert not wants_help
    return inv


def invoke(argv, transport=None):
    inv = parse(argv)
    inv.transport = transport or Fake()
    return cmd.run(inv), inv.transport


def fails(argv, transport=None):
    with pytest.raises(AxiError) as exc:
        invoke(argv, transport)
    return exc.value


def params_of(argv):
    _, t = invoke([*argv, "--in-place"])
    return t.calls[0]["params"]


# -- spec -------------------------------------------------------------------------------------------------------------

def test_spec_shape():
    assert cmd.SPEC.name == "modifier"
    assert [p.name for p in cmd.SPEC.positionals] == ["object", "kind", "file"]
    assert [p.required for p in cmd.SPEC.positionals] == [True, True, False]
    assert [f.name for f in cmd.SPEC.flags] == [
        "--name", "--remove", "--width", "--segments", "--limit", "--angle", "--levels", "--render-levels", "--simple",
        "--count", "--offset", "--constant", "--axis", "--clip", "--object", "--operation", "--solver", "--thickness",
        "--even", "--prop", "--in-place", "--out", "--force"]
    assert isinstance(cmd.BATCH, BatchSpec) and cmd.BATCH.op == "modifier"
    assert cmd.KINDS == {"bevel": "BEVEL", "subdivision": "SUBSURF", "array": "ARRAY", "mirror": "MIRROR",
                         "boolean": "BOOLEAN", "solidify": "SOLIDIFY"}


@pytest.mark.parametrize("flag,value", [("--limit", "VGROUP"), ("--operation", "XOR"), ("--solver", "FAST"),
                                        ("--axis", "w")])
def test_choices_are_enforced_by_the_parser(flag, value):
    with pytest.raises(AxiError) as exc:
        flagparser.parse(cmd.SPEC, ["Cube", "bevel", flag, value])
    assert exc.value.exit_code == 2


# -- kinds ------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("given,kind", [("bevel", "BEVEL"), ("Bevel", "BEVEL"), ("subdivision", "SUBSURF"),
                                        ("array", "ARRAY"), ("mirror", "MIRROR"), ("boolean", "BOOLEAN"),
                                        ("solidify", "SOLIDIFY"), ("WIREFRAME", "WIREFRAME"),
                                        ("SIMPLE_DEFORM", "SIMPLE_DEFORM"), ("SUBSURF", "SUBSURF")])
def test_kind_table_and_identifier_passthrough(work, given, kind):
    p = params_of(["Cube", "scene.blend", given] if given != "boolean" else ["Cube", "scene.blend", given, "--object", "C"])
    assert p["kind"] == kind and p["object"] == "Cube" and p["remove"] is False and p["name"] is None


@pytest.mark.parametrize("given", ["wobble", "Wire-frame", "2d", "wire frame"])
def test_unknown_kind_exit_2_lists_the_kinds(work, given):
    t = Fake()
    err = fails(["Cube", "scene.blend", given, "--in-place"], t)
    assert err.exit_code == 2 and t.calls == []
    assert "bevel, subdivision, array, mirror, boolean, solidify" in err.message and "WIREFRAME" in err.message


def test_file_may_come_before_or_after_the_kind(work):
    for argv in (["Cube", "scene.blend", "bevel"], ["Cube", "bevel", "scene.blend"]):
        _, t = invoke([*argv, "--width", "0.1", "--in-place"])
        assert t.calls[0]["params"]["kind"] == "BEVEL" and t.calls[0]["blend"].name == "scene.blend"


def test_file_without_a_kind_is_exit_2(work):
    t = Fake()
    err = fails(["Cube", "scene.blend", "--in-place"], t)
    assert err.exit_code == 2 and "needs a kind" in err.message and t.calls == []


def test_the_default_file_is_the_single_blend_in_the_cwd(work):
    _, t = invoke(["Cube", "bevel", "--width", "0.1", "--in-place"])
    assert t.calls[0]["blend"].name == "scene.blend"


# -- flags per kind ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("kind,flag,value,message", [
    ("subdivision", "--width", "0.1", "--width applies to bevel"),
    ("bevel", "--levels", "2", "--levels applies to subdivision"),
    ("array", "--thickness", "1", "--thickness applies to solidify"),
    ("solidify", "--count", "3", "--count applies to array"),
    ("bevel", "--offset", "1,0,0", "--offset applies to array and solidify"),
    ("array", "--object", "Cutter", "--object applies to mirror and boolean"),
    ("bevel", "--operation", "UNION", "--operation applies to boolean"),
    ("WIREFRAME", "--width", "1", "--width applies to bevel"),
])
def test_flag_not_applying_to_the_kind_exit_2(work, kind, flag, value, message):
    t = Fake()
    err = fails(["Cube", "scene.blend", kind, flag, value, "--in-place"], t)
    assert err.exit_code == 2 and err.message == message and t.calls == []


def test_boolean_flags_that_are_off_do_not_count_as_given(work):
    p = params_of(["Cube", "scene.blend", "bevel", "--width", "0.1"])
    assert p["settings"] == {"width": 0.1}


def test_bevel_settings_and_angle_in_radians(work):
    p = params_of(["Cube", "scene.blend", "bevel", "--width", "0.05", "--segments", "3", "--limit", "angle",
                   "--angle", "30"])
    s = p["settings"]
    assert list(s) == ["width", "segments", "limit_method", "angle_limit"]
    assert s["width"] == 0.05 and s["segments"] == 3 and s["limit_method"] == "ANGLE"
    assert s["angle_limit"] == pytest.approx(math.radians(30))


def test_subdivision_settings(work):
    p = params_of(["Cube", "scene.blend", "subdivision", "--levels", "0", "--render-levels", "3", "--simple"])
    assert p["settings"] == {"levels": 0, "render_levels": 3, "subdivision_type": "SIMPLE"}


def test_array_offset_is_a_vector_and_enables_relative(work):
    p = params_of(["Cube", "scene.blend", "array", "--count", "4", "--offset", "1.5,0,0"])
    assert p["settings"] == {"count": 4, "relative_offset_displace": [1.5, 0.0, 0.0], "use_relative_offset": True}


def test_array_constant_alone_disables_relative(work):
    p = params_of(["Cube", "scene.blend", "array", "--constant", "0,3,0"])
    assert p["settings"] == {"constant_offset_displace": [0.0, 3.0, 0.0], "use_constant_offset": True,
                             "use_relative_offset": False}


def test_array_constant_with_offset_keeps_relative(work):
    p = params_of(["Cube", "scene.blend", "array", "--constant", "0,3,0", "--offset", "1,0,0"])
    assert p["settings"]["use_relative_offset"] is True and p["settings"]["use_constant_offset"] is True


def test_solidify_offset_is_a_number(work):
    p = params_of(["Cube", "scene.blend", "solidify", "--thickness", "0.2", "--offset", "1", "--even"])
    assert p["settings"] == {"thickness": 0.2, "offset": 1.0, "use_even_offset": True}


@pytest.mark.parametrize("offset", ["2", "x", "1,0,0"])
def test_solidify_offset_out_of_range_exit_2(work, offset):
    err = fails(["Cube", "scene.blend", "solidify", "--offset", offset, "--in-place"])
    assert err.exit_code == 2 and "--offset for solidify is a number from -1 to 1" in err.message


def test_array_offset_must_be_a_vector(work):
    err = fails(["Cube", "scene.blend", "array", "--offset", "1,2", "--in-place"])
    assert err.exit_code == 2 and err.message.startswith("--offset expects x,y,z")


@pytest.mark.parametrize("axis,use_axis", [("x", [True, False, False]), ("yz", [False, True, True]),
                                           ("xyz", [True, True, True]), ("XZ", [True, False, True])])
def test_mirror_axis_becomes_use_axis(work, axis, use_axis):
    p = params_of(["Cube", "scene.blend", "mirror", "--axis", axis, "--clip", "--object", "Pivot"])
    assert p["settings"] == {"use_axis": use_axis, "use_clip": True, "mirror_object": "Pivot"}


def test_boolean_settings(work):
    p = params_of(["Cube", "scene.blend", "boolean", "--object", "Cutter", "--operation", "union", "--solver", "float"])
    assert p["settings"] == {"object": "Cutter", "operation": "UNION", "solver": "FLOAT"}


@pytest.mark.parametrize("argv,message", [
    (["bevel", "--width", "0"], "--width must be greater than 0, got 0"),
    (["bevel", "--width", "-1"], "--width must be greater than 0, got -1"),
    (["bevel", "--segments", "0"], "--segments must be 1-1000, got 0"),
    (["bevel", "--angle", "181"], "--angle must be 0-180, got 181"),
    (["subdivision", "--levels", "12"], "--levels must be 0-11, got 12"),
    (["subdivision", "--render-levels", "-1"], "--render-levels must be 0-11, got -1"),
    (["array", "--count", "0"], "--count must be 1-1000, got 0"),
    (["bevel", "--name", "  "], "--name is empty"),
    (["boolean", "--object", " "], "--object is empty"),
])
def test_value_errors_exit_2_without_requests(work, argv, message):
    t = Fake()
    err = fails(["Cube", "scene.blend", *argv, "--in-place"], t)
    assert err.code == "VALIDATION_ERROR" and err.exit_code == 2 and t.calls == []
    assert err.message == message


# -- remove -----------------------------------------------------------------------------------------------------------

def test_remove_params(work):
    p = params_of(["Cube", "scene.blend", "bevel", "--remove", "--name", "Rim"])
    assert p == {"object": "Cube", "kind": "BEVEL", "name": "Rim", "remove": True, "settings": {}, "props": {}}


@pytest.mark.parametrize("extra,shown", [(["--width", "0.1"], "--width"), (["--prop", "a=1"], "--prop"),
                                         (["--segments", "0"], "--segments")])
def test_remove_with_settings_exit_2(work, extra, shown):
    t = Fake()
    err = fails(["Cube", "scene.blend", "bevel", "--remove", *extra, "--in-place"], t)
    assert err.exit_code == 2 and "--remove cannot be combined" in err.message and shown in err.message
    assert t.calls == []


def test_remove_data_has_removed_and_no_changed(work):
    res, _ = invoke(["Cube", "scene.blend", "bevel", "--remove", "--in-place"],
                    Fake({**RESULT, "created": False, "removed": True, "changed": [], "stack": []}))
    assert list(res.data) == ["file", "object", "modifier", "type", "removed", "stack", "modified", "saved", "duration_ms"]
    assert res.data["removed"] is True and res.data["stack"] == "0"


def test_remove_of_a_missing_modifier_reports_removed_false(work):
    res, _ = invoke(["Cube", "scene.blend", "bevel", "--remove", "--in-place"],
                    Fake({**RESULT, "created": False, "removed": False, "changed": [], "stack": []},
                         modified=False, saved=None))
    assert res.data["removed"] is False and res.data["modified"] is False


# -- --prop -----------------------------------------------------------------------------------------------------------

def test_prop_entries_split_and_arrays_stay_together(work):
    p = params_of(["Cube", "scene.blend", "WIREFRAME", "--prop", "thickness=0.05,use_even_offset=true",
                   "--prop", "offset_vec=1,0,0"])
    assert p["props"] == {"thickness": "0.05", "use_even_offset": "true", "offset_vec": "1,0,0"}
    assert p["settings"] == {}


@pytest.mark.parametrize("entry", ["thickness", "=1", "9a=1", "a b=1"])
def test_prop_without_a_valid_key_exit_2(work, entry):
    t = Fake()
    err = fails(["Cube", "scene.blend", "WIREFRAME", "--prop", entry, "--in-place"], t)
    assert err.exit_code == 2 and err.message == f"--prop expects key=value, got '{entry}'" and t.calls == []


def test_prop_may_follow_typed_flags(work):
    p = params_of(["Cube", "scene.blend", "bevel", "--width", "0.1", "--prop", "use_clamp_overlap=false"])
    assert p["settings"] == {"width": 0.1} and p["props"] == {"use_clamp_overlap": "false"}


# -- write rules, data order, hints ----------------------------------------------------------------------------------

def test_neither_flag_exit_2_with_both_commands(work):
    t = Fake()
    err = fails(["Cube", "bevel", "scene.blend", "--width", "0.1"], t)
    assert err.exit_code == 2 and t.calls == []
    assert err.help[0] == ("Run `blender-axi modifier Cube bevel scene.blend --width 0.1 --in-place` "
                           "to write the file (a .blend1 backup is kept)")
    assert err.help[1].endswith("--out <copy.blend>` to keep the original and write a copy")


def test_swapped_order_is_replayed_in_the_canonical_order(work):
    err = fails(["Cube", "scene.blend", "bevel", "--width", "0.1"])
    assert "modifier Cube bevel scene.blend --width 0.1 --in-place" in err.help[0]
    assert "modifier Cube bevel scene.blend --width 0.1 --out <copy.blend>" in err.help[1]


def test_data_order_and_hints_headless(work):
    res, t = invoke(["Cube", "scene.blend", "bevel", "--width", "0.05", "--in-place"],
                    Fake({**RESULT, "stack": ["Bevel", "Subdivision"]}))
    assert t.ops == ["modifier"] and t.calls[0]["params"]["settings"] == {"width": 0.05}
    assert list(res.data) == ["file", "object", "modifier", "type", "created", "changed", "stack", "modified", "saved",
                              "duration_ms"]
    d = res.data
    assert d["file"] == "scene.blend" and d["object"] == "Cube" and d["modifier"] == "Bevel" and d["type"] == "BEVEL"
    assert d["created"] is True and d["changed"] == ["width: 0.1 -> 0.05"] and d["stack"] == "2 (Bevel, Subdivision)"
    assert res.help == ["Run `blender-axi objects scene.blend` to list the objects",
                        "Run `blender-axi object Cube scene.blend` to check the result"]


def test_changed_formatting(work):
    changes = [{"field": "angle_limit", "from": 30.0, "to": 45.0}, {"field": "use_axis", "from": [True, False, False],
               "to": [True, True, False]}, {"field": "mirror_object", "from": None, "to": "Pivot"},
               {"field": "width", "from": 0.0005, "to": 0.12}, {"field": "use_clip", "from": False, "to": True}]
    res, _ = invoke(["Cube", "scene.blend", "bevel", "--width", "1", "--in-place"],
                    Fake({**RESULT, "created": False, "changed": changes}))
    assert res.data["changed"] == ["angle_limit: 30 -> 45", "use_axis: true,false,false -> true,true,false",
                                   "mirror_object: none -> Pivot", "width: 0.0005 -> 0.12", "use_clip: false -> true"]


def test_unmodified_reports_the_no_change_text(work):
    res, _ = invoke(["Cube", "scene.blend", "bevel", "--width", "0.05", "--in-place"],
                    Fake({**RESULT, "created": False, "changed": []}, modified=False, saved=None))
    assert res.data["changed"] == NO_CHANGE and res.data["created"] is False
    assert res.data["modified"] is False and res.data["saved"] == "none (no changes)"


def test_out_hints_use_the_written_copy(work):
    res, _ = invoke(["Cube", "scene.blend", "bevel", "--width", "0.05", "--out", "copy.blend"])
    assert res.data["saved"] == "copy.blend"
    assert res.help[1] == "Run `blender-axi object Cube copy.blend` to check the result"


def test_object_not_found_gets_an_actionable_hint(work):
    class Missing(Fake):
        def run(self, op, params, **kw):
            raise AxiError("NOT_FOUND", "object 'Cub' not found (did you mean: Cube?)", help=["generic"])
    err = fails(["Cub", "scene.blend", "bevel", "--in-place"], Missing())
    assert err.code == "NOT_FOUND" and err.help == ["Run `blender-axi objects scene.blend` to list object names"]


def test_op_failed_passes_through(work):
    class Bad(Fake):
        def run(self, op, params, **kw):
            raise AxiError("OP_FAILED", "'Camera' (CAMERA) cannot take modifiers")
    err = fails(["Camera", "scene.blend", "bevel", "--in-place"], Bad())
    assert err.code == "OP_FAILED" and err.exit_code == 1


def test_missing_file_is_not_rewritten(work):
    err = fails(["Cube", "nope.blend", "bevel", "--in-place"])
    assert err.code == "NOT_FOUND" and err.message.startswith("file not found")


# -- live -------------------------------------------------------------------------------------------------------------

def test_live_unsaved_change():
    res, t = invoke(["Cube", "bevel", *LIVE, "--width", "0.2"], Fake(undo="unavailable", saved=None))
    assert t.ops == ["ping", "modifier"] and t.calls[1]["blend"] is None and t.calls[1]["save"] == SaveSpec("none")
    d = res.data
    assert d["file"] == "C:/work/open.blend" and d["modified"] is True
    assert d["saved"] == "none (live session, unsaved)" and d["undo"] == "unavailable (headless server)"
    assert res.help[0] == "Run `blender-axi objects --live --host 127.0.0.1 --port 9999` to list the objects"
    assert res.help[1] == "Run `blender-axi object Cube --live --host 127.0.0.1 --port 9999` to check the result"
    assert res.help[-1].startswith("Changes are only in the open session")


def test_live_refusals():
    for argv in (["Cube", "bevel", "x.blend", *LIVE], ["Cube", "bevel", *LIVE, "--out", "o.blend"]):
        t = Fake()
        err = fails(argv, t)
        assert err.exit_code == 2 and t.calls == []


# -- batch hook -------------------------------------------------------------------------------------------------------

def test_batch_params_equal_what_run_sends(work):
    argv = ["Cube", "bevel", "--width", "0.05", "--prop", "use_clamp_overlap=false"]
    inv = parse(argv)
    assert cmd.BATCH.params(inv) == params_of(argv)


def test_batch_params_validate_without_a_transport(work):
    with pytest.raises(AxiError) as exc:
        cmd.BATCH.params(parse(["Cube", "bevel", "--levels", "2"]))
    assert exc.value.exit_code == 2 and exc.value.message == "--levels applies to subdivision"


@pytest.mark.parametrize("result,text", [
    (RESULT, "Cube <- Bevel (BEVEL, created)"),
    ({**RESULT, "created": False}, "Cube <- Bevel (BEVEL, width)"),
    ({**RESULT, "created": False, "changed": [{"field": "width"}, {"field": "segments"}]},
     "Cube <- Bevel (BEVEL, width, segments)"),
    ({**RESULT, "created": False, "changed": []}, "Cube <- Bevel (BEVEL, no change)"),
    ({**RESULT, "created": False, "removed": True, "changed": []}, "Cube <- Bevel (BEVEL, removed)"),
])
def test_batch_line(result, text):
    assert cmd.BATCH.line(result) == text

