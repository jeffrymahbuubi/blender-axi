"""U6: gated `exec` command and the `exec` op (no real Blender)."""

import importlib
import json
import sys

import pytest

from blender_axi import flags, registry
from blender_axi.commands import exec_cmd
from blender_axi.commands._common import display_path
from blender_axi.errors import AxiError
from blender_axi.runner import RunOutcome


class FakeTransport:
    name = "fake"

    def __init__(self, outcome=None):
        self.calls = []
        self.outcome = outcome or RunOutcome(
            result={"result": {"n": 5}, "stdout": ""}, modified=False, saved=None,
            blender_version="5.2.0 LTS", duration_ms=7, stdout="", warnings=[])

    def run(self, op, params, *, blend, save, timeout=None):
        self.calls.append({"op": op, "params": params, "blend": blend, "save": save, "timeout": timeout})
        return self.outcome


@pytest.fixture
def tmp_path(request):
    """Project-local replacement for pytest's tmp_path (which lives in the system temp)."""
    from blender_axi import paths

    d = paths.run_dir("unit-exec")
    yield d
    paths.cleanup(d, False)


@pytest.fixture
def blend(tmp_path):
    p = tmp_path / "scene.blend"
    p.write_bytes(b"BLEND")
    return p


def invoke(argv, transport=None):
    inv, wants_help = flags.parse(exec_cmd.SPEC, argv)
    assert not wants_help
    inv.transport = transport or FakeTransport()
    return exec_cmd.run(inv), inv.transport


def fail(argv, transport=None):
    with pytest.raises(AxiError) as exc:
        invoke(argv, transport)
    return exc.value


def test_gate_blocks_before_transport(blend):
    t = FakeTransport()
    err = fail([str(blend), "--code", "result={}"], t)
    assert err.code == "EXEC_NOT_ALLOWED" and err.exit_code == 2
    assert t.calls == []
    hint = "\n".join(err.help)
    assert "--allow-exec" in hint and "blender-axi exec" in hint and '--code "result={}"' in hint
    assert str(blend) in hint


def test_gate_hint_keeps_flags_and_quotes(blend):
    err = fail([str(blend), "--code", "result={'a': 1}", "--out", "o.blend", "--force", "--timeout", "30"])
    hint = err.help[0]
    assert "--code \"result={'a': 1}\"" in hint and "--out o.blend" in hint and "--force" in hint
    assert "--timeout 30" in hint and hint.endswith("--allow-exec")


def test_gate_hint_long_code_uses_placeholder():
    err = fail(["--code", "x=1;" * 100])
    assert "--code <python>" in err.help[0] and err.help[0].endswith("--allow-exec")


def test_gate_end_to_end_never_locates_blender(monkeypatch, capsys, blend):
    from blender_axi import cli, locate

    def boom(*a, **k):
        raise AssertionError("Blender must not be located without --allow-exec")

    monkeypatch.setattr(locate, "find_blender", boom)
    monkeypatch.setitem(registry.COMMANDS, "exec", "blender_axi.commands.exec_cmd:SPEC")
    code = cli.main(["exec", str(blend), "--code", "result={}", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert code == 2 and out["code"] == "EXEC_NOT_ALLOWED"
    assert any("--allow-exec" in h for h in out["help"])


@pytest.mark.parametrize("argv", [
    ["--allow-exec"],
    ["--allow-exec", "--code", "x=1", "--script", "s.py"],
    ["--allow-exec", "--code", "  "],
    ["--allow-exec", "--code", "x=1", "--in-place", "--out", "o.blend"],
    ["--allow-exec", "--code", "x=1", "--out", "o.txt"],
])
def test_argument_errors_exit_2(argv, blend):
    t = FakeTransport()
    err = fail([str(blend), *argv], t)
    assert err.code == "VALIDATION_ERROR" and err.exit_code == 2 and t.calls == []
    assert "blender-axi exec --help" in err.help


def test_missing_file_and_script(blend, tmp_path):
    err = fail([str(tmp_path / "nope.blend"), "--code", "x=1", "--allow-exec"])
    assert err.code == "NOT_FOUND"
    err = fail([str(blend), "--script", str(tmp_path / "nope.py"), "--allow-exec"])
    assert err.code == "NOT_FOUND"


def test_default_copy_goes_to_exec_dir(blend):
    from blender_axi import paths

    res, t = invoke([str(blend), "--code", "result={}", "--allow-exec"])
    call = t.calls[0]
    assert call["op"] == "exec" and call["params"] == {"code": "result={}"}
    assert call["save"].mode == "copy"
    assert call["save"].path.startswith(str(paths.tmp_root() / "exec"))
    assert call["blend"] == blend.resolve()
    assert res.data["modified"] is False and res.data["saved"] == "none (no changes)"
    assert res.data["result"] == {"n": 5} and res.data["status"] == "ok" and res.help == []


def test_script_is_read(blend, tmp_path):
    s = tmp_path / "s.py"
    s.write_text("result={'a': 1}\n", encoding="utf-8")
    _, t = invoke([str(blend), "--script", str(s), "--allow-exec"])
    assert t.calls[0]["params"]["code"] == "result={'a': 1}\n"


def test_in_place_save_spec(blend):
    res, t = invoke([str(blend), "--code", "x=1", "--in-place", "--allow-exec"])
    assert t.calls[0]["save"].mode == "in_place" and t.calls[0]["save"].path == str(blend.resolve())
    assert "backup" not in res.data  # nothing saved by the fake, no .blend1 on disk


def test_in_place_reports_backup_when_blend1_appears(blend):
    class Saving(FakeTransport):
        def run(self, op, params, *, blend, save, timeout=None):
            (blend.parent / "scene.blend1").write_bytes(b"old")
            self.outcome = RunOutcome({"result": {}, "stdout": ""}, True, str(blend), "5.2", 1, "")
            return super().run(op, params, blend=blend, save=save, timeout=timeout)

    res, _ = invoke([str(blend), "--code", "x=1", "--in-place", "--allow-exec"], Saving())
    assert res.data["backup"] == display_path(str(blend) + "1")
    assert res.data["saved"] == display_path(str(blend))
    assert "\\" not in res.data["backup"] + res.data["saved"] + res.help[0]
    assert res.help and "objects" in res.help[0]


def test_saved_backup_and_hint_use_forward_slashes_outside_cwd(blend, tmp_path, monkeypatch):
    """U10: no Windows backslashes (TOON would quote and double them); absolute path when not under the cwd."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    target = tmp_path / "out" / "copy.blend"

    class Saving(FakeTransport):
        def run(self, op, params, *, blend, save, timeout=None):
            (blend.parent / "scene.blend1").write_bytes(b"old")
            self.outcome = RunOutcome({"result": {}, "stdout": ""}, True, str(target), "5.2", 1, "")
            return super().run(op, params, blend=blend, save=save, timeout=timeout)

    res, _ = invoke([str(blend), "--code", "x=1", "--in-place", "--allow-exec"], Saving())
    assert res.data["saved"] == target.as_posix()
    assert res.data["backup"] == (blend.parent / "scene.blend1").as_posix()
    assert res.help[0] == f"Inspect the result: `blender-axi objects {target.as_posix()}`"
    assert "\\" not in res.data["saved"] + res.data["backup"] + res.help[0]


def test_out_refuses_overwrite_without_force(blend, tmp_path):
    out = tmp_path / "o.blend"
    out.write_bytes(b"x")
    t = FakeTransport()
    err = fail([str(blend), "--code", "x=1", "--out", str(out), "--allow-exec"], t)
    assert err.code == "WRITE_REFUSED" and t.calls == []
    assert "--force" in err.help[0]
    _, t = invoke([str(blend), "--code", "x=1", "--out", str(out), "--force", "--allow-exec"])
    assert t.calls[0]["save"] == exec_cmd.SaveSpec("copy", str(out.resolve()))


def test_out_equal_to_input_is_rejected(blend):
    err = fail([str(blend), "--code", "x=1", "--out", str(blend), "--force", "--allow-exec"])
    assert err.code == "VALIDATION_ERROR" and any("--in-place" in h for h in err.help)


def test_saved_copy_gets_help_and_warning(blend):
    out = RunOutcome({"result": {"k": 1}, "stdout": ""}, True, "C:/x/y.blend", "6.0", 3, "", ["Blender 6.0 untested"])
    res, _ = invoke([str(blend), "--code", "x=1", "--allow-exec"], FakeTransport(out))
    assert res.data["saved"] == "C:/x/y.blend" and res.data["warning"] == "Blender 6.0 untested"
    assert "blender-axi objects C:/x/y.blend" in res.help[0]


def test_stdout_truncation_and_full(blend):
    big = "a" * 4000
    out = RunOutcome({"result": {}, "stdout": big}, False, None, "5.2", 1, "")
    res, _ = invoke([str(blend), "--code", "print(1)", "--allow-exec"], FakeTransport(out))
    assert "(truncated, 4000 chars total)" in res.data["stdout"] and len(res.data["stdout"]) < 1700
    assert any("--full" in h for h in res.help)
    res, _ = invoke([str(blend), "--code", "print(1)", "--allow-exec", "--full"], FakeTransport(out))
    assert res.data["stdout"] == big and res.help == []


def test_file_inferred_from_cwd(blend, monkeypatch):
    monkeypatch.chdir(blend.parent)
    res, t = invoke(["--code", "x=1", "--allow-exec"])
    assert res.data["file"] == "scene.blend" and t.calls[0]["blend"] == blend.resolve()


def test_file_inference_errors(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    err = fail(["--code", "x=1", "--allow-exec"])
    assert err.code == "VALIDATION_ERROR" and "none in" in err.message
    (tmp_path / "a.blend").write_bytes(b"1")
    (tmp_path / "b.blend").write_bytes(b"2")
    (tmp_path / "a.blend1").write_bytes(b"3")
    err = fail(["--code", "x=1", "--allow-exec"])
    assert err.exit_code == 2 and "a.blend, b.blend" in err.help[0]


def test_spec_help_renders():
    from blender_axi import helptext

    out = helptext.render(exec_cmd.SPEC)
    assert "--allow-exec" in [r["flag"] for r in out["flags"]]


# --- op_exec with a fake bpy -------------------------------------------------------------------

@pytest.fixture
def op(fake_bpy):
    sys.modules.pop("blender_axi.blender_side.op_exec", None)
    return importlib.import_module("blender_axi.blender_side.op_exec")


def test_op_result_and_stdout(op, fake_bpy):
    fake_bpy.data.objects = ["a", "b"]
    out = op.run({"code": "print('hi')\nresult = {'n': len(bpy.data.objects), 'p': (1, 2)}"})
    assert out == {"result": {"n": 2, "p": [1, 2]}, "stdout": "hi\n"}


def test_op_unserialisable_values_use_repr(op):
    out = op.run({"code": "result = {'o': object, 's': {1, 2}}"})
    assert out["result"]["o"].startswith("<class")


def test_op_default_result_is_empty_dict(op):
    assert op.run({"code": "x = 1"})["result"] == {}


@pytest.mark.parametrize("code", ["result = [1]", "result = 3", "result = None"])
def test_op_non_dict_result_fails(op, code):
    from blender_axi.blender_side.ops import OpError

    with pytest.raises(OpError) as e:
        op.run({"code": code})
    assert e.value.code == "EXEC_FAILED" and "must be a dict" in e.value.message


def test_op_exception_is_exec_failed_with_traceback(op):
    import traceback

    from blender_axi.blender_side.ops import OpError

    with pytest.raises(OpError) as e:
        op.run({"code": "x = 1\ny = 1 / 0"})
    assert e.value.code == "EXEC_FAILED"
    assert "ZeroDivisionError" in e.value.message and "line 2" in e.value.message
    assert "ZeroDivisionError" in "".join(traceback.format_exception(e.value))


def test_op_syntax_error_and_system_exit(op):
    from blender_axi.blender_side.ops import OpError

    with pytest.raises(OpError) as e:
        op.run({"code": "def ("})
    assert e.value.code == "EXEC_FAILED" and "SyntaxError" in e.value.message
    with pytest.raises(OpError) as e:
        op.run({"code": "raise SystemExit(3)"})
    assert e.value.code == "EXEC_FAILED"


def test_op_rejects_empty_code(op):
    from blender_axi.blender_side.ops import OpError

    with pytest.raises(OpError):
        op.run({"code": ""})


def test_op_runs_through_ops_dispatch(op):
    from blender_axi.blender_side import ops

    assert ops.run("exec", {"code": "result = {'ok': True}"})["result"] == {"ok": True}


# --- K9, frame_changed (headless) -------------------------------------------------------------------

def test_empty_result_is_a_definitive_string_in_toon_and_json(blend):
    from blender_axi import toon

    out = RunOutcome({"result": {}, "stdout": ""}, False, None, "5.2", 1, "")
    res, _ = invoke([str(blend), "--code", "pass", "--allow-exec"], FakeTransport(out))
    assert res.data["result"] == exec_cmd.NO_RESULT == "none (the code did not assign result)"
    assert "result: none (the code did not assign result)" in toon.encode(res.data)
    assert json.loads(toon.encode_json(res.data))["result"] == "none (the code did not assign result)"
    assert "\nresult:\n" not in toon.encode(res.data) + "\n"  # never a bare `result:` line


def test_missing_result_key_is_also_none_string(blend):
    out = RunOutcome({"stdout": ""}, False, None, "5.2", 1, "")
    res, _ = invoke([str(blend), "--code", "pass", "--allow-exec"], FakeTransport(out))
    assert res.data["result"] == exec_cmd.NO_RESULT


def test_headless_frame_changed_only_when_set(blend):
    out = RunOutcome({"result": {"f": 10}, "stdout": ""}, False, None, "5.2", 1, "", frame_changed="1 -> 10")
    res, _ = invoke([str(blend), "--code", "x=1", "--allow-exec"], FakeTransport(out))
    assert res.data["frame_changed"] == "1 -> 10" and res.data["modified"] is False
    assert list(res.data) == ["file", "status", "result", "stdout", "modified", "saved", "frame_changed", "duration_ms"]
    res, _ = invoke([str(blend), "--code", "x=1", "--allow-exec"])
    assert "frame_changed" not in res.data and "undo" not in res.data
