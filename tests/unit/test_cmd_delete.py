"""v1.2 U2: `delete` (command level with a fake transport) and `op_delete` (fake `bpy`)."""

import shutil
import types
from pathlib import Path

import pytest

from blender_axi import flags as flagparser
from blender_axi.blender_side import op_delete
from blender_axi.blender_side.ops import OpError
from blender_axi.commands import delete as cmd
from blender_axi.errors import AxiError
from blender_axi.runner import RunOutcome, SaveSpec

WORK = Path(__file__).resolve().parents[2] / ".tmp" / "tests" / "unit_cmd_delete"
LIVE = ["--live", "--host", "127.0.0.1", "--port", "9999"]


class Fake:
    name = "fake"

    def __init__(self, deleted=("Ball",), kept=(), remaining=4, file="C:\\work\\open.blend", **outcome_kw):
        self.result = {"deleted": list(deleted), "children_kept": list(kept), "remaining": remaining}
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
    assert cmd.SPEC.name == "delete"
    assert [p.name for p in cmd.SPEC.positionals] == ["names", "file"] and cmd.SPEC.positionals[1].required is False
    assert "comma-separated" in cmd.SPEC.positionals[0].help.lower()
    assert [f.name for f in cmd.SPEC.flags] == ["--in-place", "--out", "--force"]
    assert any("--live" in e for e in cmd.SPEC.examples)


def test_names_are_split_trimmed_and_deduplicated(work):
    _, t = invoke(["Cube, Ball ,Cube,,Anchor", "scene.blend", "--in-place"])
    assert t.calls[0]["op"] == "delete" and t.calls[0]["params"] == {"names": ["Cube", "Ball", "Anchor"]}


def test_names_with_spaces_survive(work):
    _, t = invoke(["My Box,Ball", "scene.blend", "--in-place"])
    assert t.calls[0]["params"] == {"names": ["My Box", "Ball"]}


@pytest.mark.parametrize("raw", [",", " , ,", ""])
def test_empty_names_exit_2(work, raw):
    t = Fake()
    err = fails([raw, "scene.blend", "--in-place"], t)
    assert err.code == "VALIDATION_ERROR" and err.exit_code == 2 and t.calls == [] and err.message == "no object names given"


def test_neither_flag_exit_2_with_both_commands(work):
    t = Fake()
    err = fails(["Ball", "scene.blend"], t)
    assert err.exit_code == 2 and t.calls == []
    assert err.help[0] == "Run `blender-axi delete Ball scene.blend --in-place` to write the file (a .blend1 backup is kept)"
    assert err.help[1] == "Run `blender-axi delete Ball scene.blend --out <copy.blend>` to keep the original and write a copy"


# -- params and result ------------------------------------------------------------------------------------------------

def test_save_specs(work):
    _, t = invoke(["Ball", "scene.blend", "--in-place"])
    assert t.calls[0]["blend"] == (WORK / "scene.blend").resolve() and t.calls[0]["save"].mode == "in_place"
    _, t = invoke(["Ball", "scene.blend", "--out", "o.blend"])
    assert t.calls[0]["save"] == SaveSpec("copy", str((WORK / "o.blend").resolve()))


def test_data_order_and_hints(work):
    res, _ = invoke(["Ball", "scene.blend", "--in-place"])
    assert list(res.data) == ["file", "deleted", "remaining", "modified", "saved", "duration_ms"]
    assert res.data["file"] == "scene.blend" and res.data["deleted"] == ["Ball"] and res.data["remaining"] == "4 objects"
    assert res.help == ["Run `blender-axi objects scene.blend` to list the objects"]


def test_children_kept_appears_only_when_any(work):
    res, _ = invoke(["Anchor", "scene.blend", "--in-place"], Fake(("Anchor",), ("Ball", "Cube"), 3))
    assert list(res.data)[:4] == ["file", "deleted", "children_kept", "remaining"]
    assert res.data["children_kept"] == ["Ball", "Cube"] and res.data["remaining"] == "3 objects"


def test_out_hints_use_the_written_copy(work):
    res, _ = invoke(["Ball", "scene.blend", "--out", "copy.blend"])
    assert res.data["saved"] == "copy.blend" and res.help[0] == "Run `blender-axi objects copy.blend` to list the objects"


def test_hints_carry_user_prefs(work):
    res, _ = invoke(["Ball", "scene.blend", "--in-place", "--user-prefs"])
    assert all(h.split("`")[1].endswith("--user-prefs") for h in res.help)


def test_not_found_gets_an_actionable_hint(work):
    class Missing(Fake):
        def run(self, op, params, **kw):
            raise AxiError("NOT_FOUND", "objects 'X' not found; nothing deleted", help=["generic"])
    err = fails(["X", "scene.blend", "--in-place"], Missing())
    assert err.code == "NOT_FOUND" and err.help == ["Run `blender-axi objects scene.blend` to list object names"]


def test_missing_file_keeps_its_own_hint(work):
    err = fails(["Ball", "nope.blend", "--in-place"])
    assert err.code == "NOT_FOUND" and err.message.startswith("file not found")


# -- live -------------------------------------------------------------------------------------------------------------

def test_live_unsaved_change():
    res, t = invoke(["Ball", *LIVE], Fake(undo="unavailable", saved=None))
    assert t.ops == ["ping", "delete"] and t.calls[1]["blend"] is None and t.calls[1]["save"] == SaveSpec("none")
    d = res.data
    assert d["file"] == "C:/work/open.blend" and d["saved"] == "none (live session, unsaved)"
    assert d["undo"] == "unavailable (headless server)"
    assert res.help[0] == "Run `blender-axi objects --live --host 127.0.0.1 --port 9999` to list the objects"
    assert res.help[-1].startswith("Changes are only in the open session")


def test_live_refusals():
    for argv in (["Ball", "x.blend", *LIVE], ["Ball", *LIVE, "--out", "o.blend"]):
        t = Fake()
        err = fails(argv, t)
        assert err.exit_code == 2 and t.calls == []


# -- op_delete (fake bpy) ---------------------------------------------------------------------------------------------

class Mat:
    def __init__(self, tag):
        self.tag = tag

    def copy(self):
        return Mat(self.tag + "'")


class Objects(list):
    def __init__(self, *items):
        super().__init__(items)
        self.removed = []

    def remove(self, ob):
        self.removed.append(ob.name)
        super().remove(ob)


def make(name, parent=None):
    ob = types.SimpleNamespace(name=name, parent=parent, matrix_world=Mat(name), children=[])
    if parent is not None:
        parent.children.append(ob)
    return ob


@pytest.fixture
def world(fake_bpy):
    cube = make("Cube")
    anchor = make("Anchor")
    ball = make("Ball", anchor)
    kid = make("Kid", anchor)
    updates = []
    fake_bpy.data = types.SimpleNamespace(objects=Objects(cube, anchor, ball, kid))
    fake_bpy.context = types.SimpleNamespace(view_layer=types.SimpleNamespace(update=lambda: updates.append(1)))
    return types.SimpleNamespace(bpy=fake_bpy, cube=cube, anchor=anchor, ball=ball, kid=kid, updates=updates)


def test_op_deletes_in_order_and_counts_remaining(world):
    r = op_delete.run({"names": ["Cube", "Cube"]})
    assert r == {"deleted": ["Cube"], "children_kept": [], "remaining": 3}
    assert world.bpy.data.objects.removed == ["Cube"]


def test_op_missing_names_are_all_reported_and_nothing_is_removed(world):
    with pytest.raises(OpError) as exc:
        op_delete.run({"names": ["Cube", "Ghost", "Anchr"]})
    assert exc.value.code == "NOT_FOUND"
    msg = exc.value.message
    assert "'Ghost'" in msg and "'Anchr' (did you mean: Anchor?)" in msg and "nothing deleted" in msg
    assert world.bpy.data.objects.removed == [] and len(world.bpy.data.objects) == 4
    assert world.ball.parent is world.anchor


def test_op_children_are_unparented_with_world_matrix_preserved(world):
    r = op_delete.run({"names": ["Anchor"]})
    assert r["deleted"] == ["Anchor"] and r["children_kept"] == ["Ball", "Kid"] and r["remaining"] == 3
    for child in (world.ball, world.kid):
        assert child.parent is None and child.matrix_world.tag == child.name + "'"
    assert world.updates  # matrix_world is read after a depsgraph update


def test_op_children_that_are_deleted_too_are_not_listed(world):
    r = op_delete.run({"names": ["Anchor", "Ball"]})
    assert r["children_kept"] == ["Kid"] and r["remaining"] == 2
    assert world.bpy.data.objects.removed == ["Anchor", "Ball"]


def test_op_no_names_is_op_failed(world):
    with pytest.raises(OpError) as exc:
        op_delete.run({"names": []})
    assert exc.value.code == "OP_FAILED"
