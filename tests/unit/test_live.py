"""Unit tests for live mode (U8): `blender_axi.live`, the `live` command and `--live` through `cli.main`.

Everything runs against tests/support/mock_live_server.py (IPv4-only like the real add-on); no Blender needed.
Tests marked `needs_cli_splice` need the lead's cli.py change (`--live` builds `live.LiveTransport`) and skip until it
is applied: test_cli_live_scene_end_to_end and test_cli_live_objects_forwards_filters.
"""

from __future__ import annotations

import inspect
import json
import socket
import sys
import threading
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from support.mock_live_server import MockLiveServer  # noqa: E402

from blender_axi import cli, live, registry  # noqa: E402
from blender_axi.commands import live as cmd_live  # noqa: E402
from blender_axi.errors import AxiError  # noqa: E402
from blender_axi.runner import SaveSpec  # noqa: E402

needs_cli_splice = pytest.mark.skipif(
    "LiveTransport" not in inspect.getsource(cli), reason="needs the lead's cli.py splice (U8, LiveTransport)")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def mock():
    with MockLiveServer() as srv:
        yield srv


def transport_for(srv, timeout=5.0):
    return live.LiveTransport("127.0.0.1", srv.port, timeout=timeout)


class RawServer:
    """Plain one-shot socket server: records the exact request bytes, replies with scripted chunks."""

    def __init__(self, chunks, close_early=False):
        self.chunks, self.close_early, self.received = chunks, close_early, b""
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        conn, _ = self.sock.accept()
        with conn:
            while b"\0" not in self.received:
                data = conn.recv(4096)
                if not data:
                    return
                self.received += data
            if self.close_early:
                return
            for chunk in self.chunks:
                conn.sendall(chunk)

    def close(self):
        self.sock.close()
        self.thread.join(timeout=5)


# -- framing ------------------------------------------------------------------------------------

def test_request_framing_and_response_split_across_chunks():
    body = json.dumps({"status": "ok", "result": {"a": 1, "text": "h\u00e9llo"}}).encode("utf-8")
    cut1, cut2 = 5, len(body) - 3
    raw = RawServer([body[:cut1], body[cut1:cut2], body[cut2:] + b"\0" + b"ignored trailing bytes"])
    try:
        response = live.send("127.0.0.1", raw.port, "result = {}", strict_json=True, timeout=5)
    finally:
        raw.close()
    assert response == {"status": "ok", "result": {"a": 1, "text": "h\u00e9llo"}}
    assert raw.received.endswith(b"\0") and raw.received.count(b"\0") == 1
    assert json.loads(raw.received[:-1]) == {"type": "execute", "code": "result = {}", "strict_json": True}


def test_multi_chunk_response_from_mock():
    with MockLiveServer(chunk_size=7, chunk_delay=0.001) as srv:
        out = transport_for(srv).run("scene_summary", {})
    assert out.result["scene"] == "Scene" and out.blender_version == "5.2.0 LTS"


def test_connection_closed_without_response_is_live_error():
    raw = RawServer([], close_early=True)
    try:
        with pytest.raises(AxiError) as ei:
            live.send("127.0.0.1", raw.port, "x", strict_json=True, timeout=5)
    finally:
        raw.close()
    assert ei.value.code == "LIVE_ERROR" and "without a complete response" in ei.value.message


def test_garbage_response_is_live_error():
    raw = RawServer([b"not json\0"])
    try:
        with pytest.raises(AxiError) as ei:
            live.send("127.0.0.1", raw.port, "x", strict_json=True, timeout=5)
    finally:
        raw.close()
    assert ei.value.code == "LIVE_ERROR" and "unreadable" in ei.value.message


# -- strict_json, ops, errors -------------------------------------------------------------------

def test_strict_json_true_for_ops_false_for_exec(mock):
    t = transport_for(mock)
    mock.canned["exec"] = {"result": {"n": 5}, "stdout": ""}
    t.run("scene_summary", {})
    t.run("exec", {"code": "result={'n': 5}"})
    assert [(r["op"], r["strict_json"]) for r in mock.requests] == [("scene_summary", True), ("exec", False)]
    assert all(r["type"] == "execute" for r in mock.requests)
    assert mock.requests[1]["params"] == {"code": "result={'n': 5}"}


def test_generated_code_is_valid_python_and_roundtrips_params():
    params = {"code": "print('hi')\nresult = {\"q\": \"it's\"}", "n": [1, 2]}
    code = live.build_code("exec", params, track=True)
    compile(code, "<live>", "exec")
    marker = [ln for ln in code.splitlines() if ln.startswith("_AXI_PARAMS = ")][0]
    import ast

    assert json.loads(ast.literal_eval(marker.split(" = ", 1)[1])) == params
    assert f"_AXI_SRC = {live.SRC_DIR!r}" in code and "_AXI_TRACK = True" in code
    assert Path(live.SRC_DIR, "blender_axi", "live.py").is_file()


def test_status_error_maps_to_live_error_with_traceback_in_detail(mock):
    tb = "Traceback (most recent call last):\n  File \"<string>\", line 1\nZeroDivisionError: division by zero"
    mock.forced_response = {"status": "error", "message": tb}
    with pytest.raises(AxiError) as ei:
        transport_for(mock).run("scene_summary", {})
    err = ei.value
    assert err.code == "LIVE_ERROR" and err.exit_code == 1
    assert "ZeroDivisionError: division by zero" in err.message and err.detail == tb
    assert any("--debug" in h for h in err.help)


def test_module_not_found_error_gets_same_machine_hint(mock):
    mock.forced_response = {"status": "error", "message": "Traceback...\nModuleNotFoundError: No module named 'blender_axi'"}
    with pytest.raises(AxiError) as ei:
        transport_for(mock).run("ping", {})
    assert any("same machine" in h for h in ei.value.help)


def test_op_error_envelope_keeps_its_code(mock):
    mock.canned["object_detail"] = {"__error__": {"code": "NOT_FOUND", "message": "object 'Nope' not found"}}
    with pytest.raises(AxiError) as ei:
        transport_for(mock).run("object_detail", {"name": "Nope"})
    assert ei.value.code == "NOT_FOUND" and "Nope" in ei.value.message and ei.value.exit_code == 1


def test_unknown_request_type_error_is_reported_by_server():
    with MockLiveServer() as srv:
        # Bypass `send` to prove the mock mirrors the real server's request validation.
        with socket.create_connection(("127.0.0.1", srv.port), timeout=5) as s:
            s.sendall(json.dumps({"type": "nope"}).encode() + b"\0")
            reply = s.recv(4096)
    assert json.loads(reply[:-1])["status"] == "error"


# -- connection failures ------------------------------------------------------------------------

def test_refused_is_live_unreachable_with_hints():
    port = free_port()
    with pytest.raises(AxiError) as ei:
        live.LiveTransport("127.0.0.1", port, timeout=5).run("ping", {})
    err = ei.value
    assert err.code == "LIVE_UNREACHABLE" and err.exit_code == 1 and f"127.0.0.1:{port}" in err.message
    joined = " ".join(err.help)
    assert "MCP add-on server" in joined and "blender-axi live" in joined and "--host" in joined


def test_read_timeout_is_timeout_error():
    with MockLiveServer(hang=True, hang_seconds=3) as srv:
        with pytest.raises(AxiError) as ei:
            live.LiveTransport("127.0.0.1", srv.port, timeout=0.3).run("ping", {})
    assert ei.value.code == "TIMEOUT" and "0.3s" in ei.value.message and any("--timeout" in h for h in ei.value.help)


def test_per_call_timeout_overrides_transport_timeout():
    with MockLiveServer(hang=True, hang_seconds=3) as srv:
        with pytest.raises(AxiError) as ei:
            live.LiveTransport("127.0.0.1", srv.port, timeout=60).run("ping", {}, timeout=0.2)
    assert ei.value.code == "TIMEOUT"


# -- ping ---------------------------------------------------------------------------------------

def test_ping(mock):
    assert live.ping("127.0.0.1", mock.port) is True
    assert live.ping("127.0.0.1", free_port()) is False
    assert mock.requests == []  # TCP connect only, nothing sent


def test_ping_and_send_work_for_localhost_with_ipv4_only_server(mock):
    """`localhost` may resolve to ::1 first; the add-on binds IPv4 only, so every address must be tried."""
    assert live.ping("localhost", mock.port, timeout=2.0) is True
    out = live.LiveTransport("localhost", mock.port, timeout=5).run("scene_summary", {})
    assert out.result["object_count"] == 5


# -- save handling ------------------------------------------------------------------------------

def test_save_copy_request_only_when_modified(mock, tmp_path):
    target = str(tmp_path / "copy.blend")
    t = transport_for(mock)
    mock.canned["exec"] = {"result": {}, "stdout": "", "__modified__": False}
    out = t.run("exec", {"code": "result={}"}, save=SaveSpec("copy", target))
    assert (out.modified, out.saved, len(mock.requests)) == (False, None, 1)
    assert "_AXI_TRACK = True" in mock.requests[0]["code"]

    mock.requests.clear()
    mock.canned["exec"] = {"result": {}, "stdout": "", "__modified__": True}
    out = t.run("exec", {"code": "result={}"}, save=SaveSpec("copy", target))
    assert (out.modified, out.saved, len(mock.requests)) == (True, target, 2)
    save_req = mock.requests[1]
    assert save_req["op"] is None and "save_as_mainfile" in save_req["code"] and repr(target) in save_req["code"]
    assert "copy=True" in save_req["code"] and save_req["strict_json"] is True


def test_no_tracking_and_no_save_request_without_save_spec(mock):
    mock.canned["exec"] = {"result": {}, "stdout": "", "__modified__": True}
    out = transport_for(mock).run("exec", {"code": "result={}"})
    assert out.saved is None and len(mock.requests) == 1 and "_AXI_TRACK = False" in mock.requests[0]["code"]


def test_save_in_place_request_only_when_modified(mock):
    t = transport_for(mock)
    mock.canned["exec"] = {"result": {}, "stdout": "", "__modified__": True}
    out = t.run("exec", {"code": "result={}"}, save=SaveSpec("in_place", "C:/x/a.blend"))
    assert out.modified and len(mock.requests) == 2 and "save_mainfile()" in mock.requests[1]["code"]
    assert out.saved == "mock"  # whatever the server reports as the saved path

    mock.requests.clear()
    mock.canned["exec"] = {"result": {}, "stdout": "", "__modified__": False}
    out = t.run("exec", {"code": "result={}"}, save=SaveSpec("in_place", "C:/x/a.blend"))
    assert out.saved is None and len(mock.requests) == 1


# -- generated code really runs (mock `exec` mode + fake bpy) -----------------------------------

def _axi_modules() -> list[str]:
    return [n for n in sys.modules if n == "blender_axi" or n.startswith("blender_axi.")]


@pytest.fixture
def exec_mock(fake_bpy):
    # The generated code purges blender_axi from sys.modules (U12); restore so other tests keep one module identity.
    saved = {n: sys.modules[n] for n in _axi_modules()}
    fake_bpy.ops.wm = types.SimpleNamespace(
        save_as_mainfile=lambda **kw: fake_bpy.__dict__.setdefault("saved", []).append(kw),
        save_mainfile=lambda: None)
    try:
        with MockLiveServer(mode="exec") as srv:
            yield srv
    finally:
        for n in _axi_modules():
            del sys.modules[n]
        sys.modules.update(saved)


def test_generated_code_runs_the_op(exec_mock, fake_bpy):
    out = transport_for(exec_mock).run("ping", {})
    assert out.result["blender_version"] == "5.2.0 LTS" and out.blender_version == "5.2.0 LTS"
    assert out.modified is False


def test_generated_code_unknown_op_is_op_failed(exec_mock):
    with pytest.raises(AxiError) as ei:
        transport_for(exec_mock).run("no_such_op", {})
    assert ei.value.code == "OP_FAILED" and "no_such_op" in ei.value.message


def test_generated_code_exec_op_non_strict_json_and_failure(exec_mock):
    t = transport_for(exec_mock)
    out = t.run("exec", {"code": "print('hello')\nresult = {'o': object(), 'n': 2}"})
    assert out.result["result"]["n"] == 2 and out.result["result"]["o"].startswith("<object object")
    assert out.result["stdout"] == "hello\n"
    with pytest.raises(AxiError) as ei:
        t.run("exec", {"code": "raise ValueError('boom')"})
    assert ei.value.code == "EXEC_FAILED" and "ValueError: boom" in ei.value.message


def test_generated_code_modified_and_copy_save(exec_mock, fake_bpy, tmp_path):
    target = str(tmp_path / "out.blend")
    out = transport_for(exec_mock).run("exec", {"code": "bpy.data.is_dirty = True\nresult = {}"},
                                       save=SaveSpec("copy", target))
    assert out.modified is True and out.saved == target
    assert fake_bpy.saved == [{"filepath": target, "copy": True}]


def test_generated_save_code_keeps_blender_save_chatter_out_of_stdout(fake_bpy, tmp_path, capsys):
    """U10: `Info: Saved copy as ...` is printed by Blender's save; the save request must swallow it."""
    def chatty(**kw):
        print(f"Info: Saved copy as {kw['filepath']}")

    fake_bpy.ops.wm = types.SimpleNamespace(save_as_mainfile=chatty, save_mainfile=lambda: print("Info: Saved x.blend"))
    for code in (live.SAVE_COPY_CODE.format(path=str(tmp_path / "o.blend")), live.SAVE_IN_PLACE_CODE):
        with MockLiveServer(mode="exec") as srv:
            resp = srv.respond(json.dumps({"type": "execute", "code": code, "strict_json": True}).encode())
        assert resp["status"] == "ok" and "stdout" not in resp
    assert capsys.readouterr().out == ""


def test_purge_precedes_the_import_in_every_template_that_imports_blender_axi():
    sources = [live.build_code("ping", {}, track=True), live.build_code("ping", {}, track=False),
               live.SAVE_COPY_CODE, live.SAVE_IN_PLACE_CODE]
    importing = 0
    for source in sources:
        lines = source.splitlines()
        imports = [i for i, ln in enumerate(lines) if ln.lstrip().startswith(("from blender_axi", "import blender_axi"))]
        if not imports:
            continue
        importing += 1
        purge = [i for i, ln in enumerate(lines) if "sys.modules" in ln and "blender_axi" in ln]
        assert purge and min(purge) < min(imports)
    assert importing == 2  # the op request (tracked and untracked); the save requests do not import the package


def test_stale_blender_axi_modules_are_replaced_by_fresh_ones(exec_mock):
    calls = []
    stale = types.ModuleType("blender_axi.blender_side.ops")
    stale.OpError = type("OpError", (Exception,), {})
    stale.run = lambda op, params: calls.append(op) or {"stale": True}
    sys.modules["blender_axi.blender_side.ops"] = stale
    out = transport_for(exec_mock).run("ping", {})
    assert calls == [] and "stale" not in out.result and out.result["blender_version"] == "5.2.0 LTS"
    assert sys.modules["blender_axi.blender_side.ops"] is not stale
    assert hasattr(sys.modules["blender_axi.blender_side.ops"], "__file__")


def test_purge_is_a_noop_when_nothing_is_cached(exec_mock):
    for n in _axi_modules():
        del sys.modules[n]
    out = transport_for(exec_mock).run("ping", {})
    assert out.result["blender_version"] == "5.2.0 LTS"


def test_generated_code_already_dirty_file_is_not_modified(exec_mock, fake_bpy):
    fake_bpy.data.is_dirty = True  # unsaved edits made by the user before we connected
    out = transport_for(exec_mock).run("ping", {}, save=SaveSpec("copy", "x.blend"))
    assert out.modified is False and out.saved is None


# -- the `live` command -------------------------------------------------------------------------

def _inv(argv):
    from blender_axi import flags

    inv, wants_help = flags.parse(cmd_live.SPEC, argv)
    assert not wants_help
    return inv


def test_live_command_shape_and_hints(mock):
    inv = _inv(["--host", "127.0.0.1", "--port", str(mock.port)])
    res = cmd_live.SPEC.run(inv)
    assert list(res.data) == ["live", "blender", "file", "unsaved_changes", "objects"]
    assert res.data["live"] == f"reachable 127.0.0.1:{mock.port}"
    assert res.data["blender"] == "5.2.0 LTS" and res.data["file"] == "C:/mock/basic.blend"
    assert res.data["unsaved_changes"] is False and res.data["objects"] == 5
    assert res.help == [f"Run `blender-axi scene --live --host 127.0.0.1 --port {mock.port}` for the scene overview",
                        f"Run `blender-axi objects --live --host 127.0.0.1 --port {mock.port}` to list objects"]


def test_live_command_unsaved_file_label(mock):
    mock.canned["scene_summary"] = {**mock.canned["scene_summary"], "filepath": "", "is_dirty": True}
    res = cmd_live.SPEC.run(_inv(["--host", "127.0.0.1", "--port", str(mock.port)]))
    assert res.data["file"] == "(unsaved)" and res.data["unsaved_changes"] is True


def test_live_command_file_uses_forward_slashes(mock):
    mock.canned["scene_summary"] = {**mock.canned["scene_summary"], "filepath": "D:\\mock dir\\basic.blend"}
    res = cmd_live.SPEC.run(_inv(["--host", "127.0.0.1", "--port", str(mock.port)]))
    assert res.data["file"] == "D:/mock dir/basic.blend"
    assert "\\" not in res.data["file"]


def run_cli(capsys, argv):
    code = cli.main(["--json", *argv])
    out, err = capsys.readouterr()
    return code, json.loads(out), err


@pytest.fixture
def with_live_registered(monkeypatch):
    monkeypatch.setitem(registry.COMMANDS, "live", "blender_axi.commands.live:SPEC")


def test_cli_live_command_reachable(capsys, mock, with_live_registered):
    code, data, err = run_cli(capsys, ["live", "--host", "127.0.0.1", "--port", str(mock.port)])
    assert code == 0 and err == ""
    assert data["live"] == f"reachable 127.0.0.1:{mock.port}" and data["objects"] == 5


def test_cli_live_command_unreachable_exit_1(capsys, with_live_registered):
    port = free_port()
    code, data, err = run_cli(capsys, ["live", "--host", "127.0.0.1", "--port", str(port)])
    assert code == 1 and data["code"] == "LIVE_UNREACHABLE" and "MCP add-on" in " ".join(data["help"])
    assert err == ""


# -- end to end through cli.main (needs the cli splice) ---------------------------------------

@needs_cli_splice
def test_cli_live_scene_end_to_end(capsys, mock):
    code, data, err = run_cli(capsys, ["--live", "--host", "127.0.0.1", "--port", str(mock.port), "scene"])
    assert code == 0 and err == ""
    assert data["file"] == "C:/mock/basic.blend" and data["scene"] == "Scene" and data["unsaved_changes"] is False
    assert data["objects"].startswith("5 (MESH 2") and data["engine"] == "EEVEE"
    assert any(f"--port {mock.port}" in h and "--live" in h for h in data["help"])
    assert [r["op"] for r in mock.requests] == ["scene_summary"] and mock.requests[0]["strict_json"] is True


@needs_cli_splice
def test_cli_live_objects_forwards_filters(capsys, mock):
    code, data, _ = run_cli(capsys, ["--live", "--host", "127.0.0.1", "--port", str(mock.port),
                                     "objects", "--type", "MESH", "--limit", "7"])
    assert code == 0 and data["objects"][0]["name"] == "Cube"
    assert mock.requests[0]["op"] == "objects_list"
    assert mock.requests[0]["params"]["type"] == "MESH" and mock.requests[0]["params"]["limit"] == 7


@needs_cli_splice
def test_cli_live_timeout_flag_reaches_transport(capsys):
    with MockLiveServer(hang=True, hang_seconds=3) as srv:
        code, data, _ = run_cli(capsys, ["--live", "--host", "127.0.0.1", "--port", str(srv.port),
                                         "--timeout", "0.3", "scene"])
    assert code == 1 and data["code"] == "TIMEOUT"
