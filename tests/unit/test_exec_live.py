"""v1.1 U1: `exec --live` (command level; fake transport plus the mock live server). Headless exec: test_cmd_exec.py."""

import json

import pytest

from blender_axi import flags
from blender_axi.commands import exec_cmd
from blender_axi.commands._common import display_path
from blender_axi.errors import AxiError
from blender_axi.runner import RunOutcome


@pytest.fixture
def tmp_path(request):
    """Project-local replacement for pytest's tmp_path (which lives in the system temp)."""
    from blender_axi import paths

    d = paths.run_dir("unit-exec-live")
    yield d
    paths.cleanup(d, False)


@pytest.fixture
def blend(tmp_path):
    p = tmp_path / "scene.blend"
    p.write_bytes(b"BLEND")
    return p


def invoke(argv, transport):
    inv, wants_help = flags.parse(exec_cmd.SPEC, argv)
    assert not wants_help
    inv.transport = transport
    return exec_cmd.run(inv), inv.transport


def fail(argv, transport):
    with pytest.raises(AxiError) as exc:
        invoke(argv, transport)
    return exc.value


# --- exec --live ----------------------------------------------------------------------------------

LIVE = ["--live", "--host", "127.0.0.1", "--port", "9999"]


class LiveFake:
    """Stands in for LiveTransport: answers `ping` with the open file, records everything."""

    name = "live"

    def __init__(self, file="C:\\work\\open.blend", exec_outcome=None):
        self.file = file
        self.calls = []
        self.exec_outcome = exec_outcome or RunOutcome(
            {"result": {"n": 4}, "stdout": ""}, False, None, "5.2.0 LTS", 9, "", undo="unavailable")

    def run(self, op, params, *, blend=None, save=exec_cmd.SaveSpec("none"), timeout=None):
        self.calls.append({"op": op, "params": params, "blend": blend, "save": save, "timeout": timeout})
        if op == "ping":
            return RunOutcome({"file": self.file}, False, None, "5.2.0 LTS", 1, "")
        if self.exec_outcome.modified and save.mode == "in_place":
            self.exec_outcome.saved = save.path
        return self.exec_outcome

    @property
    def ops(self):
        return [c["op"] for c in self.calls]


def modified_outcome(**kw):
    return RunOutcome({"result": {"n": 4}, "stdout": ""}, True, None, "5.2.0 LTS", 9, "", **kw)


def test_live_out_is_refused_with_no_copy_message():
    t = LiveFake()
    err = fail([*LIVE, "--code", "x=1", "--out", "o.blend", "--allow-exec"], t)
    assert err.code == "VALIDATION_ERROR" and err.exit_code == 2 and "no copy" in err.message
    assert "--in-place" in err.message and t.calls == []


def test_live_with_a_positional_file_is_refused(blend):
    t = LiveFake()
    err = fail([str(blend), *LIVE, "--code", "x=1", "--allow-exec"], t)
    assert err.code == "VALIDATION_ERROR" and err.exit_code == 2
    assert err.message == "--live uses the file open in Blender; drop <file>" and t.calls == []


def test_live_gate_blocks_before_any_request():
    t = LiveFake()
    err = fail([*LIVE, "--code", "result={}", "--in-place", "--user-prefs"], t)
    assert err.code == "EXEC_NOT_ALLOWED" and err.exit_code == 2 and t.calls == []
    assert "OPEN Blender session" in err.message and "--allow-exec" in err.message
    assert err.help[0].startswith("Run the same command with --allow-exec: blender-axi exec ")
    for flag in ("--code \"result={}\"", "--in-place", "--user-prefs", "--live --host 127.0.0.1 --port 9999"):
        assert flag in err.help[0]
    assert err.help[0].endswith("--allow-exec") and "Nothing is saved to disk unless you pass --in-place" in err.help[1]


def test_headless_gate_wording_is_unchanged(blend):
    err = fail([str(blend), "--code", "x=1"], LiveFake())
    assert err.message == "exec runs arbitrary Python inside Blender and needs --allow-exec"
    assert "OPEN" not in err.message


def test_live_asks_ping_first_then_runs_exec_without_a_save():
    res, t = invoke([*LIVE, "--code", "result={'n': 4}", "--allow-exec", "--timeout", "7"], LiveFake())
    assert t.ops == ["ping", "exec"]
    call = t.calls[1]
    assert call["params"] == {"code": "result={'n': 4}"} and call["blend"] is None
    assert call["save"] == exec_cmd.SaveSpec("none") and call["timeout"] == 7.0
    assert res.data["file"] == "C:/work/open.blend" and res.data["status"] == "ok"


def test_live_in_place_saves_the_open_file():
    t = LiveFake(exec_outcome=modified_outcome(undo="pushed"))
    res, t = invoke([*LIVE, "--code", "x=1", "--in-place", "--allow-exec"], t)
    assert t.calls[1]["save"] == exec_cmd.SaveSpec("in_place", "C:\\work\\open.blend")
    assert res.data["saved"] == "C:/work/open.blend" and res.data["modified"] is True
    assert not any("only in the open session" in h for h in res.help)


def test_live_in_place_without_an_open_file_is_refused_before_exec():
    t = LiveFake(file="")
    err = fail([*LIVE, "--code", "x=1", "--in-place", "--allow-exec"], t)
    assert err.code == "VALIDATION_ERROR" and err.exit_code == 2
    assert "the open session has no file; save it in Blender first" == err.message
    assert t.ops == ["ping"]


def test_live_unsaved_session_without_in_place_is_fine():
    res, t = invoke([*LIVE, "--code", "x=1", "--allow-exec"], LiveFake(file=""))
    assert res.data["file"] == "(unsaved)" and t.ops == ["ping", "exec"]


def test_live_modified_but_not_saved():
    res, _ = invoke([*LIVE, "--code", "x=1", "--allow-exec"], LiveFake(exec_outcome=modified_outcome(undo="pushed")))
    d = res.data
    assert d["modified"] is True and d["saved"] == "none (live session, unsaved)"
    assert d["undo"] == "pushed (Ctrl+Z in Blender reverts)"
    assert res.help[0] == "Inspect the session: `blender-axi objects --live --host 127.0.0.1 --port 9999`"
    assert res.help[-1] == ("Changes are only in the open session: save in Blender, or rerun with --in-place "
                            "to save the open file")


def test_live_not_modified():
    res, _ = invoke([*LIVE, "--code", "x=1", "--allow-exec"], LiveFake())
    d = res.data
    assert d["modified"] is False and d["saved"] == "none (no changes)"
    assert d["undo"] == "unavailable (headless server)"
    assert len(res.help) == 1 and res.help[0].startswith("Inspect the session")


def test_live_data_order_and_optional_fields():
    outcome = modified_outcome(undo="pushed", frame_changed="1 -> 10")
    outcome.warnings = ["Blender 6.0 untested"]
    res, _ = invoke([*LIVE, "--code", "x=1", "--allow-exec"], LiveFake(exec_outcome=outcome))
    assert list(res.data) == ["file", "status", "result", "stdout", "modified", "saved", "undo", "frame_changed",
                              "duration_ms", "warning"]
    assert res.data["frame_changed"] == "1 -> 10"
    res, _ = invoke([*LIVE, "--code", "x=1", "--allow-exec"], LiveFake(exec_outcome=RunOutcome(
        {"result": {"a": 1}, "stdout": ""}, False, None, "5.2", 3, "")))
    assert list(res.data) == ["file", "status", "result", "stdout", "modified", "saved", "duration_ms"]


def test_live_empty_result_in_toon_and_json():
    from blender_axi import toon

    outcome = RunOutcome({"result": {}, "stdout": ""}, False, None, "5.2", 1, "", undo="unavailable")
    res, _ = invoke([*LIVE, "--code", "pass", "--allow-exec"], LiveFake(exec_outcome=outcome))
    assert res.data["result"] == exec_cmd.NO_RESULT
    assert "result: none (the code did not assign result)" in toon.encode(res.data)
    assert json.loads(toon.encode_json(res.data))["result"] == exec_cmd.NO_RESULT


def test_live_script_is_read_and_a_missing_script_never_reaches_the_server(tmp_path):
    s = tmp_path / "s.py"
    s.write_text("result={'a': 1}\n", encoding="utf-8")
    _, t = invoke([*LIVE, "--script", str(s), "--allow-exec"], LiveFake())
    assert t.calls[1]["params"] == {"code": "result={'a': 1}\n"}
    t = LiveFake()
    err = fail([*LIVE, "--script", str(tmp_path / "nope.py"), "--allow-exec"], t)
    assert err.code == "NOT_FOUND" and t.calls == []


@pytest.mark.parametrize("argv", [["--allow-exec"], ["--allow-exec", "--code", "x=1", "--script", "s.py"]])
def test_live_argument_errors_exit_2_without_requests(argv):
    t = LiveFake()
    err = fail([*LIVE, *argv], t)
    assert err.exit_code == 2 and t.calls == []


def test_live_truncated_stdout_keeps_the_full_hint():
    outcome = RunOutcome({"result": {"a": 1}, "stdout": "a" * 4000}, False, None, "5.2", 1, "")
    res, _ = invoke([*LIVE, "--code", "print(1)", "--allow-exec"], LiveFake(exec_outcome=outcome))
    assert "(truncated, 4000 chars total)" in res.data["stdout"] and any("--full" in h for h in res.help)


def test_live_in_place_reports_a_new_backup(tmp_path):
    d = tmp_path
    try:
        open_file = d / "open.blend"
        open_file.write_bytes(b"BLEND")

        class Saving(LiveFake):
            def run(self, op, params, **kw):
                if op == "exec":
                    (d / "open.blend1").write_bytes(b"old")
                return super().run(op, params, **kw)

        res, _ = invoke([*LIVE, "--code", "x=1", "--in-place", "--allow-exec"],
                        Saving(file=str(open_file), exec_outcome=modified_outcome(undo="pushed")))
        assert res.data["backup"] == display_path(str(open_file) + "1")
    finally:
        from blender_axi import paths

        paths.cleanup(d, False)


def paths_dir():
    from blender_axi import paths

    return paths.run_dir("unit-exec-live")


# --- exec --live through cli.main against the mock server ------------------------------------------

def run_cli(capsys, argv):
    from blender_axi import cli

    code = cli.main(["--json", *argv])
    out, err = capsys.readouterr()
    return code, json.loads(out), err


def test_cli_live_exec_end_to_end(capsys):
    import sys as _sys
    from pathlib import Path

    _sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from support.mock_live_server import DEFAULT_CANNED, MockLiveServer

    canned = {**DEFAULT_CANNED, "ping": {**DEFAULT_CANNED["ping"], "file": "C:\\mock\\basic.blend"},
              "exec": {"result": {}, "stdout": "", "__modified__": True, "__undo__": False,
                       "__frame_changed__": "1 -> 10"}}
    with MockLiveServer(canned=canned) as srv:
        addr = ["--host", "127.0.0.1", "--port", str(srv.port)]
        code, data, err = run_cli(capsys, ["exec", "--live", *addr, "--code", "result={}"])
        assert code == 2 and data["code"] == "EXEC_NOT_ALLOWED" and "OPEN Blender session" in data["error"]
        assert srv.requests == []  # the gate runs before any socket is opened
        code, data, err = run_cli(capsys, ["exec", "--live", *addr, "--code", "result={}", "--out", "o.blend",
                                           "--allow-exec"])
        assert code == 2 and "no copy" in data["error"] and srv.requests == []
        code, data, err = run_cli(capsys, ["exec", "--live", *addr, "--code", "result={}", "--allow-exec"])
        assert code == 0 and err == ""
        assert data["file"] == "C:/mock/basic.blend" and data["result"] == "none (the code did not assign result)"
        assert data["modified"] is True and data["saved"] == "none (live session, unsaved)"
        assert data["undo"] == "unavailable (headless server)" and data["frame_changed"] == "1 -> 10"
        assert [r["op"] for r in srv.requests] == ["ping", "exec"]
        assert srv.requests[1]["strict_json"] is False and srv.requests[1]["undo"] is True
        assert any("--in-place" in h for h in data["help"]) and any(f"--port {srv.port}" in h for h in data["help"])
