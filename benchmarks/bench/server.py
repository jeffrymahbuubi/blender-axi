"""Headless Blender add-on server (`blender --background <blend> --online-mode --command blender_mcp`).

Pattern verified in tests/integration/test_live_optional.py::real_server. Only the process this module starts is ever
terminated (its own PID; `taskkill /PID <pid> /T /F` on Windows). Port 9876 belongs to the user's GUI Blender and is
never used.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path

from blender_axi import live, paths

HOST = "127.0.0.1"
RESERVED_PORT = 9876  # the user's GUI Blender


class ServerError(RuntimeError):
    """The add-on server did not come up; the message ends with the tail of its log."""


def free_port() -> int:
    """An unused TCP port on 127.0.0.1 from the OS; re-drawn while it is the reserved 9876."""
    for _ in range(50):
        with socket.socket() as s:
            s.bind((HOST, 0))
            port = s.getsockname()[1]
        if port != RESERVED_PORT:
            return port
    raise ServerError("could not draw a free port other than 9876")


class AddonServer:
    """Context manager: start the server on a fresh port and wait until it accepts connections."""

    def __init__(self, blender_exe: Path, blend: Path, log_path: Path, startup_s: float = 120.0,
                 command: Sequence[str] | None = None):
        self.blender_exe = Path(blender_exe)
        self.blend = Path(blend)
        self.log_path = Path(log_path)
        self.startup_s = startup_s
        self.port = free_port()
        self._command = list(command) if command is not None else None  # tests substitute a stub process
        self._proc: subprocess.Popen | None = None

    def argv(self) -> list[str]:
        if self._command is not None:
            return self._command
        return [str(self.blender_exe), "--background", str(self.blend), "--online-mode", "--command", "blender_mcp",
                "--host", HOST, "--port", str(self.port)]

    def _tail(self) -> str:
        try:
            return self.log_path.read_text(encoding="utf-8", errors="replace")[-600:].strip()
        except OSError:
            return ""

    def __enter__(self) -> "AddonServer":
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.log_path, "wb") as log_file:
            self._proc = subprocess.Popen(
                self.argv(), env=paths.blender_env(), cwd=str(self.log_path.parent), stdin=subprocess.DEVNULL,
                stdout=log_file, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        try:
            deadline = time.monotonic() + self.startup_s
            while time.monotonic() < deadline:
                if self._proc.poll() is not None:
                    raise ServerError(f"server exited with code {self._proc.poll()} before port {self.port} opened: "
                                      f"{self._tail()}")
                if live.ping(HOST, self.port, 0.3):
                    return self
                time.sleep(0.25)
            raise ServerError(f"server did not open port {self.port} within {self.startup_s:g}s: {self._tail()}")
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *exc) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            if proc.poll() is None:
                if sys.platform == "win32":
                    subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, timeout=30)
                else:
                    proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)
        except Exception:  # teardown must never raise
            pass
