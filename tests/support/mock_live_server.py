"""Mock of the official Blender MCP add-on socket server (NUL-framed JSON `execute` protocol).

Binds IPv4 only, like the real add-on. Two modes:

* `canned` (default): answers from a dict keyed by op name; the op is read from the `_AXI_OP = ...` marker line
  in the code that `blender_axi.live` sends. The reply uses the same envelope our generated code returns.
* `exec`: really `exec`s the code with `namespace = {"result": {}}` (the caller installs a fake `bpy` in
  `sys.modules`; the server thread shares the process), applying the real server's `result` / `strict_json` rules.

Every request is recorded in `server.requests`. Run it by hand:

    python tests/support/mock_live_server.py --port 9877 --canned
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import socketserver
import threading
import time
import traceback

SCENE_SUMMARY = {
    "filepath": "C:/mock/basic.blend", "blender_version": "5.2.0 LTS", "scene": "Scene", "engine": "BLENDER_EEVEE",
    "res_x": 64, "res_y": 64, "percent": 100, "frame_start": 1, "frame_end": 24, "frame_current": 1,
    "fps": 24, "fps_base": 1.0, "camera": "Camera", "object_count": 5,
    "types": {"MESH": 2, "CAMERA": 1, "EMPTY": 1, "LIGHT": 1},
    "collections": ["Collection", "Props"], "materials": ["Material"], "is_dirty": False,
}
OBJECTS_LIST = {
    "filepath": "C:/mock/basic.blend", "total": 2, "matched": 2,
    "rows": [{"name": "Cube", "type": "MESH", "collections": ["Collection"], "visible": True},
             {"name": "Camera", "type": "CAMERA", "collections": ["Collection"], "visible": True}],
}
DEFAULT_CANNED = {
    "ping": {"blender_version": "5.2.0 LTS", "python": "3.13", "file": "", "tempdir": "", "env_temp": ""},
    "scene_summary": SCENE_SUMMARY,
    "objects_list": OBJECTS_LIST,
}
_OP_MARKER = re.compile(r"^_AXI_OP = (.+)$", re.MULTILINE)
_PARAMS_MARKER = re.compile(r"^_AXI_PARAMS = (.+)$", re.MULTILINE)


def _encode(response: dict) -> bytes:
    return (json.dumps(response) + "\0").encode("utf-8")


class _Handler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        srv: MockLiveServer = self.server.mock  # type: ignore[attr-defined]
        conn = self.request
        conn.settimeout(10)
        buf = bytearray()
        try:
            while b"\0" not in buf:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                buf.extend(chunk)
            data = bytes(buf[: buf.index(b"\0")])
            response = srv.respond(data)
            if response is None:  # hang mode: never answer
                time.sleep(srv.hang_seconds)
                return
            if srv.delay:
                time.sleep(srv.delay)
            payload = _encode(response)
            if srv.chunk_size:
                for i in range(0, len(payload), srv.chunk_size):
                    conn.sendall(payload[i : i + srv.chunk_size])
                    time.sleep(srv.chunk_delay)
            else:
                conn.sendall(payload)
        except OSError:
            pass


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class MockLiveServer:
    def __init__(self, host: str = "127.0.0.1", port: int = 0, mode: str = "canned", canned: dict | None = None,
                 delay: float = 0.0, chunk_size: int = 0, chunk_delay: float = 0.01,
                 forced_response: dict | None = None, hang: bool = False, hang_seconds: float = 5.0):
        self.mode = mode
        self.canned = dict(DEFAULT_CANNED if canned is None else canned)
        self.delay, self.chunk_size, self.chunk_delay = delay, chunk_size, chunk_delay
        self.forced_response, self.hang, self.hang_seconds = forced_response, hang, hang_seconds
        self.requests: list[dict] = []
        self._server = _Server((host, port), _Handler)
        self._server.mock = self  # type: ignore[attr-defined]
        self.host, self.port = self._server.server_address[:2]
        self._thread: threading.Thread | None = None

    def start(self) -> "MockLiveServer":
        self._thread = threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.05},
                                        daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        if self._thread:
            self._thread.join(timeout=5)

    def __enter__(self) -> "MockLiveServer":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()

    # -- protocol -------------------------------------------------------------------------------
    def respond(self, data: bytes) -> dict | None:
        try:
            request = json.loads(data)
        except ValueError:
            return {"status": "error", "message": "invalid JSON"}
        record = {"raw": request, "type": request.get("type"), "code": request.get("code", ""),
                  "strict_json": request.get("strict_json"), "op": None, "params": None}
        code = record["code"]
        m = _OP_MARKER.search(code)
        if m:
            record["op"] = ast.literal_eval(m.group(1))
            p = _PARAMS_MARKER.search(code)
            if p:
                record["params"] = json.loads(ast.literal_eval(p.group(1)))
        self.requests.append(record)
        if self.hang:
            return None
        if request.get("type") != "execute":
            return {"status": "error", "message": f"Unknown request type: {request.get('type')!r}"}
        if not isinstance(request.get("strict_json"), bool):
            return {"status": "error", "message": "Internal error: request without the required 'strict_json' boolean"}
        if self.forced_response is not None:
            return self.forced_response
        if self.mode == "exec":
            return self._exec(code, request["strict_json"])
        return self._canned(record)

    def _canned(self, record: dict) -> dict:
        op = record["op"]
        if op is None:  # a save request: no op marker
            return {"status": "ok", "result": {"saved": "mock"}}
        if op not in self.canned:
            return {"status": "ok", "result": {"ok": False, "code": "OP_FAILED", "message": f"unknown op {op!r}"}}
        value = self.canned[op]
        if isinstance(value, dict) and value.get("status") in ("ok", "error"):
            return value  # a raw server response
        if isinstance(value, dict) and value.get("__error__"):
            return {"status": "ok", "result": {"ok": False, **value["__error__"]}}
        envelope = {"ok": True, "result": value, "modified": False, "blender_version": "5.2.0 LTS"}
        if isinstance(value, dict) and "__modified__" in value:
            value = dict(value)
            envelope["modified"] = bool(value.pop("__modified__"))
            envelope["result"] = value
        return {"status": "ok", "result": envelope}

    @staticmethod
    def _exec(code: str, strict_json: bool) -> dict:
        namespace: dict = {"result": {}}
        try:
            exec(code, namespace)
        except Exception:
            return {"status": "error", "message": traceback.format_exc()}
        result = namespace["result"]
        if not isinstance(result, dict):
            return {"status": "error", "message": f"The `result` variable must be a dict, not {type(result).__name__}."}
        if strict_json:
            try:
                json.dumps(result)
            except (TypeError, ValueError) as ex:
                return {"status": "error", "message": f"The `result` value is not JSON-serializable: {ex}"}
            return {"status": "ok", "result": result}
        return {"status": "ok", "result": json.loads(json.dumps(result, default=repr))}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9877)
    ap.add_argument("--canned", action="store_true", help="answer from canned op results (default)")
    ap.add_argument("--exec", action="store_true", help="exec the code instead (needs a bpy in sys.modules)")
    args = ap.parse_args()
    srv = MockLiveServer(args.host, args.port, mode="exec" if args.exec else "canned")
    print(f"mock live server on {srv.host}:{srv.port}", flush=True)
    srv.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        srv.stop()


if __name__ == "__main__":
    main()
