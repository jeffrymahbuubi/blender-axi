"""Minimal MCP stdio client (JSON lines) for the official blender-mcp server.

Measures what an agent would read: `call_text` joins the `text` blocks of a `tools/call` result,
`tools_list` returns the raw tool definitions (`compact_json` gives the size an agent pays every session).
Stdlib only. A reader thread + queue give every request a timeout; the server's stderr goes to a log file.
"""

from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "blender-axi-bench", "version": "0"}


class McpError(RuntimeError):
    """The server answered with an error, closed the pipe, or did not answer in time."""


def compact_json(value) -> str:
    """The compact serialisation used for size measurements (no spaces, non-ASCII kept)."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class McpStdioClient:
    """Context manager around one `blender-mcp` stdio server process."""

    def __init__(self, exe: str, env: dict[str, str], log_path: Path, timeout: float = 120.0):
        self.exe = str(exe)
        self.env = dict(env)
        self.log_path = Path(log_path)
        self.timeout = timeout
        self._proc: subprocess.Popen | None = None
        self._log = None
        self._lines: queue.Queue = queue.Queue()
        self._next_id = 0

    # -- lifecycle -----------------------------------------------------------------------------------------

    def __enter__(self) -> "McpStdioClient":
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log = open(self.log_path, "wb")
        try:
            self._proc = subprocess.Popen(
                [self.exe], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self._log, env=self.env,
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
            threading.Thread(target=self._pump, args=(self._proc.stdout,), daemon=True).start()
            self._request("initialize", {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                                         "clientInfo": CLIENT_INFO})
            self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *exc) -> None:
        proc, self._proc = self._proc, None
        try:
            if proc is not None:
                try:
                    proc.stdin.close()
                except OSError:
                    pass
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    pass
                if proc.poll() is None:
                    _kill_tree(proc)
        except Exception:  # teardown must never raise
            pass
        finally:
            if self._log is not None:
                try:
                    self._log.close()
                except OSError:
                    pass
                self._log = None

    # -- protocol ------------------------------------------------------------------------------------------

    def _pump(self, stream) -> None:
        for raw in iter(stream.readline, b""):
            self._lines.put(raw)
        self._lines.put(None)

    def _send(self, message: dict) -> None:
        if self._proc is None or self._proc.poll() is not None:
            raise McpError("server is not running" + self._log_hint())
        try:
            self._proc.stdin.write(json.dumps(message, ensure_ascii=False).encode("utf-8") + b"\n")
            self._proc.stdin.flush()
        except OSError as exc:
            raise McpError(f"cannot write to the server: {exc}" + self._log_hint()) from None

    def _request(self, method: str, params: dict | None = None) -> dict:
        self._next_id += 1
        rid = self._next_id
        message: dict = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            message["params"] = params
        self._send(message)
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                raw = self._lines.get(timeout=max(deadline - time.monotonic(), 0.001))
            except queue.Empty:
                raise McpError(f"{method}: no answer within {self.timeout:g}s" + self._log_hint()) from None
            if raw is None:
                raise McpError(f"{method}: server closed the pipe" + self._log_hint())
            try:
                reply = json.loads(raw.decode("utf-8"))
            except ValueError:
                continue  # not JSON-RPC (stray output)
            if not isinstance(reply, dict) or reply.get("id") != rid or "method" in reply:
                continue  # notification or server-side request
            if "error" in reply:
                raise McpError(f"{method}: {reply['error']}")
            return reply.get("result") or {}

    def _log_hint(self) -> str:
        try:
            tail = self.log_path.read_text(encoding="utf-8", errors="replace")[-400:].strip()
        except OSError:
            return ""
        return f" (server log tail: {tail})" if tail else ""

    # -- public --------------------------------------------------------------------------------------------

    def tools_list(self) -> list[dict]:
        return list(self._request("tools/list").get("tools", []))

    def call_text(self, name: str, arguments: dict) -> tuple[str, int]:
        """(joined text of the result content, elapsed ms). `McpError` on a protocol error or `isError`."""
        started = time.monotonic()
        result = self._request("tools/call", {"name": name, "arguments": arguments})
        ms = round((time.monotonic() - started) * 1000)
        text = "".join(c.get("text", "") for c in result.get("content", []) if c.get("type", "text") == "text")
        if result.get("isError"):
            raise McpError(f"{name} failed: {text[:300]}")
        return text, ms


def _kill_tree(proc: subprocess.Popen) -> None:
    """Terminate only the process we started (and its children on Windows)."""
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, timeout=30)
    else:
        proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)
