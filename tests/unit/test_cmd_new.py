"""Unit tests for `new` and the `--user-prefs` hint carry-forward in `_common` (v1.1 U3)."""

from pathlib import Path

import pytest

from blender_axi import flags as flagparser
from blender_axi.commands import _common
from blender_axi.commands import new as cmd
from blender_axi.commands import scene as scene_cmd
from blender_axi.errors import AxiError
from blender_axi.runner import RunOutcome, SaveSpec
from blender_axi.spec import GlobalOpts, Invocation

WORK = Path(__file__).resolve().parents[2] / ".tmp" / "tests" / "unit_cmd_new"


class FakeTransport:
    name = "fake"

    def __init__(self, objects=0, size=61440):
        self.calls = []
        self.objects, self.size = objects, size

    def run(self, op, params, *, blend=None, save=None, timeout=None):
        self.calls.append((op, params, blend, save, timeout))
        result = {"path": params["out"], "template": params["template"], "objects": self.objects,
                  "size_bytes": self.size}
        return RunOutcome(result=result, modified=False, saved=None, blender_version="5.2.0 LTS",
                          duration_ms=420, stdout="", warnings=[])


@pytest.fixture
def work(monkeypatch):
    WORK.mkdir(parents=True, exist_ok=True)
    for p in WORK.iterdir():
        if p.is_file():
            p.unlink()
    monkeypatch.chdir(WORK)
    return WORK


def invoke(argv, transport=None):
    inv, wants_help = flagparser.parse(cmd.SPEC, argv)
    assert not wants_help
    inv.transport = transport or FakeTransport()
    return cmd.run(inv), inv.transport


def fails(argv, transport=None):
    with pytest.raises(AxiError) as exc:
        invoke(argv, transport)
    return exc.value


def test_creates_with_empty_template_by_default(work):
    res, t = invoke(["fresh.blend"])
    op, params, blend, save, _ = t.calls[0]
    assert op == "new" and params == {"out": str(work / "fresh.blend"), "template": "empty"}
    assert blend is None and save == SaveSpec("none")
    assert list(res.data) == ["created", "template", "objects", "size_kb", "blender", "duration_ms"]
    assert res.data == {"created": "fresh.blend", "template": "empty", "objects": 0, "size_kb": 60.0,
                        "blender": "5.2.0 LTS", "duration_ms": 420}
    assert res.exit_code == 0


def test_default_template_and_size_rounding(work):
    res, t = invoke(["sub/dir/d.blend", "--template", "default"], FakeTransport(objects=3, size=65000))
    assert t.calls[0][1]["template"] == "default"
    assert res.data["created"] == "sub/dir/d.blend" and res.data["objects"] == 3 and res.data["size_kb"] == 63.5


def test_hints_name_the_file_and_the_next_steps(work):
    res, _ = invoke(["fresh.blend"])
    assert res.help == [
        "Run `blender-axi objects fresh.blend` to list its objects",
        "Run `blender-axi apply <steps.json> fresh.blend --in-place` to build the scene from a list of steps",
        "Run `blender-axi render fresh.blend --out <path.png>` to render it",
    ]


def test_hints_quote_names_with_spaces(work):
    res, _ = invoke(["my scene.blend"])
    assert "objects \"my scene.blend\"" in res.help[0]


def test_hints_carry_user_prefs(work):
    inv, _ = flagparser.parse(cmd.SPEC, ["fresh.blend", "--user-prefs"])
    inv.transport = FakeTransport()
    res = cmd.run(inv)
    assert inv.globals.user_prefs is True
    assert all(h.split("`")[1].endswith("--user-prefs") or "--user-prefs `" in h for h in res.help)
    assert "blender-axi objects fresh.blend --user-prefs" in res.help[0]
    assert res.help[1].startswith("Run `blender-axi apply <steps.json> fresh.blend --in-place --user-prefs`")


def test_suffix_must_be_blend(work):
    err = fails(["fresh.txt"])
    assert err.code == "VALIDATION_ERROR" and err.exit_code == 2 and ".blend" in err.message
    assert fails(["noext"]).exit_code == 2


def test_uppercase_suffix_accepted(work):
    res, _ = invoke(["FRESH.BLEND"])
    assert res.data["created"] == "FRESH.BLEND"


def test_bad_template_choice_is_validation_error():
    with pytest.raises(AxiError) as exc:
        flagparser.parse(cmd.SPEC, ["a.blend", "--template", "huge"])
    assert exc.value.exit_code == 2


def test_file_is_required():
    with pytest.raises(AxiError) as exc:
        flagparser.parse(cmd.SPEC, [])
    assert exc.value.exit_code == 2


def test_existing_file_refused_without_blender(work):
    (work / "there.blend").write_bytes(b"")
    t = FakeTransport()
    err = fails(["there.blend", "--template", "default"], t)
    assert err.code == "WRITE_REFUSED" and err.exit_code == 1 and err.message == "output exists: there.blend"
    assert err.help[0] == "Run `blender-axi new there.blend --template default --force` to overwrite"
    assert "--out" not in " ".join(err.help)
    assert t.calls == []


def test_refusal_hint_carries_user_prefs(work):
    (work / "there.blend").write_bytes(b"")
    inv, _ = flagparser.parse(cmd.SPEC, ["there.blend", "--user-prefs"])
    inv.transport = FakeTransport()
    with pytest.raises(AxiError) as exc:
        cmd.run(inv)
    assert exc.value.help[0] == "Run `blender-axi new there.blend --user-prefs --force` to overwrite"


def test_force_overwrites(work):
    (work / "there.blend").write_bytes(b"")
    res, t = invoke(["there.blend", "--force"])
    assert t.calls[0][1]["out"] == str(work / "there.blend") and res.data["created"] == "there.blend"


def test_live_is_exit_2_before_any_request(work):
    inv, _ = flagparser.parse(cmd.SPEC, ["a.blend", "--live"])
    inv.transport = FakeTransport()
    with pytest.raises(AxiError) as exc:
        cmd.run(inv)
    assert exc.value.exit_code == 2 and "live mode works on the open session" in exc.value.message
    assert inv.transport.calls == []


def test_timeout_is_forwarded(work):
    inv, _ = flagparser.parse(cmd.SPEC, ["a.blend", "--timeout", "7"])
    inv.transport = FakeTransport()
    cmd.run(inv)
    assert inv.transport.calls[0][4] == 7.0


def test_spec_is_discoverable():
    assert cmd.SPEC.name == "new" and cmd.SPEC.positionals[0].required
    assert {f.name for f in cmd.SPEC.flags} == {"--template", "--force"}


# -- carry-forward helpers in _common ----------------------------------------------------------

def _inv(**opts) -> Invocation:
    return Invocation(command="scene", globals=GlobalOpts(**opts))


@pytest.mark.parametrize("opts,want", [
    ({}, ""),
    ({"user_prefs": True}, " --user-prefs"),
    ({"live": True}, " --live"),
    ({"live": True, "host": "10.0.0.2", "port": 1234}, " --live --host 10.0.0.2 --port 1234"),
    ({"live": True, "port": 1234, "user_prefs": True}, " --live --port 1234 --user-prefs"),
])
def test_carry_suffix(opts, want):
    assert _common.carry_suffix(_inv(**opts)) == want
    assert _common.live_suffix(_inv(**opts)) == want


def test_hints_append_user_prefs():
    assert _common.Hints(_inv(user_prefs=True)).cmd("objects <file>") == "blender-axi objects <file> --user-prefs"
    assert _common.Hints(_inv()).cmd("objects <file>") == "blender-axi objects <file>"
    live = _common.Hints(_inv(live=True, port=1234, user_prefs=True)).cmd("objects <file>")
    assert live == "blender-axi objects --live --port 1234 --user-prefs"


def test_replay_command_appends_user_prefs():
    from blender_axi.commands import export

    inv, _ = flagparser.parse(export.SPEC, ["a.blend", "--out", "o.glb", "--user-prefs"])
    assert _common.replay_command(inv, export.SPEC, "--force") == \
        "blender-axi export a.blend --out o.glb --user-prefs --force"


def test_refused_default_alternative_unchanged():
    inv = Invocation(command="export", positionals={}, flags={}, globals=GlobalOpts())
    from blender_axi.commands import export

    err = _common.refused(inv, export.SPEC, str(WORK / "x.glb"))
    assert err.help[1] == "Or choose another path with --out <path>"


def test_scene_hints_carry_user_prefs(monkeypatch):
    class T:
        def run(self, op, params, **kw):
            return RunOutcome(
                result={"scene": "Scene", "engine": "BLENDER_EEVEE", "res_x": 1, "res_y": 1, "percent": 100,
                        "frame_start": 1, "frame_end": 2, "frame_current": 1, "fps": 24, "fps_base": 1,
                        "camera": None, "object_count": 0, "types": {}, "collections": [], "materials": []},
                modified=False, saved=None, blender_version="5.2.0 LTS", duration_ms=1, stdout="")

    blend = WORK / "s.blend"
    WORK.mkdir(parents=True, exist_ok=True)
    blend.write_bytes(b"")
    inv, _ = flagparser.parse(scene_cmd.SPEC, [str(blend), "--user-prefs"])
    inv.transport = T()
    res = scene_cmd.run(inv)
    assert all(h.split("`")[1].endswith("--user-prefs") for h in res.help)
