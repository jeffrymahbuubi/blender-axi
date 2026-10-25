"""Live transport: run blender-axi ops inside an already running Blender (plan section 3.9, decisions D13/D15).

The wire protocol belongs to the official Blender Lab MCP add-on ("MCP", extension id `mcp`, GPL-3.0-or-later,
commit dbbf836): `addon/blender_mcp_addon/mcp_to_blender_server.py` in
https://projects.blender.org/lab/blender_mcp. Adapted from its documented behaviour:

* one TCP connection per request, request and response are UTF-8 JSON terminated by a NUL byte;
* request  `{"type": "execute", "code": <python source>, "strict_json": <bool>}`;
* response `{"status": "ok", "result": <the dict the code assigned to `result`>, "stdout"?, "stderr"?}`
  or `{"status": "error", "message": <traceback>}`;
* `strict_json=True` makes the server reject a non-JSON-serialisable `result`; we send True for our own ops and
  False for `exec`, where user code may return anything (the server then falls back to `repr`).

The code we send imports `blender_axi.blender_side.ops` from this package's `src` directory, so the live Blender must
run on the same machine as blender-axi. Python would cache that import for the life of the Blender session, so every
request first purges `blender_axi` / `blender_axi.*` from `sys.modules` and imports fresh (U12).

Stdlib only; no import of the Blender-side modules at import time.
"""

from __future__ import annotations

import json
import queue
import socket
import threading
import time
from pathlib import Path

from .errors import AxiError
from .runner import RunOutcome, SaveSpec
from .spec import DEFAULT_LIVE_HOST, DEFAULT_LIVE_PORT

DEFAULT_TIMEOUT = 300.0
CONNECT_TIMEOUT = 2.0
PING_TIMEOUT = 0.3
RECV_BYTES = 65536
NON_STRICT_OPS = frozenset({"exec", "exec_code"})  # decision D13: user code may return non-JSON values
MUTATING_OPS = NON_STRICT_OPS | frozenset({"add", "set", "delete", "material"})  # tracked + undo step in live mode
SRC_DIR = str(Path(__file__).resolve().parents[1])

# Every request first puts our src on sys.path and purges any cached blender_axi modules (U12), so the code it
# imports is the code on disk, however long the Blender session has been running.
_PRELUDE = """\
import sys
_AXI_SRC = {src!r}
if _AXI_SRC not in sys.path:
    sys.path.insert(0, _AXI_SRC)
for _name in [_n for _n in sys.modules if _n == "blender_axi" or _n.startswith("blender_axi.")]:
    del sys.modules[_name]  # U12: a long-lived Blender would otherwise keep running stale cached op code
"""

# Markers on their own lines (`_AXI_OP = ...`) are also what tests/support/mock_live_server.py parses.
OP_CODE = """\
import json
_AXI_OP = {op!r}
_AXI_PARAMS = {params!r}
_AXI_TRACK = {track!r}
_AXI_UNDO = {undo!r}
""" + _PRELUDE + """\
import bpy
from blender_axi.blender_side import ops, session
_undo = session.undo_push(bpy, "blender-axi exec") if _AXI_UNDO else None
_before_dirty = bool(bpy.data.is_dirty)  # after the undo push, which may tag the file
_base = None
if _AXI_TRACK:
    from blender_axi.blender_side import bootstrap
    _base = bootstrap.baseline(bpy)
try:
    _out = ops.run(_AXI_OP, json.loads(_AXI_PARAMS))
    _modified = bool(bpy.data.is_dirty) and not _before_dirty
    _frame = None
    if _base is not None:
        _changed, _frame = bootstrap.modified_since(bpy, _base)
        _modified = _modified or _changed
    if _modified:
        session.mark_modified(bpy)
    result = {{"ok": True, "result": _out, "modified": _modified, "blender_version": bpy.app.version_string,
              "frame_changed": _frame, "undo": _undo}}
except ops.OpError as _exc:
    if _base is not None and bootstrap.modified_since(bpy, _base)[0]:
        session.mark_modified(bpy)  # the code changed the session before it failed
    result = {{"ok": False, "code": _exc.code, "message": _exc.message}}
"""

SAVE_COPY_CODE = """\
import contextlib, io, os
import bpy
_AXI_SAVE = {path!r}
os.makedirs(os.path.dirname(os.path.abspath(_AXI_SAVE)), exist_ok=True)
with contextlib.redirect_stdout(io.StringIO()):
    bpy.ops.wm.save_as_mainfile(filepath=_AXI_SAVE, copy=True)
result = {{"saved": _AXI_SAVE}}
"""

# Saving in place makes the file clean again, so the "modified by blender-axi" marker is dropped with it.
SAVE_IN_PLACE_CODE = (_PRELUDE + """\
import contextlib, io
import bpy
from blender_axi.blender_side import session
with contextlib.redirect_stdout(io.StringIO()):
    bpy.ops.wm.save_mainfile()
session.clear_marker(bpy)
result = {{"saved": bpy.data.filepath}}
""").format(src=SRC_DIR)


def build_code(op: str, params: dict, track: bool = False, undo: bool = False) -> str:
    return OP_CODE.format(src=SRC_DIR, op=op, params=json.dumps(params), track=track, undo=undo)


def _unreachable(host: str, port: int, reason: str) -> AxiError:
    other = (f" --host {host}" if host != DEFAULT_LIVE_HOST else "") + (
        f" --port {port}" if port != DEFAULT_LIVE_PORT else "")
    return AxiError(
        "LIVE_UNREACHABLE", f"cannot connect to Blender at {host}:{port} ({reason})",
        help=["Start Blender with the MCP add-on server enabled, or run blender-axi without --live",
              f"Check the address with `blender-axi live{other}`; change it with --host <host> --port <n>"],
    )


def _connect_one(address: str, port: int, timeout: float) -> socket.socket:
    return socket.create_connection((address, port), timeout=timeout)


def _addresses(host: str, port: int) -> list[str]:
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    seen: list[str] = []
    for info in infos:
        addr = info[4][0]
        if addr not in seen:
            seen.append(addr)
    return seen


def connect(host: str, port: int, timeout: float = CONNECT_TIMEOUT) -> socket.socket:
    """Connected socket to the first address of `host` that accepts; all resolved addresses are tried in parallel.

    `localhost` resolves to ::1 and 127.0.0.1 on Windows while the add-on binds IPv4 only, and a refused loopback
    connect costs the full timeout there, so serial tries would add up. Raises AxiError LIVE_UNREACHABLE.
    """
    try:
        addrs = _addresses(host, port)
    except OSError as exc:
        raise _unreachable(host, port, f"cannot resolve host: {exc}") from None
    if not addrs:
        raise _unreachable(host, port, "no address")
    if len(addrs) == 1:
        try:
            return _connect_one(addrs[0], port, timeout)
        except OSError as exc:
            raise _unreachable(host, port, _reason(exc)) from None
    results: queue.Queue = queue.Queue()

    def attempt(addr: str) -> None:
        try:
            results.put((True, _connect_one(addr, port, timeout)))
        except OSError as exc:
            results.put((False, exc))

    for addr in addrs:
        threading.Thread(target=attempt, args=(addr,), daemon=True).start()
    winner: socket.socket | None = None
    last: OSError | None = None
    pending = len(addrs)
    while pending and winner is None:
        ok, value = results.get()
        pending -= 1
        if ok:
            winner = value
        else:
            if last is None or isinstance(value, ConnectionRefusedError):  # refused beats timed out (::1 vs IPv4)
                last = value
    if winner is not None and pending:  # do not wait for slow attempts (a refused ::1 costs the full timeout)
        threading.Thread(target=_reap, args=(results, pending), daemon=True).start()
    if winner is None:
        raise _unreachable(host, port, _reason(last))
    return winner


def _reap(results: queue.Queue, pending: int) -> None:
    """Close sockets from connect attempts that finish after another address already won."""
    for _ in range(pending):
        ok, value = results.get()
        if ok:
            value.close()


def _reason(exc: OSError | None) -> str:
    if exc is None:
        return "no response"
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return "no response; nothing is listening there or it timed out"
    if isinstance(exc, ConnectionRefusedError):
        return "connection refused"
    return str(exc) or type(exc).__name__


def ping(host: str = DEFAULT_LIVE_HOST, port: int = DEFAULT_LIVE_PORT, timeout: float = PING_TIMEOUT) -> bool:
    """TCP connect only, no request is sent."""
    try:
        sock = connect(host, port, timeout)
    except AxiError:
        return False
    sock.close()
    return True


def send(host: str, port: int, code: str, *, strict_json: bool, timeout: float = DEFAULT_TIMEOUT) -> dict:
    """One `execute` round trip. Returns the parsed response dict (`status` is not interpreted here)."""
    started = time.monotonic()
    payload = json.dumps({"type": "execute", "code": code, "strict_json": strict_json}).encode("utf-8") + b"\0"
    sock = connect(host, port, min(CONNECT_TIMEOUT, timeout))
    buf = bytearray()
    try:
        with sock:
            sock.settimeout(max(timeout - (time.monotonic() - started), 0.001))
            sock.sendall(payload)
            while b"\0" not in buf:
                remaining = timeout - (time.monotonic() - started)
                if remaining <= 0:
                    raise socket.timeout()
                sock.settimeout(remaining)
                chunk = sock.recv(RECV_BYTES)
                if not chunk:
                    break
                buf.extend(chunk)
    except (socket.timeout, TimeoutError):
        raise AxiError("TIMEOUT", f"live request timed out after {timeout:g}s",
                       help=["Raise the limit with `--timeout <seconds>`"]) from None
    except OSError as exc:
        raise AxiError("LIVE_ERROR", f"connection to Blender at {host}:{port} failed: {exc}",
                       help=["Rerun with `--debug` for details", "Check that the MCP add-on server is still running"],
                       detail=str(exc)) from None
    if b"\0" not in buf:
        raise AxiError("LIVE_ERROR", f"Blender at {host}:{port} closed the connection without a complete response",
                       help=["Rerun with `--debug` for details", "Check that the MCP add-on server is still running"])
    try:
        response = json.loads(bytes(buf[: buf.index(b"\0")]).decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise AxiError("LIVE_ERROR", "Blender sent an unreadable response",
                       help=["Rerun with `--debug` for details"], detail=str(exc)) from None
    if not isinstance(response, dict):
        raise AxiError("LIVE_ERROR", f"Blender sent a {type(response).__name__} instead of an object",
                       help=["Rerun with `--debug` for details"])
    return response


def _server_error(response: dict, host: str, port: int) -> AxiError:
    message = str(response.get("message") or "unknown error")
    last = next((ln.strip() for ln in reversed(message.splitlines()) if ln.strip()), message)
    help_ = ["Rerun with `--debug` for the traceback"]
    if "No module named 'blender_axi'" in message or "No module named blender_axi" in message:
        help_.append(f"The live Blender must run on the same machine as blender-axi (it imports {SRC_DIR})")
    return AxiError("LIVE_ERROR", f"Blender reported an error: {last}", help=help_, detail=message)


class LiveTransport:
    """Transport that executes ops in a running Blender over the MCP add-on socket."""

    name = "live"

    def __init__(self, host: str = DEFAULT_LIVE_HOST, port: int = DEFAULT_LIVE_PORT, timeout: float = DEFAULT_TIMEOUT):
        self.host = host
        self.port = port
        self.timeout = timeout

    def _execute(self, code: str, strict_json: bool, timeout: float) -> dict:
        response = send(self.host, self.port, code, strict_json=strict_json, timeout=timeout)
        if response.get("status") != "ok":
            raise _server_error(response, self.host, self.port)
        return response

    def run(self, op: str, params: dict, *, blend: Path | None = None, save: SaveSpec = SaveSpec("none"),
            timeout: float | None = None) -> RunOutcome:
        """`blend` is ignored: live mode always works on the file open in Blender."""
        timeout = self.timeout if timeout is None else timeout
        started = time.perf_counter()
        strict = op not in NON_STRICT_OPS
        track = save.mode != "none" or op in MUTATING_OPS  # mutating ops always report modified (session marker)
        undo = op in MUTATING_OPS  # best-effort undo step before the op runs
        response = self._execute(build_code(op, params or {}, track=track, undo=undo), strict, timeout)
        envelope = response.get("result")
        if not isinstance(envelope, dict):
            raise AxiError("LIVE_ERROR", "Blender returned no result", help=["Rerun with `--debug` for details"])
        if not envelope.get("ok", False):
            raise AxiError(str(envelope.get("code") or "OP_FAILED"),
                           str(envelope.get("message") or f"`{op}` failed inside Blender"),
                           help=["Rerun with `--debug` for details"], detail=envelope.get("message"))
        modified = bool(envelope.get("modified"))
        saved: str | None = None
        if modified and save.mode == "copy" and save.path:
            self._execute(SAVE_COPY_CODE.format(path=save.path), True, timeout)
            saved = save.path
        elif modified and save.mode == "in_place":
            saved_resp = self._execute(SAVE_IN_PLACE_CODE, True, timeout)
            saved = str((saved_resp.get("result") or {}).get("saved") or save.path or "") or None
        return RunOutcome(
            result=envelope.get("result") or {}, modified=modified, saved=saved,
            blender_version=str(envelope.get("blender_version") or ""),
            duration_ms=int((time.perf_counter() - started) * 1000),
            stdout=str(response.get("stdout") or ""), warnings=[],
            frame_changed=envelope.get("frame_changed") or None,
            undo={True: "pushed", False: "unavailable"}.get(envelope.get("undo")),
        )
