"""A tiny MCP server over stdio (JSON lines) for the benchmark client tests. Run: `python fake_mcp_server.py`.

Answers `initialize`, ignores notifications, `tools/list` -> two tools, `tools/call` -> text content echoing
`{"name": ..., "args": ...}`. Special tool names: `boom` -> isError result, `multi` -> three text blocks,
`_seen` -> the methods received so far (handshake-order checks).
"""

import json
import sys

TOOLS = [
    {"name": "echo", "description": "Echo the arguments", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "boom", "description": "Always fails", "inputSchema": {"type": "object", "properties": {}}},
]


def reply(rid, result):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": rid, "result": result}) + "\n")
    sys.stdout.flush()


def main() -> None:
    seen: list[str] = []
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        method = msg.get("method", "")
        seen.append(method)
        if "id" not in msg:
            # a notification; emit one of our own to prove the client skips unrelated lines
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/message", "params": {}}) + "\n")
            sys.stdout.flush()
            continue
        rid = msg["id"]
        if method == "initialize":
            reply(rid, {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                        "serverInfo": {"name": "fake", "version": "0"}})
        elif method == "tools/list":
            reply(rid, {"tools": TOOLS})
        elif method == "tools/call":
            name = msg["params"]["name"]
            args = msg["params"].get("arguments", {})
            if name == "boom":
                reply(rid, {"content": [{"type": "text", "text": "kaboom"}], "isError": True})
            elif name == "multi":
                reply(rid, {"content": [{"type": "text", "text": "a"}, {"type": "image", "data": "x"},
                                        {"type": "text", "text": "b"}, {"type": "text", "text": "ü"}]})
            elif name == "_seen":
                reply(rid, {"content": [{"type": "text", "text": json.dumps(seen)}]})
            else:
                reply(rid, {"content": [{"type": "text", "text": json.dumps({"name": name, "args": args})}]})
        else:
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": rid,
                                         "error": {"code": -32601, "message": "unknown method"}}) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
