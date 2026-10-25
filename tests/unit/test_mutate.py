"""v1.2 U1: the shared helper of the typed modeling commands (`commands/_mutate.py`). Fake transport only."""

import shutil
from pathlib import Path

import pytest

from blender_axi import flags as flagparser
from blender_axi.commands import _mutate, exec_cmd
from blender_axi.errors import AxiError
from blender_axi.runner import RunOutcome, SaveSpec
from blender_axi.spec import CommandSpec, Flag, Positional

WORK = Path(__file__).resolve().parents[2] / ".tmp" / "tests" / "unit_mutate"
LIVE = ["--live", "--host", "127.0.0.1", "--port", "9999"]

SPEC = CommandSpec(
    name="demo", summary="demo",
    positionals=(Positional("kind"), Positional("file", required=False)),
    flags=(Flag("--loc", "str"), *_mutate.MUTATE_FLAGS),
    examples=("blender-axi demo cube scene.blend --in-place", "blender-axi demo cube --live"),
    run=lambda inv: None,
)


class Fake:
    name = "fake"

    def __init__(self, file="C:\\work\\open.blend", outcome=None):
        self.file, self.calls, self.outcome = file, [], outcome

    def run(self, op, params, *, blend=None, save=SaveSpec("none"), timeout=None):
        self.calls.append({"op": op, "params": params, "blend": blend, "save": save, "timeout": timeout})
        if op == "ping":
            return RunOutcome({"file": self.file}, False, None, "5.2", 1, "")
        return self.outcome or outcome()

    @property
    def ops(self):
        return [c["op"] for c in self.calls]


def outcome(modified=True, saved=None, **kw):
    return RunOutcome({"x": 1}, modified, saved, "5.2.0 LTS", 12, "", **kw)


@pytest.fixture
def work(monkeypatch):
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(parents=True)
    (WORK / "scene.blend").write_bytes(b"BLEND")
    monkeypatch.chdir(WORK)
    return WORK


def inv_of(argv, transport=None):
    inv, wants_help = flagparser.parse(SPEC, argv)
    assert not wants_help
    inv.transport = transport or Fake()
    return inv


def fail(argv, transport=None):
    inv = inv_of(argv, transport)
    with pytest.raises(AxiError) as exc:
        _mutate.resolve_target(inv, SPEC)
    return exc.value, inv.transport


def test_constants_equal_execs():
    assert _mutate.NO_CHANGES == exec_cmd.NO_CHANGES
    assert _mutate.LIVE_UNSAVED == exec_cmd.LIVE_UNSAVED
    assert _mutate.UNDO_TEXT == exec_cmd.UNDO_TEXT


def test_mutate_flags():
    assert [f.name for f in _mutate.MUTATE_FLAGS] == ["--in-place", "--out", "--force"]
    assert [f.kind for f in _mutate.MUTATE_FLAGS] == ["bool", "path", "bool"]


# -- headless ---------------------------------------------------------------------------------------------------------

def test_neither_flag_explains_both_commands(work):
    err, t = fail(["cube", "scene.blend", "--loc", "1,2,3"])
    assert err.code == "VALIDATION_ERROR" and err.exit_code == 2
    assert err.message == "demo changes scene.blend; say where to write it"
    assert err.help == [
        "Run `blender-axi demo cube scene.blend --loc 1,2,3 --in-place` to write the file (a .blend1 backup is kept)",
        "Run `blender-axi demo cube scene.blend --loc 1,2,3 --out <copy.blend>` to keep the original and write a copy"]
    assert t.calls == []


def test_neither_flag_carries_user_prefs_and_infers_the_cwd_file(work):
    err, _ = fail(["cube", "--user-prefs"])
    assert err.message == "demo changes scene.blend; say where to write it"
    assert err.help[0] == "Run `blender-axi demo cube --user-prefs --in-place` to write the file (a .blend1 backup is kept)"


def test_both_flags_are_exclusive(work):
    err, _ = fail(["cube", "scene.blend", "--in-place", "--out", "o.blend"])
    assert err.exit_code == 2 and err.message == "--in-place and --out are mutually exclusive"


def test_missing_file_is_not_found(work):
    err, _ = fail(["cube", "nope.blend", "--in-place"])
    assert err.code == "NOT_FOUND"


def test_out_must_be_a_blend(work):
    err, _ = fail(["cube", "scene.blend", "--out", "x.fbx"])
    assert err.exit_code == 2 and ".blend" in err.message


def test_out_pointing_at_the_input_is_refused(work):
    err, _ = fail(["cube", "scene.blend", "--out", "./scene.blend", "--force"])
    assert err.exit_code == 2 and err.message == "--out points at the input file; use --in-place"
    assert err.help == ["Run `blender-axi demo cube scene.blend --in-place`"]


def test_existing_out_is_refused_and_force_passes(work):
    (work / "o.blend").write_bytes(b"x")
    err, t = fail(["cube", "scene.blend", "--out", "o.blend", "--loc", "1,1,1"])
    assert err.code == "WRITE_REFUSED" and err.exit_code == 1 and err.message == "output exists: o.blend"
    assert err.help == ["Run `blender-axi demo cube scene.blend --loc 1,1,1 --out o.blend --force` to overwrite",
                        "Or choose another path with --out <path>"]
    assert t.calls == []
    target = _mutate.resolve_target(inv_of(["cube", "scene.blend", "--out", "o.blend", "--force"]), SPEC)
    assert target.save == SaveSpec("copy", str((work / "o.blend").resolve()))


def test_in_place_target_and_backup_probe(work):
    target = _mutate.resolve_target(inv_of(["cube", "scene.blend", "--in-place"]), SPEC)
    blend = (work / "scene.blend").resolve()
    assert target.blend == blend and target.label == "scene.blend"
    assert target.save == SaveSpec("in_place", str(blend))
    assert target.backup_probe == (blend.with_name("scene.blend1"), None)
    bak = work / "scene.blend1"
    bak.write_bytes(b"old")
    target = _mutate.resolve_target(inv_of(["cube", "scene.blend", "--in-place"]), SPEC)
    assert target.backup_probe == (blend.with_name("scene.blend1"), bak.stat().st_mtime_ns)


def test_copy_target_has_no_probe(work):
    target = _mutate.resolve_target(inv_of(["cube", "scene.blend", "--out", "new.blend"]), SPEC)
    assert target.save == SaveSpec("copy", str((work / "new.blend").resolve())) and target.backup_probe is None


def test_run_mutation_passes_target_and_timeout(work):
    t = Fake()
    inv = inv_of(["cube", "scene.blend", "--in-place", "--timeout", "7"], t)
    target, out = _mutate.run_mutation(inv, SPEC, "add", {"a": 1})
    assert t.ops == ["add"] and t.calls[0]["params"] == {"a": 1} and t.calls[0]["timeout"] == 7.0
    assert t.calls[0]["blend"] == target.blend and t.calls[0]["save"] == target.save and out.modified is True


# -- live -------------------------------------------------------------------------------------------------------------

def test_live_positional_is_refused_before_any_call(work):
    err, t = fail(["cube", "scene.blend", *LIVE])
    assert err.exit_code == 2 and err.message == "--live uses the file open in Blender; drop <file>"
    assert err.help == ["blender-axi demo cube --live"] and t.calls == []


def test_live_out_is_refused_before_any_call():
    err, t = fail(["cube", *LIVE, "--out", "o.blend"])
    assert err.exit_code == 2 and "no copy" in err.message and "--in-place" in err.message and t.calls == []


def test_live_pings_first_and_saves_nothing_by_default():
    t = Fake()
    target = _mutate.resolve_target(inv_of(["cube", *LIVE], t), SPEC)
    assert t.ops == ["ping"] and target.blend is None
    assert target.label == "C:/work/open.blend" and target.save == SaveSpec("none") and target.backup_probe is None


def test_live_in_place_saves_the_open_file():
    target = _mutate.resolve_target(inv_of(["cube", *LIVE, "--in-place"], Fake()), SPEC)
    assert target.save == SaveSpec("in_place", "C:\\work\\open.blend")
    assert target.backup_probe == (Path("C:\\work\\open.blend1"), None)


def test_live_in_place_without_an_open_file_stops_after_the_ping():
    err, t = fail(["cube", *LIVE, "--in-place"], Fake(file=""))
    assert err.exit_code == 2 and err.message == "the open session has no file; save it in Blender first"
    assert t.ops == ["ping"]


def test_live_unsaved_session_label():
    target = _mutate.resolve_target(inv_of(["cube", *LIVE], Fake(file="")), SPEC)
    assert target.label == "(unsaved)" and target.save == SaveSpec("none")


# -- finish -----------------------------------------------------------------------------------------------------------

def finish(argv, out, data=None, hints=None, transport=None):
    inv = inv_of(argv, transport or Fake())
    target = _mutate.resolve_target(inv, SPEC)
    return _mutate.finish(inv, SPEC, target, out, {"file": target.label} if data is None else data, hints or [])


def test_tail_order_and_saved_variants_headless(work):
    res = finish(["cube", "scene.blend", "--in-place"], outcome(saved=str(work / "scene.blend")))
    assert list(res.data) == ["file", "modified", "saved", "duration_ms"]
    assert res.data["saved"] == "scene.blend" and res.data["modified"] is True and res.data["duration_ms"] == 12
    res = finish(["cube", "scene.blend", "--out", "c.blend"], outcome(modified=False))
    assert res.data["modified"] is False and res.data["saved"] == "none (no changes)"


def test_backup_only_when_the_probe_changed(work):
    saved = str(work / "scene.blend")
    inv = inv_of(["cube", "scene.blend", "--in-place"])
    target = _mutate.resolve_target(inv, SPEC)
    res = _mutate.finish(inv, SPEC, target, outcome(saved=saved), {}, [])
    assert "backup" not in res.data  # no .blend1 appeared
    bak = work / "scene.blend1"
    bak.write_bytes(b"old")
    res = _mutate.finish(inv, SPEC, target, outcome(saved=saved), {}, [])
    assert res.data["backup"] == "scene.blend1"  # appeared
    target = _mutate.resolve_target(inv_of(["cube", "scene.blend", "--in-place"]), SPEC)  # probe now sees it
    res = _mutate.finish(inv, SPEC, target, outcome(saved=saved), {}, [])
    assert "backup" not in res.data  # unchanged since the probe
    bak.write_bytes(b"newer")
    import os
    os.utime(bak, ns=(bak.stat().st_atime_ns, bak.stat().st_mtime_ns + 5_000_000_000))
    res = _mutate.finish(inv, SPEC, target, outcome(saved=saved), {}, [])
    assert res.data["backup"] == "scene.blend1"  # changed
    res = _mutate.finish(inv, SPEC, target, outcome(modified=False), {}, [])
    assert "backup" not in res.data  # nothing was saved


def test_undo_frame_and_warning_land_in_the_tail():
    res = finish(["cube", *LIVE], outcome(undo="pushed", frame_changed="1 -> 10", warnings=["w1", "w2"]))
    assert list(res.data) == ["file", "modified", "saved", "undo", "frame_changed", "duration_ms", "warning"]
    assert res.data["undo"] == "pushed (Ctrl+Z in Blender reverts)" and res.data["frame_changed"] == "1 -> 10"
    assert res.data["warning"] == "w1; w2"
    res = finish(["cube", *LIVE], outcome(undo="unavailable"))
    assert res.data["undo"] == "unavailable (headless server)"
    res = finish(["cube", *LIVE], outcome(undo=None))
    assert "undo" not in res.data and "frame_changed" not in res.data and "warning" not in res.data


def test_live_saved_variants_and_unsaved_hint():
    unsaved = "Changes are only in the open session: save in Blender, or rerun with --in-place to save the open file"
    res = finish(["cube", *LIVE], outcome(undo="pushed"))
    assert res.data["saved"] == "none (live session, unsaved)" and res.help[-1] == unsaved
    res = finish(["cube", *LIVE], outcome(modified=False))
    assert res.data["saved"] == "none (no changes)" and unsaved not in res.help
    res = finish(["cube", *LIVE, "--in-place"], outcome(saved="C:\\work\\open.blend"))
    assert res.data["saved"].endswith("open.blend") and unsaved not in res.help


def test_hints_start_with_objects_then_the_commands_own(work):
    res = finish(["cube", "scene.blend", "--in-place", "--user-prefs"], outcome(saved=str(work / "scene.blend")),
                 hints=["own hint"])
    assert res.help == ["Run `blender-axi objects scene.blend --user-prefs` to list the objects", "own hint"]
    res = finish(["cube", "scene.blend", "--out", "c.blend"], outcome(saved=str(work / "c.blend")))
    assert res.help[0] == "Run `blender-axi objects c.blend` to list the objects"  # the written copy
    res = finish(["cube", "scene.blend", "--out", "c.blend"], outcome(modified=False))
    assert res.help[0] == "Run `blender-axi objects scene.blend` to list the objects"  # nothing was written
    res = finish(["cube", *LIVE], outcome(modified=False))
    assert res.help == ["Run `blender-axi objects --live --host 127.0.0.1 --port 9999` to list the objects"]


def test_hint_cmd_fills_and_drops_the_file(work):
    inv = inv_of(["cube", "my scene.blend", "--in-place"])
    target = _mutate.Target(None, "my scene.blend", SaveSpec("none"), None)
    assert _mutate.hint_cmd(inv, target, outcome(), "set A <file> --location 1,2,3") == \
        'blender-axi set A "my scene.blend" --location 1,2,3'
    live = inv_of(["cube", *LIVE])
    assert _mutate.hint_cmd(live, target, outcome(), "set A <file> --location 1,2,3") == \
        "blender-axi set A --location 1,2,3 --live --host 127.0.0.1 --port 9999"


# -- parse_vec --------------------------------------------------------------------------------------------------------

def test_parse_vec_accepts_numbers():
    assert _mutate.parse_vec(SPEC, "--location", "1,2,3") == [1.0, 2.0, 3.0]
    assert _mutate.parse_vec(SPEC, "--location", " -1.5, 0 ,2e1") == [-1.5, 0.0, 20.0]
    assert _mutate.parse_vec(SPEC, "--location", None) is None
    assert _mutate.parse_vec(SPEC, "--color", "1,0,0,0.5", n=(3, 4)) == [1.0, 0.0, 0.0, 0.5]
    assert _mutate.parse_vec(SPEC, "--color", "1,0,0", n=(3, 4)) == [1.0, 0.0, 0.0]


@pytest.mark.parametrize("raw,n", [("1,2", (3,)), ("1,2,3,4", (3,)), ("a,b,c", (3,)), ("", (3,)), ("1,,3", (3,)),
                                   ("nan,0,0", (3,)), ("inf,0,0", (3,)), ("1,2", (3, 4)), ("1,2,3,4,5", (3, 4))])
def test_parse_vec_rejects(raw, n):
    with pytest.raises(AxiError) as exc:
        _mutate.parse_vec(SPEC, "--location", raw, n)
    assert exc.value.code == "VALIDATION_ERROR" and exc.value.exit_code == 2 and "--location expects" in exc.value.message


def test_parse_vec_message_names_the_expected_shape():
    with pytest.raises(AxiError) as exc:
        _mutate.parse_vec(SPEC, "--location", "1,2")
    assert exc.value.message == "--location expects x,y,z, got '1,2'"
    with pytest.raises(AxiError) as exc:
        _mutate.parse_vec(SPEC, "--color", "1,2", n=(3, 4))
    assert exc.value.message == "--color expects 3 or 4 comma-separated numbers, got '1,2'"
