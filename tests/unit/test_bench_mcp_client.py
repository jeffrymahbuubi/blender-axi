"""bench.mcp_client against tests/support/fake_mcp_server.py (no Blender, no network)."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from bench.mcp_client import McpError, McpStdioClient, compact_json

FAKE = Path(__file__).resolve().parents[1] / "support" / "fake_mcp_server.py"


@pytest.fixture
def exe(tmp_path):
    """An executable that runs the fake server (a .cmd launcher on Windows, a script elsewhere)."""
    if sys.platform == "win32":
        launcher = tmp_path / "fake-mcp.cmd"
        launcher.write_text(f'@"{sys.executable}" "{FAKE}"\r\n', encoding="utf-8")
    else:
        launcher = tmp_path / "fake-mcp.sh"
        launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE}"\n', encoding="utf-8")
        launcher.chmod(0o755)
    return str(launcher)


def _client(exe, tmp_path, **kw):
    return McpStdioClient(exe, dict(os.environ), tmp_path / "logs" / "mcp.log", **kw)


def test_handshake_order_and_tools_list(exe, tmp_path):
    with _client(exe, tmp_path) as c:
        assert [t["name"] for t in c.tools_list()] == ["echo", "boom"]
        text, ms = c.call_text("_seen", {})
        assert ms >= 0
    assert json.loads(text) == ["initialize", "notifications/initialized", "tools/list", "tools/call"]


def test_call_text_echoes_and_joins_text_blocks(exe, tmp_path):
    with _client(exe, tmp_path) as c:
        text, _ = c.call_text("echo", {"name": "Mug"})
        assert json.loads(text) == {"name": "echo", "args": {"name": "Mug"}}
        joined, _ = c.call_text("multi", {})
        assert joined == "abü"  # the image block is skipped, text blocks are concatenated


def test_is_error_raises_mcp_error(exe, tmp_path):
    with _client(exe, tmp_path) as c:
        with pytest.raises(McpError, match="kaboom"):
            c.call_text("boom", {})
        assert c.call_text("echo", {})[0]  # the client is still usable


def test_protocol_error_raises(exe, tmp_path):
    with _client(exe, tmp_path) as c:
        with pytest.raises(McpError, match="unknown method"):
            c._request("no/such/method")


def test_no_child_left_after_exit(exe, tmp_path):
    c = _client(exe, tmp_path)
    with c:
        proc = c._proc
        assert proc.poll() is None
    assert proc.poll() is not None
    c.__exit__(None, None, None)  # idempotent, never raises


def test_missing_exe_raises_before_any_request(tmp_path):
    with pytest.raises(OSError):
        with _client(str(tmp_path / "nope.exe"), tmp_path):
            pass


def test_timeout_when_server_never_answers(tmp_path):
    silent = tmp_path / "silent.py"
    silent.write_text("import sys, time\ntime.sleep(30)\n", encoding="utf-8")
    if sys.platform == "win32":
        launcher = tmp_path / "silent.cmd"
        launcher.write_text(f'@"{sys.executable}" "{silent}"\r\n', encoding="utf-8")
    else:
        launcher = tmp_path / "silent.sh"
        launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{silent}"\n', encoding="utf-8")
        launcher.chmod(0o755)
    c = _client(str(launcher), tmp_path, timeout=0.5)
    with pytest.raises(McpError, match="no answer"):
        c.__enter__()
    assert c._proc is None  # the failed start already tore the process down


def test_compact_json_has_no_spaces_and_keeps_unicode():
    out = compact_json({"a": [1, 2], "b": "ü"})
    assert out == '{"a":[1,2],"b":"ü"}'
    assert " " not in out


def test_exit_leaves_no_python_child(exe, tmp_path):
    """The .cmd launcher spawns python as a child; the whole tree must be gone after __exit__."""
    c = _client(exe, tmp_path)
    with c:
        pid = c._proc.pid
    if sys.platform == "win32":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True).stdout
        assert str(pid) not in out
