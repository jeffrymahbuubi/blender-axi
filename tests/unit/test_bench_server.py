"""bench.server: free_port and AddonServer start-up failure handling with stub processes (no Blender)."""

import socket
import subprocess
import sys

import pytest

from bench import server
from bench.server import AddonServer, ServerError, free_port


def test_free_port_is_bindable_and_not_9876():
    for _ in range(20):
        port = free_port()
        assert port != 9876 and 1024 < port < 65536
        with socket.socket() as s:
            s.bind(("127.0.0.1", port))


def test_free_port_redraws_when_it_gets_9876(monkeypatch):
    real = socket.socket
    drawn = []

    class Fake:
        def __init__(self):
            self.s = real()
            self.calls = 0

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self.s.close()

        def bind(self, addr):
            self.s.bind(addr)

        def getsockname(self):
            port = self.s.getsockname()[1]
            if not drawn:  # first draw pretends to be the GUI Blender's port
                drawn.append(1)
                return ("127.0.0.1", 9876)
            return ("127.0.0.1", port)

    monkeypatch.setattr(server.socket, "socket", Fake)
    assert free_port() != 9876
    assert drawn


def _alive(pid: int) -> bool:
    if sys.platform == "win32":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        import os
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def test_never_opening_the_port_raises_server_error_with_log_tail_and_reaps_the_stub(tmp_path):
    code = "import time; print('stub says hello', flush=True); time.sleep(30)"
    log = tmp_path / "logs" / "server.log"
    srv = AddonServer("unused", tmp_path / "x.blend", log, startup_s=1.5, command=[sys.executable, "-c", code])
    with pytest.raises(ServerError) as ei:
        srv.__enter__()
    assert "stub says hello" in str(ei.value) and str(srv.port) in str(ei.value)
    assert srv._proc is None  # torn down by the failed start


def test_server_that_exits_early_reports_exit_code_and_log(tmp_path):
    log = tmp_path / "server.log"
    srv = AddonServer("unused", tmp_path / "x.blend", log, startup_s=10,
                      command=[sys.executable, "-c", "import sys; print('bad flag', file=sys.stderr); sys.exit(3)"])
    with pytest.raises(ServerError, match="exited with code 3") as ei:
        srv.__enter__()
    assert "bad flag" in str(ei.value)


def test_stub_process_is_gone_after_a_failed_start(tmp_path, monkeypatch):
    pids = []
    real_popen = subprocess.Popen

    def spy(*a, **kw):
        p = real_popen(*a, **kw)
        pids.append(p.pid)
        return p

    monkeypatch.setattr(server.subprocess, "Popen", spy)
    srv = AddonServer("unused", tmp_path / "x.blend", tmp_path / "s.log", startup_s=1.0,
                      command=[sys.executable, "-c", "import time; time.sleep(5)"])
    with pytest.raises(ServerError):
        srv.__enter__()
    assert pids and not _alive(pids[0])


def test_context_manager_succeeds_when_the_port_opens(tmp_path):
    """A stub that listens on the server's port: __enter__ returns, __exit__ kills it."""
    srv = AddonServer("unused", tmp_path / "x.blend", tmp_path / "s.log", startup_s=20)
    code = ("import socket, time; s = socket.socket(); s.bind(('127.0.0.1', %d)); s.listen(); time.sleep(30)"
            % srv.port)
    srv._command = [sys.executable, "-c", code]
    with srv as running:
        assert running.port == srv.port
        pid = srv._proc.pid
        assert _alive(pid)
    assert not _alive(pid)


def test_argv_matches_the_verified_real_server_pattern(tmp_path):
    srv = AddonServer(tmp_path / "blender.exe", tmp_path / "a.blend", tmp_path / "s.log")
    argv = srv.argv()
    assert argv[1:5] == ["--background", str(tmp_path / "a.blend"), "--online-mode", "--command"]
    assert argv[5:] == ["blender_mcp", "--host", "127.0.0.1", "--port", str(srv.port)]
    assert srv.port != 9876


def test_strip_png_text_drops_metadata_chunks(tmp_path):
    import struct
    import zlib

    from bench.scenes import strip_png_text

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0))
    idat = chunk(b"IDAT", zlib.compress(b"\x00\x00"))
    text = chunk(b"tEXt", b"File\x00D:\secret\path\scene.blend")
    png = tmp_path / "r.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + ihdr + text + idat + chunk(b"IEND", b""))
    strip_png_text(png)
    out = png.read_bytes()
    assert b"secret" not in out and b"tEXt" not in out
    assert out == b"\x89PNG\r\n\x1a\n" + ihdr + idat + chunk(b"IEND", b"")
