"""Live mode against a REAL headless Blender MCP server (U8). Needs Blender 5.2 with the official MCP extension.

The module starts `blender --background <fixture> --online-mode --command blender_mcp --host 127.0.0.1 --port <free>`
(no --factory-startup: the extension lives in the user config), skips when it never comes up, and always kills the
process tree at teardown. The mutating exec test starts its own server so the shared one stays pristine.
`test_cli_live_*` also need the lead's cli.py splice and skip until it is applied.
"""

from __future__ import annotations

import inspect
import json
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

from blender_axi import cli, live, paths, registry
from blender_axi.runner import SaveSpec

pytestmark = pytest.mark.blender

STARTUP_SECONDS = 60
needs_cli_splice = pytest.mark.skipif(
    "LiveTransport" not in inspect.getsource(cli), reason="needs the lead's cli.py splice (U8, LiveTransport)")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _kill_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
    if sys.platform == "win32":  # also reaps any child Blender spawned
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, timeout=30)
    elif proc.poll() is None:
        proc.kill()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


@contextmanager
def real_server(blender_info, blend: Path):
    """Yield (port, log_path) of a running headless MCP server on `blend`; skip when it never starts."""
    port = _free_port()
    rd = paths.run_dir("liveserver")
    log = rd / "server.log"
    argv = [str(blender_info.path), "--background", str(blend), "--online-mode", "--command", "blender_mcp",
            "--host", "127.0.0.1", "--port", str(port)]
    with open(log, "wb") as log_file:
        proc = subprocess.Popen(argv, env=paths.blender_env(), cwd=str(rd), stdin=subprocess.DEVNULL,
                                stdout=log_file, stderr=subprocess.STDOUT,
                                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        try:
            deadline = time.monotonic() + STARTUP_SECONDS
            up = False
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    break
                if live.ping("127.0.0.1", port, 0.3):
                    up = True
                    break
                time.sleep(0.25)
            if not up:
                log_file.flush()
                tail = log.read_text(encoding="utf-8", errors="replace")[-600:]
                pytest.skip(f"headless MCP server did not come up on port {port} "
                            f"(exit={proc.poll()}): {tail.strip()}")
            yield port, log
        finally:
            _kill_tree(proc)
    paths.cleanup(rd, False)


@pytest.fixture(scope="module")
def server(blender_info, fixtures_dir):
    with real_server(blender_info, fixtures_dir / "basic.blend") as (port, _log):
        yield port


@pytest.fixture
def run_cli(capsys):
    def go(argv):
        code = cli.main(["--json", *argv])
        out, err = capsys.readouterr()
        return code, json.loads(out), err

    return go


def _addr(port: int) -> list[str]:
    return ["--host", "127.0.0.1", "--port", str(port)]


def test_ping_and_scene_summary_over_the_real_socket(server):
    assert live.ping("127.0.0.1", server)
    t = live.LiveTransport("127.0.0.1", server, timeout=60)
    out = t.run("scene_summary", {})
    r = out.result
    assert r["filepath"].endswith("basic.blend") and r["object_count"] == 5 and r["res_x"] == 64
    assert out.blender_version.startswith("5.") and out.modified is False


def test_localhost_resolves_and_connects(server):
    assert live.ping("localhost", server, timeout=2.0)
    assert live.LiveTransport("localhost", server, timeout=60).run("ping", {}).result["blender_version"]


def test_live_command(run_cli, server, monkeypatch):
    monkeypatch.setitem(registry.COMMANDS, "live", "blender_axi.commands.live:SPEC")
    code, data, err = run_cli(["live", *_addr(server)])
    assert code == 0 and err == ""
    assert data["live"] == f"reachable 127.0.0.1:{server}" and data["blender"].startswith("5.")
    assert data["file"].endswith("basic.blend") and data["unsaved_changes"] is False and data["objects"] == 5
    assert "\\" not in data["file"]  # U10: forward slashes
    assert any("scene --live" in h for h in data["help"])


def test_live_command_unreachable(run_cli, monkeypatch):
    monkeypatch.setitem(registry.COMMANDS, "live", "blender_axi.commands.live:SPEC")
    code, data, _ = run_cli(["live", "--host", "127.0.0.1", "--port", str(_free_port())])
    assert code == 1 and data["code"] == "LIVE_UNREACHABLE"


@needs_cli_splice
def test_cli_live_scene(run_cli, server):
    code, data, err = run_cli(["--live", *_addr(server), "scene"])
    assert code == 0 and err == ""
    assert data["file"].endswith("basic.blend") and data["scene"] == "Scene" and data["resolution"] == "64x64@100%"
    assert "\\" not in data["file"]
    assert data["frames"] == "1-24 (current 1)" and data["objects"] == "5 (MESH 2, CAMERA 1, EMPTY 1, LIGHT 1)"
    assert data["unsaved_changes"] is False and data["camera"] == "Camera"


@needs_cli_splice
def test_cli_live_objects_and_object(run_cli, server):
    code, data, _ = run_cli(["--live", *_addr(server), "objects", "--type", "MESH", "--fields", "verts"])
    assert code == 0 and {o["name"] for o in data["objects"]} == {"Cube", "Ball"} and "\\" not in data["file"]
    assert data["count"].startswith("2 of 2 matching")
    code, data, _ = run_cli(["--live", *_addr(server), "object", "Ball"])
    assert code == 0 and data["name"] == "Ball" and data["type"] == "MESH" and data["collection"] == "Props"
    code, data, _ = run_cli(["--live", *_addr(server), "object", "NoSuchObject"])
    assert code == 1 and data["code"] == "NOT_FOUND"


def test_read_only_exec_is_not_modified_and_not_strict(server):
    t = live.LiveTransport("127.0.0.1", server, timeout=60)
    out = t.run("exec", {"code": "print('hi')\nresult = {'n': len(bpy.data.objects), 'o': object()}"},
                save=SaveSpec("copy", str(paths.tmp_root() / "live" / "never.blend")))
    assert out.result["result"]["n"] == 5 and out.result["result"]["o"].startswith("<object object")
    assert out.modified is False and out.saved is None
    assert not (paths.tmp_root() / "live" / "never.blend").exists()


def test_exec_failure_is_exec_failed(server):
    t = live.LiveTransport("127.0.0.1", server, timeout=60)
    with pytest.raises(live.AxiError) as ei:
        t.run("exec", {"code": "raise ValueError('boom')"})
    assert ei.value.code == "EXEC_FAILED" and "boom" in ei.value.message


def test_modifying_exec_saves_a_copy_in_a_private_server(blender_info, fixtures_dir):
    target = paths.tmp_root() / "live" / "modified-copy.blend"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)
    with real_server(blender_info, fixtures_dir / "basic.blend") as (port, _log):
        t = live.LiveTransport("127.0.0.1", port, timeout=60)
        code = "bpy.data.objects.remove(bpy.data.objects['Ball'])\nresult = {'n': len(bpy.data.objects)}"
        out = t.run("exec", {"code": code}, save=SaveSpec("copy", str(target)))
        assert out.result["result"]["n"] == 4 and out.modified is True and out.saved == str(target)
        assert out.result["stdout"] == "" and out.stdout == ""  # the save's own output is not reported
        assert target.is_file() and target.stat().st_size > 0
        # the live session now differs from the file on disk, which the scene summary reports
        assert t.run("scene_summary", {}).result["object_count"] == 4
    target.unlink(missing_ok=True)
