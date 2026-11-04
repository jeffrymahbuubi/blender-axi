"""Unit tests for `diff` (fake transports answering `snapshot`)."""

from __future__ import annotations

import pytest

from blender_axi import flags, registry, toon
from blender_axi.commands import diff as cmd
from blender_axi.errors import AxiError
from blender_axi.runner import RunOutcome


def obj(name, kind="MESH", **kw):
    base = {"name": name, "type": kind, "collections": ["Collection"], "parent": None, "location": [0.0, 0.0, 0.0],
            "rotation_deg": [0.0, 0.0, 0.0], "scale": [1.0, 1.0, 1.0], "dimensions": [2.0, 2.0, 2.0], "visible": True,
            "hide_render": False, "data": name, "verts": 8, "faces": 6, "materials": [], "modifiers": [],
            "custom_props": {}}
    base.update(kw)
    return base


def snap(*objects, path="x.blend", **scene):
    s = {"name": "Scene", "engine": "BLENDER_EEVEE", "resolution": [1920, 1080, 100], "frames": [1, 250], "fps": 24.0,
         "camera": None, "world": None}
    s.update(scene)
    return {"filepath": path, "blender_version": "5.2.0", "scene": s, "objects": list(objects)}


class FakeTransport:
    name = "fake"

    def __init__(self, result, ms=7, warnings=None):
        self.result, self.ms, self.warnings, self.calls = result, ms, warnings or [], []

    def run(self, op, params, *, blend=None, save=None, timeout=None):
        self.calls.append((op, params, blend, timeout))
        return RunOutcome(result=self.result, modified=False, saved=None, blender_version="5.2.0 LTS",
                          duration_ms=self.ms, stdout="", warnings=list(self.warnings))


@pytest.fixture(autouse=True)
def blends(tmp_path, monkeypatch):
    for name in ("a.blend", "b.blend", "my file.blend"):
        (tmp_path / name).write_bytes(b"x")
    (tmp_path / "notes.txt").write_text("x")
    monkeypatch.chdir(tmp_path)
    return tmp_path


class Pair:
    """Transport answering the first call with `a` and the second with `b` (headless: one transport, two runs)."""
    name = "pair"

    def __init__(self, a, b, ms=7):
        self.parts = [FakeTransport(a, ms), FakeTransport(b, ms)]
        self.calls = []

    def run(self, op, params, **kw):
        part = self.parts[len(self.calls)]
        self.calls.append((op, params, kw.get("blend")))
        return part.run(op, params, **kw)


def invoke(argv, transport):
    inv, wants_help = flags.parse(cmd.SPEC, argv)
    assert not wants_help
    inv.transport = transport
    return cmd.SPEC.run(inv)


def test_registers_and_resolves(monkeypatch):
    monkeypatch.setitem(registry.COMMANDS, "diff", "blender_axi.commands.diff:SPEC")
    assert cmd.SPEC.name == "diff" and [p.name for p in cmd.SPEC.positionals] == ["a", "b"]


def test_changed_shape_order_and_hints():
    a = snap(obj("A"), obj("B"), obj("Keep"))
    b = snap(obj("A", location=[1.0, 0.0, 0.0]), obj("C"), obj("Keep"), engine="CYCLES")
    t = Pair(a, b, ms=5)
    res = invoke(["a.blend", "b.blend"], t)
    assert list(res.data) == ["a", "b", "objects", "added", "removed", "changed", "scene", "status", "duration_ms"]
    d = res.data
    assert d["a"] == "a.blend" and d["b"] == "b.blend"
    assert d["objects"] == "3 vs 3: 1 added, 1 removed, 1 changed, 1 unchanged"
    assert d["added"] == [{"name": "C", "type": "MESH"}] and d["removed"] == [{"name": "B", "type": "MESH"}]
    assert d["changed"] == [{"name": "A", "changes": "location 0,0,0 -> 1,0,0"}]
    assert d["scene"] == [{"setting": "engine", "from": "EEVEE", "to": "CYCLES"}]
    assert d["status"] == "different" and d["duration_ms"] == 10
    assert [c[0] for c in t.calls] == ["snapshot", "snapshot"]
    assert [c[2].name for c in t.calls] == ["a.blend", "b.blend"]
    assert res.help == ["Run `blender-axi object A b.blend` for the details"]
    toon.encode({**d, "help": res.help})


def test_hint_quotes_names_with_spaces():
    t = Pair(snap(obj("Big Cube")), snap(obj("Big Cube", scale=[2.0, 2.0, 2.0])))
    res = invoke(["a.blend", "my file.blend"], t)
    assert res.data["b"] == "my file.blend"
    assert res.help == ['Run `blender-axi object "Big Cube" "my file.blend"` for the details']


def test_identical_empty_state():
    s = snap(obj("A"), obj("B"))
    res = invoke(["a.blend", "a.blend"], Pair(s, s))
    assert list(res.data) == ["a", "b", "objects", "changes", "status", "duration_ms"]
    assert res.data["changes"] == "none (2 objects, scene settings equal)"
    assert res.data["status"] == "identical" and res.data["objects"].endswith("0 changed, 2 unchanged")
    assert "added" not in res.data and "truncated" not in res.data
    assert res.help == ["Run `blender-axi objects a.blend` to list the objects"]


def test_scene_only_difference_is_different():
    res = invoke(["a.blend", "b.blend"], Pair(snap(obj("A")), snap(obj("A"), fps=30.0)))
    assert res.data["status"] == "different" and "changes" not in res.data
    assert res.data["scene"] == [{"setting": "fps", "from": "24", "to": "30"}]
    assert res.help == []


def _many(n, **kw):
    return [obj(f"O{i:03d}", **kw) for i in range(n)]


def test_tables_cut_at_20_rows_with_truncated_line_and_hint():
    a = snap(*_many(57))
    b = snap(*_many(57, location=[1.0, 0.0, 0.0]))
    res = invoke(["a.blend", "b.blend"], Pair(a, b))
    assert len(res.data["changed"]) == 20 and res.data["truncated"] == "changed 20 of 57 (--full shows all)"
    assert list(res.data)[-3:] == ["truncated", "status", "duration_ms"]
    assert any("diff a.blend b.blend --full" in h for h in res.help)


def test_full_shows_all_rows():
    a = snap(*_many(30))
    b = snap(*_many(30, location=[1.0, 0.0, 0.0]))
    inv, _ = flags.parse(cmd.SPEC, ["a.blend", "b.blend", "--full"])
    inv.transport = Pair(a, b)
    res = cmd.SPEC.run(inv)
    assert len(res.data["changed"]) == 30 and "truncated" not in res.data
    assert not any("--full" in h for h in res.help)


def test_two_tables_cut_are_both_named():
    a = snap(*_many(25))
    b = snap(*_many(25, location=[1.0, 0.0, 0.0]), *[obj(f"New{i:03d}") for i in range(25)])
    res = invoke(["a.blend", "b.blend"], Pair(a, b))
    assert res.data["truncated"] == "added 20 of 25, changed 20 of 25 (--full shows all)" or \
        res.data["truncated"] == "changed 20 of 25, added 20 of 25 (--full shows all)"


def test_object_filter_applies_to_both_snapshots():
    a = snap(obj("A"), obj("B"))
    b = snap(obj("A", scale=[2.0, 2.0, 2.0]), obj("B", scale=[2.0, 2.0, 2.0]), obj("C"))
    res = invoke(["a.blend", "b.blend", "--object", "A"], Pair(a, b))
    assert res.data["objects"] == "1 vs 1: 0 added, 0 removed, 1 changed, 0 unchanged"
    assert [r["name"] for r in res.data["changed"]] == ["A"]


def test_collection_filter():
    a = snap(obj("A", collections=["Props"]), obj("B"))
    b = snap(obj("A", collections=["Props"], scale=[2.0, 2.0, 2.0]), obj("B", scale=[3.0, 3.0, 3.0]))
    res = invoke(["a.blend", "b.blend", "--collection", "Props"], Pair(a, b))
    assert [r["name"] for r in res.data["changed"]] == ["A"] and res.data["objects"].startswith("1 vs 1")


def test_object_missing_from_both_is_not_found():
    with pytest.raises(AxiError) as e:
        invoke(["a.blend", "b.blend", "--object", "Ghost"], Pair(snap(obj("A")), snap(obj("A"))))
    assert e.value.code == "NOT_FOUND" and "Ghost" in e.value.message and "objects a.blend" in e.value.help[0]


def test_empty_object_name_is_rejected():
    with pytest.raises(AxiError) as e:
        invoke(["a.blend", "b.blend", "--object", ""], Pair(snap(), snap()))
    assert e.value.exit_code == 2


def test_warnings_from_both_runs_are_merged():
    t = Pair(snap(obj("A")), snap(obj("A")))
    t.parts[0].warnings, t.parts[1].warnings = ["w1"], ["w1", "w2"]
    assert invoke(["a.blend", "b.blend"], t).data["warning"] == "w1; w2"


# -- positional rules ---------------------------------------------------------------------------

def test_headless_needs_two_files():
    with pytest.raises(AxiError) as e:
        invoke(["a.blend"], Pair(snap(), snap()))
    assert e.value.exit_code == 2 and e.value.message == "diff needs two files (or one file with --live)"


def test_missing_and_non_blend_files():
    with pytest.raises(AxiError) as e:
        invoke(["a.blend", "nope.blend"], Pair(snap(), snap()))
    assert e.value.code == "NOT_FOUND" and e.value.message == "file not found: nope.blend"
    with pytest.raises(AxiError) as e:
        invoke(["notes.txt", "a.blend"], Pair(snap(), snap()))
    assert e.value.exit_code == 2 and e.value.message == "expected a .blend file, got 'notes.txt'"


def test_no_blender_is_started_on_a_validation_error():
    t = Pair(snap(), snap())
    with pytest.raises(AxiError):
        invoke(["a.blend"], t)
    assert t.calls == []


# -- live ---------------------------------------------------------------------------------------

class Headless:
    def __init__(self, result):
        self.t = FakeTransport(result, ms=11)
        self.opts = None

    def __call__(self, opts):
        self.opts = opts
        return self.t


def test_live_compares_the_file_headless_with_the_session(monkeypatch):
    headless = Headless(snap(obj("A")))
    monkeypatch.setattr(cmd, "_headless", headless)
    live = FakeTransport(snap(obj("A", location=[1.0, 0.0, 0.0]), path="C:\\w\\s.blend", camera="Cam"), ms=3)
    inv, _ = flags.parse(cmd.SPEC, ["a.blend", "--live", "--port", "9999"])
    inv.transport = live
    res = cmd.SPEC.run(inv)
    assert headless.opts is inv.globals and headless.t.calls[0][0] == "snapshot" and headless.t.calls[0][2].name == "a.blend"
    assert live.calls[0][0] == "snapshot" and live.calls[0][2] is None
    assert res.data["a"] == "a.blend" and res.data["b"] == "(live session: C:/w/s.blend)"
    assert res.data["duration_ms"] == 14 and res.data["status"] == "different"
    assert res.data["scene"] == [{"setting": "camera", "from": "none", "to": "Cam"}]
    assert res.help == ["Run `blender-axi object A --live --port 9999` for the details"]


def test_live_unsaved_session_label(monkeypatch):
    monkeypatch.setattr(cmd, "_headless", Headless(snap(obj("A"))))
    inv, _ = flags.parse(cmd.SPEC, ["a.blend", "--live"])
    inv.transport = FakeTransport(snap(obj("A"), path=""))
    assert cmd.SPEC.run(inv).data["b"] == "(live session)"


def test_live_rejects_b():
    inv, _ = flags.parse(cmd.SPEC, ["a.blend", "b.blend", "--live"])
    inv.transport = FakeTransport(snap())
    with pytest.raises(AxiError) as e:
        cmd.SPEC.run(inv)
    assert e.value.exit_code == 2 and e.value.message == "--live compares <a> with the open session; drop <b>"


def test_live_truncation_hint_keeps_live_form(monkeypatch):
    monkeypatch.setattr(cmd, "_headless", Headless(snap(*_many(25))))
    inv, _ = flags.parse(cmd.SPEC, ["a.blend", "--live"])
    inv.transport = FakeTransport(snap(*_many(25, location=[1.0, 0.0, 0.0])))
    res = cmd.SPEC.run(inv)
    assert any("`blender-axi diff a.blend --live --full`" in h for h in res.help)


def test_headless_helper_builds_the_cli_lazy_transport():
    from blender_axi import cli
    from blender_axi.spec import GlobalOpts

    t = cmd._headless(GlobalOpts())
    assert isinstance(t, cli._LazyHeadless) and t.name == "headless"


def test_exit_code_flag_is_1_on_different_and_0_on_identical():
    a, b = snap(obj("A")), snap(obj("A", scale=[2.0, 2.0, 2.0]))
    different = invoke(["a.blend", "b.blend", "--exit-code"], Pair(a, b))
    assert different.exit_code == 1 and different.data["status"] == "different"
    assert "objects" in different.data  # the report is still printed
    same = invoke(["a.blend", "a.blend", "--exit-code"], Pair(a, a))
    assert same.exit_code == 0 and same.data["status"] == "identical"


def test_exit_code_defaults_to_0_without_the_flag():
    res = invoke(["a.blend", "b.blend"], Pair(snap(obj("A")), snap(obj("B"))))
    assert res.data["status"] == "different" and res.exit_code == 0


def test_exit_code_listed_in_help():
    flag = next(f for f in cmd.SPEC.flags if f.name == "--exit-code")
    assert "Exit 1 when the files differ" in flag.help
